from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Sequence
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Expense
from app.sheets.client import GoogleSheetsClient
from app.sheets.sync_service import sync_google_sheets_to_postgres

logger = logging.getLogger(__name__)

REPORT_TIME = time(hour=22, minute=0)
REPORT_CURRENCY = "EUR"
EXCHANGE_RATE_API_URL = "https://open.er-api.com/v6/latest/EUR"
EXCHANGE_RATE_TIMEOUT_SECONDS = 10
OTHER_CATEGORY_KEY = "остальное"
REPORT_SYNC_OK_STATUSES = {"success", "partial_success"}
MONTH_NAMES_RU = {
    1: "января",
    2: "февраля",
    3: "марта",
    4: "апреля",
    5: "мая",
    6: "июня",
    7: "июля",
    8: "августа",
    9: "сентября",
    10: "октября",
    11: "ноября",
    12: "декабря",
}


@dataclass(frozen=True)
class BudgetCategory:
    category: str
    category_key: str
    budget: Decimal
    currency: str
    currency_key: str


@dataclass(frozen=True)
class BudgetCategorySpend:
    category: str
    spent: Decimal
    budget: Decimal
    currency: str


@dataclass(frozen=True)
class ExchangeRates:
    base_currency: str
    rates: dict[str, Decimal]
    updated_at: str | int | None = None

    def convert(
        self, value: Decimal, source_currency: str, target_currency: str
    ) -> Decimal:
        source = _currency_key(source_currency)
        target = _currency_key(target_currency)
        if not source:
            raise ValueError("У транзакции не указана валюта")
        if not target:
            raise ValueError("Не указана валюта назначения")
        if source == target:
            return value

        source_rate = self.rates.get(source)
        target_rate = self.rates.get(target)
        if not source_rate or not target_rate:
            missing = source if not source_rate else target
            raise ValueError(f"Нет курса для валюты {missing}")
        return value / source_rate * target_rate


async def run_daily_report_scheduler(
    *,
    bot: Bot,
    chat_id: int,
    sheets_client: GoogleSheetsClient,
    session_factory: sessionmaker[Session],
    timezone_name: str,
    budget_worksheet_name: str,
    excluded_categories: Sequence[str],
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
                budget_worksheet_name=budget_worksheet_name,
                report_date=datetime.now(timezone).date(),
                excluded_categories=excluded_categories,
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
    budget_worksheet_name: str,
    report_date: date,
    excluded_categories: Sequence[str],
) -> None:
    problems: list[str] = []
    sync_problem = await sync_google_sheets_before_daily_report(
        session_factory=session_factory,
        sheets_client=sheets_client,
    )
    if sync_problem:
        problems.append(sync_problem)

    exchange_rates: ExchangeRates | None = None
    try:
        exchange_rates = await load_exchange_rates()
    except Exception as exc:
        logger.exception("Failed to load exchange rates before daily report")
        problems.append(
            "error: не удалось загрузить курсы валют перед отчетом: "
            f"{_short_error_message(exc)}."
        )

    try:
        budget_values = await sheets_client.get_worksheet_values(budget_worksheet_name)
        budget_categories = parse_budget_sheet(budget_values)
    except Exception as exc:
        logger.exception("Failed to read budget sheet before daily report")
        budget_categories = []
        problems.append(
            "error: не удалось прочитать бюджет перед отчетом: "
            f"{_short_error_message(exc)}. Категории выше не рассчитаны."
        )

    if exchange_rates is None:
        message = "Не удалось сформировать ежедневный отчет."
    else:
        try:
            with session_factory() as db:
                message = build_daily_report_message(
                    db,
                    report_date,
                    budget_categories,
                    exchange_rates,
                    excluded_categories,
                )
        except Exception as exc:
            logger.exception("Failed to build daily Telegram report")
            message = "Не удалось сформировать ежедневный отчет."
            problems.append(f"error: {_short_error_message(exc)}")

    if problems:
        message = f"{message}\n\nПроблемы:\n" + "\n".join(problems)
    await bot.send_message(chat_id=chat_id, text=message)


async def load_exchange_rates() -> ExchangeRates:
    return await asyncio.to_thread(_load_exchange_rates_sync)


