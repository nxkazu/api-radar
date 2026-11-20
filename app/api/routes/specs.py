"""Tracked spec CRUD."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import Repository, Spec
from ...schemas import SpecCreate, SpecOut, SpecUpdate

router = APIRouter(prefix="/api/v1/specs", tags=["specs"])


@router.post("", response_model=SpecOut, status_code=201)
def create_spec(payload: SpecCreate, db: Session = Depends(get_db)) -> Spec:
    if db.get(Repository, payload.repository_id) is None:
        raise HTTPException(status_code=404, detail="repository not found")
    if payload.source_type == "url" and not payload.source.startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="url specs must start with http:// or https://")

    existing = db.scalar(
        select(Spec).where(
            Spec.repository_id == payload.repository_id, Spec.name == payload.name
        )
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="a spec with this name already exists")

    spec = Spec(
        repository_id=payload.repository_id,
        name=payload.name,
        source_type=payload.source_type,
        source=payload.source,
    )
    db.add(spec)
    db.commit()
    db.refresh(spec)
    return spec


@router.get("", response_model=list[SpecOut])
def list_specs(
    repository_id: int | None = None,
    enabled: bool | None = None,
    db: Session = Depends(get_db),
) -> list[Spec]:
    statement = select(Spec).order_by(Spec.id)
    if repository_id is not None:
        statement = statement.where(Spec.repository_id == repository_id)
    if enabled is not None:
        statement = statement.where(Spec.enabled.is_(enabled))
    return list(db.scalars(statement).all())


@router.get("/{spec_id}", response_model=SpecOut)
def get_spec(spec_id: int, db: Session = Depends(get_db)) -> Spec:
    spec = db.get(Spec, spec_id)
    if spec is None:
        raise HTTPException(status_code=404, detail="spec not found")
    return spec


@router.patch("/{spec_id}", response_model=SpecOut)
def update_spec(spec_id: int, payload: SpecUpdate, db: Session = Depends(get_db)) -> Spec:
    spec = db.get(Spec, spec_id)
    if spec is None:
        raise HTTPException(status_code=404, detail="spec not found")
    spec.enabled = payload.enabled
    db.commit()
    db.refresh(spec)
    return spec