from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    database_url: str
    google_sheets_id: str | None
    google_sheets_url: str | None
    google_worksheet_name: str
    google_service_account_json: str | None
    google_application_credentials: Path | None
    telegram_bot_token: str | None
    openai_api_key: str | None
    openai_transcribe_model: str
    openai_text_model: str
    app_username: str | None
    app_password: str | None
    backend_url: str
    frontend_url: str
    system_prompt_path: Path

    @property
    def auth_enabled(self) -> bool:
        return bool(self.app_username and self.app_password)


def load_settings() -> Settings:
    load_dotenv()
    credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or os.getenv(
        "GOOGLE_SERVICE_ACCOUNT_FILE"
    )
    return Settings(
        database_url=os.getenv(
            "DATABASE_URL",
            "postgresql+psycopg://expenses:expenses@postgres:5432/expenses",
        ),
        google_sheets_id=os.getenv("GOOGLE_SHEETS_ID")
        or os.getenv("GOOGLE_SPREADSHEET_ID"),
        google_sheets_url=os.getenv("GOOGLE_SHEETS_URL"),
        google_worksheet_name=os.getenv("GOOGLE_WORKSHEET_NAME", "Trans"),
        google_service_account_json=os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON"),
        google_application_credentials=Path(credentials_path).expanduser()
        if credentials_path
        else None,
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        openai_transcribe_model=os.getenv(
            "OPENAI_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe"
        ),
        openai_text_model=os.getenv("OPENAI_TEXT_MODEL", "gpt-4.1-nano"),
        app_username=os.getenv("APP_USERNAME"),
        app_password=os.getenv("APP_PASSWORD"),
        backend_url=os.getenv("BACKEND_URL", "http://localhost:8000"),
        frontend_url=os.getenv("FRONTEND_URL", "http://localhost:8000"),
        system_prompt_path=Path(
            os.getenv("SYSTEM_PROMPT_PATH", "app/llm/prompts.py")
        ),
    )


def get_google_service_account_info(settings: Settings) -> dict | None:
    if not settings.google_service_account_json:
        return None
    return json.loads(settings.google_service_account_json)


settings = load_settings()
