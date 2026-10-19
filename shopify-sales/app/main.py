import asyncio
import contextlib
import secrets
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path
from time import monotonic

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from .config import PRODUCT_TITLE, TRACKED_SKUS, Settings
from .shopify import Shopify
from .store import Store
from .sync import Sync
from .traffic import TrafficSync, dashboard as traffic_dashboard
from .acquisition import AcquisitionSync, dashboard as acquisition_dashboard

STATIC = Path(__file__).parent / "static"


def create_app(settings=None, shopify=None, poll=True):
    settings = settings or Settings.from_env()
    store = Store(settings.db_path)
    shopify = shopify or Shopify(settings)
    sync = Sync(settings, store, shopify)
    traffic = TrafficSync(settings, store, shopify)
    acquisition = AcquisitionSync(settings, store, shopify)
    failures = OrderedDict()

    @asynccontextmanager
    async def lifespan(app):
        tasks = [asyncio.create_task(worker.run()) for worker in (sync, traffic, acquisition)] if poll else []
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await shopify.close()

    app = FastAPI(title="Sales Pulse", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store, app.state.sync = store, sync
    basic = HTTPBasic(auto_error=False)

    async def authenticate(request: Request, credentials: HTTPBasicCredentials | None = Depends(basic)):
        address = request.client.host if request.client else "unknown"
        now = monotonic()
        count, until = failures.get(address, (0, 0))
        if now >= until:
            count = 0
        if count >= 10:
            raise HTTPException(429, "Too many login attempts; try again in 5 minutes", headers={"Retry-After": "300"})
        valid_user = secrets.compare_digest((credentials.username if credentials else "").encode(), settings.username.encode())
        valid_pass = secrets.compare_digest((credentials.password if credentials else "").encode(), settings.password.encode())
        if not (valid_user and valid_pass):
            if credentials:
                failures[address] = (count + 1, now + 300)
                failures.move_to_end(address)
                if len(failures) > 4096:
                    failures.popitem(last=False)
            raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": 'Basic realm="Sales Pulse", charset="UTF-8"'})
        failures.pop(address, None)

    @app.middleware("http")
    async def security(request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("X-Sales-Request") != "1":
            return JSONResponse({"detail": "Missing request header"}, status_code=403)
        response = await call_next(request)
        response.headers.update({
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        })
        return response

    @app.get("/healthz")
    async def health():
        return {"status": "ok"}

    @app.get("/favicon.png")
    async def favicon():
        return FileResponse(STATIC / "favicon.png", media_type="image/png")

    @app.get("/", dependencies=[Depends(authenticate)])
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/assets/{name}", dependencies=[Depends(authenticate)])
    async def asset(name: str):
        if name not in ("app.js", "style.css", "chart.umd.js", "flipper-one.png"):
            raise HTTPException(404)
        return FileResponse(STATIC / name)

    @app.get("/api/status", dependencies=[Depends(authenticate)])
    async def status():
        return {"configured": settings.configured, "shop": settings.shop, "shop_name": store.get("shop_name"),
                "timezone": settings.timezone, "shop_timezone": store.get("shop_timezone"),
                "history_days": settings.history_days, "poll_seconds": settings.poll_seconds,
                "syncing": sync.running, "imported_orders": store.get("sync_progress", 0),
                "last_success": store.get("last_success"), "last_attempt": store.get("last_attempt"),
                "last_error": store.get("last_error"), "product_title": PRODUCT_TITLE,
                "refunds_ready": store.refunds_ready(),
                "tracked_skus": TRACKED_SKUS}

    @app.get("/api/dashboard", dependencies=[Depends(authenticate)])
    async def dashboard(days: int = Query(7, ge=1, le=60)):
        try:
            return await asyncio.to_thread(store.dashboard, days, settings.timezone)
        except ValueError:
            raise HTTPException(409, "Shop currency changed; choose a shorter period")

    @app.get("/api/traffic", dependencies=[Depends(authenticate)])
    async def sessions(days: int = Query(7, ge=1, le=60)):
        return await asyncio.to_thread(traffic_dashboard, store, settings, days)

    @app.get("/api/acquisition", dependencies=[Depends(authenticate)])
    async def source_charts(days: int = Query(7, ge=1, le=60)):
        return await asyncio.to_thread(acquisition_dashboard, store, settings, days)

    return app
