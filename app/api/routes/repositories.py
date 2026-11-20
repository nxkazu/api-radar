"""Repository registration, sync and spec discovery."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import Repository
from ...schemas import DiscoverRequest, DiscoverResponse, RepositoryCreate, RepositoryOut
from ...services.github import GitHubError
from ...services.specs import discover_specs_for_repo, sync_repository

router = APIRouter(prefix="/api/v1/repositories", tags=["repositories"])


def _get_repository(db: Session, repository_id: int) -> Repository:
    repository = db.get(Repository, repository_id)
    if repository is None:
        raise HTTPException(status_code=404, detail="repository not found")
    return repository


@router.post("", response_model=RepositoryOut, status_code=201)
def create_repository(payload: RepositoryCreate, db: Session = Depends(get_db)) -> Repository:
    existing = db.scalar(
        select(Repository).where(Repository.full_name == payload.full_name.lower())
    )
    if existing is not None:
        return existing
    repository = Repository(
        full_name=payload.full_name.lower(),
        default_branch=payload.default_branch,
        installation_id=payload.installation_id,
    )
    db.add(repository)
    db.commit()
    db.refresh(repository)
    return repository


@router.get("", response_model=list[RepositoryOut])
def list_repositories(db: Session = Depends(get_db)) -> list[Repository]:
    return list(db.scalars(select(Repository).order_by(Repository.id)).all())


@router.get("/{repository_id}", response_model=RepositoryOut)
def get_repository(repository_id: int, db: Session = Depends(get_db)) -> Repository:
    return _get_repository(db, repository_id)


@router.post("/{repository_id}/sync", response_model=RepositoryOut)
def sync(repository_id: int, db: Session = Depends(get_db)) -> Repository:
    repository = _get_repository(db, repository_id)
    try:
        sync_repository(
            db,
            repository.full_name,
            installation_id=repository.installation_id,
        )
    except GitHubError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    db.commit()
    db.refresh(repository)
    return repository


@router.post("/{repository_id}/discover", response_model=DiscoverResponse)
def discover(
    repository_id: int,
    payload: DiscoverRequest | None = None,
    db: Session = Depends(get_db),
) -> DiscoverResponse:
    repository = _get_repository(db, repository_id)
    if payload and payload.installation_id is not None:
        repository.installation_id = payload.installation_id
    try:
        created, skipped = discover_specs_for_repo(db, repository)
    except GitHubError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    db.commit()
    return DiscoverResponse(
        repository=repository.full_name, created=created, skipped=skipped
    )