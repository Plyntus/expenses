import os
import re
from dataclasses import dataclass
from zoneinfo import ZoneInfo

PRODUCT_TITLE = "Flipper One Pre-order"
TRACKED_SKUS = (
    "FLP-O-COUPON-349",
    "FLP-O-COUPON-399",
    "FLP-O-COUPON-449",
)


@dataclass(frozen=True)
class Settings:
    username: str
    password: str
    shop: str = ""
    client_id: str = ""
    client_secret: str = ""
    access_token: str = ""
    api_version: str = "2026-10"
    db_path: str = "/data/sales.sqlite3"
    timezone: str = "Europe/Belgrade"
    history_days: int = 30
    poll_seconds: int = 60

    @property
    def configured(self):
        return bool(self.shop and (self.access_token or (self.client_id and self.client_secret)))

    @classmethod
    def from_env(cls):
        settings = cls(
            username=os.getenv("DASHBOARD_USERNAME", ""),
            password=os.getenv("DASHBOARD_PASSWORD", ""),
            shop=os.getenv("SHOPIFY_SHOP", "").strip().lower(),
            client_id=os.getenv("SHOPIFY_CLIENT_ID", ""),
            client_secret=os.getenv("SHOPIFY_CLIENT_SECRET", ""),
            access_token=os.getenv("SHOPIFY_ACCESS_TOKEN", ""),
            api_version=os.getenv("SHOPIFY_API_VERSION", "2026-10"),
            db_path=os.getenv("DATABASE_PATH", "/data/sales.sqlite3"),
            timezone=os.getenv("DASHBOARD_TIMEZONE", "Europe/Belgrade"),
            history_days=int(os.getenv("HISTORY_DAYS", "30")),
            poll_seconds=int(os.getenv("POLL_SECONDS", "60")),
        )
        if not settings.username or len(settings.password) < 16:
            raise ValueError("Set DASHBOARD_USERNAME and DASHBOARD_PASSWORD (at least 16 characters)")
        if settings.shop and not re.fullmatch(r"[a-z0-9][a-z0-9-]*\.myshopify\.com", settings.shop):
            raise ValueError("SHOPIFY_SHOP must be a hostname such as store.myshopify.com")
        if not re.fullmatch(r"20\d{2}-(01|04|07|10)", settings.api_version):
            raise ValueError("Invalid SHOPIFY_API_VERSION")
        if not 1 <= settings.history_days <= 60 or settings.poll_seconds < 60:
            raise ValueError("HISTORY_DAYS must be 1..60; POLL_SECONDS must be at least 60")
        ZoneInfo(settings.timezone)
        return settings
