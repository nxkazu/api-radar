"""Celery application and scheduled scan configuration."""

from __future__ import annotations

from datetime import timedelta

from celery import Celery

from ..config import get_settings

settings = get_settings()

celery_app = Celery(
    "apiradar",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_always_eager=settings.celery_task_always_eager,
    task_eager_propagates=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    beat_schedule={
        "scan-all-specs": {
            "task": "apiradar.scan_all",
            "schedule": timedelta(minutes=max(settings.scan_interval_minutes, 1)),
        }
    },
)