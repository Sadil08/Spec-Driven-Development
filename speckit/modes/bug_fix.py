"""
Mode B — Bug fix pipeline.

Stages:
  1.  Fetch issue from GitHub (or accept a pre-built Issue object)
  2.  Classify → 01_classification.md
  3.  Search specs (BM25 or Supabase) for relevant files
  4.  Read source files referenced in spec frontmatter
  5.  Draft bug report → 02_bug_report.md (draft)
  6.  Judge loop → refine until approved or max iterations
  7.  Write final 02_bug_report.md (approved / human-review-needed)
  8.  Write test plan → 03_test_plan.md
  9.  Write code fix → 04_code_changes.md  (skipped if judge not approved)
  10. Run tests → 05_test_results.md
  11. Retry code fix up to 2× if tests fail
  12. Commit + push branch + open PR  (skipped if --no-github)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from speckit.adapters.github import GitHubAdapter, Issue
    from speckit.core.config import SpeckitConfig


@dataclass
class RunResult:
    issue_number: int
    run_dir: Path
    approved: bool
    judge_score: float
    judge_iterations: int
    artifacts: list[str] = field(default_factory=list)
    pr_url: str = ""
    branch: str = ""
    tests_passed: bool = False
    error: str = ""


class BugFixPipeline:
    """
    Orchestrates the complete bug-fix pipeline for a single GitHub issue.

    Pass github=None to skip GitHub steps (useful for local testing or
    when the issue is provided directly as an Issue object).
    """

    def __init__(
        self,
        config: "SpeckitConfig",
        project_root: Path,
        github: Optional["GitHubAdapter"] = None,
        on_step: Optional[Callable[[str, str], None]] = None,
    ):
        self.config = config
        self.project_root = project_root
        self.github = github
        self.on_step = on_step or (lambda title, detail: None)
        self._run_dir: Optional[Path] = None
        self._log_lines: list[str] = []

    # ── internal helpers ──────────────────────────────────────────────────────

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

    def _slug(self, title: str) -> str:
        import re
        return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40]

    def _git(self, *args: str) -> tuple[int, str]:
        import subprocess
        r = subprocess.run(
            ["git", *args],
            cwd=self.project_root,
            capture_output=True,
            text=True,
        )
        return r.returncode, (r.stdout + r.stderr).strip()

    def _apply_code_changes(self, code_plan) -> list[str]:
        """Write code changes to disk. Returns list of changed file paths."""
        changed: list[str] = []
        for change in code_plan.changes:
            if not change.file or not change.content:
                continue
            target = self.project_root / change.file
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(change.content, encoding="utf-8")
            changed.append(change.file)
        return changed

    def _build_code_changes_md(self, code_plan, test_result=None) -> str:
        lines = [f"# Code changes\n\n**Summary:** {code_plan.summary}\n"]
        for c in code_plan.changes:
            lines.append(f"\n## `{c.file}` ({c.action})\n")
            lines.append(f"**Why:** {c.explanation}\n")
            ext = c.file.rsplit(".", 1)[-1] if "." in c.file else ""
            lines.append(f"```{ext}\n{c.content}\n```")
        if test_result:
            status = "PASSED" if test_result.passed else "FAILED"
            lines.append(f"\n## Test results — {status}\n```\n{test_result.summary()}\n```")
        return "\n".join(lines)

    def _sync_specs(self, classification, bug_report_md: str, code_plan) -> None:
        """Update spec files touched by this fix to reflect what changed."""
        from speckit.core.agents import sync_spec_file

        changes_summary = (
            f"Bug fix summary: {code_plan.summary}\n\n"
            "Files changed:\n"
            + "\n".join(f"- {c.file}: {c.explanation}" for c in code_plan.changes)
        )

        specs_root = self.project_root / self.config.paths.specs.lstrip("./") / "modules"
        synced: list[str] = []

        for module in classification.affected_modules:
            spec_path = specs_root / f"{module}.md"
            if not spec_path.exists():
                continue
            try:
                current = spec_path.read_text(encoding="utf-8")
                updated = sync_spec_file(
                    spec_name=f"{module}.md",
                    current_spec_content=current,
                    changes_summary=changes_summary,
                    config=self.config,
                    project_root=self.project_root,
                )
                spec_path.write_text(updated, encoding="utf-8")
                synced.append(module)
            except Exception as e:
                self._step(f"Spec sync skipped for {module}", str(e))

        if synced:
            self._step("Spec files updated", ", ".join(synced))

    # ── pipeline steps ────────────────────────────────────────────────────────

    def _fetch_issue(self, issue_number: int) -> "Issue":
        if self.github is None:
            raise RuntimeError("GitHub adapter is required to fetch issues.")
        return self.github.get_issue(issue_number)

    def _classify(self, issue: "Issue") -> object:
        from speckit.core.agents import classify_issue
        return classify_issue(
            issue.title, issue.body, self.config, self.project_root
        )

    def _search_specs(self, classification) -> list[dict]:
        from speckit.adapters.vector_db import search_specs
        from speckit.core.spec_parser import discover_spec_files

        query = " ".join(classification.search_keywords[:5])
        results = search_specs(
            query, self.config, self.project_root,
            top_k=self.config.agent.max_spec_read_files,
        )

        if not results:
            # Index not built or empty — fall back to all specs
            all_specs = discover_spec_files(
                self.project_root, self.config.paths.specs
            )
            results = [
                {
                    "path": s.path,
                    "module": s.module,
                    "title": s.title,
                    "summary": s.summary,
                    "content": s.content,
                    "source_files": s.source_files,
                    "affects": s.affects,
                }
                for s in all_specs
            ]

        return results

    def _read_source_files(self, spec_results: list[dict]) -> dict[str, str]:
        """Read source files referenced in spec frontmatter (local or via GitHub)."""
        source_files: dict[str, str] = {}
        for spec in spec_results:
            for fpath in spec.get("source_files", []):
                if fpath in source_files:
                    continue
                local = self.project_root / fpath
                if local.exists():
                    try:
                        source_files[fpath] = local.read_text(encoding="utf-8")[:2000]
                    except OSError:
                        pass
                elif self.github:
                    try:
                        source_files[fpath] = self.github.get_file_contents(fpath)[:2000]
                    except Exception:
                        pass
        return source_files

    def _build_classification_md(self, issue_number: int, c) -> str:
        return (
            f"# Classification: issue #{issue_number}\n\n"
            f"- **Type:** {c.issue_type}\n"
            f"- **Severity:** {c.severity}\n"
            f"- **Affected modules:** {', '.join(c.affected_modules)}\n"
            f"- **Search keywords:** {', '.join(c.search_keywords)}\n"
            f"- **Summary:** {c.summary}\n"
        )

    # ── main entry point ──────────────────────────────────────────────────────

    def run(
        self,
        issue_number: int,
        issue: Optional["Issue"] = None,
    ) -> RunResult:
        """
        Run the full bug-fix pipeline.

        If `issue` is provided, GitHub fetch is skipped — useful for
        --no-github mode where the user enters issue details manually.
        """
        from speckit.core.agents import draft_bug_report, write_test_plan, reset_cost_log, get_cost_summary_md
        from speckit.core.judge import run_judge_loop
        from speckit.adapters.slack import SlackNotifier
        reset_cost_log()
        slack = SlackNotifier(self.config)

        # Setup run directory
        runs_path = self.config.paths.runs.lstrip("./")
        run_dir = self.project_root / runs_path / f"bug-fix-{issue_number}"
        run_dir.mkdir(parents=True, exist_ok=True)
        self._run_dir = run_dir

        self._step(f"Starting pipeline", f"issue #{issue_number}")

        result = RunResult(
            issue_number=issue_number,
            run_dir=run_dir,
            approved=False,
            judge_score=0.0,
            judge_iterations=0,
        )

        try:
            # ── 1. Fetch or accept issue ──────────────────────────────────────
            if issue is None:
                self._step("Fetching issue from GitHub")
                issue = self._fetch_issue(issue_number)
            self._step("Issue loaded", f"{issue.title!r}")

            # ── 2. Classify ──────────────────────────────────────────────────
            self._step("Classifying issue")
            classification = self._classify(issue)
            self._step(
                "Classified",
                f"{classification.issue_type} | {classification.severity} | "
                f"modules={classification.affected_modules}",
            )
            self._write(
                "01_classification.md",
                self._build_classification_md(issue_number, classification),
            )
            result.artifacts.append("01_classification.md")

            # ── 3. Search specs ───────────────────────────────────────────────
            self._step("Searching spec index", " ".join(classification.search_keywords[:4]))
            spec_results = self._search_specs(classification)
            self._step("Spec files selected", f"{len(spec_results)} files")

            # ── 4. Read source files ──────────────────────────────────────────
            source_files = self._read_source_files(spec_results)
            if source_files:
                self._step("Source files read", f"{len(source_files)} files")

            # ── 5. Load architecture + security + learnings ───────────────────
            arch_spec = self._read_spec_file("architecture.md")
            sec_spec = self._read_spec_file("security.md")
            learnings = self._read_spec_file("global_learnings.md")

            # ── 6. Draft bug report ───────────────────────────────────────────
            self._step("Drafting bug report")
            draft = draft_bug_report(
                issue.title, issue.body, classification,
                spec_results, source_files, learnings,
                self.config, self.project_root,
            )
            self._write(
                "02_bug_report.md",
                draft + "\n\n---\n*status: draft — pending judge*",
            )
            result.artifacts.append("02_bug_report.md")

            # ── 7. Judge loop ─────────────────────────────────────────────────
            self._step("Starting judge loop", f"threshold={self.config.agent.judge_threshold}")

            def _on_judge_iter(i: int, score: float, gaps: list[str]) -> None:
                self._step(
                    f"Judge iteration {i}",
                    f"score={score:.2f} | {len(gaps)} gap(s)",
                )

            judge_result = run_judge_loop(
                draft=draft,
                architecture_spec=arch_spec,
                security_spec=sec_spec,
                config=self.config,
                project_root=self.project_root,
                on_iteration=_on_judge_iter,
            )

            result.judge_score = judge_result.final_score
            result.judge_iterations = judge_result.iterations
            result.approved = judge_result.approved

            status_tag = (
                "judge-approved"
                if judge_result.approved
                else "needs-human-review"
            )
            self._write(
                "02_bug_report.md",
                judge_result.final_spec
                + f"\n\n---\n*status: {status_tag} | "
                f"score: {judge_result.final_score:.2f} | "
                f"iterations: {judge_result.iterations}*",
            )

            if judge_result.approved:
                self._step(
                    "Bug report approved",
                    f"score={judge_result.final_score:.2f} in {judge_result.iterations} iteration(s)",
                )
            else:
                self._step(
                    "Judge threshold not met — human review required",
                    f"score={judge_result.final_score:.2f} < {self.config.agent.judge_threshold}",
                )
                slack.judge_failed(
                    run_type="bug-fix",
                    name=f"issue #{issue_number}",
                    score=judge_result.final_score,
                    threshold=self.config.agent.judge_threshold,
                    gaps=[],
                )

            # ── 8. Test plan ─────────────────────────────────────────────────
            self._step("Writing test plan")
            test_plan = write_test_plan(
                judge_result.final_spec, self.config, self.project_root
            )
            self._write("03_test_plan.md", test_plan)
            result.artifacts.append("03_test_plan.md")

            # ── 9-11. Code fix + tests + PR (only if judge approved) ─────────
            if not judge_result.approved:
                self._step(
                    "Skipping code phase",
                    "bug report needs human review before code is written",
                )
            else:
                from speckit.core.agents import write_code, CodePlan
                from speckit.adapters.shell import ShellAdapter

                test_runner = self.config.testing.backend_runner or "pytest"
                branch_name = f"fix/issue-{issue_number}-{self._slug(issue.title)}"

                # Create git branch
                rc, _ = self._git("checkout", "-b", branch_name)
                if rc != 0:
                    # Branch may already exist
                    self._git("checkout", branch_name)
                result.branch = branch_name
                self._step("Branch created", branch_name)

                # Retry loop: write code → run tests (up to 3 attempts)
                shell = ShellAdapter(cwd=self.project_root)
                code_plan: Optional[CodePlan] = None
                test_result = None
                prev_output = ""

                for attempt in range(1, 4):
                    self._step(
                        f"Writing code fix (attempt {attempt})",
                        f"{len(source_files)} source file(s)",
                    )
                    code_plan = write_code(
                        bug_report_md=judge_result.final_spec,
                        source_file_contents=source_files,
                        test_runner=test_runner,
                        config=self.config,
                        project_root=self.project_root,
                        previous_test_output=prev_output,
                    )
                    if not code_plan or not code_plan.changes:
                        raise RuntimeError("write_code() returned an empty plan — no changes to apply")
                    changed_files = self._apply_code_changes(code_plan)
                    self._step(
                        "Code applied",
                        f"{len(changed_files)} file(s): {', '.join(changed_files[:3])}",
                    )

                    # Run tests
                    self._step("Running tests", code_plan.test_command)
                    try:
                        test_result = shell.run_tests(code_plan.test_command)
                    except ValueError as e:
                        self._step("Test runner skipped", str(e))
                        test_result = None
                        break

                    status = "passed" if test_result.passed else "failed"
                    self._step(f"Tests {status}", f"exit code {test_result.return_code}")

                    if test_result.passed:
                        break
                    prev_output = test_result.summary()

                # Write code changes + test results artifacts
                if code_plan:
                    self._write(
                        "04_code_changes.md",
                        self._build_code_changes_md(code_plan, test_result),
                    )
                    result.artifacts.append("04_code_changes.md")

                if test_result:
                    result.tests_passed = test_result.passed
                    self._write(
                        "05_test_results.md",
                        f"# Test results\n\n```\n{test_result.summary()}\n```",
                    )
                    result.artifacts.append("05_test_results.md")

                # Commit + PR (only if tests passed and GitHub connected)
                if test_result and test_result.passed and self.github and changed_files:
                    self._step("Committing changes")
                    self._git("add", *changed_files)
                    self._git(
                        "commit", "-m",
                        f"fix(#{issue_number}): {code_plan.summary}\n\n"
                        f"Closes #{issue_number}",
                    )
                    self._git("push", "-u", "origin", branch_name)

                    pr_body = (
                        f"## Summary\n\nFixes #{issue_number}: {issue.title}\n\n"
                        f"{code_plan.summary}\n\n"
                        f"## Changes\n"
                        + "\n".join(f"- `{c.file}`: {c.explanation}" for c in code_plan.changes)
                        + f"\n\n## Test results\nAll tests passed.\n\n"
                        f"---\n*Generated by speckit — review before merging.*"
                    )
                    pr = self.github.create_pr(
                        title=f"fix(#{issue_number}): {issue.title}",
                        body=pr_body,
                        head=branch_name,
                        base=self.config.github.default_branch,
                    )
                    result.pr_url = pr.get("html_url", "")
                    self._step("PR opened", result.pr_url)
                    slack.pr_opened(
                        issue_number=issue_number,
                        title=issue.title,
                        pr_url=result.pr_url,
                        score=judge_result.final_score,
                    )

                elif changed_files:
                    # No GitHub — just commit locally
                    self._git("add", *changed_files)
                    self._git(
                        "commit", "-m",
                        f"fix(#{issue_number}): {code_plan.summary}",
                    )
                    self._step(
                        "Committed locally",
                        "push manually when ready",
                    )

            # ── 12. Spec sync ─────────────────────────────────────────────
            if judge_result.approved and code_plan:
                self._step("Syncing spec files")
                self._sync_specs(classification, judge_result.final_spec, code_plan)

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

        return result
