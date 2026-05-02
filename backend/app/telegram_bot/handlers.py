from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import pandas as pd
from aiogram import Bot, F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.llm.expense_parser import (
    ExpenseParser,
    dataframe_to_confirmation_text,
    parse_rows,
    rows_to_dataframe,
)
from app.telegram_bot.sheets_writer import SheetsWriter

router = Router()
pending_tables: dict[int, pd.DataFrame] = {}
expense_parser: ExpenseParser
sheets_writer: SheetsWriter


@router.message(CommandStart())
async def start(message: Message) -> None:
    await message.answer(
        "Отправь голосовое или текст. Я распознаю расход, покажу таблицу "
        "и после подтверждения допишу ее в Google Sheets."
    )


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
