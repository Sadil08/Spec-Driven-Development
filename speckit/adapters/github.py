"""GitHub REST API v3 adapter — issues, branches, file contents, PRs."""
from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field
from typing import Optional

import httpx


@dataclass
class Issue:
    number: int
    title: str
    body: str
    labels: list[str] = field(default_factory=list)
    state: str = "open"


class GitHubAdapter:
    """
    Thin wrapper around GitHub REST API v3.

    Required env vars:
      GITHUB_TOKEN  — personal access token or Actions GITHUB_TOKEN
      GITHUB_REPO   — "org/repo-name"
    """

    _BASE = "https://api.github.com"
    _TIMEOUT = 20.0

    def __init__(self, repo: str = "", token: str = ""):
        self.repo = repo or os.environ.get("GITHUB_REPO", "")
        self.token = token or os.environ.get("GITHUB_TOKEN", "")
        if not self.token or self.token in ("paste-your-token-here", "ghp_..."):
            raise EnvironmentError(
                "GITHUB_TOKEN not set. Add a real token to .env:\n"
                "  1. Go to github.com/settings/tokens\n"
                "  2. Generate new token (classic) → check 'repo' scope\n"
                "  3. Paste the ghp_... token as GITHUB_TOKEN in .env"
            )
        if not self.repo:
            raise EnvironmentError(
                "GITHUB_REPO not set. Add it to .env (format: org/repo-name)."
            )

    def verify_token(self) -> None:
        """Verify the token is valid before starting a pipeline run."""
        try:
            self._get("/user")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                raise EnvironmentError(
                    "GITHUB_TOKEN is invalid or expired (got 401).\n\n"
                    "  Fix: go to github.com/settings/tokens → generate a new\n"
                    "  classic token with 'repo' scope → update GITHUB_TOKEN in .env"
                ) from None
            raise

    # ── HTTP helpers ──────────────────────────────────────────────────────────

    @property
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _get(self, path: str, params: dict | None = None) -> dict | list:
        resp = httpx.get(
            f"{self._BASE}{path}",
            headers=self._headers,
            params=params,
            timeout=self._TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, body: dict) -> dict:
        resp = httpx.post(
            f"{self._BASE}{path}",
            json=body,
            headers=self._headers,
            timeout=self._TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    # ── issues ────────────────────────────────────────────────────────────────

    def get_issue(self, number: int) -> Issue:
        data = self._get(f"/repos/{self.repo}/issues/{number}")
        return Issue(
            number=data["number"],
            title=data["title"],
            body=data.get("body") or "",
            labels=[lb["name"] for lb in data.get("labels", [])],
            state=data["state"],
        )

    def add_comment(self, number: int, body: str) -> None:
        self._post(f"/repos/{self.repo}/issues/{number}/comments", {"body": body})

    # ── branches ─────────────────────────────────────────────────────────────

    def _default_branch(self) -> str:
        data = self._get(f"/repos/{self.repo}")
        return data["default_branch"]

    def _default_branch_sha(self) -> str:
        branch = self._default_branch()
        ref = self._get(f"/repos/{self.repo}/git/ref/heads/{branch}")
        return ref["object"]["sha"]

    def create_branch(self, branch_name: str, from_sha: Optional[str] = None) -> str:
        """Create a new branch and return its name."""
        sha = from_sha or self._default_branch_sha()
        self._post(
            f"/repos/{self.repo}/git/refs",
            {"ref": f"refs/heads/{branch_name}", "sha": sha},
        )
        return branch_name

    def branch_exists(self, branch_name: str) -> bool:
        try:
            self._get(f"/repos/{self.repo}/git/ref/heads/{branch_name}")
            return True
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return False
            raise

    # ── file contents ─────────────────────────────────────────────────────────

    def get_file_contents(self, path: str, ref: str = "") -> str:
        """Return decoded file contents as UTF-8 string."""
        params = {"ref": ref} if ref else None
        data = self._get(f"/repos/{self.repo}/contents/{path}", params=params)
        if isinstance(data, list):
            raise ValueError(f"{path} is a directory, not a file")
        return base64.b64decode(data["content"]).decode("utf-8")

    def list_files(self, directory: str = "", ref: str = "") -> list[str]:
        """List all file paths recursively under a directory."""
        params = {"recursive": "1"}
        if ref:
            params["ref"] = ref
        data = self._get(f"/repos/{self.repo}/git/trees/HEAD", params=params)
        prefix = (directory.rstrip("/") + "/") if directory else ""
        return [
            item["path"]
            for item in data.get("tree", [])
            if item["type"] == "blob" and item["path"].startswith(prefix)
        ]

    # ── pull requests ─────────────────────────────────────────────────────────

    def create_pr(
        self,
        title: str,
        body: str,
        head: str,
        base: str = "",
    ) -> dict:
        """Create a pull request and return the full response dict."""
        base = base or self._default_branch()
        return self._post(
            f"/repos/{self.repo}/pulls",
            {"title": title, "head": head, "base": base, "body": body},
        )
