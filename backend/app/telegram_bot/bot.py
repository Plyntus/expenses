from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from aiogram import Bot, Dispatcher

from app.core.config import settings
from app.core.logging import configure_logging
from app.db.session import SessionLocal
from app.llm.expense_parser import ExpenseParser
from app.sheets.client import GoogleSheetsClient
from app.telegram_bot import handlers
from app.telegram_bot.daily_report import run_daily_report_scheduler
from app.telegram_bot.sheets_writer import SheetsWriter


async def main() -> None:
    configure_logging()
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required")

    handlers.expense_parser = ExpenseParser(
        api_key=settings.openai_api_key,
        transcribe_model=settings.openai_transcribe_model,
        text_model=settings.openai_text_model,
        system_prompt_path=settings.system_prompt_path,
    )
    sheets_client = GoogleSheetsClient(settings)
    handlers.sheets_writer = SheetsWriter(sheets_client)

    bot = Bot(token=settings.telegram_bot_token)
    dispatcher = Dispatcher()
    dispatcher.include_router(handlers.router)
    daily_report_task: asyncio.Task[None] | None = None
    if settings.telegram_daily_report_chat_id:
        daily_report_task = asyncio.create_task(
            run_daily_report_scheduler(
                bot=bot,
                chat_id=settings.telegram_daily_report_chat_id,
                sheets_client=sheets_client,
                session_factory=SessionLocal,
                timezone_name=settings.telegram_daily_report_timezone,
            )
        )
    else:
        logging.warning(
            "TELEGRAM_DAILY_REPORT_CHAT_ID is not set; daily Telegram reports are disabled"
        )

    logging.info("Starting Telegram bot polling")
    try:
        await dispatcher.start_polling(bot)
    finally:
        if daily_report_task:
            daily_report_task.cancel()
            with suppress(asyncio.CancelledError):
                await daily_report_task


if __name__ == "__main__":
    asyncio.run(main())
