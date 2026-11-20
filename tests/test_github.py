"""GitHub client behaviour against a mocked HTTP transport."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

import httpx
import pytest

from app.services.github import GitHubClient, GitHubError, verify_webhook_signature


def make_client(handler) -> GitHubClient:
    return GitHubClient(
        token="test-token",
        api_url="https://api.github.test",
        transport=httpx.MockTransport(handler),
    )


def test_get_file_decodes_base64_and_sends_auth_header() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["path"] = request.url.path
        return httpx.Response(
            200,
            json={
                "type": "file",
                "path": "specs/openapi.yaml",
                "sha": "abc123",
                "encoding": "base64",
                "content": base64.b64encode(b"openapi: 3.0.3\n").decode(),
            },
        )

    file = make_client(handler).get_file("acme/app", "specs/openapi.yaml", "main")
    assert seen["auth"] == "Bearer test-token"
    assert seen["path"] == "/repos/acme/app/contents/specs/openapi.yaml"
    assert file is not None
    assert file.content.startswith("openapi")


def test_get_file_returns_none_on_404() -> None:
    client = make_client(lambda request: httpx.Response(404, json={"message": "Not Found"}))
    assert client.get_file("acme/app", "missing.yaml") is None


def test_fetch_text_files_filters_binary_large_and_vendored() -> None:
    entries = [
        {"path": "src/app.py", "type": "blob", "sha": "s1", "size": 10},
        {"path": "node_modules/x/index.js", "type": "blob", "sha": "s2", "size": 10},
        {"path": "logo.png", "type": "blob", "sha": "s3", "size": 10},
        {"path": "big.py", "type": "blob", "sha": "s4", "size": 10_000_000},
    ]
    fetched = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/git/trees/main"):
            return httpx.Response(200, json={"tree": entries, "truncated": False})
        if "/git/blobs/" in request.url.path:
            fetched.append(request.url.path.rsplit("/", 1)[-1])
            return httpx.Response(
                200, json={"content": base64.b64encode(b"print('hi')").decode()}
            )
        return httpx.Response(404, json={"message": "not found"})

    files = make_client(handler).fetch_text_files("acme/app", "main")
    assert files == {"src/app.py": "print('hi')"}
    assert fetched == ["s1"]


def test_commit_files_writes_blobs_tree_commit_and_ref() -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        path = request.url.path
        if path.endswith("/git/ref/heads/main"):
            return httpx.Response(200, json={"object": {"sha": "base-sha"}})
        if path.endswith("/git/commits/base-sha"):
            return httpx.Response(200, json={"tree": {"sha": "base-tree"}})
        if path.endswith("/git/blobs"):
            return httpx.Response(201, json={"sha": "new-blob"})
        if path.endswith("/git/trees"):
            body = json.loads(request.content)
            assert body["base_tree"] == "base-tree"
            assert body["tree"][0]["path"] == "a.py"
            return httpx.Response(201, json={"sha": "new-tree"})
        if path.endswith("/git/commits"):
            body = json.loads(request.content)
            assert body["tree"] == "new-tree"
            assert body["parents"] == ["base-sha"]
            return httpx.Response(201, json={"sha": "new-commit"})
        if path.endswith("/git/refs"):
            body = json.loads(request.content)
            assert body == {"ref": "refs/heads/apiradar/fix", "sha": "new-commit"}
            return httpx.Response(201, json={"ref": "refs/heads/apiradar/fix"})
        return httpx.Response(404, json={"message": "unexpected"})

    sha = make_client(handler).commit_files(
        "acme/app",
        branch="apiradar/fix",
        base_branch="main",
        files={"a.py": "print(1)"},
        message="fix",
    )
    assert sha == "new-commit"
    assert calls[0] == ("GET", "/repos/acme/app/git/ref/heads/main")
    assert ("POST", "/repos/acme/app/git/refs") in calls


def test_create_branch_updates_existing_ref_on_422() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        if request.method == "POST" and request.url.path.endswith("/git/refs"):
            return httpx.Response(422, json={"message": "Reference already exists"})
        if request.method == "PATCH" and "/git/refs/heads/" in request.url.path:
            return httpx.Response(200, json={"ref": "refs/heads/apiradar/fix"})
        return httpx.Response(404, json={"message": "unexpected"})

    make_client(handler).create_branch("acme/app", "apiradar/fix", "abc")
    assert calls == ["POST", "PATCH"]


def test_open_pull_request_returns_existing_pr_on_422() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(422, json={"message": "A pull request already exists"})
        if request.url.path.endswith("/pulls"):
            return httpx.Response(
                200, json=[{"html_url": "https://github.com/acme/app/pull/3"}]
            )
        return httpx.Response(404, json={"message": "unexpected"})

    pull = make_client(handler).open_pull_request(
        "acme/app", head="apiradar/fix", base="main", title="t", body="b"
    )
    assert pull["html_url"].endswith("/pull/3")


def test_github_error_carries_status_and_message() -> None:
    client = make_client(lambda request: httpx.Response(403, json={"message": "rate limited"}))
    with pytest.raises(GitHubError) as exc:
        client.get_repository("acme/app")
    assert exc.value.status_code == 403
    assert "rate limited" in str(exc.value)


def test_verify_webhook_signature() -> None:
    body = b'{"action": "opened"}'
    good = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    assert verify_webhook_signature("secret", body, good)
    assert not verify_webhook_signature("secret", body, "sha256=deadbeef")
    assert not verify_webhook_signature("secret", body, None)