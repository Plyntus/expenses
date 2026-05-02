from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

import gspread

from app.core.config import Settings, get_google_service_account_info


SHEET_HEADERS = [
    "Date",
    "Account",
    "Sum",
    "Comment",
    "Category",
    "Subcategory",
    "Currency",
    "Account type",
    "Account status",
]


class GoogleSheetsClient:
    def __init__(self, settings: Settings) -> None:
        if not settings.google_sheets_id:
            raise RuntimeError("GOOGLE_SHEETS_ID is required")
        self._settings = settings
        self._spreadsheet_id = settings.google_sheets_id
        self._worksheet_name = settings.google_worksheet_name

    async def get_all_values(self) -> list[list[str]]:
        return await asyncio.to_thread(self._get_all_values_sync)

    async def append_rows(self, rows: list[dict[str, Any]]) -> None:
        return await asyncio.to_thread(self._append_rows_sync, rows)

    def _worksheet(self):
        client = self._gspread_client()
        spreadsheet = client.open_by_key(self._spreadsheet_id)
        return spreadsheet.worksheet(self._worksheet_name)

    def _gspread_client(self):
        info = get_google_service_account_info(self._settings)
        if info:
            return gspread.service_account_from_dict(info)
        if self._settings.google_application_credentials:
            return gspread.service_account(
                filename=str(self._settings.google_application_credentials)
            )
        raise RuntimeError(
            "Set GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_APPLICATION_CREDENTIALS"
        )

    def _get_all_values_sync(self) -> list[list[str]]:
        return self._worksheet().get_all_values()

    def _append_rows_sync(self, rows: list[dict[str, Any]]) -> None:
        worksheet = self._worksheet()
        values = worksheet.get_all_values()
        if not values:
            worksheet.append_row(SHEET_HEADERS, value_input_option="USER_ENTERED")
        else:
            validate_sheet_headers(values[0])
        aligned_rows = [align_row_to_headers(row, SHEET_HEADERS) for row in rows]
        worksheet.append_rows(aligned_rows, value_input_option="USER_ENTERED")


def validate_sheet_headers(sheet_headers: list[str]) -> None:
    missing_headers = [header for header in SHEET_HEADERS if header not in sheet_headers]
    if missing_headers:
        raise ValueError(
            "Google Sheet is missing expected headers: "
            f"{missing_headers}. Sheet header: {sheet_headers}"
        )


def align_row_to_headers(row: dict[str, Any], sheet_headers: list[str]) -> list[str]:
    extra_columns = [column for column in row if column not in sheet_headers]
    if extra_columns:
        raise ValueError(
            "Generated columns are not present in Google Sheet header: "
            f"{extra_columns}. Sheet header: {sheet_headers}"
        )
    return ["" if row.get(header) is None else str(row.get(header, "")) for header in sheet_headers]
