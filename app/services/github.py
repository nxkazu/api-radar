"""GitHub App / REST client.

Supports two authentication modes:

* ``GITHUB_TOKEN`` – a personal token (or fine-grained token) for local use.
* GitHub App – ``GITHUB_APP_ID`` + ``GITHUB_APP_PRIVATE_KEY``; installation
  tokens are minted per repository and cached in memory.

Repository contents are read through the Git data API (tree + blobs) so a scan
uses one tree request plus bounded parallel blob fetches instead of one request
per file.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import jwt

from ..config import Settings, get_settings
from ..core.fixgen import is_text_file

logger = logging.getLogger(__name__)


class GitHubError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class GitHubFile:
    path: str
    sha: str
    content: str


class GitHubClient:
    def __init__(
        self,
        *,
        token: str | None = None,
        api_url: str = "https://api.github.com",
        app_id: str | None = None,
        private_key: str | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._token = token
        self._api_url = api_url.rstrip("/")
        self._app_id = app_id
        self._private_key = private_key
        self._installation_tokens: dict[int, str] = {}
        self._http = httpx.Client(
            base_url=self._api_url,
            timeout=timeout,
            transport=transport,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "api-radar",
            },
        )

    # ------------------------------------------------------------- factories
    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> GitHubClient:
        settings = settings or get_settings()
        return cls(
            token=settings.github_token,
            api_url=settings.github_api_url,
            app_id=settings.github_app_id,
            private_key=settings.github_app_private_key,
        )

    # ------------------------------------------------------------------ auth
    def _app_jwt(self) -> str:
        if not self._app_id or not self._private_key:
            raise GitHubError("GitHub App credentials are not configured")
        key = self._private_key
        if "BEGIN" not in key:
            key = Path(key).read_text()
        now = int(time.time())
        return jwt.encode(
            {"iat": now - 60, "exp": now + 540, "iss": self._app_id},
            key,
            algorithm="RS256",
        )

    def _installation_token(self, installation_id: int) -> str:
        if installation_id in self._installation_tokens:
            return self._installation_tokens[installation_id]
        response = self._request(
            "POST",
            f"/app/installations/{installation_id}/access_tokens",
            token=self._app_jwt(),
        )
        token = response.get("token")
        if not token:
            raise GitHubError("GitHub did not return an installation token")
        self._installation_tokens[installation_id] = token
        return token

    def authenticate_for_repo(self, full_name: str, installation_id: int | None = None) -> None:
        """Resolve credentials for a repository (no-op when a token is set)."""
        if self._token:
            return
        if not self._app_id:
            return  # public repository, unauthenticated access

        if installation_id is None:
            installation_id = self._installation_id_for_repo(full_name)
        self._token = self._installation_token(installation_id)

    def _installation_id_for_repo(self, full_name: str) -> int:
        response = self._request(
            "GET", f"/repos/{full_name}/installation", token=self._app_jwt()
        )
        return int(response["id"])

    # --------------------------------------------------------------- request
    def _request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        **kwargs: Any,
    ) -> Any:
        headers = kwargs.pop("headers", {})
        auth = token if token is not None else self._token
        if auth:
            headers["Authorization"] = f"Bearer {auth}"

        response = self._http.request(method, path, headers=headers, **kwargs)
        if response.status_code >= 400:
            message = response.text
            try:
                data = response.json()
                message = data.get("message") or message
            except ValueError:
                pass
            raise GitHubError(
                f"GitHub {method} {path} failed ({response.status_code}): {message}",
                status_code=response.status_code,
            )
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    # ------------------------------------------------------------- repository
    def get_repository(self, full_name: str) -> dict[str, Any]:
        return self._request("GET", f"/repos/{full_name}")

    def default_branch(self, full_name: str) -> str:
        return str(self.get_repository(full_name).get("default_branch") or "main")

    def get_branch_sha(self, full_name: str, branch: str) -> str:
        data = self._request("GET", f"/repos/{full_name}/git/ref/heads/{branch}")
        return str(data["object"]["sha"])

    # ----------------------------------------------------------------- files
    def get_file(self, full_name: str, path: str, ref: str | None = None) -> GitHubFile | None:
        params = {"ref": ref} if ref else None
        try:
            data = self._request(
                "GET", f"/repos/{full_name}/contents/{path.lstrip('/')}", params=params
            )
        except GitHubError as exc:
            if exc.status_code == 404:
                return None
            raise
        if not isinstance(data, dict) or data.get("type") != "file":
            raise GitHubError(f"{path} is not a file in {full_name}")
        content = data.get("content") or ""
        if data.get("encoding") == "base64" and content:
            decoded = base64.b64decode(content)
        else:  # files over 1 MB come back without inline content
            blob = self._request("GET", f"/repos/{full_name}/git/blobs/{data['sha']}")
            decoded = base64.b64decode(blob["content"])
        try:
            text = decoded.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise GitHubError(f"{path} is not valid UTF-8") from exc
        return GitHubFile(path=data["path"], sha=data["sha"], content=text)

    def get_tree(self, full_name: str, ref: str) -> list[dict[str, Any]]:
        data = self._request(
            "GET", f"/repos/{full_name}/git/trees/{ref}", params={"recursive": "1"}
        )
        if data.get("truncated"):
            logger.warning("GitHub tree for %s@%s was truncated", full_name, ref)
        return [entry for entry in data.get("tree") or [] if entry.get("type") == "blob"]

    def fetch_text_files(
        self,
        full_name: str,
        ref: str,
        *,
        max_files: int = 4000,
        max_file_bytes: int = 200_000,
    ) -> dict[str, str]:
        """Fetch text blobs from a repository tree, bounded in count and size."""
        entries = [
            entry
            for entry in self.get_tree(full_name, ref)
            if is_text_file(str(entry.get("path", "")))
            and int(entry.get("size") or 0) <= max_file_bytes
            and int(entry.get("size") or 0) > 0
        ][:max_files]

        files: dict[str, str] = {}

        def fetch(entry: dict[str, Any]) -> None:
            try:
                blob = self._request(
                    "GET", f"/repos/{full_name}/git/blobs/{entry['sha']}"
                )
                raw = base64.b64decode(blob.get("content") or "")
                files[str(entry["path"])] = raw.decode("utf-8")
            except (GitHubError, UnicodeDecodeError) as exc:
                logger.debug("skipping %s: %s", entry.get("path"), exc)

        if entries:
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(fetch, entries))
        return files

    # --------------------------------------------------------------- commits
    def create_branch(self, full_name: str, branch: str, sha: str) -> None:
        try:
            self._request(
                "POST",
                f"/repos/{full_name}/git/refs",
                json={"ref": f"refs/heads/{branch}", "sha": sha},
            )
        except GitHubError as exc:
            if exc.status_code != 422:
                raise
            self._request(
                "PATCH",
                f"/repos/{full_name}/git/refs/heads/{branch}",
                json={"sha": sha, "force": True},
            )

    def commit_files(
        self,
        full_name: str,
        *,
        branch: str,
        base_branch: str,
        files: dict[str, str],
        message: str,
    ) -> str:
        if not files:
            raise GitHubError("no files to commit")
        base_sha = self.get_branch_sha(full_name, base_branch)
        base_commit = self._request("GET", f"/repos/{full_name}/git/commits/{base_sha}")

        tree_entries = []
        for path, content in files.items():
            blob = self._request(
                "POST",
                f"/repos/{full_name}/git/blobs",
                json={"content": content, "encoding": "utf-8"},
            )
            tree_entries.append(
                {"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]}
            )

        tree = self._request(
            "POST",
            f"/repos/{full_name}/git/trees",
            json={"base_tree": base_commit["tree"]["sha"], "tree": tree_entries},
        )
        commit = self._request(
            "POST",
            f"/repos/{full_name}/git/commits",
            json={"message": message, "tree": tree["sha"], "parents": [base_sha]},
        )
        self.create_branch(full_name, branch, commit["sha"])
        return str(commit["sha"])

    def open_pull_request(
        self,
        full_name: str,
        *,
        head: str,
        base: str,
        title: str,
        body: str,
        draft: bool = False,
    ) -> dict[str, Any]:
        try:
            return self._request(
                "POST",
                f"/repos/{full_name}/pulls",
                json={"title": title, "head": head, "base": base, "body": body, "draft": draft},
            )
        except GitHubError as exc:
            if exc.status_code != 422:
                raise
            owner = full_name.split("/", 1)[0]
            existing = self._request(
                "GET",
                f"/repos/{full_name}/pulls",
                params={"head": f"{owner}:{head}", "state": "open"},
            )
            if existing:
                return existing[0]
            raise

    def list_installation_repositories(self, installation_id: int) -> list[dict[str, Any]]:
        token = self._installation_token(installation_id)
        data = self._request(
            "GET", "/installation/repositories", token=token, params={"per_page": 100}
        )
        return list(data.get("repositories") or [])


def verify_webhook_signature(secret: str, body: bytes, signature: str | None) -> bool:
    """Validate ``X-Hub-Signature-256`` for a GitHub webhook payload."""
    if not signature:
        return False
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)