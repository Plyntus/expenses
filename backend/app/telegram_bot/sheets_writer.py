from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from app.sheets.client import GoogleSheetsClient
from app.sheets.mapper import MappedExpense, map_sheet_row, sheet_values_to_rows


class SheetsWriter:
    def __init__(self, client: GoogleSheetsClient) -> None:
        self._client = client

    async def append_dataframe(self, df: pd.DataFrame) -> None:
        rows: list[dict[str, Any]] = df.fillna("").to_dict("records")
        await self._client.append_rows(rows)

    async def list_last_expenses(self, limit: int = 5) -> list[MappedExpense]:
        values = await self._client.get_all_values()
        if len(values) <= 1:
            return []

        expenses: list[MappedExpense] = []
        rows = sheet_values_to_rows(values)
        for row in reversed(rows):
            try:
                expense = map_sheet_row(row)
            except ValueError:
                logging.warning(
                    "Skipping invalid Google Sheets row while listing last expenses",
                    exc_info=True,
                )
                continue
            if expense.amount >= 0:
                continue
            expenses.append(expense)
            if len(expenses) >= limit:
                break
        return expenses
