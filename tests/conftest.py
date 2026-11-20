from __future__ import annotations

import os

os.environ.setdefault("APIRADAR_DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("APIRADAR_CELERY_TASK_ALWAYS_EAGER", "true")
os.environ.setdefault("APIRADAR_GITHUB_WEBHOOK_SECRET", "test-secret")
os.environ.setdefault("APIRADAR_REDIS_URL", "redis://localhost:6379/15")

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="session", autouse=True)
def _configure() -> None:
    from app.config import get_settings
    from app.db import init_db

    get_settings.cache_clear()
    init_db()


@pytest.fixture()
def client() -> TestClient:
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def settings():
    from app.config import get_settings

    return get_settings()