def _load_exchange_rates_sync() -> ExchangeRates:
    request = Request(
        EXCHANGE_RATE_API_URL,
        headers={"User-Agent": "expense-tracker-telegram-bot/1.0"},
    )
    with urlopen(request, timeout=EXCHANGE_RATE_TIMEOUT_SECONDS) as response:
        payload = json.load(response)

    if payload.get("result") == "error" or not isinstance(
        payload.get("rates"), dict
    ):
        raise ValueError("API курсов валют вернул неожиданный ответ")

    base_currency = _currency_key(payload.get("base_code") or REPORT_CURRENCY)
    if base_currency != REPORT_CURRENCY:
        raise ValueError(
            f"API курсов валют вернул базовую валюту {base_currency}, ожидалась EUR"
        )
    rates: dict[str, Decimal] = {base_currency: Decimal("1")}
    for currency, raw_rate in payload["rates"].items():
        rate = _decimal(raw_rate)
        if rate > 0:
            rates[_currency_key(currency)] = rate
    return ExchangeRates(
        base_currency=base_currency,
        rates=rates,
        updated_at=payload.get("time_last_update_utc")
        or payload.get("time_last_update_unix"),
    )


async def sync_google_sheets_before_daily_report(
    *,
    session_factory: sessionmaker[Session],
    sheets_client: GoogleSheetsClient,
) -> str | None:
    try:
        with session_factory() as db:
            sync_run = await sync_google_sheets_to_postgres(
                db,
                sheets_client,
                triggered_by="telegram_daily_report",
            )
    except Exception as exc:
        logger.exception("Google Sheets sync before daily report crashed")
        return (
            "error: не удалось обновить расходы из Google Sheets перед отчетом: "
            f"{_short_error_message(exc)}. Отчет выше построен по данным, "
            "которые уже были в БД."
        )

    if sync_run.status not in REPORT_SYNC_OK_STATUSES:
        return (
            "error: не удалось обновить расходы из Google Sheets перед отчетом: "
            f"{_short_error_message(sync_run.error_message or sync_run.status)}. "
            "Отчет выше построен по данным, которые уже были в БД."
        )
    if sync_run.status != "success":
        logger.warning(
            "Google Sheets sync before daily report finished with status=%s: %s",
            sync_run.status,
            sync_run.error_message,
        )
        return (
            "warning: Google Sheets обновились частично: "
            f"{sync_run.rows_failed} строк не импортировано. "
            "Отчет выше построен по успешно импортированным строкам."
        )
    logger.info(
        "Google Sheets sync before daily report imported %s rows",
        sync_run.rows_imported,
    )
    return None


def _short_error_message(error: object, max_length: int = 240) -> str:
    text = str(error).strip().splitlines()[0] if str(error).strip() else "unknown error"
    if len(text) <= max_length:
        return text
    return f"{text[: max_length - 1]}..."


def build_daily_report_message(
    db: Session,
    report_date: date,
    budget_categories: list[BudgetCategory],
    exchange_rates: ExchangeRates,
    excluded_categories: Sequence[str],
) -> str:
    month_start = report_date.replace(day=1)
    spending, latest_date = _monthly_spending(
        db,
        date_from=month_start,
        date_to=report_date,
        excluded_categories=excluded_categories,
    )
    monthly_spending = sum(
        (
            exchange_rates.convert(total, source_currency, REPORT_CURRENCY)
            for (source_currency, _), total in spending.items()
        ),
        Decimal("0"),
    )
    category_spending = _budget_category_spending(
        spending,
        budget_categories=budget_categories,
        exchange_rates=exchange_rates,
        excluded_categories=excluded_categories,
    )

    return "\n".join(
        [
            (
                f"За текущий месяц потрачено {_format_amount(monthly_spending)} "
                f"{REPORT_CURRENCY}. Последняя дата: "
                f"{_format_report_date(latest_date)}."
            ),
            "Категории:",
            *_format_budget_category_spending(category_spending),
        ]
    )


def parse_budget_sheet(values: list[list[str]]) -> list[BudgetCategory]:
    if not values:
        raise ValueError("Budget sheet is empty")

    header_indexes = {
        _budget_header_key(header): index for index, header in enumerate(values[0])
    }
    required_headers = {"category", "budget", "currency"}
    missing_headers = sorted(required_headers - set(header_indexes))
    if missing_headers:
        raise ValueError(
            "Budget sheet is missing expected headers: "
            f"{missing_headers}. Sheet header: {values[0]}"
        )

    categories: list[BudgetCategory] = []
    for row_number, raw_row in enumerate(values[1:], start=2):
        if not any(str(cell).strip() for cell in raw_row):
            continue
        category = _row_cell(raw_row, header_indexes["category"]).strip()
        if not category:
            raise ValueError(f"Budget row {row_number}: Category is empty")
        budget = _parse_budget_amount(_row_cell(raw_row, header_indexes["budget"]))
        currency = _currency_key(_row_cell(raw_row, header_indexes["currency"]))
        if not currency:
            raise ValueError(f"Budget row {row_number}: Currency is empty")
        categories.append(
            BudgetCategory(
                category=category,
                category_key=_category_key(category),
                budget=budget,
                currency=currency,
                currency_key=_currency_key(currency),
            )
        )

    if not categories:
        raise ValueError("Budget sheet has no budget rows")
    return categories


