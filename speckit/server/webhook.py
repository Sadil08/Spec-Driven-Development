"""
speckit webhook server — GitHub webhook receiver.

Routes:
  POST /webhook/github   — receives issue events, triggers pipeline
  GET  /health           — liveness check

Environment variables (in addition to LLM backend vars):
  GITHUB_WEBHOOK_SECRET   — the secret set in GitHub webhook settings
  GITHUB_TOKEN            — personal access token with 'repo' scope
  GITHUB_REPO             — org/repo to operate on (overrides config)

Multi-project routing:
  Set SPECKIT_PROJECT_MAP as a JSON string mapping repo full names to
  absolute paths of their project roots:

    SPECKIT_PROJECT_MAP='{"acme/backend": "/srv/backend", "acme/mobile": "/srv/mobile"}'

  If SPECKIT_PROJECT_MAP is set, the server routes each webhook to the
  matching project root. If a repo is not in the map, it falls back to
  the default project_root passed to create_app().

Install extras: pip install 'speckit[server]'
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _verify_signature(payload_bytes: bytes, signature_header: str, secret: str) -> bool:
    """Validate GitHub's HMAC-SHA256 webhook signature."""
    if not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"), payload_bytes, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def _extract_labels(issue_data: dict) -> list[str]:
    return [lb["name"] for lb in issue_data.get("labels", [])]


def _csv_env(name: str) -> set[str]:
    """Parse a comma-separated env var into a lowercased set (empty = unset)."""
    raw = os.environ.get(name, "")
    return {x.strip().lower() for x in raw.split(",") if x.strip()}


def _is_authorized(repo_full_name: str, actor: str) -> tuple[bool, str]:
    """
    Authorize a webhook trigger against optional allowlists.

    SPECKIT_ALLOWED_REPOS  — comma-separated org/repo names permitted to trigger runs.
    SPECKIT_ALLOWED_ACTORS — comma-separated GitHub usernames permitted to trigger runs.

    An unset allowlist means "allow all" (backwards compatible). When set, the
    request must match or it is rejected — this stops an arbitrary outsider's issue
    from launching an autonomous code-writing run.
    """
    allowed_repos = _csv_env("SPECKIT_ALLOWED_REPOS")
    allowed_actors = _csv_env("SPECKIT_ALLOWED_ACTORS")

    if allowed_repos and repo_full_name.lower() not in allowed_repos:
        return False, f"repo {repo_full_name!r} not in SPECKIT_ALLOWED_REPOS"
    if allowed_actors and actor.lower() not in allowed_actors:
        return False, f"actor {actor!r} not in SPECKIT_ALLOWED_ACTORS"
    return True, ""


def _load_project_map() -> dict[str, Path]:
    """
    Load SPECKIT_PROJECT_MAP from env.
    Format: JSON object mapping "org/repo" → "/absolute/path"
    Returns empty dict if not set.
    """
    raw = os.environ.get("SPECKIT_PROJECT_MAP", "")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return {repo: Path(path) for repo, path in data.items()}
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning("SPECKIT_PROJECT_MAP is invalid JSON: %s", e)
        return {}


def _run_pipeline_in_background(
    pipeline_type: str,   # "bug_fix" | "feature"
    issue_number: int,
    project_root: Path,
    extra_kwargs: dict,
) -> None:
    """Spawn pipeline in a daemon thread so the webhook returns immediately."""
    def _worker():
        github = None
        config = None
        try:
            from dotenv import load_dotenv
            load_dotenv(project_root / ".env", override=False)
            from speckit.core.config import load_config
            config = load_config(project_root)

            from speckit.adapters.github import GitHubAdapter
            github = GitHubAdapter(
                repo=os.environ.get("GITHUB_REPO", "") or config.repo,
                token=os.environ.get("GITHUB_TOKEN", ""),
            )

            if pipeline_type == "bug_fix":
                from speckit.modes.bug_fix import BugFixPipeline
                pipeline = BugFixPipeline(config=config, project_root=project_root, github=github)
                pipeline.run(issue_number=issue_number)

            elif pipeline_type == "feature":
                from speckit.modes.feature import FeaturePipeline
                pipeline = FeaturePipeline(config=config, project_root=project_root)
                pipeline.run(
                    feature_name=extra_kwargs.get("feature_name", f"issue-{issue_number}"),
                    feature_description=extra_kwargs.get("description", ""),
                )

        except Exception as e:
            logger.exception("Pipeline failed for issue #%s", issue_number)
            # Surface the failure on the issue so it is never silently dropped.
            if github is not None:
                try:
                    github.add_comment(
                        issue_number,
                        f"❌ speckit {pipeline_type} pipeline failed for this issue:\n\n"
                        f"```\n{type(e).__name__}: {e}\n```\n\n"
                        "No PR was opened. Check the speckit server logs for the full trace.",
                    )
                except Exception:
                    logger.exception("Could not post failure comment to issue #%s", issue_number)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    logger.info("Started %s pipeline for issue #%s in thread %s", pipeline_type, issue_number, t.name)


