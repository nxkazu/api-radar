"""Scan history, reports and change listings."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ...db import get_db
from ...models import Change, Scan, Spec
from ...schemas import ChangeOut, ScanDetail, ScanSummary
from ...services.scanner import queue_scan

router = APIRouter(prefix="/api/v1", tags=["scans"])


def _get_scan(db: Session, scan_id: int, *, with_changes: bool = False) -> Scan:
    statement = select(Scan).where(Scan.id == scan_id)
    if with_changes:
        statement = statement.options(selectinload(Scan.changes))
    scan = db.scalar(statement)
    if scan is None:
        raise HTTPException(status_code=404, detail="scan not found")
    return scan


@router.post("/specs/{spec_id}/scan", response_model=ScanSummary, status_code=202)
def trigger_scan(spec_id: int, db: Session = Depends(get_db)) -> Scan:
    spec = db.get(Spec, spec_id)
    if spec is None:
        raise HTTPException(status_code=404, detail="spec not found")
    if not spec.enabled:
        raise HTTPException(status_code=409, detail="spec is disabled")
    return queue_scan(db, spec_id, "manual")


@router.get("/scans", response_model=list[ScanSummary])
def list_scans(
    spec_id: int | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[Scan]:
    statement = select(Scan).order_by(Scan.id.desc()).offset(offset).limit(limit)
    if spec_id is not None:
        statement = statement.where(Scan.spec_id == spec_id)
    if status is not None:
        statement = statement.where(Scan.status == status)
    return list(db.scalars(statement).all())


@router.get("/scans/{scan_id}", response_model=ScanDetail)
def get_scan(scan_id: int, db: Session = Depends(get_db)) -> Scan:
    return _get_scan(db, scan_id, with_changes=True)


@router.get("/scans/{scan_id}/report", response_class=PlainTextResponse)
def get_report(scan_id: int, db: Session = Depends(get_db)) -> str:
    scan = _get_scan(db, scan_id)
    if not scan.report_md:
        raise HTTPException(status_code=404, detail="report is not available yet")
    return scan.report_md


@router.get("/scans/{scan_id}/changes", response_model=list[ChangeOut])
def list_changes(
    scan_id: int,
    severity: str | None = None,
    db: Session = Depends(get_db),
) -> list[Change]:
    _get_scan(db, scan_id)
    statement = select(Change).where(Change.scan_id == scan_id).order_by(Change.id)
    if severity is not None:
        statement = statement.where(Change.severity == severity)
    return list(db.scalars(statement).all())