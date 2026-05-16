"""Slack notification adapter — fire-and-forget webhook posts."""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from speckit.core.config import SpeckitConfig

logger = logging.getLogger(__name__)


class SlackNotifier:
    """
    Send notifications to Slack via incoming webhook.

    Configure in sdd.config.yml:
      notifications:
        slack_webhook_url: https://hooks.slack.com/services/...
        slack_channel: "#engineering"
        notify_on: [pr_opened, judge_failed, tests_failed, pipeline_complete]

    Or via env var: SLACK_WEBHOOK_URL (overrides config).
    """

    EVENTS = frozenset([
        "pr_opened",
        "judge_failed",
        "tests_failed",
        "pipeline_complete",
        "feature_approved",
        "feature_blocked",
    ])

    def __init__(self, config: "SpeckitConfig"):
        self._url = (
            os.environ.get("SLACK_WEBHOOK_URL", "")
            or config.notifications.slack_webhook_url
        )
        self._channel = config.notifications.slack_channel or "#engineering"
        self._notify_on = set(config.notifications.notify_on)
        self._project = config.project_name

    @property
    def enabled(self) -> bool:
        return bool(self._url)

    def _should_send(self, event: str) -> bool:
        return self.enabled and (event in self._notify_on or "all" in self._notify_on)

    def _send(self, text: str, blocks: list | None = None) -> None:
        if not self._url:
            return
        try:
            import httpx
            payload: dict = {"text": text, "channel": self._channel}
            if blocks:
                payload["blocks"] = blocks
            httpx.post(self._url, json=payload, timeout=10.0)
        except Exception as e:
            logger.warning("Slack notification failed: %s", e)

    # ── public notification methods ───────────────────────────────────────────

    def pr_opened(self, issue_number: int, title: str, pr_url: str, score: float) -> None:
        if not self._should_send("pr_opened"):
            return
        self._send(
            f"[{self._project}] PR opened for issue #{issue_number}",
            blocks=[{
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*[{self._project}]* PR opened for issue #{issue_number}: _{title}_\n"
                        f"Judge score: `{score:.2f}` ✅\n"
                        f"<{pr_url}|View pull request>"
                    ),
                },
            }],
        )

    def judge_failed(self, run_type: str, name: str, score: float, threshold: float, gaps: list[str]) -> None:
        if not self._should_send("judge_failed"):
            return
        gaps_text = "\n".join(f"• {g}" for g in gaps[:5])
        self._send(
            f"[{self._project}] Judge threshold not met for {run_type}: {name}",
            blocks=[{
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*[{self._project}]* ⚠️ Judge threshold not met\n"
                        f"*{run_type}:* {name}\n"
                        f"Score: `{score:.2f}` < `{threshold:.2f}`\n"
                        f"*Top gaps:*\n{gaps_text}\n"
                        f"_Human review required._"
                    ),
                },
            }],
        )

    def tests_failed(self, issue_number: int, title: str, attempts: int, output: str) -> None:
        if not self._should_send("tests_failed"):
            return
        preview = output[-300:] if len(output) > 300 else output
        self._send(
            f"[{self._project}] Tests failed for issue #{issue_number} after {attempts} attempt(s)",
            blocks=[{
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*[{self._project}]* ❌ Tests failed for #{issue_number}: _{title}_\n"
                        f"After {attempts} attempt(s).\n"
                        f"```{preview}```"
                    ),
                },
            }],
        )

    def pipeline_complete(self, run_type: str, name: str, run_dir: str, approved: bool) -> None:
        if not self._should_send("pipeline_complete"):
            return
        icon = "✅" if approved else "⚠️"
        status = "approved" if approved else "needs review"
        self._send(
            f"[{self._project}] {run_type} pipeline complete: {name} — {status}",
            blocks=[{
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*[{self._project}]* {icon} *{run_type}* pipeline complete\n"
                        f"*{name}* — {status}\n"
                        f"Artifacts: `{run_dir}`"
                    ),
                },
            }],
        )

    def feature_blocked(self, feature_name: str, verdict: str, score: float) -> None:
        if not self._should_send("feature_blocked"):
            return
        self._send(
            f"[{self._project}] Feature blocked: {feature_name}",
            blocks=[{
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*[{self._project}]* 🚫 Feature blocked by architecture review\n"
                        f"*{feature_name}* — verdict: `{verdict}` (score `{score:.2f}`)\n"
                        f"_Review 02_compatibility.md before proceeding._"
                    ),
                },
            }],
        )