def create_app(project_root: Path):
    """Create and return the FastAPI application."""
    try:
        from fastapi import FastAPI, HTTPException, Request, Response
    except ImportError:
        raise ImportError(
            "FastAPI not installed. Run: pip install 'speckit[server]'"
        ) from None

    app = FastAPI(title="speckit webhook server", version="0.1.0")

    webhook_secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
    project_map = _load_project_map()

    if project_map:
        logger.info("Multi-project mode: %d project(s) registered", len(project_map))
        for repo, path in project_map.items():
            logger.info("  %s → %s", repo, path)

    @app.get("/health")
    async def health() -> dict:
        return {
            "status": "ok",
            "default_project_root": str(project_root),
            "multi_project": {repo: str(path) for repo, path in project_map.items()} if project_map else None,
        }

    @app.post("/webhook/github")
    async def github_webhook(request: Request) -> Response:
        payload_bytes = await request.body()

        # ── Signature validation ──────────────────────────────────────────────
        if webhook_secret:
            sig = request.headers.get("X-Hub-Signature-256", "")
            if not sig:
                raise HTTPException(status_code=400, detail="Missing X-Hub-Signature-256 header")
            if not _verify_signature(payload_bytes, sig, webhook_secret):
                logger.warning("Webhook signature mismatch — rejecting request")
                raise HTTPException(status_code=403, detail="Invalid webhook signature")

        # ── Parse event ───────────────────────────────────────────────────────
        event_type = request.headers.get("X-GitHub-Event", "")
        if event_type != "issues":
            return Response(content=json.dumps({"ignored": True, "event": event_type}),
                            media_type="application/json", status_code=200)

        try:
            payload: dict[str, Any] = json.loads(payload_bytes)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON payload")

        action = payload.get("action", "")
        if action not in ("opened", "labeled", "reopened"):
            return Response(content=json.dumps({"ignored": True, "action": action}),
                            media_type="application/json", status_code=200)

        issue_data = payload.get("issue", {})
        issue_number: int = issue_data.get("number", 0)
        issue_title: str = issue_data.get("title", "")
        issue_body: str = issue_data.get("body") or ""
        labels = _extract_labels(issue_data)

        if not issue_number:
            raise HTTPException(status_code=400, detail="Missing issue number in payload")

        # ── Resolve project root (multi-project support) ───────────────────────
        repo_full_name: str = payload.get("repository", {}).get("full_name", "")

        # ── Authorization: only allowlisted repos/actors may trigger runs ──────
        actor = payload.get("sender", {}).get("login", "")
        authorized, why = _is_authorized(repo_full_name, actor)
        if not authorized:
            logger.warning("Rejecting unauthorized webhook: %s", why)
            return Response(
                content=json.dumps({"ignored": True, "reason": f"unauthorized: {why}"}),
                media_type="application/json",
                status_code=403,
            )

        resolved_root = project_map.get(repo_full_name, project_root)
        if repo_full_name and repo_full_name not in project_map and project_map:
            logger.warning(
                "Repo %s not in SPECKIT_PROJECT_MAP — using default project root", repo_full_name
            )

        # ── Route to pipeline ─────────────────────────────────────────────────
        from dotenv import load_dotenv
        load_dotenv(resolved_root / ".env", override=False)
        from speckit.core.config import load_config
        config = load_config(resolved_root)

        bug_labels = set(config.github.bug_labels)
        feature_labels = set(config.github.feature_labels)

        has_bug_label = bool(set(labels) & bug_labels)
        has_feature_label = bool(set(labels) & feature_labels)

        if has_bug_label:
            logger.info("Routing issue #%s to bug-fix pipeline (labels: %s)", issue_number, labels)
            _run_pipeline_in_background("bug_fix", issue_number, resolved_root, {})
            return Response(
                content=json.dumps({
                    "accepted": True, "pipeline": "bug_fix",
                    "issue": issue_number, "project": str(resolved_root),
                }),
                media_type="application/json",
                status_code=202,
            )

        elif has_feature_label:
            logger.info("Routing issue #%s to feature pipeline (labels: %s)", issue_number, labels)
            _run_pipeline_in_background("feature", issue_number, resolved_root, {
                "feature_name": issue_title or f"issue-{issue_number}",
                "description": issue_body,
            })
            return Response(
                content=json.dumps({
                    "accepted": True, "pipeline": "feature",
                    "issue": issue_number, "project": str(resolved_root),
                }),
                media_type="application/json",
                status_code=202,
            )

        else:
            logger.info(
                "Issue #%s has no recognized labels (%s) — ignoring", issue_number, labels
            )
            return Response(
                content=json.dumps({
                    "ignored": True,
                    "reason": f"No matching labels. Bug labels: {sorted(bug_labels)}, feature labels: {sorted(feature_labels)}",
                }),
                media_type="application/json",
                status_code=200,
            )

    return app
