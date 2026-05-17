from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.expenses import repository


def get_expenses(
    db: Session,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    category: str | None = None,
    expenses_only: bool = True,
    limit: int = 500,
):
    return repository.list_expenses(
        db,
        date_from=date_from,
        date_to=date_to,
        category=category,
        expenses_only=expenses_only,
        limit=limit,
    )


def get_dashboard_summary(db: Session) -> dict:
    return repository.dashboard_summary(db)