def _budget_category_spending(
    spending: dict[tuple[str, str], Decimal],
    *,
    budget_categories: list[BudgetCategory],
    exchange_rates: ExchangeRates,
    excluded_categories: Sequence[str],
) -> list[BudgetCategorySpend]:
    excluded_keys = {_category_key(category) for category in excluded_categories}
    explicit_categories_by_currency: dict[str, set[str]] = {}
    for item in budget_categories:
        if (
            item.category_key == OTHER_CATEGORY_KEY
            or item.category_key in excluded_keys
        ):
            continue
        explicit_categories_by_currency.setdefault(item.currency_key, set()).add(
            item.category_key
        )

    report_items: list[BudgetCategorySpend] = []
    for item in budget_categories:
        if item.category_key in excluded_keys:
            continue
        if item.category_key == OTHER_CATEGORY_KEY:
            explicit_keys = explicit_categories_by_currency.get(item.currency_key, set())
            spent = sum(
                (
                    exchange_rates.convert(total, source_currency, item.currency_key)
                    for (source_currency, category_key), total in spending.items()
                    if category_key not in explicit_keys
                ),
                Decimal("0"),
            )
        else:
            spent = sum(
                (
                    exchange_rates.convert(total, source_currency, item.currency_key)
                    for (source_currency, category_key), total in spending.items()
                    if category_key == item.category_key
                ),
                Decimal("0"),
            )

        report_items.append(
            BudgetCategorySpend(
                category=item.category,
                spent=spent,
                budget=item.budget,
                currency=item.currency,
            )
        )
    return report_items


def _monthly_spending(
    db: Session,
    *,
    date_from: date,
    date_to: date,
    excluded_categories: Sequence[str],
) -> tuple[dict[tuple[str, str], Decimal], date | None]:
    rows = db.execute(
        select(Expense.currency, Expense.category, Expense.amount, Expense.date)
        .where(
            Expense.amount < 0,
            Expense.date >= date_from,
            Expense.date <= date_to,
        )
    ).all()
    excluded_keys = {_category_key(category) for category in excluded_categories}
    spending: dict[tuple[str, str], Decimal] = {}
    latest_date: date | None = None
    for currency, category, amount, transaction_date in rows:
        category_key = _category_key(category)
        if category_key in excluded_keys:
            continue
        key = (_currency_key(currency), category_key)
        spending[key] = spending.get(key, Decimal("0")) + abs(_decimal(amount))
        if latest_date is None or transaction_date > latest_date:
            latest_date = transaction_date
    return spending, latest_date


def _format_budget_category_spending(
    items: list[BudgetCategorySpend],
) -> list[str]:
    if not items:
        return ["нет категорий"]
    return [
        f"{item.category}: {_format_amount(item.spent)} ({_format_budget_percent(item)})"
        for item in items
    ]


def _format_budget_percent(item: BudgetCategorySpend) -> str:
    if item.budget == 0:
        return "0%" if item.spent == 0 else "n/a"
    percent = (item.spent / item.budget * Decimal("100")).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    )
    return f"{percent}%"


def _format_report_date(value: date | None) -> str:
    if value is None:
        return "нет транзакций"
    return f"{value.day} {MONTH_NAMES_RU[value.month]}"


def _budget_header_key(value: object) -> str:
    key = str(value).strip().lower().replace("ё", "е")
    key = re.sub(r"\s+", " ", key)
    return {
        "category": "category",
        "категория": "category",
        "budget": "budget",
        "бюджет": "budget",
        "currency": "currency",
        "валюта": "currency",
    }.get(key, key)


def _row_cell(row: list[str], index: int) -> str:
    return str(row[index]).strip() if index < len(row) else ""


def _parse_budget_amount(value: object) -> Decimal:
    text = str(value).strip()
    if not text:
        return Decimal("0")
    cleaned = text.replace("\u00a0", "").replace(" ", "")
    cleaned = re.sub(r"[^0-9,.\-]", "", cleaned)
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return Decimal(cleaned).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid Budget: {value}") from exc


def _category_key(value: object) -> str:
    text = str(value or "").strip().lower().replace("ё", "е")
    text = re.sub(r"\s+", " ", text)
    return text or "без категории"


def _currency_key(value: object) -> str:
    return str(value or "").strip().upper()


def _format_amount(value: Decimal) -> str:
    amount = value.quantize(Decimal("0.01"))
    if amount == amount.to_integral_value():
        return f"{amount:,.0f}".replace(",", " ")
    return f"{amount:,.2f}".replace(",", " ")


def _decimal(value: object) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))
