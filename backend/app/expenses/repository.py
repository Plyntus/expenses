from __future__ import annotations

from datetime import date

from sqlalchemy import desc, extract, func, select
from sqlalchemy.orm import Session

from app.db.models import Expense, SyncRun


def list_expenses(
    db: Session,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    category: str | None = None,
    limit: int = 500,
    expenses_only: bool = True,
) -> list[Expense]:
    stmt = select(Expense).order_by(desc(Expense.date), desc(Expense.id)).limit(limit)
    if expenses_only:
        stmt = stmt.where(Expense.amount < 0)
    if date_from:
        stmt = stmt.where(Expense.date >= date_from)
    if date_to:
        stmt = stmt.where(Expense.date <= date_to)
    if category:
        stmt = stmt.where(Expense.category == category)
    return list(db.scalars(stmt))


def latest_sync_run(db: Session) -> SyncRun | None:
    return db.scalar(select(SyncRun).order_by(desc(SyncRun.started_at)).limit(1))


def dashboard_summary(db: Session) -> dict:
    expense_filter = Expense.amount < 0
    total = (
        db.scalar(
            select(func.coalesce(func.sum(func.abs(Expense.amount)), 0)).where(
                expense_filter
            )
        )
        or 0
    )
    category_rows = db.execute(
        select(Expense.category, func.sum(func.abs(Expense.amount)).label("total"))
        .where(expense_filter)
        .group_by(Expense.category)
        .order_by(desc("total"))
    ).all()
    month_rows = db.execute(
        select(
            extract("year", Expense.date).label("year"),
            extract("month", Expense.date).label("month"),
            func.sum(func.abs(Expense.amount)).label("total"),
        )
        .where(expense_filter)
        .group_by("year", "month")
        .order_by("year", "month")
    ).all()
    day_rows = db.execute(
        select(Expense.date, func.sum(func.abs(Expense.amount)).label("total"))
        .where(expense_filter)
        .group_by(Expense.date)
        .order_by(Expense.date)
    ).all()
    return {
        "total_spending": float(total),
        "spending_by_category": [
            {"category": row[0] or "Без категории", "total": float(row[1] or 0)}
            for row in category_rows
        ],
        "spending_by_month": [
            {
                "month": f"{int(row.year):04d}-{int(row.month):02d}",
                "total": float(row.total or 0),
            }
            for row in month_rows
        ],
        "spending_by_day": [
            {"date": row[0].isoformat(), "total": float(row[1] or 0)}
            for row in day_rows
        ],
        "latest_expenses": list_expenses(db, limit=20),
        "last_sync": latest_sync_run(db),
    }
