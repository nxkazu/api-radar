"""Liveness/readiness endpoint."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from ...config import get_settings
from ...db import get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz(db: Session = Depends(get_db)) -> JSONResponse:
    database = "ok"
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - depends on infrastructure
        logger.warning("database health check failed: %s", exc)
        database = "unavailable"

    redis_status = "not_configured"
    try:
        import redis

        client = redis.Redis.from_url(
            get_settings().redis_url, socket_connect_timeout=1, socket_timeout=1
        )
        redis_status = "ok" if client.ping() else "unavailable"
    except Exception:
        redis_status = "unavailable"

    healthy = database == "ok"
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "status": "ok" if healthy else "degraded",
            "database": database,
            "redis": redis_status,
        },
    )