"""Judge loop — repeatedly score and refine a spec until threshold is met."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from speckit.core.config import SpeckitConfig


@dataclass
class JudgeResult:
    final_spec: str
    final_score: float
    iterations: int
    approved: bool


def run_judge_loop(
    draft: str,
    architecture_spec: str,
    security_spec: str,
    config: "SpeckitConfig",
    project_root: Path,
    on_iteration: Optional[Callable[[int, float, list[str]], None]] = None,
) -> JudgeResult:
    """
    Iteratively judge and refine a spec until it reaches config.agent.judge_threshold
    or config.agent.max_judge_iterations is exhausted.

    on_iteration(iteration_number, score, gaps) is called after each judge pass
    so the caller can log or display progress.
    """
    from speckit.core.agents import judge_bug_report, refine_bug_report

    spec = draft
    score_obj = None

    for i in range(1, config.agent.max_judge_iterations + 1):
        score_obj = judge_bug_report(
            spec, architecture_spec, security_spec, config, project_root
        )

        if on_iteration:
            on_iteration(i, score_obj.score, score_obj.gaps)

        if score_obj.approved:
            return JudgeResult(
                final_spec=spec,
                final_score=score_obj.score,
                iterations=i,
                approved=True,
            )

        spec = refine_bug_report(spec, score_obj, config, project_root)

    # Max iterations reached — return best effort
    return JudgeResult(
        final_spec=spec,
        final_score=score_obj.score if score_obj else 0.0,
        iterations=config.agent.max_judge_iterations,
        approved=False,
    )
