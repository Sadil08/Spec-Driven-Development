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

    def _apply_code_changes(self, code_plan) -> tuple[list[str], list[str]]:
        """
        Write code changes to disk after validating each full-file rewrite.

        Returns (changed_files, rejected_changes). A change is rejected — and NOT
        written — when overwriting an existing file would destroy code the model
        never saw (truncated input) or when the rewrite is suspiciously short.
        Rejections abort the PR upstream, so a destructive write is never merged.
        """
        from speckit.core.file_safety import validate_rewrite

        truncated = getattr(self, "_truncated_source_files", set())
        # Track what we touched so a failed run can be rolled back precisely.
        if not hasattr(self, "_created_files"):
            self._created_files: set[str] = set()
        if not hasattr(self, "_modified_files"):
            self._modified_files: set[str] = set()

        changed: list[str] = []
        rejected: list[str] = []
        for change in code_plan.changes:
            if not change.file or not change.content:
                continue
            target = self.project_root / change.file
            pre_existing = target.exists()
            if change.action == "modify" and pre_existing:
                try:
                    original = target.read_text(encoding="utf-8")
                except OSError:
                    original = ""
                ok, reason = validate_rewrite(
                    original, change.content, change.file in truncated
                )
                if not ok:
                    rejected.append(f"{change.file}: {reason}")
                    continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(change.content, encoding="utf-8")
            changed.append(change.file)
            if pre_existing:
                self._modified_files.add(change.file)
            else:
                self._created_files.add(change.file)
        return changed, rejected

    def _snapshot_tree(self) -> None:
        """Record whether the working tree was clean before we wrote any code."""
        rc, out = self._git("status", "--porcelain")
        self._tree_was_clean = (rc == 0 and not out.strip())
        self._created_files = set()
        self._modified_files = set()

    def _rollback_tree(self) -> bool:
        """
        Revert exactly the files this run wrote, restoring the repo to its
        pre-run state. Only runs when the tree was clean at the start, so a
        user's pre-existing uncommitted edits are never destroyed. Returns True
        if a rollback was performed.
        """
        if not getattr(self.config.agent, "rollback_on_failure", True):
            return False
        if not getattr(self, "_tree_was_clean", False):
            return False
        created = getattr(self, "_created_files", set())
        modified = getattr(self, "_modified_files", set())
        if not created and not modified:
            return False
        for rel in modified:
            self._git("checkout", "HEAD", "--", rel)
        for rel in created:
            try:
                (self.project_root / rel).unlink()
            except OSError:
                pass
        return True

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
        """
        Read source files referenced in spec frontmatter (local or via GitHub).

        Files are read in full up to MAX_SOURCE_FILE_CHARS. Any file that exceeds
        that cap is recorded in self._truncated_source_files so the code-apply
        step can refuse to blindly full-file-rewrite a file the model never saw
        in its entirety.
        """
        from speckit.core.file_safety import read_capped

        self._truncated_source_files: set[str] = set()
        source_files: dict[str, str] = {}
        for spec in spec_results:
            for fpath in spec.get("source_files", []):
                if fpath in source_files:
                    continue
                raw: str | None = None
                local = self.project_root / fpath
                if local.exists():
                    try:
                        raw = local.read_text(encoding="utf-8")
                    except OSError:
                        raw = None
                elif self.github:
                    try:
                        raw = self.github.get_file_contents(fpath)
                    except Exception:
                        raw = None
                if raw is None:
                    continue
                rr = read_capped(raw)
                source_files[fpath] = rr.content
                if rr.was_truncated:
                    self._truncated_source_files.add(fpath)
        return source_files

    @staticmethod
    def _tests_were_vacuous(test_result) -> bool:
        """
        Detect a 'green' test run that didn't actually exercise anything.

        A passing exit code means nothing if zero tests were collected. This
        catches the silent-test-gap failure mode where a fix ships because the
        suite ran no assertions against it.
        """
        if test_result is None:
            return False
        from speckit.adapters.shell import output_looks_vacuous
        return output_looks_vacuous(test_result.stdout + "\n" + test_result.stderr)

    _GREP_EXTS = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".java", ".rb", ".rs", ".cs"}
    _GREP_SKIP = {
        "node_modules", "__pycache__", ".git", ".venv", "venv", "dist", "build",
        ".next", "target", "vendor", "specs", "runs", ".speckit",
    }

    def _grep_source_files(self, classification, limit: int = 6) -> dict[str, str]:
        """
        Fallback source-file discovery when the spec index points at nothing useful.

        Ranks repo files by how many of the classification's search keywords and
        affected-module names they match (filename match weighted higher), reads the
        top `limit` in full (capped), and records truncation. This stops the pipeline
        from inventing a fix against a file it never read when specs are stale.
        """
        from speckit.core.file_safety import read_capped

        terms = [t.lower() for t in (
            list(classification.search_keywords) + list(classification.affected_modules)
        ) if t and len(t) >= 3]
        if not terms:
            return {}
        modules = {m.lower() for m in classification.affected_modules if m}

        src_root = self.project_root / self.config.paths.src.lstrip("./")
        search_root = src_root if src_root.exists() else self.project_root

        scored: list[tuple[int, Path]] = []
        for p in search_root.rglob("*"):
            if not p.is_file() or p.suffix not in self._GREP_EXTS:
                continue
            if any(part in self._GREP_SKIP for part in p.parts):
                continue
            name = p.stem.lower()
            score = 0
            if name in modules:
                score += 5
            score += sum(2 for t in terms if t in name)
            try:
                head = p.read_text(encoding="utf-8", errors="replace")[:8000].lower()
            except OSError:
                continue
            score += sum(1 for t in terms if t in head)
            if score > 0:
                scored.append((score, p))

        scored.sort(key=lambda x: x[0], reverse=True)
        out: dict[str, str] = {}
        for _, p in scored[:limit]:
            try:
                rr = read_capped(p.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            rel = str(p.relative_to(self.project_root))
            out[rel] = rr.content
            if rr.was_truncated:
                self._truncated_source_files.add(rel)
        return out

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
        from speckit.core.agents import (
            draft_bug_report, write_test_plan, reset_cost_log, get_cost_summary_md,
            set_pipeline_cache, clear_pipeline_cache,
            review_generated_code, scan_for_secrets,
        )
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

            # Fallback: if specs pointed at no real source, grep the repo by the
            # classification keywords so we never write a fix against unseen code.
            if len(source_files) < 2:
                grep_hits = self._grep_source_files(classification)
                added = 0
                for path, content in grep_hits.items():
                    if path not in source_files:
                        source_files[path] = content
                        added += 1
                if added:
                    self._step(
                        "Source files via keyword grep (specs were thin)",
                        f"+{added} file(s)",
                    )
            if not source_files:
                self._step(
                    "No source files located",
                    "spec index empty and keyword grep found nothing — fix may be unreliable",
                )

            # ── 5. Load architecture + security + learnings ───────────────────
            arch_spec = self._read_spec_file("architecture.md")
            sec_spec = self._read_spec_file("security.md")
            learnings = self._read_spec_file("global_learnings.md")
            set_pipeline_cache(arch_spec, sec_spec, learnings)

            # ── 6. Draft bug report (skip if already approved) ────────────────
            run_dir = self._run_dir
            existing_report = (run_dir / "02_bug_report.md").read_text(encoding="utf-8") \
                if run_dir and (run_dir / "02_bug_report.md").exists() else None
            report_approved = existing_report is not None and "judge-approved" in existing_report

            if report_approved:
                from speckit.core.judge import JudgeResult
                import re as _re
                _s = _re.search(r"score:\s*([\d.]+)", existing_report)
                _it = _re.search(r"iterations:\s*(\d+)", existing_report)
                judge_result = JudgeResult(
                    final_spec=existing_report,
                    final_score=float(_s.group(1)) if _s else self.config.agent.judge_threshold,
                    iterations=int(_it.group(1)) if _it else 1,
                    approved=True,
                )
                result.judge_score = judge_result.final_score
                result.judge_iterations = judge_result.iterations
                result.approved = True
                self._step("Reusing approved bug report", "02_bug_report.md")
                result.artifacts.append("02_bug_report.md")
            else:
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

                # ── 7. Judge loop ─────────────────────────────────────────────
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

                # Snapshot the tree so a failed run can be rolled back cleanly.
                self._snapshot_tree()

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
                code_review = None
                prev_output = ""

                rejected_changes: list[str] = []
                from speckit.core.limits import check_cost_budget
                for attempt in range(1, 4):
                    # Abort before another expensive write/test cycle if over budget.
                    check_cost_budget(self.config)
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
                        truncated_files=sorted(getattr(self, "_truncated_source_files", set())),
                    )
                    if not code_plan or not code_plan.changes:
                        raise RuntimeError("write_code() returned an empty plan — no changes to apply")
                    changed_files, rejected_changes = self._apply_code_changes(code_plan)
                    if rejected_changes:
                        self._step(
                            "Unsafe rewrite(s) rejected — see DESTRUCTIVE_CHANGE_BLOCKED.md",
                            "; ".join(rejected_changes),
                        )
                        self._write(
                            "DESTRUCTIVE_CHANGE_BLOCKED.md",
                            "# Destructive change blocked\n\n"
                            "speckit refused to overwrite the following files because the "
                            "generated rewrite would have destroyed code the model did not "
                            "fully see, or was suspiciously short:\n\n"
                            + "\n".join(f"- {r}" for r in rejected_changes)
                            + "\n\nNo PR will be opened. Re-run with a smaller scope or fix "
                            "the affected file manually.",
                        )
                    self._step(
                        "Code applied",
                        f"{len(changed_files)} file(s): {', '.join(changed_files[:3])}",
                    )

                    # Generate runnable tests once (attempt 1) so a green run
                    # actually proves the fix — closes the silent-test-gap.
                    if attempt == 1:
                        try:
                            from speckit.core.agents import generate_tests
                            self._step("Generating tests for the fix")
                            test_plan_code = generate_tests(
                                spec_md=judge_result.final_spec,
                                code_changes=[
                                    {"file": c.file, "action": c.action, "content": c.content}
                                    for c in code_plan.changes
                                ],
                                test_runner=code_plan.test_command or test_runner,
                                language=self.config.primary_language,
                                config=self.config,
                                project_root=self.project_root,
                                mode="fix",
                            )
                            test_changed, _ = self._apply_code_changes(test_plan_code)
                            if test_changed:
                                changed_files = list(dict.fromkeys(changed_files + test_changed))
                                self._step("Tests generated", f"{len(test_changed)} test file(s)")
                        except Exception as e:
                            self._step("Test generation skipped", str(e))

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

                    if test_result.passed and self._tests_were_vacuous(test_result):
                        self._step(
                            "Tests passed but collected 0 tests — treating as not verified",
                            "a green run with no tests does not prove the fix works",
                        )
                        test_result.passed = False
                        prev_output = (
                            "The test command exited 0 but ran no tests. Add or point to "
                            "real tests that exercise the changed code, then ensure they pass."
                        )
                        continue

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

                # ── Code review (agent 28) before commit ──────────────────────
                if code_plan and test_result and test_result.passed:
                    self._step("Running code review")
                    code_review = review_generated_code(
                        spec_md=judge_result.final_spec,
                        code_changes=[
                            {"file": c.file, "action": c.action,
                             "content": c.content, "explanation": c.explanation}
                            for c in code_plan.changes
                        ],
                        architecture_spec=arch_spec,
                        config=self.config,
                        project_root=self.project_root,
                    )
                    review_lines = (
                        f"# Code review\n\n"
                        f"**Score:** {code_review.score:.2f} | "
                        f"**Approved:** {code_review.approved}\n\n"
                    )
                    if code_review.blocking_issues:
                        review_lines += "## Blocking issues\n" + "\n".join(
                            f"- {i}" for i in code_review.blocking_issues
                        ) + "\n\n"
                    if code_review.warnings:
                        review_lines += "## Warnings\n" + "\n".join(
                            f"- {w}" for w in code_review.warnings
                        ) + "\n\n"
                    review_lines += f"## Feedback\n{code_review.feedback}\n"
                    self._write("06_code_review.md", review_lines)
                    result.artifacts.append("06_code_review.md")
                    if not code_review.approved:
                        self._step(
                            "Code review failed — human review required before merge",
                            f"score={code_review.score:.2f}, "
                            f"{len(code_review.blocking_issues)} blocking issue(s)",
                        )

                # ── Secrets scan before PR ─────────────────────────────────────
                secrets_found: list[str] = []
                if code_plan:
                    secrets_found = scan_for_secrets(code_plan.changes)
                    if secrets_found:
                        self._step(
                            "SECRETS DETECTED in generated code — aborting PR",
                            "; ".join(secrets_found),
                        )
                        self._write(
                            "SECRETS_DETECTED.md",
                            "# Secrets detected\n\n"
                            "The following potential secrets were found in generated code.\n"
                            "Review and remove before committing.\n\n"
                            + "\n".join(f"- {s}" for s in secrets_found),
                        )

                # ── Blast-radius check ─────────────────────────────────────────
                from speckit.core.limits import check_blast_radius
                blast_violations = (
                    check_blast_radius(code_plan.changes, self.config) if code_plan else []
                )

                # ── PR gate ────────────────────────────────────────────────────
                # A PR is only opened when EVERY safety gate passes:
                #   1. tests actually passed (and were not vacuous)
                #   2. no unsafe/destructive rewrites were rejected
                #   3. no secrets detected
                #   4. code review approved (score >= threshold AND no blocking issues)
                #   5. change is within the blast-radius budget
                review_approved = code_review.approved if code_review is not None else False
                pr_blockers: list[str] = []
                if not (test_result and test_result.passed):
                    pr_blockers.append("tests did not pass")
                if rejected_changes:
                    pr_blockers.append("destructive rewrite(s) blocked")
                if secrets_found:
                    pr_blockers.append("secrets detected")
                if code_review is not None and not review_approved:
                    pr_blockers.append("code review not approved")
                if blast_violations:
                    pr_blockers.append("change too large: " + "; ".join(blast_violations))

                if (
                    not pr_blockers
                    and self.github and changed_files
                ):
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
                        + "\n\n## Test results\nAll tests passed."
                        + "\n\n✅ Code review passed."
                        + "\n\n---\n*Generated by speckit — review before merging.*"
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

                elif pr_blockers and self.github and changed_files:
                    # Safety gate failed — commit to the branch for human review,
                    # but do NOT open a PR. Surface why on the issue.
                    self._step("PR blocked by safety gate", "; ".join(pr_blockers))
                    self._git("add", *changed_files)
                    self._git(
                        "commit", "-m",
                        f"wip(#{issue_number}): {code_plan.summary} [needs human review]",
                    )
                    try:
                        self.github.add_comment(
                            issue_number,
                            f"⚠️ speckit generated a fix on branch `{branch_name}` but did "
                            f"**not** open a PR because: {', '.join(pr_blockers)}. "
                            "Review the branch and the run artifacts before merging.",
                        )
                    except Exception:
                        pass

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
            # Leave the user's repo as we found it if we wrote code then failed.
            try:
                if self._rollback_tree():
                    self._step("Rolled back working-tree changes", "repo restored to pre-run state")
            except Exception:
                pass
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
