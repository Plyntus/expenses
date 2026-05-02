from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import require_auth
from app.db.session import get_db
from app.expenses.repository import latest_sync_run
from app.expenses.schemas import SyncRunRead
from app.sheets.client import GoogleSheetsClient
from app.sheets.sync_service import sync_google_sheets_to_postgres

router = APIRouter(prefix="/api/sync", tags=["sync"], dependencies=[Depends(require_auth)])


@router.post("/google-sheets", response_model=SyncRunRead)
async def sync_google_sheets(db: Session = Depends(get_db)):
    client = GoogleSheetsClient(settings)
    return await sync_google_sheets_to_postgres(db, client, triggered_by="web")


@router.get("/last", response_model=SyncRunRead | None)
def last_sync(db: Session = Depends(get_db)):
    return latest_sync_run(db)
