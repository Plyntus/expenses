from __future__ import annotations

from typing import Any

import pandas as pd

from app.sheets.client import GoogleSheetsClient


class SheetsWriter:
    def __init__(self, client: GoogleSheetsClient) -> None:
        self._client = client

    async def append_dataframe(self, df: pd.DataFrame) -> None:
        rows: list[dict[str, Any]] = df.fillna("").to_dict("records")
        await self._client.append_rows(rows)
