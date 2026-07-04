from __future__ import annotations

import logging
import tempfile
from contextlib import suppress
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd
from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.llm.expense_parser import (
    ExpenseParser,
    dataframe_to_confirmation_text,
    parse_rows,
    rows_to_dataframe,
)
from app.sheets.client import GoogleSheetsClient
from app.telegram_bot.daily_report import send_daily_report
from app.telegram_bot.sheets_writer import SheetsWriter

router = Router()
pending_tables: dict[int, pd.DataFrame] = {}
expense_parser: ExpenseParser
sheets_client: GoogleSheetsClient
sheets_writer: SheetsWriter
session_factory: sessionmaker[Session]

HELP_TEXT = "\n".join(
    [
        "Доступные команды:",
        "/help - показать это сообщение",
        "/last5 - пять последних трат",
        "/report - отправить отчет за текущий месяц",
    ]
)


@router.message(CommandStart())
async def start(message: Message) -> None:
    text = (
        "Отправь голосовое или текст. Я распознаю расход, покажу таблицу "
        "и после подтверждения допишу ее в Google Sheets.\n\n"
        f"{HELP_TEXT}"
    )
    if not settings.telegram_daily_report_chat_id:
        text += f"\n\nID этого чата для ежедневного отчета: {message.chat.id}"
    await message.answer(text)


@router.message(Command("help"))
async def help_command(message: Message) -> None:
    await message.answer(HELP_TEXT)


@router.message(Command("last5"))
async def last5(message: Message) -> None:
    try:
        expenses = await sheets_writer.list_last_expenses(limit=5)
    except Exception as exc:
        logging.exception("Failed to list last expenses")
        await message.answer(f"Не получилось получить последние траты: {exc}")
        return

    if not expenses:
        await message.answer("Трат пока нет.")
        return

    lines = [
        f"- {expense.date.isoformat()} - {expense.amount:.2f} - {expense.comment or '-'}"
        for expense in expenses
    ]
    await message.answer("\n".join(lines))


@router.message(Command("report"))
async def report(message: Message, bot: Bot) -> None:
    status = await message.answer("Формирую отчет...")
    try:
        await send_daily_report(
            bot=bot,
            chat_id=message.chat.id,
            sheets_client=sheets_client,
            session_factory=session_factory,
            budget_worksheet_name=settings.google_budget_worksheet_name,
            report_date=_today_for_report_timezone(),
        )
        with suppress(Exception):
            await status.delete()
    except Exception as exc:
        logging.exception("Failed to send requested Telegram report")
        await status.edit_text(f"Не получилось отправить отчет: {exc}")


@router.message(F.voice)
async def handle_voice(message: Message, bot: Bot) -> None:
    if not message.voice:
        return
    status = await message.answer("Скачиваю голосовое и распознаю текст...")
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp_file:
        audio_path = Path(tmp_file.name)
    try:
        file = await bot.get_file(message.voice.file_id)
        await bot.download_file(file.file_path, destination=audio_path)
        transcript = await expense_parser.transcribe(audio_path)
        await status.edit_text(
            f"Распознанный текст:\n\n{transcript}\n\nОтправляю в LLM для создания JSON..."
        )
        await process_text(message, transcript)
    except Exception as exc:
        logging.exception("Failed to process voice message")
        await status.edit_text(f"Не получилось обработать голосовое: {exc}")
    finally:
        audio_path.unlink(missing_ok=True)


@router.message(F.text)
async def handle_text(message: Message) -> None:
    if not message.text:
        return
    await message.answer("Структурирую данные...")
    await process_text(message, message.text)


async def process_text(message: Message, text: str) -> None:
    try:
        raw_json = await expense_parser.structure_text(text)
        rows = parse_rows(raw_json)
        df = rows_to_dataframe(rows)
        pending_tables[message.from_user.id] = df
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="OK, дальше", callback_data="confirm_append"),
                    InlineKeyboardButton(text="Отмена", callback_data="cancel_append"),
                ]
            ]
        )
        await message.answer(
            "Проверь данные:\n\n"
            f"{dataframe_to_confirmation_text(df)}\n\n"
            "Дописывать в Google Sheets?",
            reply_markup=keyboard,
        )
    except Exception as exc:
        logging.exception("Failed to structure text")
        await message.answer(f"Не получилось сделать таблицу: {exc}")


@router.callback_query(F.data == "confirm_append")
async def confirm_append(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id
    df = pending_tables.get(user_id)
    if df is None:
        await callback.answer("Нет ожидающей таблицы", show_alert=True)
        return
    try:
        await sheets_writer.append_dataframe(df)
        pending_tables.pop(user_id, None)
        if callback.message:
            await callback.message.edit_text("Готово. Строки дописаны в Google Sheets.")
        await callback.answer()
    except Exception as exc:
        logging.exception("Failed to append rows")
        await callback.answer("Ошибка записи в Google Sheets", show_alert=True)
        if callback.message:
            await callback.message.answer(f"Не получилось дописать строки: {exc}")


@router.callback_query(F.data == "cancel_append")
async def cancel_append(callback: CallbackQuery) -> None:
    pending_tables.pop(callback.from_user.id, None)
    if callback.message:
        await callback.message.edit_text("Ок, не дописываю.")
    await callback.answer()


def _today_for_report_timezone() -> date:
    try:
        timezone = ZoneInfo(settings.telegram_daily_report_timezone)
    except ZoneInfoNotFoundError:
        logging.exception(
            "Invalid TELEGRAM_DAILY_REPORT_TIMEZONE=%s; using local date",
            settings.telegram_daily_report_timezone,
        )
        return datetime.now().date()
    return datetime.now(timezone).date()
