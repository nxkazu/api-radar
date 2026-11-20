"""End-to-end scan pipeline: fetch → diff → persist → report → PR."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.core.openapi import parse_spec

BEFORE = Path("demo/before.yaml").read_text()
AFTER = Path("demo/after.yaml").read_text()


class FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


class FakeGitHub:
    def __init__(self, files: dict[str, str] | None = None) -> None:
        self.files = files or {}
        self.committed: dict[str, str] | None = None
        self.pr: dict | None = None
        self.branch: str | None = None

    def authenticate_for_repo(self, *args, **kwargs) -> None:
        return None

    def fetch_text_files(self, full_name, ref, **kwargs) -> dict[str, str]:
        return dict(self.files)

    def commit_files(self, full_name, *, branch, base_branch, files, message):
        self.committed = dict(files)
        self.branch = branch
        return "deadbeef"

    def open_pull_request(self, full_name, *, head, base, title, body, draft=False):
        self.pr = {"head": head, "base": base, "title": title, "body": body}
        return {"html_url": "https://github.com/acme/petstore/pull/7"}


@pytest.fixture(autouse=True)
def clean_tables():
    from app.db import get_engine
    from app.models import Base

    with get_engine().begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())
    yield


def _create_spec(baseline: str | None) -> tuple[int, int]:
    from app.db import session_scope
    from app.models import Repository, Spec

    parsed = parse_spec(baseline) if baseline else None
    with session_scope() as session:
        repository = Repository(full_name="acme/petstore", default_branch="main")
        session.add(repository)
        session.flush()
        spec = Spec(
            repository_id=repository.id,
            name="petstore",
            source_type="url",
            source="https://example.test/openapi.yaml",
            baseline_content=baseline,
            baseline_version=parsed.version if parsed else None,
            baseline_sha=parsed.sha256 if parsed else None,
        )
        session.add(spec)
        session.flush()
        return repository.id, spec.id


def _scan(spec_id: int, *, auto_pr: bool = False, client=None, monkeypatch=None):
    from app.db import session_scope
    from app.services import scanner

    monkeypatch.setattr(scanner.get_settings(), "auto_pr", auto_pr)
    with session_scope() as session:
        scan = scanner.create_scan(session, spec_id, "test")
        session.flush()
        scan_id = scan.id
    with session_scope() as session:
        return scanner.execute_scan(
            session,
            scan_id,
            client_factory=(lambda: client) if client else None,
        )


def test_baseline_scan_records_snapshot(monkeypatch) -> None:
    _, spec_id = _create_spec(None)
    monkeypatch.setattr("app.services.specs.httpx.get", lambda *a, **k: FakeResponse(BEFORE))

    scan = _scan(spec_id, monkeypatch=monkeypatch)

    assert scan.status == "completed"
    assert scan.total_count == 0
    assert "Baseline recorded" in (scan.report_md or "")
    from app.db import session_scope
    from app.models import Spec

    with session_scope() as session:
        spec = session.get(Spec, spec_id)
        assert spec.baseline_sha == parse_spec(BEFORE).sha256


def test_scan_detects_breaking_changes_and_persists_them(monkeypatch) -> None:
    _, spec_id = _create_spec(BEFORE)
    monkeypatch.setattr("app.services.specs.httpx.get", lambda *a, **k: FakeResponse(AFTER))

    scan = _scan(spec_id, monkeypatch=monkeypatch)

    assert scan.status == "completed"
    assert scan.breaking_count > 0
    assert scan.total_count > 0
    assert scan.report_md and "Breaking changes" in scan.report_md
    assert "Verdict: breaking changes detected" in scan.report_md
    assert scan.pr_url is None  # auto_pr disabled

    from app.db import session_scope
    from app.models import Change

    with session_scope() as session:
        count = session.scalar(select(func.count()).select_from(Change))
        kinds = set(session.scalars(select(Change.kind)).all())
    assert count == scan.total_count
    assert "operation.removed" in kinds
    assert "security.requirement.added" in kinds


def test_baseline_advances_after_scan(monkeypatch) -> None:
    _, spec_id = _create_spec(BEFORE)
    monkeypatch.setattr("app.services.specs.httpx.get", lambda *a, **k: FakeResponse(AFTER))
    first = _scan(spec_id, monkeypatch=monkeypatch)
    assert first.breaking_count > 0

    # Same document again: comparing the new baseline against itself is clean.
    second = _scan(spec_id, monkeypatch=monkeypatch)
    assert second.status == "completed"
    assert second.total_count == 0


def test_auto_pr_rewrites_call_sites_and_attaches_report(monkeypatch) -> None:
    _, spec_id = _create_spec(BEFORE)
    monkeypatch.setattr("app.services.specs.httpx.get", lambda *a, **k: FakeResponse(AFTER))
    client = FakeGitHub(
        files={
            "src/petstore_client.py": (
                'def owner(self, owner_id):\n'
                '    return self._http.get(f"/v1/owners/{owner_id}")\n'
            )
        }
    )

    scan = _scan(spec_id, auto_pr=True, client=client, monkeypatch=monkeypatch)

    assert scan.status == "completed"
    assert scan.pr_url == "https://github.com/acme/petstore/pull/7"
    assert scan.pr_branch and scan.pr_branch.startswith("apiradar/petstore-")
    assert client.pr and "breaking" in client.pr["body"].lower()
    assert client.committed is not None
    assert '"/v2/owners/{owner_id}"' in client.committed["src/petstore_client.py"]

    report_files = [path for path in client.committed if path.startswith(".apiradar/reports/")]
    assert len(report_files) == 1
    assert "API Radar report" in client.committed[report_files[0]]
    assert "src/petstore_client.py:2" in client.committed[report_files[0]]


def test_pr_is_not_opened_without_breaking_changes(monkeypatch) -> None:
    _, spec_id = _create_spec(BEFORE)
    monkeypatch.setattr("app.services.specs.httpx.get", lambda *a, **k: FakeResponse(BEFORE))
    client = FakeGitHub()

    scan = _scan(spec_id, auto_pr=True, client=client, monkeypatch=monkeypatch)

    assert scan.breaking_count == 0
    assert client.pr is None
    assert scan.pr_url is None


def test_fetch_failure_is_recorded_and_baseline_is_kept(monkeypatch) -> None:
    class Boom:
        text = ""

        def raise_for_status(self) -> None:
            import httpx

            raise httpx.ConnectError("connection refused")

    _, spec_id = _create_spec(BEFORE)
    monkeypatch.setattr("app.services.specs.httpx.get", lambda *a, **k: Boom())

    scan = _scan(spec_id, monkeypatch=monkeypatch)

    assert scan.status == "failed"
    assert "could not fetch" in (scan.error or "")
    from app.db import session_scope
    from app.models import Spec

    with session_scope() as session:
        spec = session.get(Spec, spec_id)
        assert spec.baseline_sha == parse_spec(BEFORE).sha256


def test_pr_failure_does_not_fail_the_scan(monkeypatch) -> None:
    class FailingPR(FakeGitHub):
        def open_pull_request(self, *args, **kwargs):
            from app.services.github import GitHubError

            raise GitHubError("no permission", status_code=403)

    _, spec_id = _create_spec(BEFORE)
    monkeypatch.setattr("app.services.specs.httpx.get", lambda *a, **k: FakeResponse(AFTER))
    client = FailingPR()

    scan = _scan(spec_id, auto_pr=True, client=client, monkeypatch=monkeypatch)

    assert scan.status == "completed"
    assert scan.pr_url is None
    assert "no permission" in (scan.pr_error or "")
    assert scan.breaking_count > 0