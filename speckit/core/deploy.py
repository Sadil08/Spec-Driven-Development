"""
Deploy → health-check → auto-revert loop.

Strictly opt-in (config.deploy.auto_deploy). Runs a configured deploy command,
polls a health-check URL until healthy or timeout, and — if the deployment does not
become healthy — runs a configured rollback command. The deploy and rollback
commands go through the same allowlist/no-shell safety policy as test commands.
"""
from __future__ import annotations

import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


@dataclass
class DeployResult:
    attempted: bool
    deployed: bool
    healthy: bool
    rolled_back: bool
    detail: str


def _http_ok(url: str, timeout: float = 5.0) -> bool:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        return False


def _poll_healthy(url: str, timeout_s: int, interval_s: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if _http_ok(url):
            return True
        time.sleep(interval_s)
    return _http_ok(url)


def run_deploy(config, project_root: Path) -> DeployResult:
    """
    Execute the configured deploy → health-check → auto-revert loop.

    Returns a DeployResult describing what happened. No-op (attempted=False) unless
    config.deploy.auto_deploy is true and a deploy_command is set.
    """
    from speckit.adapters.shell import ShellAdapter

    deploy_cfg = getattr(config, "deploy", None)
    if deploy_cfg is None or not deploy_cfg.auto_deploy or not deploy_cfg.deploy_command:
        return DeployResult(False, False, False, False, "auto-deploy disabled")

    shell = ShellAdapter(cwd=project_root)

    # ── deploy (operator-configured, trusted command) ────────────────────────
    ok, output = shell.run_trusted(deploy_cfg.deploy_command)
    if not ok:
        return DeployResult(True, False, False, False, f"deploy command failed:\n{output[:500]}")

    # ── health check ────────────────────────────────────────────────────────
    if not deploy_cfg.health_check_url:
        return DeployResult(True, True, True, False, "deployed (no health check configured)")

    healthy = _poll_healthy(deploy_cfg.health_check_url, deploy_cfg.health_check_timeout)
    if healthy:
        return DeployResult(True, True, True, False, "deployed and healthy")

    # ── auto-revert ─────────────────────────────────────────────────────────
    if not deploy_cfg.rollback_command:
        return DeployResult(
            True, True, False, False,
            "deployed but UNHEALTHY and no rollback_command configured — manual intervention needed",
        )
    rb_ok, rb_out = shell.run_trusted(deploy_cfg.rollback_command)
    if rb_ok:
        return DeployResult(True, True, False, True, "deploy unhealthy — rolled back successfully")
    return DeployResult(
        True, True, False, False,
        f"deploy unhealthy AND rollback failed — manual intervention needed:\n{rb_out[:500]}",
    )
