"""GitHub webhook receiver.

Handled events:

* ``push`` to the default branch — rescan every spec tracked for that repo.
* ``installation`` / ``installation_repositories`` — register repositories and
  auto-discover OpenAPI documents.
* ``ping`` — health check from GitHub when the webhook is created.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config import get_settings
from ...db import get_db, session_scope
from ...models import Repository, Spec
from ...services.github import GitHubClient, GitHubError, verify_webhook_signature
from ...services.scanner import create_scan, execute_scan
from ...services.specs import discover_specs_for_repo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _dispatch_or_run(scan_ids: list[int]) -> None:
    from ...workers.tasks import scan_spec_task

    for scan_id in scan_ids:
        try:
            scan_spec_task.delay(scan_id)
        except Exception as exc:  # broker unavailable: run inline in the worker thread
            logger.warning("dispatch failed for scan %s, running inline: %s", scan_id, exc)
            with session_scope() as session:
                execute_scan(session, scan_id)


def _handle_push(db: Session, payload: dict) -> list[int]:
    full_name = str((payload.get("repository") or {}).get("full_name") or "").lower()
    branch = str(payload.get("ref") or "").removeprefix("refs/heads/")
    if not full_name:
        return []

    repository = db.scalar(select(Repository).where(Repository.full_name == full_name))
    if repository is None or branch != repository.default_branch:
        return []

    specs = list(
        db.scalars(
            select(Spec).where(Spec.repository_id == repository.id, Spec.enabled.is_(True))
        ).all()
    )
    scan_ids = [create_scan(db, spec.id, "push").id for spec in specs]
    db.commit()
    return scan_ids


def _handle_installation(payload: dict) -> None:
    installation_id = (payload.get("installation") or {}).get("id")
    repositories = payload.get("repositories") or []
    if not installation_id:
        return

    client = GitHubClient.from_settings()
    scan_ids: list[int] = []

    for repo_data in repositories:
        full_name = str(repo_data.get("full_name") or "").lower()
        if not full_name:
            continue
        with session_scope() as session:
            repository = session.scalar(
                select(Repository).where(Repository.full_name == full_name)
            )
            if repository is None:
                repository = Repository(
                    full_name=full_name,
                    installation_id=installation_id,
                    default_branch=str(repo_data.get("default_branch") or "main"),
                )
                session.add(repository)
                session.flush()
            else:
                repository.installation_id = installation_id
                repository.default_branch = str(
                    repo_data.get("default_branch") or repository.default_branch
                )

            try:
                created, _ = discover_specs_for_repo(session, repository, client)
                logger.info("installation %s: discovered %s in %s", installation_id, created, full_name)
            except GitHubError as exc:
                logger.warning("spec discovery failed for %s: %s", full_name, exc)

            session.flush()
            specs = session.scalars(
                select(Spec).where(Spec.repository_id == repository.id, Spec.enabled.is_(True))
            ).all()
            scan_ids.extend(create_scan(session, spec.id, "installation").id for spec in specs)

    if scan_ids:
        _dispatch_or_run(scan_ids)


@router.post("/github", status_code=202)
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    settings = get_settings()
    body = await request.body()

    if settings.github_webhook_secret:
        signature = request.headers.get("x-hub-signature-256")
        if not verify_webhook_signature(settings.github_webhook_secret, body, signature):
            raise HTTPException(status_code=401, detail="invalid webhook signature")

    event = request.headers.get("x-github-event", "")
    try:
        payload = json.loads(body or b"{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="invalid JSON payload") from exc

    if event == "ping":
        return {"status": "pong"}

    if event == "push":
        scan_ids = _handle_push(db, payload)
        if scan_ids:
            background_tasks.add_task(_dispatch_or_run, scan_ids)
        return {"status": "accepted", "queued": str(len(scan_ids))}

    if event == "installation" or event == "installation_repositories":
        action = payload.get("action")
        if action in {"created", "added", "new_permissions_accepted"}:
            background_tasks.add_task(_handle_installation, payload)
        return {"status": "accepted"}

    return {"status": "ignored", "event": event}