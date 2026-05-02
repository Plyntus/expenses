from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class ExpenseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sheet_row_number: int | None
    sheet_row_hash: str | None
    date: date
    amount: Decimal
    currency: str | None
    category: str | None
    subcategory: str | None
    comment: str | None
    payment_method: str | None
    raw_values_json: dict
    synced_at: datetime


class SyncRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    rows_read: int = 0
    rows_imported: int = 0
    rows_failed: int = 0
    error_message: str | None = None
    triggered_by: str | None = None


class DashboardSummary(BaseModel):
    total_spending: float
    spending_by_category: list[dict]
    spending_by_month: list[dict]
    spending_by_day: list[dict]
    latest_expenses: list[ExpenseRead]
    last_sync: SyncRunRead | None
    google_sheets_url: str | None = None
