from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher

from app.core.config import settings
from app.core.logging import configure_logging
from app.llm.expense_parser import ExpenseParser
from app.sheets.client import GoogleSheetsClient
from app.telegram_bot import handlers
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
    handlers.sheets_writer = SheetsWriter(GoogleSheetsClient(settings))

    bot = Bot(token=settings.telegram_bot_token)
    dispatcher = Dispatcher()
    dispatcher.include_router(handlers.router)
    logging.info("Starting Telegram bot polling")
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
