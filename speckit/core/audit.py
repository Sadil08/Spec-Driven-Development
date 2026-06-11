"""
Structured audit trail — one JSON line per decision.

Markdown run logs are for humans; this is the machine-readable record an enterprise
needs: every gate result, agent decision, cost checkpoint, and PR action, with a
timestamp, written to runs/<run>/audit.jsonl. Append-only, never overwritten.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


class AuditLog:
    """Append-only structured event log for a single pipeline run."""

    def __init__(self, run_dir: Optional[Path], run_type: str, subject: str):
        self.path: Optional[Path] = (run_dir / "audit.jsonl") if run_dir else None
        self.run_type = run_type
        self.subject = subject

    def record(self, event: str, **fields: Any) -> None:
        """Append one structured event. Never raises — auditing must not break a run."""
        if self.path is None:
            return
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "run_type": self.run_type,
            "subject": self.subject,
            "event": event,
            **fields,
        }
        try:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except OSError:
            pass

    def gate(self, name: str, passed: bool, **fields: Any) -> None:
        """Record a safety-gate decision."""
        self.record("gate", gate=name, passed=passed, **fields)

    def decision(self, name: str, outcome: str, **fields: Any) -> None:
        """Record a pipeline decision (e.g. PR opened / held / rolled back)."""
        self.record("decision", decision=name, outcome=outcome, **fields)
