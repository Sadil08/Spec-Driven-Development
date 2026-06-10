"""
Mode C — Feature pipeline.

Stages:
  1.  Research implementation patterns + packages → 01_research.md
  2.  Compatibility check against architecture   → 02_compatibility.md
  3.  Draft feature spec                          → 03_feature_spec.md (draft)
  4.  Judge loop → refine until approved or max iterations
  5.  Write final 03_feature_spec.md (approved / human-review-needed)
  6.  Write test plan                             → 04_test_plan.md
  7.  Write build plan                            → 05_build_plan.md
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from speckit.core.config import SpeckitConfig


@dataclass
class FeatureResult:
    feature_name: str
    run_dir: Path
    approved: bool
    judge_score: float
    judge_iterations: int
    compatibility_score: float = 0.0
    compatibility_verdict: str = "unknown"
    artifacts: list[str] = field(default_factory=list)
    error: str = ""


class FeaturePipeline:
    """Orchestrates the complete feature-spec pipeline for a new feature request."""

    def __init__(
        self,
        config: "SpeckitConfig",
        project_root: Path,
        on_step: Optional[Callable[[str, str], None]] = None,
    ):
        self.config = config
        self.project_root = project_root
        self.on_step = on_step or (lambda title, detail: None)
        self._run_dir: Optional[Path] = None
        self._log_lines: list[str] = []

    # ── helpers ───────────────────────────────────────────────────────────────

    def _step(self, title: str, detail: str = "") -> None:
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        self._log_lines.append(f"[{ts}] {title}{': ' + detail if detail else ''}")
        self._flush_log()
        self.on_step(title, detail)

    def _flush_log(self) -> None:
        if self._run_dir:
            (self._run_dir / "00_run_log.md").write_text(
                "# Run log\n\n" + "\n".join(self._log_lines) + "\n",
                encoding="utf-8",
            )

    def _write(self, filename: str, content: str) -> Path:
        path = self._run_dir / filename
        path.write_text(content, encoding="utf-8")
        return path

    def _read_spec_file(self, name: str) -> str:
        p = self.project_root / self.config.paths.specs.lstrip("./") / name
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def _slug(self, name: str) -> str:
        import re
        return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40]

    def _load_artifact(self, filename: str) -> str | None:
        """Return artifact content if it exists in the run dir, else None."""
        if self._run_dir is None:
            return None
        path = self._run_dir / filename
        return path.read_text(encoding="utf-8") if path.exists() else None

    def _artifact_approved(self, filename: str) -> bool:
        """Return True if the artifact exists and contains judge-approved status."""
        content = self._load_artifact(filename)
        return content is not None and "judge-approved" in content

    def _collect_module_specs_summary(self, query: str = "") -> str:
        """Return a brief summary of relevant module specs via BM25, or all if no query."""
        from speckit.adapters.vector_db import search_specs
        from speckit.core.spec_parser import discover_spec_files

        if query:
            results = search_specs(
                query, self.config, self.project_root,
                top_k=self.config.agent.max_spec_read_files,
            )
            if results:
                return "\n\n".join(
                    f"### {r['module']}\n{r.get('summary', r.get('content', ''))[:200]}"
                    for r in results
                )

        # Fallback: load all module specs from disk
        specs_root = self.project_root / self.config.paths.specs.lstrip("./") / "modules"
        if not specs_root.exists():
            return ""
        parts: list[str] = []
        for p in sorted(specs_root.glob("*.md")):
            text = p.read_text(encoding="utf-8")
            parts.append(f"### {p.stem}\n{text[:200]}")
        return "\n\n".join(parts)

    @staticmethod
    def _compatibility_to_md(c) -> str:
        lines = [
            f"# Compatibility assessment\n",
            f"**Verdict:** {c.verdict}  **Score:** {c.score:.2f}\n",
        ]
        if c.conflicts:
            lines.append("## Conflicts\n" + "\n".join(f"- {x}" for x in c.conflicts))
        if c.recommendations:
            lines.append("## Recommendations\n" + "\n".join(f"- {x}" for x in c.recommendations))
        if c.breaking_changes:
            lines.append("## Breaking changes\n" + "\n".join(f"- {x}" for x in c.breaking_changes))
        return "\n\n".join(lines)

    def _sync_feature_spec(self, feature_name: str, feature_spec_md: str) -> None:
        """Write approved feature spec into specs/features/ for future agent reference."""
        features_dir = self.project_root / self.config.paths.specs.lstrip("./") / "features"
        features_dir.mkdir(parents=True, exist_ok=True)
        slug = self._slug(feature_name)
        target = features_dir / f"{slug}.md"
        target.write_text(feature_spec_md, encoding="utf-8")
        self._step("Feature spec saved", f"specs/features/{slug}.md")

    # ── main entry point ──────────────────────────────────────────────────────

    def run(self, feature_name: str, feature_description: str) -> FeatureResult:
        from speckit.core.agents import (
            research_feature,
            check_compatibility,
            write_feature_spec,
            judge_feature_spec,
            refine_feature_spec,
            write_test_plan,
            write_build_plan,
            reset_cost_log,
            get_cost_summary_md,
            set_pipeline_cache,
            clear_pipeline_cache,
        )
        from speckit.adapters.slack import SlackNotifier
        reset_cost_log()
        slack = SlackNotifier(self.config)

        slug = self._slug(feature_name)
        runs_path = self.config.paths.runs.lstrip("./")
        run_dir = self.project_root / runs_path / f"feature-{slug}"
        run_dir.mkdir(parents=True, exist_ok=True)
        self._run_dir = run_dir

        self._step("Starting feature pipeline", feature_name)

        result = FeatureResult(
            feature_name=feature_name,
            run_dir=run_dir,
            approved=False,
            judge_score=0.0,
            judge_iterations=0,
        )

        try:
            # ── 1. Load context specs ─────────────────────────────────────────
            arch_spec = self._read_spec_file("architecture.md")
            sec_spec = self._read_spec_file("security.md")
            learnings = self._read_spec_file("global_learnings.md")
            module_summary = self._collect_module_specs_summary(
                query=f"{feature_name} {feature_description}"
            )
            set_pipeline_cache(arch_spec, sec_spec, learnings)

            # ── 2. Research (skip if artifact exists) ─────────────────────────
            existing_research = self._load_artifact("01_research.md")
            if existing_research:
                research_md = existing_research
                self._step("Reusing existing research", "01_research.md")
            else:
                self._step("Researching implementation patterns")
                research_md = research_feature(
                    feature_name=feature_name,
                    feature_description=feature_description,
                    architecture_spec=arch_spec,
                    language=self.config.primary_language,
                    config=self.config,
                    project_root=self.project_root,
                )
                self._write("01_research.md", research_md)
                self._step("Research complete")
            result.artifacts.append("01_research.md")

            # ── 3. Compatibility check (skip if artifact exists + not blocked) ─
            existing_compat_md = self._load_artifact("02_compatibility.md")
            if existing_compat_md and "blocked" not in existing_compat_md:
                import re as _re
                _m = _re.search(r"\*\*Score:\*\*\s*([\d.]+)", existing_compat_md)
                _v = _re.search(r"\*\*Verdict:\*\*\s*(\w[\w-]*)", existing_compat_md)
                compat_score = float(_m.group(1)) if _m else 0.8
                compat_verdict = _v.group(1) if _v else "approved"
                result.compatibility_score = compat_score
                result.compatibility_verdict = compat_verdict
                compat_md = existing_compat_md
                self._step("Reusing existing compatibility check", "02_compatibility.md")
            else:
                self._step("Checking architecture compatibility")
                compat = check_compatibility(
                    feature_name=feature_name,
                    feature_description=feature_description,
                    architecture_spec=arch_spec,
                    module_specs_summary=module_summary,
                    config=self.config,
                    project_root=self.project_root,
                )
                result.compatibility_score = compat.score
                result.compatibility_verdict = compat.verdict
                compat_md = self._compatibility_to_md(compat)
                self._write("02_compatibility.md", compat_md)
                self._step(
                    "Compatibility check complete",
                    f"verdict={compat.verdict} | score={compat.score:.2f}",
                )
                if compat.verdict == "blocked":
                    self._step(
                        "Feature blocked by architecture review",
                        "address conflicts in 02_compatibility.md before proceeding",
                    )
                    slack.feature_blocked(feature_name, compat.verdict, compat.score)
                    return result
            result.artifacts.append("02_compatibility.md")

            # ── 4. Draft + judge loop (skip if already approved) ──────────────
            if self._artifact_approved("03_feature_spec.md"):
                spec = self._load_artifact("03_feature_spec.md") or ""
                import re as _re
                _s = _re.search(r"score:\s*([\d.]+)", spec)
                result.judge_score = float(_s.group(1)) if _s else self.config.agent.judge_threshold
                result.judge_iterations = 0
                result.approved = True
                self._step("Reusing approved spec", "03_feature_spec.md")
                result.artifacts.append("03_feature_spec.md")
            else:
                # ── 4a. Draft feature spec ────────────────────────────────────
                self._step("Drafting feature spec")
                draft = write_feature_spec(
                    feature_name=feature_name,
                    feature_description=feature_description,
                    research_md=research_md,
                    compatibility_md=compat_md,
                    architecture_spec=arch_spec,
                    security_spec=sec_spec,
                    config=self.config,
                    project_root=self.project_root,
                )
                self._write(
                    "03_feature_spec.md",
                    draft + "\n\n---\n*status: draft — pending judge*",
                )
                result.artifacts.append("03_feature_spec.md")

                # ── 4b. Judge loop ────────────────────────────────────────────
                self._step("Starting judge loop", f"threshold={self.config.agent.judge_threshold}")

                spec = draft
                score_obj = None

                for i in range(1, self.config.agent.max_judge_iterations + 1):
                    score_obj = judge_feature_spec(
                        spec, arch_spec, sec_spec, self.config, self.project_root
                    )
                    if score_obj is None:
                        raise RuntimeError("judge_feature_spec returned None on iteration %d" % i)
                    self._step(
                        f"Judge iteration {i}",
                        f"score={score_obj.score:.2f} | {len(score_obj.gaps)} gap(s)",
                    )
                    if score_obj.approved:
                        break
                    spec = refine_feature_spec(spec, score_obj, self.config, self.project_root)

                result.judge_score = score_obj.score if score_obj else 0.0
                result.judge_iterations = min(i, self.config.agent.max_judge_iterations)
                result.approved = score_obj.approved if score_obj else False

                status_tag = "judge-approved" if result.approved else "needs-human-review"
                self._write(
                    "03_feature_spec.md",
                    spec
                    + f"\n\n---\n*status: {status_tag} | "
                    f"score: {result.judge_score:.2f} | "
                    f"iterations: {result.judge_iterations}*",
                )

                if result.approved:
                    self._step(
                        "Feature spec approved",
                        f"score={result.judge_score:.2f} in {result.judge_iterations} iteration(s)",
                    )
                else:
                    self._step(
                        "Judge threshold not met — human review required",
                        f"score={result.judge_score:.2f} < {self.config.agent.judge_threshold}",
                    )
                    slack.judge_failed(
                        run_type="feature",
                        name=feature_name,
                        score=result.judge_score,
                        threshold=self.config.agent.judge_threshold,
                        gaps=[],
                    )

            # ── 6. Test plan ──────────────────────────────────────────────────
            self._step("Writing test plan")
            test_plan = write_test_plan(spec, self.config, self.project_root)
            self._write("04_test_plan.md", test_plan)
            result.artifacts.append("04_test_plan.md")

            # ── 7. Build plan ─────────────────────────────────────────────────
            self._step("Writing build plan")
            build_plan = write_build_plan(spec, arch_spec, self.config, self.project_root)
            self._write("05_build_plan.md", build_plan)
            result.artifacts.append("05_build_plan.md")

            # ── 8. Spec sync (create new module spec for the feature) ─────────
            if result.approved:
                self._step("Syncing spec files")
                self._sync_feature_spec(feature_name, spec)

            cost_md = get_cost_summary_md()
            if cost_md:
                self._log_lines.append(cost_md)
                self._flush_log()
            self._step("Pipeline complete")

        except Exception as e:
            self._step("PIPELINE FAILED", str(e))
            result.error = str(e)
            self._write(
                "FAILED.md",
                "# Pipeline failed\n\n"
                f"**Error:** {e}\n\n"
                "**Run log:**\n\n"
                + "\n".join(self._log_lines),
            )
            raise
        finally:
            clear_pipeline_cache()

        return result
