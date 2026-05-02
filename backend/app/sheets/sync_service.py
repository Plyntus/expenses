from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.db.models import Expense, SyncRun
from app.sheets.client import GoogleSheetsClient
from app.sheets.mapper import map_sheet_row, sheet_values_to_rows

logger = logging.getLogger(__name__)


async def sync_google_sheets_to_postgres(
    db: Session,
    sheets_client: GoogleSheetsClient,
    *,
    triggered_by: str = "web",
) -> SyncRun:
    started_at = datetime.now(UTC)
    logger.info("Starting Google Sheets sync")
    run = SyncRun(
        status="running",
        started_at=started_at,
        rows_read=0,
        rows_imported=0,
        rows_failed=0,
        triggered_by=triggered_by,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    try:
        values = await sheets_client.get_all_values()
        rows = sheet_values_to_rows(values)
        logger.info("Read %s non-empty rows from Google Sheets", len(rows))
        mapped = []
        failures: list[str] = []
        synced_at = datetime.now(UTC)
        for row in rows:
            try:
                mapped.append(map_sheet_row(row))
            except Exception as exc:
                row_number = row.get("_sheet_row_number", "?")
                message = f"row {row_number}: {exc}"
                failures.append(message)
                logger.warning("Skipping invalid Google Sheets %s", message)

        if rows and not mapped:
            raise ValueError("All Google Sheets rows failed validation")

        db.query(Expense).delete()
        for item in mapped:
            db.add(
                Expense(
                    sheet_row_number=item.sheet_row_number,
                    sheet_row_hash=item.sheet_row_hash,
                    date=item.date,
                    amount=item.amount,
                    currency=item.currency,
                    category=item.category,
                    subcategory=item.subcategory,
                    comment=item.comment,
                    payment_method=item.payment_method,
                    raw_values_json=item.raw_values_json,
                    synced_at=synced_at,
                )
            )
        run.status = "success" if not failures else "partial_success"
        run.finished_at = datetime.now(UTC)
        run.rows_read = len(rows)
        run.rows_imported = len(mapped)
        run.rows_failed = len(failures)
        run.error_message = "\n".join(failures[:20]) if failures else None
        db.add(run)
        db.commit()

        logger.info(
            "Finished Google Sheets sync: imported=%s failed=%s",
            run.rows_imported,
            run.rows_failed,
        )
        db.refresh(run)
        return run
    except Exception as exc:
        logger.exception("Google Sheets sync failed")
        db.rollback()
        run.status = "failed"
        run.finished_at = datetime.now(UTC)
        run.error_message = str(exc)
        db.add(run)
        db.commit()
        db.refresh(run)
        return run
