from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from openai import OpenAI

from app.llm.prompts import get_system_prompt


def _format_date(dt: datetime) -> str:
    return f"{dt.month}/{dt.day}/{dt.year}"


class ExpenseParser:
    def __init__(
        self,
        *,
        api_key: str,
        transcribe_model: str,
        text_model: str,
        system_prompt_path: Path | None = None,
    ) -> None:
        self._client = OpenAI(api_key=api_key)
        self._transcribe_model = transcribe_model
        self._text_model = text_model
        self._custom_prompt_path = system_prompt_path

    async def transcribe(self, audio_path: Path) -> str:
        return await asyncio.to_thread(self._transcribe_sync, audio_path)

    async def structure_text(self, user_text: str) -> str:
        return await asyncio.to_thread(self._structure_text_sync, user_text)

    def _transcribe_sync(self, audio_path: Path) -> str:
        with audio_path.open("rb") as audio_file:
            transcription = self._client.audio.transcriptions.create(
                model=self._transcribe_model,
                file=audio_file,
            )
        return transcription.text

    def _get_system_prompt(self) -> str:
        if (
            self._custom_prompt_path
            and self._custom_prompt_path.exists()
            and self._custom_prompt_path.suffix != ".py"
        ):
            return self._custom_prompt_path.read_text(encoding="utf-8")
        return get_system_prompt()

    def _structure_text_sync(self, user_text: str) -> str:
        response = self._client.chat.completions.create(
            model=self._text_model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": self._get_system_prompt()},
                {"role": "user", "content": user_text},
            ],
        )
        content = response.choices[0].message.content
        if not content:
            raise ValueError("OpenAI returned an empty response")
        return content


def parse_rows(
    raw_json: str, current_date: datetime | None = None
) -> list[dict[str, Any]]:
    data = json.loads(raw_json)
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict) and isinstance(data.get("rows"), list):
        rows = data["rows"]
    elif isinstance(data, dict):
        rows = [data]
    else:
        raise ValueError("LLM response must be a JSON object or array")

    normalized_rows = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Every row must be a JSON object")
        _normalize_date_placeholders(row, current_date)
        normalized_rows.append(row)
    if not normalized_rows:
        raise ValueError("LLM response contains no rows")
    return normalized_rows


def _normalize_date_placeholders(
    row: dict[str, Any], current_date: datetime | None = None
) -> None:
    date_value = row.get("Date")
    if not isinstance(date_value, str):
        return

    if current_date is None:
        current_date = datetime.now()

    placeholders = {
        "[TODAY]": current_date,
        "TODAY": current_date,
        "CURRENT DATE": current_date,
        "[CURRENT_DATE]": current_date,
        "[YESTERDAY]": current_date - timedelta(days=1),
        "YESTERDAY": current_date - timedelta(days=1),
    }
    normalized = date_value.strip().upper()
    if normalized in placeholders:
        row["Date"] = _format_date(placeholders[normalized])


def rows_to_dataframe(rows: Iterable[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(list(rows)).fillna("")


def dataframe_to_confirmation_text(df: pd.DataFrame) -> str:
    preview = df.fillna("").head(20)
    blocks = []
    for _, row in preview.iterrows():
        blocks.append(
            "\n".join(
                [
                    f"Date: {row.get('Date', '')}",
                    f"Sum: {row.get('Sum', '')}",
                    f"Comment: {row.get('Comment', '')}",
                ]
            )
        )
    return "\n\n".join(blocks)
