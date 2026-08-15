from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Iterable

from fastapi import Depends, FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.api.dashboard_routes import router as dashboard_router
from app.api.health_routes import router as health_router
from app.api.sync_routes import router as sync_router
from app.core.logging import configure_logging
from app.core.security import require_auth

configure_logging()

app = FastAPI(title="Expense Tracker")
app.include_router(health_router)
app.include_router(dashboard_router)
app.include_router(sync_router)

STATIC_DIR = Path(__file__).resolve().parents[2] / "frontend" / "static"


def _build_asset_version(paths: Iterable[Path]) -> str:
    digest = sha256()
    for path in paths:
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


ASSET_VERSION = _build_asset_version(
    [STATIC_DIR / "styles.css", STATIC_DIR / "app.js"]
)
INDEX_HTML = (STATIC_DIR / "index.html").read_text(encoding="utf-8").replace(
    "{{ASSET_VERSION}}", ASSET_VERSION
)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get(
    "/",
    include_in_schema=False,
    dependencies=[Depends(require_auth)],
    response_class=HTMLResponse,
)
def index():
    return HTMLResponse(
        INDEX_HTML,
        headers={"Cache-Control": "no-store"},
    )
