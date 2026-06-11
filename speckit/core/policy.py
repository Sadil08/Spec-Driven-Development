"""
Policy engine — guardrails on what an autonomous run may change.

Some changes are too consequential to merge without a human looking: database
schema migrations, dependency bumps, public API signature changes, infrastructure
and CI edits, and file deletions. evaluate_policy() inspects a set of code changes
and returns human-review reasons for any blocked category, so the pipeline can hold
the PR instead of opening it automatically.

This is heuristic, not a proof — it errs toward holding for review.
"""
from __future__ import annotations

import re

# ── filename / path signals ────────────────────────────────────────────────────

_DEP_MANIFESTS = {
    "requirements.txt", "requirements-dev.txt", "pyproject.toml", "poetry.lock",
    "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock",
    "go.mod", "go.sum", "cargo.toml", "cargo.lock", "gemfile", "gemfile.lock",
    "pom.xml", "build.gradle", "build.gradle.kts", "composer.json", "composer.lock",
}

_INFRA_NAME_HINTS = (
    "dockerfile", "docker-compose", ".tf", ".tfvars", "terraform",
    "kubernetes", "k8s", "helm", "chart.yaml", "values.yaml",
    "serverless.yml", "serverless.yaml", "procfile", "ansible",
)

_MIGRATION_PATH_HINTS = ("migration", "migrations", "alembic", "/ddl/")
_API_PATH_HINTS = ("route", "router", "routes", "api", "handler", "handlers",
                   "controller", "controllers", "endpoint", "endpoints")

# ── content signals ─────────────────────────────────────────────────────────────

_DDL_RE = re.compile(
    r"\b(CREATE|ALTER|DROP)\s+(TABLE|INDEX|SCHEMA|COLUMN|DATABASE)\b", re.IGNORECASE
)
# A public function/route signature. We compare which signatures exist before vs.
# after; a removed or changed signature in an API file is a potential break.
_SIGNATURE_RES = (
    re.compile(r"^\s*def\s+([a-zA-Z_]\w*)\s*\(([^)]*)\)", re.MULTILINE),        # python
    re.compile(r"^\s*func\s+([a-zA-Z_]\w*)\s*\(([^)]*)\)", re.MULTILINE),       # go
    re.compile(r"export\s+(?:async\s+)?function\s+([a-zA-Z_]\w*)\s*\(([^)]*)\)"),  # ts/js
)


def _name(path: str) -> str:
    return path.rsplit("/", 1)[-1].lower()


def _content_of(change) -> tuple[str, str, str]:
    """Return (file, action, content) from a CodeChange or dict."""
    if hasattr(change, "file"):
        return change.file or "", getattr(change, "action", "modify"), change.content or ""
    return change.get("file", ""), change.get("action", "modify"), change.get("content", "")


def _is_schema_change(path: str, content: str) -> bool:
    low = path.lower()
    if any(h in low for h in _MIGRATION_PATH_HINTS):
        return True
    if low.endswith(".sql") and _DDL_RE.search(content):
        return True
    if "schema.prisma" in low:
        return True
    # ORM model files containing DDL-ish migration ops
    if _DDL_RE.search(content):
        return True
    return False


def _is_dependency_change(path: str) -> bool:
    return _name(path) in _DEP_MANIFESTS


def _is_infra_change(path: str) -> bool:
    low = path.lower()
    if any(h in low for h in _INFRA_NAME_HINTS):
        return True
    if "/.github/workflows/" in f"/{low}" or low.startswith(".github/workflows/"):
        return True
    return False


def _signatures(content: str) -> set[str]:
    sigs: set[str] = set()
    for rx in _SIGNATURE_RES:
        for m in rx.finditer(content):
            name = m.group(1)
            # ignore private/dunder
            if name.startswith("_"):
                continue
            params = re.sub(r"\s+", "", m.group(2))
            sigs.add(f"{name}({params})")
    return sigs


def _is_api_signature_change(path: str, new_content: str, original: str) -> bool:
    """A public signature present originally is now missing or altered, in an API file."""
    low = path.lower()
    if not any(h in low for h in _API_PATH_HINTS):
        return False
    if not original:
        return False
    before = _signatures(original)
    after = _signatures(new_content)
    # names that existed before; did their signature survive unchanged?
    before_names = {s.split("(")[0] for s in before}
    after_names = {s.split("(")[0] for s in after}
    removed = before_names - after_names
    if removed:
        return True
    # same name, different parameter list => signature change
    changed = (before - after) & {s for s in before if s.split("(")[0] in after_names}
    return bool(changed)


def evaluate_policy(code_changes, config, read_original=None) -> list[str]:
    """
    Return a list of policy violations (human-review reasons) for these changes.

    read_original: optional callable(path) -> str returning the on-disk content of a
    file before the change, used for API-signature comparison. If omitted, signature
    analysis is skipped (path-based rules still apply).
    """
    policy = getattr(config, "policy", None)
    if policy is None:
        return []

    violations: list[str] = []
    for change in code_changes:
        path, action, content = _content_of(change)
        if not path:
            continue

        if policy.block_file_deletions and action == "delete":
            violations.append(f"deletes {path}")

        if policy.block_dependency_changes and _is_dependency_change(path):
            violations.append(f"changes dependency manifest {path}")

        if policy.block_schema_changes and _is_schema_change(path, content):
            violations.append(f"changes database schema ({path})")

        if policy.block_infra_changes and _is_infra_change(path):
            violations.append(f"changes infrastructure/CI ({path})")

        if policy.block_api_signature_changes and read_original is not None:
            try:
                original = read_original(path)
            except Exception:
                original = ""
            if _is_api_signature_change(path, content, original):
                violations.append(f"changes a public API signature in {path}")

    # de-duplicate while preserving order
    seen: set[str] = set()
    out: list[str] = []
    for v in violations:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out
