"""Pydantic request/response models for the HTTP API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class RepositoryCreate(BaseModel):
    full_name: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    default_branch: str = "main"
    installation_id: int | None = None


class RepositoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    default_branch: str
    installation_id: int | None
    created_at: datetime


class SpecCreate(BaseModel):
    repository_id: int
    name: str = Field(min_length=1, max_length=255)
    source_type: Literal["repo_path", "url"] = "repo_path"
    source: str = Field(min_length=1, max_length=1024)


class SpecUpdate(BaseModel):
    enabled: bool


class SpecOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    repository_id: int
    name: str
    source_type: str
    source: str
    enabled: bool
    baseline_version: str | None
    baseline_sha: str | None
    last_checked_at: datetime | None
    created_at: datetime


class ChangeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    severity: str
    location: str
    pointer: str
    message: str
    before: Any | None
    after: Any | None
    fix_hint: str | None


class ScanSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    spec_id: int
    status: str
    trigger: str
    base_version: str | None
    head_version: str | None
    head_sha: str | None
    breaking_count: int
    risky_count: int
    non_breaking_count: int
    total_count: int
    pr_url: str | None
    pr_branch: str | None
    error: str | None
    pr_error: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class ScanDetail(ScanSummary):
    report_md: str | None = None
    changes: list[ChangeOut] = Field(default_factory=list)


class DiffRequest(BaseModel):
    before: dict[str, Any]
    after: dict[str, Any]
    spec_name: str | None = None
    repository: str | None = None


class DiffResponse(BaseModel):
    counts: dict[str, int]
    has_breaking: bool
    fingerprint: str
    changes: list[dict[str, Any]]
    markdown: str


class DiscoverRequest(BaseModel):
    installation_id: int | None = None


class DiscoverResponse(BaseModel):
    repository: str
    created: list[str]
    skipped: list[str]