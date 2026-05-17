from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import require_auth
from app.db.session import get_db
from app.expenses.schemas import DashboardSummary, ExpenseRead
from app.expenses.service import get_dashboard_summary, get_expenses

router = APIRouter(prefix="/api", tags=["dashboard"], dependencies=[Depends(require_auth)])


@router.get("/expenses", response_model=list[ExpenseRead])
def expenses(
    date_from: date | None = None,
    date_to: date | None = None,
    category: str | None = None,
    expenses_only: bool = True,
    limit: int = Query(default=500, ge=1, le=50000),
    db: Session = Depends(get_db),
):
    return get_expenses(
        db,
        date_from=date_from,
        date_to=date_to,
        category=category,
        expenses_only=expenses_only,
        limit=limit,
    )


@router.get("/dashboard/summary", response_model=DashboardSummary)
def dashboard_summary(db: Session = Depends(get_db)):
    summary = get_dashboard_summary(db)
    summary["google_sheets_url"] = settings.google_sheets_url
    return summary
