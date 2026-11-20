"""HTTP API behaviour, including the webhook receiver."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import pytest
import yaml

BEFORE_TEXT = Path("demo/before.yaml").read_text()
AFTER_TEXT = Path("demo/after.yaml").read_text()
BEFORE = yaml.safe_load(BEFORE_TEXT)
AFTER = yaml.safe_load(AFTER_TEXT)


class FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


@pytest.fixture(autouse=True)
def clean_tables():
    from app.db import get_engine
    from app.models import Base

    with get_engine().begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())
    yield


@pytest.fixture()
def fake_spec(monkeypatch):
    state = {"text": BEFORE_TEXT}

    def fake_get(*args, **kwargs):
        return FakeResponse(state["text"])

    monkeypatch.setattr("app.services.specs.httpx.get", fake_get)
    return state


def _bootstrap(client) -> tuple[dict, dict]:
    repository = client.post(
        "/api/v1/repositories", json={"full_name": "Acme/Petstore", "default_branch": "main"}
    )
    assert repository.status_code == 201
    repo = repository.json()
    spec = client.post(
        "/api/v1/specs",
        json={
            "repository_id": repo["id"],
            "name": "petstore",
            "source_type": "url",
            "source": "https://example.test/openapi.yaml",
        },
    )
    assert spec.status_code == 201
    return repo, spec.json()


def test_health_and_root(client) -> None:
    root = client.get("/")
    assert root.status_code == 200
    assert root.json()["name"]

    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.json()["database"] == "ok"


def test_repository_registration_is_idempotent(client) -> None:
    first = client.post("/api/v1/repositories", json={"full_name": "Acme/Petstore"})
    second = client.post("/api/v1/repositories", json={"full_name": "acme/petstore"})
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["full_name"] == "acme/petstore"

    listing = client.get("/api/v1/repositories").json()
    assert len(listing) == 1


def test_spec_validation(client) -> None:
    repo, _ = _bootstrap(client)
    bad_url = client.post(
        "/api/v1/specs",
        json={
            "repository_id": repo["id"],
            "name": "broken",
            "source_type": "url",
            "source": "not-a-url",
        },
    )
    assert bad_url.status_code == 422

    duplicate = client.post(
        "/api/v1/specs",
        json={"repository_id": repo["id"], "name": "petstore", "source": "x"},
    )
    assert duplicate.status_code == 409

    missing_repo = client.post(
        "/api/v1/specs",
        json={"repository_id": 9999, "name": "x", "source_type": "url", "source": "https://x"},
    )
    assert missing_repo.status_code == 404


def test_scan_flow_end_to_end(client, fake_spec) -> None:
    _, spec = _bootstrap(client)

    first = client.post(f"/api/v1/specs/{spec['id']}/scan")
    assert first.status_code == 202
    scan = first.json()
    assert scan["status"] == "completed"
    assert scan["total_count"] == 0  # baseline snapshot

    fake_spec["text"] = AFTER_TEXT
    second = client.post(f"/api/v1/specs/{spec['id']}/scan")
    assert second.status_code == 202
    scan = second.json()
    assert scan["status"] == "completed"
    assert scan["breaking_count"] > 0
    assert scan["base_version"] == "1.4.0"
    assert scan["head_version"] == "2.0.0"

    detail = client.get(f"/api/v1/scans/{scan['id']}").json()
    assert detail["changes"]
    assert any(change["kind"] == "operation.removed" for change in detail["changes"])
    assert any(change["fix_hint"] for change in detail["changes"])

    report = client.get(f"/api/v1/scans/{scan['id']}/report")
    assert report.status_code == 200
    assert "API Radar report" in report.text
    assert "Breaking changes" in report.text

    breaking = client.get(
        f"/api/v1/scans/{scan['id']}/changes", params={"severity": "breaking"}
    ).json()
    assert breaking and all(change["severity"] == "breaking" for change in breaking)

    history = client.get("/api/v1/scans", params={"spec_id": spec["id"]}).json()
    assert len(history) == 2
    assert history[0]["id"] == scan["id"]


def test_disabled_spec_cannot_be_scanned(client, fake_spec) -> None:
    _, spec = _bootstrap(client)
    assert client.patch(f"/api/v1/specs/{spec['id']}", json={"enabled": False}).status_code == 200
    response = client.post(f"/api/v1/specs/{spec['id']}/scan")
    assert response.status_code == 409


def test_diff_endpoint_returns_json_and_markdown(client) -> None:
    response = client.post(
        "/api/v1/diff",
        json={"before": BEFORE, "after": AFTER, "spec_name": "petstore", "repository": "acme/x"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["has_breaking"] is True
    assert payload["counts"]["breaking"] > 0
    assert payload["fingerprint"]
    assert "API Radar report — petstore" in payload["markdown"]
    assert any(change["kind"] == "operation.removed" for change in payload["changes"])


def test_diff_endpoint_rejects_invalid_spec(client) -> None:
    response = client.post("/api/v1/diff", json={"before": {"foo": "bar"}, "after": BEFORE})
    assert response.status_code == 422


def _signed(body: bytes, event: str) -> dict[str, str]:
    signature = "sha256=" + hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
    return {
        "x-github-event": event,
        "x-hub-signature-256": signature,
        "content-type": "application/json",
    }


def test_webhook_rejects_bad_signature(client) -> None:
    body = b'{"zen": "hi"}'
    response = client.post(
        "/webhooks/github",
        content=body,
        headers={"x-github-event": "ping", "x-hub-signature-256": "sha256=bad"},
    )
    assert response.status_code == 401


def test_webhook_ping(client) -> None:
    body = b'{"zen": "Keep it logically awesome."}'
    response = client.post("/webhooks/github", content=body, headers=_signed(body, "ping"))
    assert response.status_code == 202
    assert response.json()["status"] == "pong"


def test_webhook_push_triggers_scan(client, fake_spec) -> None:
    repo, spec = _bootstrap(client)
    fake_spec["text"] = AFTER_TEXT

    payload = {"ref": "refs/heads/main", "repository": {"full_name": repo["full_name"]}}
    body = json.dumps(payload).encode()
    response = client.post("/webhooks/github", content=body, headers=_signed(body, "push"))
    assert response.status_code == 202
    assert response.json()["queued"] == "1"

    scans = client.get("/api/v1/scans", params={"spec_id": spec["id"]}).json()
    assert len(scans) == 1
    assert scans[0]["trigger"] == "push"
    assert scans[0]["status"] == "completed"


def test_webhook_push_ignores_other_branches(client, fake_spec) -> None:
    repo, spec = _bootstrap(client)
    payload = {"ref": "refs/heads/feature", "repository": {"full_name": repo["full_name"]}}
    body = json.dumps(payload).encode()
    response = client.post("/webhooks/github", content=body, headers=_signed(body, "push"))
    assert response.status_code == 202
    assert response.json()["queued"] == "0"
    assert client.get("/api/v1/scans", params={"spec_id": spec["id"]}).json() == []