from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.core.config import settings

security = HTTPBasic(auto_error=False)


def require_auth(credentials: HTTPBasicCredentials | None = Depends(security)) -> None:
    if not settings.auth_enabled:
        return
    if credentials is None:
        raise _unauthorized()
    username_ok = secrets.compare_digest(credentials.username, settings.app_username or "")
    password_ok = secrets.compare_digest(credentials.password, settings.app_password or "")
    if not (username_ok and password_ok):
        raise _unauthorized()


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
        headers={"WWW-Authenticate": "Basic"},
    )
