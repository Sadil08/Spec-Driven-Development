"""
Per-run safety limits: cost budget and blast-radius checks.

These make an autonomous run's cost and scope bounded and predictable. The cost
budget is enforced at stage boundaries (before the next expensive agent call) so
a refinement/retry loop cannot run away. The blast-radius check holds oversized
changes for human review instead of auto-opening a PR.
"""
from __future__ import annotations


class BudgetExceeded(RuntimeError):
    """Raised when a run exceeds its configured token or cost ceiling."""


def check_cost_budget(config) -> None:
    """
    Raise BudgetExceeded if this run has passed its token or USD ceiling.

    Call at stage boundaries. No-op when both ceilings are disabled (0).
    """
    from speckit.core.agents import get_total_tokens, get_total_cost_usd

    max_tokens = getattr(config.agent, "max_run_tokens", 0)
    max_cost = getattr(config.agent, "max_run_cost_usd", 0.0)

    if max_tokens:
        spent = get_total_tokens()
        if spent > max_tokens:
            raise BudgetExceeded(
                f"Run exceeded token budget: {spent:,} > {max_tokens:,} tokens. "
                "Aborting before further LLM calls. Raise agent.max_run_tokens to allow more."
            )
    if max_cost:
        spent_usd = get_total_cost_usd()
        if spent_usd > max_cost:
            raise BudgetExceeded(
                f"Run exceeded cost budget: ${spent_usd:.2f} > ${max_cost:.2f}. "
                "Aborting before further LLM calls. Raise agent.max_run_cost_usd to allow more."
            )


def check_blast_radius(code_changes, config) -> list[str]:
    """
    Return a list of blast-radius violations for a set of code changes.

    Empty list = within budget. A non-empty list means the change is too large to
    apply autonomously and should be held for human review.
    """
    max_files = getattr(config.agent, "max_changed_files", 0)
    max_lines = getattr(config.agent, "max_changed_lines", 0)

    violations: list[str] = []
    n_files = len(code_changes)
    n_lines = 0
    for c in code_changes:
        content = c.content if hasattr(c, "content") else c.get("content", "")
        n_lines += content.count("\n") + 1

    if max_files and n_files > max_files:
        violations.append(f"touches {n_files} files (limit {max_files})")
    if max_lines and n_lines > max_lines:
        violations.append(f"writes ~{n_lines} lines (limit {max_lines})")
    return violations
