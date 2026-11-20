"""SQLAlchemy models: repositories, tracked specs, scans and changes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Repository(Base):
    __tablename__ = "repositories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    installation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    default_branch: Mapped[str] = mapped_column(String(255), default="main")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    specs: Mapped[list[Spec]] = relationship(
        back_populates="repository", cascade="all, delete-orphan"
    )


class Spec(Base):
    """A tracked external API contract."""

    __tablename__ = "specs"
    __table_args__ = (UniqueConstraint("repository_id", "name", name="uq_spec_repo_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    source_type: Mapped[str] = mapped_column(String(32))  # repo_path | url
    source: Mapped[str] = mapped_column(String(1024))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    baseline_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    baseline_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    baseline_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    repository: Mapped[Repository] = relationship(back_populates="specs")
    scans: Mapped[list[Scan]] = relationship(
        back_populates="spec", cascade="all, delete-orphan", order_by="Scan.id.desc()"
    )


class Scan(Base):
    __tablename__ = "scans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    spec_id: Mapped[int] = mapped_column(
        ForeignKey("specs.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    trigger: Mapped[str] = mapped_column(String(32), default="manual")
    base_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    head_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    head_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    breaking_count: Mapped[int] = mapped_column(Integer, default=0)
    risky_count: Mapped[int] = mapped_column(Integer, default=0)
    non_breaking_count: Mapped[int] = mapped_column(Integer, default=0)
    total_count: Mapped[int] = mapped_column(Integer, default=0)
    report_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    pr_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    pr_branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pr_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    spec: Mapped[Spec] = relationship(back_populates="scans")
    changes: Mapped[list[Change]] = relationship(
        back_populates="scan", cascade="all, delete-orphan", order_by="Change.id"
    )


class Change(Base):
    __tablename__ = "changes"
    __table_args__ = (Index("ix_changes_scan_severity", "scan_id", "severity"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scan_id: Mapped[int] = mapped_column(ForeignKey("scans.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(64), index=True)
    severity: Mapped[str] = mapped_column(String(32), index=True)
    location: Mapped[str] = mapped_column(String(1024))
    pointer: Mapped[str] = mapped_column(String(2048))
    message: Mapped[str] = mapped_column(Text)
    before: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    after: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    fix_hint: Mapped[str | None] = mapped_column(Text, nullable=True)

    scan: Mapped[Scan] = relationship(back_populates="changes")