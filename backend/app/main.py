from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse
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
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False, dependencies=[Depends(require_auth)])
def index():
    return FileResponse(STATIC_DIR / "index.html")
