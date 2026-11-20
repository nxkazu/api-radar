"""Spec source retrieval and discovery."""

from __future__ import annotations

import logging
import re

import httpx

from ..core.fixgen import is_text_file
from ..models import Repository, Spec
from .github import GitHubClient

logger = logging.getLogger(__name__)

SPEC_EXTENSIONS = (".yaml", ".yml", ".json")
MAX_DISCOVERED_SPECS = 20


class SpecFetchError(RuntimeError):
    pass


def fetch_spec_source(spec: Spec, repository: Repository, client: GitHubClient) -> tuple[str, str | None]:
    """Return ``(raw_text, blob_sha)`` for a tracked spec."""
    if spec.source_type == "url":
        try:
            response = httpx.get(spec.source, follow_redirects=True, timeout=30.0)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SpecFetchError(f"could not fetch {spec.source}: {exc}") from exc
        return response.text, None

    if spec.source_type != "repo_path":
        raise SpecFetchError(f"unknown source_type {spec.source_type!r}")

    client.authenticate_for_repo(repository.full_name, repository.installation_id)
    file = client.get_file(repository.full_name, spec.source, repository.default_branch)
    if file is None:
        raise SpecFetchError(
            f"{spec.source} not found in {repository.full_name}@{repository.default_branch}"
        )
    return file.content, file.sha


def discover_spec_candidates(paths: list[str]) -> list[str]:
    """Heuristically find OpenAPI documents in a repository tree."""
    candidates: list[str] = []
    for path in paths:
        if not is_text_file(path):
            continue
        segments = path.lower().split("/")
        if any(part in {"node_modules", "vendor", "dist", "build"} for part in segments[:-1]):
            continue
        name = segments[-1]
        if not name.endswith(SPEC_EXTENSIONS):
            continue
        if not re.search(r"openapi|swagger", name):
            continue
        candidates.append(path)
    return sorted(candidates)[:MAX_DISCOVERED_SPECS]


def discover_specs_for_repo(
    session,
    repository: Repository,
    client: GitHubClient | None = None,
) -> tuple[list[str], list[str]]:
    """Register spec files found in a repository. Returns (created, skipped)."""
    client = client or GitHubClient()
    client.authenticate_for_repo(repository.full_name, repository.installation_id)
    tree = client.get_tree(repository.full_name, repository.default_branch)
    paths = [str(entry.get("path")) for entry in tree]
    candidates = discover_spec_candidates(paths)

    existing = {spec.source for spec in repository.specs if spec.source_type == "repo_path"}
    name_counts: dict[str, int] = {}
    for path in candidates:
        name_counts[path.rsplit("/", 1)[-1]] = name_counts.get(path.rsplit("/", 1)[-1], 0) + 1

    created: list[str] = []
    skipped: list[str] = []
    for path in candidates:
        if path in existing:
            skipped.append(path)
            continue
        basename = path.rsplit("/", 1)[-1]
        name = basename if name_counts[basename] == 1 else path
        session.add(
            Spec(
                repository_id=repository.id,
                name=name,
                source_type="repo_path",
                source=path,
            )
        )
        created.append(path)
    return created, skipped


def sync_repository(
    session,
    full_name: str,
    *,
    installation_id: int | None = None,
    client: GitHubClient | None = None,
) -> Repository:
    """Create or refresh a repository row from GitHub metadata."""
    from sqlalchemy import select

    client = client or GitHubClient()
    client.authenticate_for_repo(full_name, installation_id)
    data = client.get_repository(full_name)

    repository = session.scalar(select(Repository).where(Repository.full_name == full_name))
    if repository is None:
        repository = Repository(full_name=full_name)
        session.add(repository)
        session.flush()
    repository.default_branch = str(data.get("default_branch") or repository.default_branch)
    if installation_id is not None:
        repository.installation_id = installation_id
    return repository