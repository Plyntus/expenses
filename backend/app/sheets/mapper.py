from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from dateutil import parser


HEADER_ALIASES = {
    "date": "Date",
    "дата": "Date",
    "account": "Account",
    "счет": "Account",
    "счёт": "Account",
    "sum": "Sum",
    "amount": "Sum",
    "сумма": "Sum",
    "comment": "Comment",
    "комментарий": "Comment",
    "category": "Category",
    "категория": "Category",
    "subcategory": "Subcategory",
    "субкатегория": "Subcategory",
    "currency": "Currency",
    "валюта": "Currency",
    "account type": "Account type",
    "account status": "Account status",
}


@dataclass(frozen=True)
class MappedExpense:
    sheet_row_number: int
    sheet_row_hash: str
    date: date
    amount: Decimal
    currency: str | None
    category: str | None
    subcategory: str | None
    comment: str | None
    payment_method: str | None
    raw_values_json: dict[str, Any]


def sheet_values_to_rows(values: list[list[str]]) -> list[dict[str, Any]]:
    if not values:
        raise ValueError("Google Sheet is empty")
    headers = [_normalize_header(cell) for cell in values[0]]
    rows: list[dict[str, Any]] = []
    for index, raw_row in enumerate(values[1:], start=2):
        width = len(headers)
        cells = list(raw_row[:width]) + [""] * max(0, width - len(raw_row))
        if not any(str(cell).strip() for cell in cells):
            continue
        row = {headers[i]: cells[i] for i in range(width) if headers[i]}
        row["_sheet_row_number"] = index
        rows.append(row)
    return rows


def map_sheet_row(row: dict[str, Any]) -> MappedExpense:
    raw = {k: v for k, v in row.items() if not k.startswith("_")}
    normalized = {_canonical_header(k): v for k, v in raw.items()}
    row_number = int(row.get("_sheet_row_number", 0))
    date_value = _parse_date(_cell(normalized, "Date"))
    amount = _parse_amount(_cell(normalized, "Sum"))
    raw_json = {k: _json_safe(v) for k, v in raw.items()}
    row_hash = hashlib.sha256(
        json.dumps(raw_json, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return MappedExpense(
        sheet_row_number=row_number,
        sheet_row_hash=row_hash,
        date=date_value,
        amount=amount,
        currency=_optional_text(_cell(normalized, "Currency")),
        category=_optional_text(_cell(normalized, "Category")),
        subcategory=_optional_text(_cell(normalized, "Subcategory")),
        comment=_optional_text(_cell(normalized, "Comment")),
        payment_method=_optional_text(_cell(normalized, "Account")),
        raw_values_json=raw_json,
    )


def _normalize_header(value: Any) -> str:
    text = str(value).strip()
    return _canonical_header(text) or text


def _canonical_header(value: Any) -> str:
    key = str(value).strip().lower().replace("ё", "е")
    key = re.sub(r"\s+", " ", key)
    return HEADER_ALIASES.get(key, str(value).strip())


def _cell(row: dict[str, Any], key: str) -> Any:
    return row.get(key, "")


def _optional_text(value: Any) -> str | None:
    text = "" if value is None else str(value).strip()
    return text or None


def _parse_date(value: Any) -> date:
    text = _optional_text(value)
    if not text:
        raise ValueError("Date is empty")
    try:
        return parser.parse(text, dayfirst=False).date()
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"Invalid Date: {text}") from exc


def _parse_amount(value: Any) -> Decimal:
    text = _optional_text(value)
    if not text:
        raise ValueError("Sum is empty")
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
        raise ValueError(f"Invalid Sum: {text}") from exc


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
