"""FastAPI application entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api.routes import diff, health, repositories, scans, specs, webhooks
from .config import get_settings
from .db import init_db

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="API Radar",
    version="0.1.0",
    description=(
        "Track OpenAPI contracts your repository depends on, detect breaking "
        "changes, and generate migration pull requests."
    ),
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(repositories.router)
app.include_router(specs.router)
app.include_router(scans.router)
app.include_router(diff.router)
app.include_router(webhooks.router)


@app.get("/", tags=["meta"])
def root() -> dict[str, object]:
    return {
        "name": settings.app_name,
        "version": app.version,
        "docs": "/docs",
        "endpoints": {
            "diff": "POST /api/v1/diff",
            "repositories": "GET/POST /api/v1/repositories",
            "specs": "GET/POST /api/v1/specs",
            "scans": "GET /api/v1/scans",
            "trigger_scan": "POST /api/v1/specs/{id}/scan",
            "webhook": "POST /webhooks/github",
            "health": "GET /healthz",
        },
    }