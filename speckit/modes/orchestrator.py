"""
Orchestrator pipelines — cross-service feature and bug-fix coordination.

OrchestratorPipeline  (speckit orchestrate)
  Stage 0 — Topology planning: decide what services to create / modify
  Stage 1 — Load full context
  Stage 2 — Analyze service impact
  Stage 3 — Check contract compatibility
  Stage 4 — Decompose into per-service sub-features
  Stage 5 — Run per-service FeaturePipelines
  Stage 6 — Write cross-service build plan
  Stage 7 — Write integration test plan
  Stage 8 — Code generation (only if build=True)
  Stage 9 — Final status

BugOrchestratePipeline  (speckit orchestrate-bug)
  Stage 1 — Load context + classify issue
  Stage 2 — Analyze bug service impact
  Stage 3 — Check contract compatibility
  Stage 4 — Run per-service BugFixPipelines
  Stage 5 — Write cross-service fix plan
  Stage 6 — Write integration test plan
  Stage 7 — Final status
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from speckit.core.global_config import GlobalSpeckitConfig, ServiceEntry
    from speckit.modes.feature import FeatureResult
    from speckit.modes.bug_fix import RunResult


@dataclass
class OrchestrateResult:
    feature_name: str
    run_dir: Path
    approved: bool
    services_created: list[str] = field(default_factory=list)
    services_affected: list[str] = field(default_factory=list)
    service_results: dict = field(default_factory=dict)   # service_name → FeatureResult
    contract_verdict: str = "unknown"
    build_ran: bool = False
    services_built: dict = field(default_factory=dict)    # service_name → tests_passed (bool)
    pr_urls: dict = field(default_factory=dict)           # service_name → PR URL
    artifacts: list[str] = field(default_factory=list)
    error: str = ""


@dataclass
class BugOrchestrateResult:
    issue_number: int
    run_dir: Path
    approved: bool
    services_affected: list[str] = field(default_factory=list)
    service_results: dict = field(default_factory=dict)   # service_name → RunResult
    contract_verdict: str = "unknown"
    build_ran: bool = False
    services_built: dict = field(default_factory=dict)
    pr_urls: dict = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)
    error: str = ""


# ── shared helpers ─────────────────────────────────────────────────────────────

class _PipelineBase:
    def __init__(
        self,
        global_config: "GlobalSpeckitConfig",
        on_step: Optional[Callable[[str, str], None]] = None,
    ):
        self.gc = global_config
        self.on_step = on_step or (lambda title, detail: None)
        self._run_dir: Optional[Path] = None
        self._log_lines: list[str] = []

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

    def _slug(self, name: str) -> str:
        import re
        return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40]

    def _load_services_summary(self, services: list["ServiceEntry"]) -> dict[str, str]:
        """Read architecture.md for each service and return {name: content[:800]}.
        Services with no architecture.md are logged and skipped (not included in summary).
        """
        summary: dict[str, str] = {}
        for svc in services:
            arch_path = svc.path / svc.config.paths.specs.lstrip("./") / "architecture.md"
            if arch_path.exists():
                summary[svc.name] = arch_path.read_text(encoding="utf-8")[:800]
            else:
                self._step(
                    f"Warning: no architecture.md for {svc.name}",
                    f"run 'speckit init' in {svc.path} then 'speckit scan' or 'speckit build'",
                )
        return summary

    def _load_contract_specs(self) -> dict[str, str]:
        """Read all .md files from contracts_dir."""
        contracts: dict[str, str] = {}
        if self.gc.contracts_dir.exists():
            for p in sorted(self.gc.contracts_dir.glob("*.md")):
                contracts[p.name] = p.read_text(encoding="utf-8")
        return contracts

    def _load_system_architecture(self) -> str:
        arch = self.gc.global_specs / "system-architecture.md"
        return arch.read_text(encoding="utf-8") if arch.exists() else ""

    @staticmethod
    def _compat_to_md(result) -> str:
        lines = [
            f"# Contract compatibility\n",
            f"**Verdict:** {result.verdict}\n",
        ]
        if result.conflicts:
            lines.append("## Conflicts\n" + "\n".join(f"- {x}" for x in result.conflicts))
        if result.breaking_changes:
            lines.append("## Breaking changes\n" + "\n".join(f"- {x}" for x in result.breaking_changes))
        if result.recommendations:
            lines.append("## Recommendations\n" + "\n".join(f"- {x}" for x in result.recommendations))
        return "\n\n".join(lines)

    def _make_service_config(self, svc: "ServiceEntry", services_subdir: Path):
        """Return a copy of the service config with runs_dir pointing to services_subdir."""
        cfg = copy.deepcopy(svc.config)
        rel = str(services_subdir.relative_to(svc.path)) if services_subdir.is_relative_to(svc.path) else str(services_subdir)
        cfg.paths.runs = rel
        return cfg


# ── OrchestratorPipeline ───────────────────────────────────────────────────────

class OrchestratorPipeline(_PipelineBase):
    """Cross-service feature pipeline."""

    def run(
        self,
        feature_name: str,
        feature_description: str,
        build: bool = False,
    ) -> OrchestrateResult:
        from speckit.core.agents import (
            plan_service_topology,
            analyze_service_impact,
            check_contract_compatibility,
            decompose_cross_service_feature,
            write_cross_service_build_plan,
            write_integration_test_plan,
            write_code,
            reset_cost_log,
            get_cost_summary_md,
            generate_spec_from_vision,
            set_pipeline_cache,
            clear_pipeline_cache,
            review_generated_code,
            scan_for_secrets,
        )
        from speckit.core.global_config import topological_sort, save_global_config
        from speckit.modes.feature import FeaturePipeline
        from speckit.adapters.slack import SlackNotifier

        reset_cost_log()

        slug = self._slug(feature_name)
        run_dir = self.gc.runs_dir / f"orchestrate-{slug}"
        run_dir.mkdir(parents=True, exist_ok=True)
        self._run_dir = run_dir

        services_dir = run_dir / "services"
        services_dir.mkdir(exist_ok=True)

        self._step("Starting orchestrator pipeline", feature_name)

        # Use first service's config for Slack (project-level notifications)
        slack = None
        if self.gc.services:
            try:
                slack = SlackNotifier(self.gc.services[0].config)
            except Exception:
                pass

        result = OrchestrateResult(
            feature_name=feature_name,
            run_dir=run_dir,
            approved=False,
        )

        try:
            # ── Stage 0: Topology planning ────────────────────────────────────
            self._step("Stage 0 — Topology planning")
            existing_summary = self._load_services_summary(self.gc.services)
            system_arch = self._load_system_architecture()

            # Use first service's config for agent calls
            agent_config = self.gc.services[0].config if self.gc.services else None
            if not agent_config:
                raise RuntimeError("No services registered in global.sdd.config.yml")

            topology = plan_service_topology(
                feature_name=feature_name,
                feature_description=feature_description,
                existing_services_summary=existing_summary,
                system_architecture=system_arch,
                config=agent_config,
                project_root=self.gc.root,
            )

            topology_lines = [f"# Topology plan: {feature_name}\n"]
            topology_lines.append(f"## Reasoning\n{topology.reasoning}\n")
            if topology.services_to_create:
                topology_lines.append("## New services to create")
                for ns in topology.services_to_create:
                    topology_lines.append(
                        f"- **{ns.name}**: {ns.purpose}  \n"
                        f"  Tech stack hint: {ns.tech_stack_hint}  \n"
                        f"  Depends on: {', '.join(ns.depends_on) or 'none'}"
                    )
            topology_lines.append(f"\n## Existing services to modify\n" +
                                   "\n".join(f"- {s}" for s in topology.services_to_modify))
            self._write("00_topology_plan.md", "\n".join(topology_lines))
            result.artifacts.append("00_topology_plan.md")

            # Scaffold new services
            for ns in topology.services_to_create:
                self._step(f"Scaffolding new service: {ns.name}")
                svc_path = (self.gc.root / ns.name).resolve()
                svc_path.mkdir(parents=True, exist_ok=True)
                (svc_path / "specs").mkdir(exist_ok=True)
                (svc_path / "specs" / "modules").mkdir(exist_ok=True)

                # Generate architecture spec from topology info
                arch_content = generate_spec_from_vision(
                    spec_type="architecture",
                    vision_md=f"# {ns.name}\n\n{ns.purpose}\n\nTech stack: {ns.tech_stack_hint}",
                    tech_stack_md=f"Tech stack: {ns.tech_stack_hint}",
                    module_list=["core"],
                    config=agent_config,
                    project_root=svc_path,
                )
                (svc_path / "specs" / "architecture.md").write_text(arch_content, encoding="utf-8")

                # Write minimal sdd.config.yml
                from speckit.core.config import SpeckitConfig, PathsConfig, AgentConfig
                minimal_cfg_data = {
                    "project_name": ns.name,
                    "mode": "greenfield",
                    "primary_language": agent_config.primary_language,
                    "paths": {"specs": "./specs", "runs": "./runs", "src": "./src", "tests": "./tests"},
                    "agent": {"model": self.gc.agent.model, "judge_threshold": self.gc.agent.judge_threshold,
                               "max_judge_iterations": self.gc.agent.max_judge_iterations},
                }
                import yaml as _yaml
                (svc_path / "sdd.config.yml").write_text(
                    _yaml.dump(minimal_cfg_data, default_flow_style=False, sort_keys=False),
                    encoding="utf-8",
                )

                # Load the new config and register service
                from speckit.core.config import load_config
                new_svc_config = load_config(svc_path)
                from speckit.core.global_config import ServiceEntry
                new_entry = ServiceEntry(
                    name=ns.name,
                    path=svc_path,
                    depends_on=ns.depends_on,
                    config=new_svc_config,
                )
                self.gc.services.append(new_entry)
                result.services_created.append(ns.name)
                self._step(f"Service scaffolded", ns.name)

            # Persist new services to global.sdd.config.yml
            if topology.services_to_create:
                save_global_config(self.gc)

            # ── Stage 1: Load full context ────────────────────────────────────
            # Reuse existing_summary from Stage 0, refreshed to include any newly scaffolded services.
            # Re-read only if new services were created (their specs were just written to disk).
            self._step("Stage 1 — Loading service context")
            if topology.services_to_create:
                services_summary = self._load_services_summary(self.gc.services)
            else:
                services_summary = existing_summary  # already loaded in Stage 0 — no re-read
            contract_specs = self._load_contract_specs()
            # Set pipeline cache: use system architecture as the shared context prefix
            set_pipeline_cache(system_arch, "")
            self._step("Context loaded",
                       f"{len(services_summary)} services | {len(contract_specs)} contracts")

            # ── Stage 2: Analyze service impact ──────────────────────────────
            self._step("Stage 2 — Analyzing service impact")
            impact = analyze_service_impact(
                feature_name=feature_name,
                feature_description=feature_description,
                services_summary=services_summary,
                config=agent_config,
                project_root=self.gc.root,
            )
            affected_names = [i.service_name for i in impact.impacts]
            result.services_affected = affected_names

            impact_lines = [f"# Service impact: {feature_name}\n"]
            for imp in impact.impacts:
                impact_lines.append(
                    f"## {imp.service_name} — {imp.scope}\n{imp.reason}\n"
                    f"**Contracts touched:** {', '.join(imp.affected_contracts) or 'none'}"
                )
            self._write("01_impact_analysis.md", "\n\n".join(impact_lines))
            result.artifacts.append("01_impact_analysis.md")
            self._step("Impact analysis complete", f"{len(affected_names)} service(s) affected")

            # ── Stage 3: Contract compatibility ──────────────────────────────
            self._step("Stage 3 — Checking contract compatibility")
            compat = check_contract_compatibility(
                feature_name=feature_name,
                impacted_services=impact.impacts,
                contract_specs=contract_specs,
                config=agent_config,
                project_root=self.gc.root,
            )
            result.contract_verdict = compat.verdict
            compat_md = self._compat_to_md(compat)
            self._write("02_contract_compatibility.md", compat_md)
            result.artifacts.append("02_contract_compatibility.md")
            self._step("Compatibility check complete", f"verdict={compat.verdict}")

            if compat.verdict == "blocked":
                self._step("Feature blocked by contract review",
                            "fix conflicts in 02_contract_compatibility.md")
                if slack:
                    slack.contract_conflict(feature_name, compat.conflicts)
                return result

            # ── Stage 4: Decompose ────────────────────────────────────────────
            self._step("Stage 4 — Decomposing into per-service sub-features")
            decomposition = decompose_cross_service_feature(
                feature_name=feature_name,
                feature_description=feature_description,
                impacted_services=impact.impacts,
                contract_specs=contract_specs,
                config=agent_config,
                project_root=self.gc.root,
            )

            decomp_lines = [f"# Feature decomposition: {feature_name}\n"]
            for sf in decomposition.sub_features:
                decomp_lines.append(
                    f"## {sf.service_name}: {sf.sub_feature_name}\n"
                    f"{sf.sub_feature_description}\n\n"
                    f"**Contract constraints:** {sf.contract_constraints}"
                )
            self._write("03_decomposition.md", "\n\n".join(decomp_lines))
            result.artifacts.append("03_decomposition.md")
            self._step("Decomposition complete",
                       f"{len(decomposition.sub_features)} sub-feature(s)")

            # ── Stage 5: Per-service FeaturePipelines ─────────────────────────
            self._step("Stage 5 — Running per-service feature pipelines")
            service_map = {s.name: s for s in self.gc.services}
            affected_entries = [service_map[n] for n in affected_names if n in service_map]
            sorted_services = topological_sort(affected_entries)

            sub_feature_map = {sf.service_name: sf for sf in decomposition.sub_features}
            service_build_plans: dict[str, str] = {}
            service_feature_specs: dict[str, str] = {}

            for svc in sorted_services:
                sf = sub_feature_map.get(svc.name)
                if not sf:
                    continue
                self._step(f"Running feature pipeline", svc.name)

                # Override runs path to write into global services/ subfolder
                svc_run_dir = services_dir / svc.name
                svc_run_dir.mkdir(parents=True, exist_ok=True)
                svc_config = copy.deepcopy(svc.config)
                svc_config.paths.runs = str(svc_run_dir.relative_to(svc.path)) if svc_run_dir.is_relative_to(svc.path) else str(svc_run_dir)

                def _on_svc_step(title: str, detail: str = "", _name: str = svc.name) -> None:
                    self._step(f"  [{_name}] {title}", detail)

                pipeline = FeaturePipeline(
                    config=svc_config,
                    project_root=svc.path,
                    on_step=_on_svc_step,
                )
                svc_result = pipeline.run(sf.sub_feature_name, sf.sub_feature_description)
                result.service_results[svc.name] = svc_result

                # Read generated spec and build plan for later agents
                spec_path = svc_result.run_dir / "03_feature_spec.md"
                plan_path = svc_result.run_dir / "05_build_plan.md"
                if spec_path.exists():
                    service_feature_specs[svc.name] = spec_path.read_text(encoding="utf-8")
                if plan_path.exists():
                    service_build_plans[svc.name] = plan_path.read_text(encoding="utf-8")

                self._step(f"Pipeline complete",
                           f"{svc.name} score={svc_result.judge_score:.2f} "
                           f"approved={svc_result.approved}")

            result.approved = all(
                r.approved for r in result.service_results.values()
            )

            # ── Stage 6: Cross-service build plan ─────────────────────────────
            self._step("Stage 6 — Writing cross-service build plan")
            build_plan_md = write_cross_service_build_plan(
                feature_name=feature_name,
                service_build_plans=service_build_plans,
                dependency_order=[s.name for s in sorted_services],
                config=agent_config,
                project_root=self.gc.root,
            )
            self._write("05_cross_service_build_plan.md", build_plan_md)
            result.artifacts.append("05_cross_service_build_plan.md")

            # ── Stage 7: Integration test plan ───────────────────────────────
            self._step("Stage 7 — Writing integration test plan")
            integration_test_md = write_integration_test_plan(
                feature_name=feature_name,
                service_feature_specs=service_feature_specs,
                contract_specs=contract_specs,
                config=agent_config,
                project_root=self.gc.root,
            )
            self._write("06_integration_test_plan.md", integration_test_md)
            result.artifacts.append("06_integration_test_plan.md")

            # ── Stage 8: Code generation (optional) ───────────────────────────
            if build:
                result.build_ran = True
                self._step("Stage 8 — Code generation")
                from speckit.adapters.github import GitHubAdapter
                from speckit.adapters.shell import ShellRunner

                for svc in sorted_services:
                    spec_md = service_feature_specs.get(svc.name, "")
                    if not spec_md:
                        self._step(f"  [{svc.name}] Skipping code gen — no approved spec")
                        continue

                    self._step(f"  [{svc.name}] Writing code")

                    # Gather source files from the service
                    src_path = svc.path / svc.config.paths.src.lstrip("./")
                    source_files: dict[str, str] = {}
                    if src_path.exists():
                        for p in list(src_path.rglob("*"))[:8]:
                            if p.is_file() and p.suffix in {".py", ".ts", ".js", ".go", ".java"}:
                                try:
                                    source_files[str(p.relative_to(svc.path))] = p.read_text(encoding="utf-8")
                                except Exception:
                                    pass

                    test_runner = svc.config.testing.backend_runner or "pytest"
                    previous_output = ""

                    code_plan = None
                    for attempt in range(1, 4):
                        code_plan = write_code(
                            bug_report_md=spec_md,
                            source_file_contents=source_files,
                            test_runner=test_runner,
                            config=svc.config,
                            project_root=svc.path,
                            previous_test_output=previous_output,
                            mode="feature",
                        )

                        # Apply changes
                        for change in code_plan.changes:
                            target = svc.path / change.file
                            target.parent.mkdir(parents=True, exist_ok=True)
                            target.write_text(change.content, encoding="utf-8")

                        # Run tests
                        runner = ShellRunner(svc.path)
                        test_ok, test_output = runner.run(code_plan.test_command)
                        result.services_built[svc.name] = test_ok

                        if test_ok:
                            self._step(f"  [{svc.name}] Tests passed", f"attempt {attempt}")
                            break
                        else:
                            self._step(f"  [{svc.name}] Tests failed",
                                       f"attempt {attempt} — retrying")
                            previous_output = test_output

                    if not code_plan:
                        continue

                    # ── Code review ───────────────────────────────────────────
                    self._step(f"  [{svc.name}] Code review")
                    svc_arch = (svc.path / svc.config.paths.specs.lstrip("./") / "architecture.md")
                    arch_text = svc_arch.read_text(encoding="utf-8") if svc_arch.exists() else ""
                    code_review = review_generated_code(
                        spec_md=spec_md,
                        code_changes=[
                            {"file": c.file, "action": c.action,
                             "content": c.content, "explanation": c.explanation}
                            for c in code_plan.changes
                        ],
                        architecture_spec=arch_text,
                        config=svc.config,
                        project_root=svc.path,
                    )
                    if not code_review.approved:
                        self._step(
                            f"  [{svc.name}] Code review flagged issues",
                            f"score={code_review.score:.2f}, "
                            f"{len(code_review.blocking_issues)} blocking",
                        )
                        self._write(
                            f"services/{svc.name}_code_review.md",
                            f"# Code review: {svc.name}\n\n"
                            + "\n".join(f"- {i}" for i in code_review.blocking_issues),
                        )

                    # ── Secrets scan ──────────────────────────────────────────
                    secrets_found = scan_for_secrets(code_plan.changes)
                    if secrets_found:
                        self._step(
                            f"  [{svc.name}] SECRETS DETECTED — skipping PR",
                            "; ".join(secrets_found),
                        )
                        continue

                    # Create branch + PR if GitHub is configured
                    try:
                        gh = GitHubAdapter(
                            repo=svc.config.repo,
                            token=__import__("os").environ.get("GITHUB_TOKEN", ""),
                        )
                        branch = f"speckit/{slug}-{svc.name}"
                        import subprocess
                        subprocess.run(["git", "-C", str(svc.path), "checkout", "-b", branch],
                                       capture_output=True)
                        subprocess.run(["git", "-C", str(svc.path), "add", "."],
                                       capture_output=True)
                        subprocess.run(
                            ["git", "-C", str(svc.path), "commit", "-m",
                             f"feat({svc.name}): {feature_name}"],
                            capture_output=True,
                        )
                        subprocess.run(["git", "-C", str(svc.path), "push", "-u",
                                        "origin", branch], capture_output=True)
                        review_note = (
                            "\n\n⚠️ Code review flagged issues — see `services/"
                            f"{svc.name}_code_review.md`."
                            if not code_review.approved else ""
                        )
                        pr = gh.create_pr(
                            title=f"feat({svc.name}): {feature_name}",
                            body=(
                                f"Auto-generated by speckit orchestrate.\n\n"
                                f"{spec_md[:1000]}{review_note}"
                            ),
                            head=branch,
                        )
                        result.pr_urls[svc.name] = pr.get("html_url", "")
                        self._step(f"  [{svc.name}] PR created", result.pr_urls[svc.name])
                    except Exception as e:
                        self._step(f"  [{svc.name}] PR skipped", str(e))

            # ── Stage 9: Final status ─────────────────────────────────────────
            cost_md = get_cost_summary_md()
            if cost_md:
                self._log_lines.append(cost_md)
                self._flush_log()

            self._step("Orchestration complete",
                       f"approved={result.approved} | "
                       f"services={len(result.services_affected)} | "
                       f"created={len(result.services_created)}")

            if slack:
                slack.pipeline_complete(
                    run_type="orchestrate",
                    name=feature_name,
                    run_dir=str(run_dir),
                    approved=result.approved,
                )

        except Exception as e:
            self._step("ORCHESTRATION FAILED", str(e))
            result.error = str(e)
            self._write(
                "FAILED.md",
                f"# Orchestration failed\n\n**Error:** {e}\n\n"
                "**Run log:**\n\n" + "\n".join(self._log_lines),
            )
            raise
        finally:
            clear_pipeline_cache()

        return result


# ── BugOrchestratePipeline ─────────────────────────────────────────────────────

class BugOrchestratePipeline(_PipelineBase):
    """Cross-service bug-fix pipeline."""

    def run(
        self,
        issue_number: int,
        issue_title: str = "",
        issue_body: str = "",
        build: bool = False,
    ) -> BugOrchestrateResult:
        from speckit.core.agents import (
            classify_issue,
            analyze_bug_service_impact,
            check_contract_compatibility,
            write_cross_service_build_plan,
            write_integration_test_plan,
            reset_cost_log,
            get_cost_summary_md,
        )
        from speckit.core.global_config import topological_sort
        from speckit.modes.bug_fix import BugFixPipeline
        from speckit.adapters.slack import SlackNotifier

        reset_cost_log()

        slug = self._slug(f"bug-{issue_number}")
        run_dir = self.gc.runs_dir / f"orchestrate-{slug}"
        run_dir.mkdir(parents=True, exist_ok=True)
        self._run_dir = run_dir

        services_dir = run_dir / "services"
        services_dir.mkdir(exist_ok=True)

        self._step("Starting bug orchestrator pipeline", f"issue #{issue_number}")

        slack = None
        if self.gc.services:
            try:
                slack = SlackNotifier(self.gc.services[0].config)
            except Exception:
                pass

        result = BugOrchestrateResult(
            issue_number=issue_number,
            run_dir=run_dir,
            approved=False,
        )
        agent_config = self.gc.services[0].config if self.gc.services else None
        if not agent_config:
            raise RuntimeError("No services registered in global.sdd.config.yml")

        try:
            # ── Stage 1: Load context + classify ─────────────────────────────
            self._step("Stage 1 — Loading context")
            services_summary = self._load_services_summary(self.gc.services)
            contract_specs = self._load_contract_specs()

            # Fetch issue from GitHub if title/body not provided
            if not issue_title:
                try:
                    from speckit.adapters.github import GitHubAdapter
                    gh = GitHubAdapter(
                        repo=agent_config.repo,
                        token=__import__("os").environ.get("GITHUB_TOKEN", ""),
                    )
                    issue = gh.get_issue(issue_number)
                    issue_title = issue.title
                    issue_body = issue.body
                except Exception as e:
                    self._step("GitHub fetch failed — using provided title/body", str(e))

            self._step("Classifying issue")
            classification = classify_issue(
                issue_title=issue_title,
                issue_body=issue_body,
                config=agent_config,
                project_root=self.gc.root,
            )
            self._step("Classification complete",
                       f"type={classification.issue_type} severity={classification.severity}")

            # ── Stage 2: Bug service impact ───────────────────────────────────
            self._step("Stage 2 — Analyzing bug service impact")
            impact = analyze_bug_service_impact(
                issue_title=issue_title,
                issue_body=issue_body,
                classification=classification,
                services_summary=services_summary,
                config=agent_config,
                project_root=self.gc.root,
            )
            affected_names = [i.service_name for i in impact.impacts]
            result.services_affected = affected_names

            impact_lines = [f"# Bug impact: issue #{issue_number} — {issue_title}\n"]
            for imp in impact.impacts:
                impact_lines.append(
                    f"## {imp.service_name} — {imp.scope}\n{imp.reason}\n"
                    f"**Contracts touched:** {', '.join(imp.affected_contracts) or 'none'}"
                )
            self._write("01_impact_analysis.md", "\n\n".join(impact_lines))
            result.artifacts.append("01_impact_analysis.md")
            self._step("Impact analysis complete", f"{len(affected_names)} service(s) affected")

            # ── Stage 3: Contract compatibility ──────────────────────────────
            self._step("Stage 3 — Checking contract compatibility")
            compat = check_contract_compatibility(
                feature_name=f"bug fix #{issue_number}: {issue_title}",
                impacted_services=impact.impacts,
                contract_specs=contract_specs,
                config=agent_config,
                project_root=self.gc.root,
            )
            result.contract_verdict = compat.verdict
            compat_md = self._compat_to_md(compat)
            self._write("02_contract_compatibility.md", compat_md)
            result.artifacts.append("02_contract_compatibility.md")
            self._step("Compatibility check complete", f"verdict={compat.verdict}")

            # ── Stage 4: Per-service BugFixPipelines ──────────────────────────
            self._step("Stage 4 — Running per-service bug fix pipelines")
            service_map = {s.name: s for s in self.gc.services}
            affected_entries = [service_map[n] for n in affected_names if n in service_map]
            sorted_services = topological_sort(affected_entries)

            service_build_plans: dict[str, str] = {}
            service_feature_specs: dict[str, str] = {}

            for svc in sorted_services:
                self._step(f"Running bug fix pipeline", svc.name)

                svc_run_dir = services_dir / svc.name
                svc_run_dir.mkdir(parents=True, exist_ok=True)
                svc_config = copy.deepcopy(svc.config)
                svc_config.paths.runs = str(svc_run_dir.relative_to(svc.path)) if svc_run_dir.is_relative_to(svc.path) else str(svc_run_dir)

                def _on_svc_step(title: str, detail: str = "", _name: str = svc.name) -> None:
                    self._step(f"  [{_name}] {title}", detail)

                pipeline = BugFixPipeline(
                    config=svc_config,
                    project_root=svc.path,
                    on_step=_on_svc_step,
                )
                from speckit.adapters.github import Issue
                svc_issue = Issue(
                    number=issue_number,
                    title=issue_title,
                    body=issue_body,
                )
                svc_result = pipeline.run(issue_number=issue_number, issue=svc_issue)
                result.service_results[svc.name] = svc_result

                report_path = svc_result.run_dir / "02_bug_report.md"
                plan_path = svc_result.run_dir / "03_test_plan.md"
                if report_path.exists():
                    service_feature_specs[svc.name] = report_path.read_text(encoding="utf-8")
                if plan_path.exists():
                    service_build_plans[svc.name] = plan_path.read_text(encoding="utf-8")

                self._step(f"Pipeline complete",
                           f"{svc.name} approved={svc_result.approved}")

            result.approved = all(
                r.approved for r in result.service_results.values()
            )

            # ── Stage 5: Cross-service fix plan ──────────────────────────────
            self._step("Stage 5 — Writing cross-service fix plan")
            fix_plan_md = write_cross_service_build_plan(
                feature_name=f"bug fix #{issue_number}: {issue_title}",
                service_build_plans=service_build_plans,
                dependency_order=[s.name for s in sorted_services],
                config=agent_config,
                project_root=self.gc.root,
            )
            self._write("05_cross_service_fix_plan.md", fix_plan_md)
            result.artifacts.append("05_cross_service_fix_plan.md")

            # ── Stage 6: Integration test plan ───────────────────────────────
            self._step("Stage 6 — Writing integration test plan")
            integration_test_md = write_integration_test_plan(
                feature_name=f"bug fix #{issue_number}: {issue_title}",
                service_feature_specs=service_feature_specs,
                contract_specs=contract_specs,
                config=agent_config,
                project_root=self.gc.root,
            )
            self._write("06_integration_test_plan.md", integration_test_md)
            result.artifacts.append("06_integration_test_plan.md")

            # ── Stage 7: Final status ─────────────────────────────────────────
            cost_md = get_cost_summary_md()
            if cost_md:
                self._log_lines.append(cost_md)
                self._flush_log()

            self._step("Bug orchestration complete",
                       f"approved={result.approved} services={len(result.services_affected)}")

            if slack:
                slack.pipeline_complete(
                    run_type="orchestrate-bug",
                    name=f"#{issue_number}: {issue_title}",
                    run_dir=str(run_dir),
                    approved=result.approved,
                )

        except Exception as e:
            self._step("BUG ORCHESTRATION FAILED", str(e))
            result.error = str(e)
            self._write(
                "FAILED.md",
                f"# Bug orchestration failed\n\n**Error:** {e}\n\n"
                "**Run log:**\n\n" + "\n".join(self._log_lines),
            )
            raise

        return result
