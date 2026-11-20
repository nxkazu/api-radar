"""Celery tasks. Each task owns its database session."""

from __future__ import annotations

import logging

from ..db import session_scope
from ..models import Spec
from ..services.scanner import create_scan, execute_scan
from .celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="apiradar.scan_spec")
def scan_spec_task(scan_id: int) -> dict[str, object]:
    with session_scope() as session:
        scan = execute_scan(session, scan_id)
        return {
            "scan_id": scan.id,
            "status": scan.status,
            "breaking": scan.breaking_count,
            "total": scan.total_count,
            "error": scan.error,
        }


@celery_app.task(name="apiradar.scan_all")
def scan_all_task() -> dict[str, object]:
    from sqlalchemy import select

    with session_scope() as session:
        spec_ids = list(
            session.scalars(select(Spec.id).where(Spec.enabled.is_(True)).order_by(Spec.id)).all()
        )
        scan_ids = [create_scan(session, spec_id, "schedule").id for spec_id in spec_ids]

    for scan_id in scan_ids:
        scan_spec_task.delay(scan_id)
    return {"specs": len(spec_ids), "queued": len(scan_ids)}