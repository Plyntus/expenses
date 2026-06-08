from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Bot
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Expense
from app.sheets.client import GoogleSheetsClient
from app.sheets.sync_service import sync_google_sheets_to_postgres

logger = logging.getLogger(__name__)

REPORT_TIME = time(hour=22, minute=0)


async def run_daily_report_scheduler(
    *,
    bot: Bot,
    chat_id: int,
    sheets_client: GoogleSheetsClient,
    session_factory: sessionmaker[Session],
    timezone_name: str,
) -> None:
    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        logger.exception(
            "Invalid TELEGRAM_DAILY_REPORT_TIMEZONE=%s; daily reports are disabled",
            timezone_name,
        )
        return

    while True:
        now = datetime.now(timezone)
        next_run = next_report_datetime(now, REPORT_TIME)
        sleep_seconds = (
            next_run.astimezone(UTC) - now.astimezone(UTC)
        ).total_seconds()
        logger.info("Next daily Telegram report scheduled at %s", next_run.isoformat())
        await asyncio.sleep(max(0, sleep_seconds))

        try:
            await send_daily_report(
                bot=bot,
                chat_id=chat_id,
                sheets_client=sheets_client,
                session_factory=session_factory,
                report_date=datetime.now(timezone).date(),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Failed to send daily Telegram report")


def next_report_datetime(now: datetime, report_time: time) -> datetime:
    next_run = now.replace(
        hour=report_time.hour,
        minute=report_time.minute,
        second=0,
        microsecond=0,
    )
    if next_run <= now:
        next_run += timedelta(days=1)
    return next_run


async def send_daily_report(
    *,
    bot: Bot,
    chat_id: int,
    sheets_client: GoogleSheetsClient,
    session_factory: sessionmaker[Session],
    report_date: date,
) -> None:
    with session_factory() as db:
        sync_run = await sync_google_sheets_to_postgres(
            db,
            sheets_client,
            triggered_by="telegram_daily_report",
        )
        if sync_run.status != "success":
            logger.warning(
                "Google Sheets sync before daily report finished with status=%s: %s",
                sync_run.status,
                sync_run.error_message,
            )

        message = build_daily_report_message(db, report_date)

    await bot.send_message(chat_id=chat_id, text=message)


def build_daily_report_message(db: Session, report_date: date) -> str:
    month_start = report_date.replace(day=1)
    today_spending = _spending_total(
        db,
        date_from=report_date,
        date_to=report_date,
    )
    monthly_spending_by_account = _spending_by_account(
        db,
        date_from=month_start,
        date_to=report_date,
    )
    latest = _latest_transaction(db)

    return "\n".join(
        [
            f"Траты за сегодня: {_format_amount(today_spending)}",
            "В этом месяце:",
            *_format_spending_by_account(monthly_spending_by_account),
            f"Последняя транзакция: {_format_latest_transaction(latest)}",
        ]
    )


def _spending_total(db: Session, *, date_from: date, date_to: date) -> Decimal:
    value = db.scalar(
        select(func.coalesce(func.sum(func.abs(Expense.amount)), 0)).where(
            Expense.amount < 0,
            Expense.date >= date_from,
            Expense.date <= date_to,
        )
    )
    return _decimal(value)


def _spending_by_account(
    db: Session, *, date_from: date, date_to: date
) -> list[tuple[str, Decimal]]:
    total = func.sum(func.abs(Expense.amount)).label("total")
    rows = db.execute(
        select(Expense.payment_method, total)
        .where(
            Expense.amount < 0,
            Expense.date >= date_from,
            Expense.date <= date_to,
        )
        .group_by(Expense.payment_method)
        .order_by(desc(total), Expense.payment_method)
    ).all()
    return [(_account_name(row[0]), _decimal(row[1])) for row in rows]


def _latest_transaction(db: Session) -> Expense | None:
    return db.scalar(
        select(Expense).order_by(desc(Expense.date), desc(Expense.id)).limit(1)
    )


def _format_spending_by_account(items: list[tuple[str, Decimal]]) -> list[str]:
    if not items:
        return ["- нет трат"]
    return [f"- {account} : {_format_amount(total)}" for account, total in items]


def _format_latest_transaction(expense: Expense | None) -> str:
    if expense is None:
        return "нет транзакций"
    comment = expense.comment or ""
    currency = f" {expense.currency}" if expense.currency else ""
    return (
        f'{expense.date.strftime("%d.%m.%Y")}, '
        f'{_format_amount(expense.amount)}{currency}, "{comment}"'
    )


def _account_name(value: str | None) -> str:
    return value.strip() if value and value.strip() else "Без счета"


def _format_amount(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')):,.2f}".replace(",", " ")


def _decimal(value: object) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))
