"""
Brownfield init pipeline — bootstrap speckit for an existing multi-service codebase.

Designed for projects that have code but no spec files. Runs automatically across
every detected service in a monorepo.

Stages:
  0.  Service discovery — find services by indicator files (go.mod, package.json, etc.)
  1.  Per-service spec generation — module specs + architecture.md (reuses scan agents)
  2.  Per-service security audit — specs/security.md
  3.  Per-service scalability audit — specs/scalability.md
  4.  Per-service ambiguity detection — specs/ambiguities.md
  5.  Per-service sdd.config.yml bootstrap (if missing)
  6.  Contract extraction — global-specs/contracts/{service}.contract.md
  7.  Write global.sdd.config.yml
  8.  Health report — per-service scores + prioritised remediation list
"""
from __future__ import annotations

import re as _re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional


# ── Service indicator files ────────────────────────────────────────────────────

_SERVICE_INDICATORS = {
    "package.json", "go.mod", "requirements.txt", "pyproject.toml",
    "Cargo.toml", "pom.xml", "build.gradle", "build.gradle.kts",
    "composer.json", "Gemfile",
}

_SKIP_DIRS = {
    "node_modules", "__pycache__", ".git", ".venv", "venv", "env",
    "dist", "build", ".next", ".nuxt", "target", "vendor",
    ".speckit", "runs", "specs", ".pytest_cache", "coverage",
    ".mypy_cache", ".ruff_cache", ".idea", ".vscode",
}

_CODE_EXTS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".java", ".rb",
    ".rs", ".cs", ".cpp", ".c", ".h", ".swift", ".kt",
}

# File name keywords that suggest API/route/handler files (for contract extraction)
_ROUTE_KEYWORDS = {
    "route", "router", "routes", "api", "handler", "handlers",
    "controller", "controllers", "endpoint", "endpoints",
    "views", "server", "app", "main",
}

# File name keywords prioritised for security/scalability scans
_SECURITY_PRIORITY_NAMES = {
    "auth", "login", "password", "token", "session", "user", "users",
    "admin", "api", "route", "handler", "controller", "middleware",
    "db", "database", "query", "sql", "model",
}

_SCALE_PRIORITY_NAMES = {
    "db", "database", "query", "model", "repository", "repo",
    "cache", "redis", "queue", "worker", "job", "async",
    "service", "handler", "controller", "store",
}


# ── result dataclass ───────────────────────────────────────────────────────────

@dataclass
class ServiceHealth:
    name: str
    path: Path
    security_score: float = 0.0
    security_risk: str = "unknown"
    security_findings: int = 0
    scalability_score: float = 0.0
    scalability_concerns: int = 0
    ambiguities: int = 0
    spec_files_written: list[str] = field(default_factory=list)
    contract_written: bool = False
    config_written: bool = False
    error: str = ""


@dataclass
class BrownfieldInitResult:
    root: Path
    services_discovered: list[str]
    services_failed: list[str]
    health: dict[str, ServiceHealth]   # service_name → ServiceHealth
    global_config_written: bool = False
    health_report_path: Optional[Path] = None
    error: str = ""


# ── pipeline ───────────────────────────────────────────────────────────────────

class BrownfieldInitPipeline:
    """
    Bootstrap speckit for an existing codebase with no spec files.

    Usage:
        pipeline = BrownfieldInitPipeline(root=Path("."), on_step=print_fn)
        result = pipeline.run()
    """

    def __init__(
        self,
        root: Path,
        on_step: Optional[Callable[[str, str], None]] = None,
        force: bool = False,
        only_services: Optional[list[str]] = None,
    ):
        self.root = root.resolve()
        self.on_step = on_step or (lambda title, detail: None)
        self.force = force
        # If set, only process services whose directory name is in this list.
        self.only_services: Optional[list[str]] = (
            [s.strip() for s in only_services] if only_services else None
        )
        self._log_lines: list[str] = []
        self._run_dir: Optional[Path] = None

    def _step(self, title: str, detail: str = "") -> None:
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        line = f"[{ts}] {title}{': ' + detail if detail else ''}"
        self._log_lines.append(line)
        if self._run_dir:
            (self._run_dir / "brownfield_init_log.md").write_text(
                "# Brownfield init log\n\n" + "\n".join(self._log_lines) + "\n",
                encoding="utf-8",
            )
        self.on_step(title, detail)

    # ── helpers ────────────────────────────────────────────────────────────────

    def _discover_services(self) -> list[Path]:
        """
        Walk root (up to 3 levels deep) and find directories with service indicators.
        Returns a de-duplicated, sorted list of service root paths.
        """
        candidates: set[Path] = set()

        for depth in range(1, 4):
            pattern = "/".join(["*"] * depth)
            for indicator in _SERVICE_INDICATORS:
                for indicator_path in self.root.glob(f"{pattern}/{indicator}"):
                    # The service root is the directory that contains the indicator
                    svc_root = indicator_path.parent
                    if any(part in _SKIP_DIRS for part in svc_root.parts):
                        continue
                    # Must have actual source code
                    if self._has_source_code(svc_root):
                        candidates.add(svc_root)

        # De-duplicate: if both parent and child are candidates, keep the parent
        deduped: list[Path] = []
        sorted_candidates = sorted(candidates)
        for path in sorted_candidates:
            if not any(path != other and path.is_relative_to(other)
                       for other in sorted_candidates):
                deduped.append(path)

        return deduped

    def _has_source_code(self, path: Path) -> bool:
        count = 0
        for p in path.rglob("*"):
            if p.is_file() and p.suffix in _CODE_EXTS:
                if not any(part in _SKIP_DIRS for part in p.parts):
                    count += 1
                    if count >= 2:
                        return True
        return False

    def _collect_source_files(
        self,
        svc_path: Path,
        priority_names: set[str],
        max_files: int = 12,
        max_chars: int = 1200,
    ) -> dict[str, str]:
        """Collect source files, prioritising files with names in priority_names."""
        all_files: list[Path] = []
        for p in sorted(svc_path.rglob("*")):
            if not p.is_file() or p.suffix not in _CODE_EXTS:
                continue
            if any(part in _SKIP_DIRS for part in p.parts):
                continue
            all_files.append(p)

        # Sort: priority files first
        def _priority(p: Path) -> int:
            stem = p.stem.lower()
            return 0 if any(kw in stem for kw in priority_names) else 1

        all_files.sort(key=_priority)

        result: dict[str, str] = {}
        for p in all_files[:max_files]:
            try:
                rel = str(p.relative_to(svc_path))
                result[rel] = p.read_text(encoding="utf-8", errors="replace")[:max_chars]
            except Exception:
                pass
        return result

    def _collect_route_files(self, svc_path: Path) -> dict[str, str]:
        return self._collect_source_files(
            svc_path, priority_names=_ROUTE_KEYWORDS, max_files=8, max_chars=1500
        )

    def _detect_language(self, svc_path: Path) -> str:
        counts: dict[str, int] = {}
        for p in svc_path.rglob("*"):
            if p.is_file() and p.suffix in _CODE_EXTS:
                if not any(part in _SKIP_DIRS for part in p.parts):
                    counts[p.suffix] = counts.get(p.suffix, 0) + 1
        if not counts:
            return "unknown"
        ext = max(counts, key=counts.__getitem__)
        return {
            ".py": "Python", ".ts": "TypeScript", ".tsx": "TypeScript",
            ".js": "JavaScript", ".jsx": "JavaScript", ".go": "Go",
            ".java": "Java", ".rb": "Ruby", ".rs": "Rust",
            ".cs": "C#", ".swift": "Swift", ".kt": "Kotlin",
        }.get(ext, ext.lstrip(".").upper())

    def _group_modules(self, svc_path: Path) -> dict[str, list[Path]]:
        """Group source files by top-level subdirectory (same as scan command)."""
        modules: dict[str, list[Path]] = {}
        for p in sorted(svc_path.rglob("*")):
            if not p.is_file() or p.suffix not in _CODE_EXTS:
                continue
            if any(part in _SKIP_DIRS for part in p.parts):
                continue
            rel = p.relative_to(svc_path)
            parts = rel.parts
            module = parts[0].rstrip("/") if len(parts) > 1 else "core"
            modules.setdefault(module, []).append(p)
        return modules

    def _read_module_files(
        self, files: list[Path], svc_path: Path, max_chars: int = 1000
    ) -> dict[str, str]:
        result = {}
        for p in files[:10]:
            try:
                rel = str(p.relative_to(svc_path))
                result[rel] = p.read_text(encoding="utf-8", errors="replace")[:max_chars]
            except Exception:
                pass
        return result

    def _write_sdd_config(self, svc_path: Path, svc_name: str, language: str) -> bool:
        """Write a minimal sdd.config.yml if one doesn't exist yet."""
        config_path = svc_path / "sdd.config.yml"
        if config_path.exists() and not self.force:
            return False
        pkg_map = {
            "Python": "pip", "TypeScript": "pnpm", "JavaScript": "pnpm",
            "Go": "go", "Java": "maven", "Rust": "cargo",
        }
        pkg = pkg_map.get(language, "—")
        config_path.write_text(
            f"project_name: {svc_name}\n"
            f"mode: brownfield-with-specs\n"
            f"primary_language: {language.lower()}\n"
            f"package_manager: {pkg}\n"
            f"repo: \"\"\n\n"
            f"paths:\n"
            f"  specs: ./specs\n"
            f"  runs: ./runs\n\n"
            f"agent:\n"
            f"  model: claude-sonnet-4-6\n"
            f"  judge_threshold: 0.85\n"
            f"  max_judge_iterations: 3\n"
            f"  max_spec_read_files: 6\n\n"
            f"hooks:\n"
            f"  on_pr_created: \"\"\n"
            f"  on_judge_failed: \"\"\n",
            encoding="utf-8",
        )
        return True

    # ── main entry ─────────────────────────────────────────────────────────────

    def run(
        self,
        skip_audit: bool = False,
        skip_contracts: bool = False,
    ) -> BrownfieldInitResult:
        from speckit.core.agents import (
            generate_module_spec,
            generate_architecture_spec,
            audit_security,
            audit_scalability,
            detect_ambiguities,
            extract_service_contracts,
            reset_cost_log,
            get_cost_summary_md,
        )

        reset_cost_log()

        # Bootstrap run dir in root
        self._run_dir = self.root / "runs" / "brownfield-init"
        self._run_dir.mkdir(parents=True, exist_ok=True)

        result = BrownfieldInitResult(
            root=self.root,
            services_discovered=[],
            services_failed=[],
            health={},
        )

        self._step("Starting brownfield init", str(self.root))

        # ── Stage 0: Discover services ────────────────────────────────────────
        self._step("Stage 0 — Discovering services")
        service_paths = self._discover_services()

        if not service_paths:
            self._step("No services found",
                       "Ensure each service has package.json / go.mod / requirements.txt")
            result.error = "No services discovered"
            return result

        service_names = [p.name for p in service_paths]
        result.services_discovered = service_names
        self._step(
            f"Found {len(service_paths)} service(s)",
            " | ".join(service_names),
        )

        # Load a minimal config for agent calls (use first service's config if available,
        # otherwise build one from root)
        agent_config = self._load_or_build_config(service_paths[0])

        # ── Stages 1–5: Per-service processing ───────────────────────────────
        if self.only_services:
            skipped = [p.name for p in service_paths if p.name not in self.only_services]
            service_paths = [p for p in service_paths if p.name in self.only_services]
            if not service_paths:
                available = [p.name for p in result.health.values()] if result.health else \
                    [p.name for p in self._discover_services()]
                result.error = (
                    f"No matching services for --service filter {self.only_services}. "
                    f"Available: {available}"
                )
                return result
            if skipped:
                self._step(f"  Skipping (--service filter)", ", ".join(skipped))

        for svc_path in service_paths:
            svc_name = svc_path.name
            health = ServiceHealth(name=svc_name, path=svc_path)
            result.health[svc_name] = health

            self._step(f"Processing service", svc_name)

            try:
                svc_config = self._load_or_build_config(svc_path)
                language = self._detect_language(svc_path)
                specs_dir = svc_path / "specs"
                specs_dir.mkdir(parents=True, exist_ok=True)
                modules_dir = specs_dir / "modules"
                modules_dir.mkdir(exist_ok=True)

                # ── Stage 1: Module specs + architecture.md ───────────────────
                self._step(f"  [{svc_name}] Generating module specs")
                modules = self._group_modules(svc_path)
                modules_summary_lines: list[str] = []
                sample_files: dict[str, str] = {}

                for module_name, files in sorted(modules.items()):
                    out_file = modules_dir / f"{module_name}.md"
                    if out_file.exists() and not self.force:
                        modules_summary_lines.append(f"- **{module_name}**: (pre-existing)")
                        health.spec_files_written.append(f"specs/modules/{module_name}.md")
                        continue

                    file_contents = self._read_module_files(files, svc_path)
                    if len(sample_files) < 6:
                        sample_files.update(dict(list(file_contents.items())[:2]))

                    try:
                        spec_md = generate_module_spec(
                            module_name=module_name,
                            file_contents=file_contents,
                            language=language,
                            config=svc_config,
                            project_root=svc_path,
                        )
                        out_file.write_text(spec_md.strip(), encoding="utf-8")
                        health.spec_files_written.append(f"specs/modules/{module_name}.md")
                        modules_summary_lines.append(f"- **{module_name}**: {len(files)} files")
                    except Exception as e:
                        self._step(f"  [{svc_name}] Module spec failed", f"{module_name}: {e}")

                arch_out = specs_dir / "architecture.md"
                arch_spec = ""
                if arch_out.exists() and not self.force:
                    arch_spec = arch_out.read_text(encoding="utf-8")
                    self._step(f"  [{svc_name}] architecture.md exists — reusing")
                else:
                    try:
                        arch_spec = generate_architecture_spec(
                            project_name=svc_name,
                            language=language,
                            modules_summary="\n".join(modules_summary_lines),
                            sample_files=sample_files,
                            config=svc_config,
                            project_root=svc_path,
                        )
                        arch_out.write_text(arch_spec.strip(), encoding="utf-8")
                        health.spec_files_written.append("specs/architecture.md")
                        self._step(f"  [{svc_name}] architecture.md written")
                    except Exception as e:
                        self._step(f"  [{svc_name}] architecture.md failed", str(e))

                # ── Stage 2: Security audit ───────────────────────────────────
                if not skip_audit:
                    self._step(f"  [{svc_name}] Security audit")
                    sec_out = specs_dir / "security.md"
                    if sec_out.exists() and not self.force:
                        self._step(f"  [{svc_name}] security.md exists — skipping audit")
                    else:
                        try:
                            sec_files = self._collect_source_files(
                                svc_path, _SECURITY_PRIORITY_NAMES
                            )
                            sec_result = audit_security(
                                service_name=svc_name,
                                code_files=sec_files,
                                architecture_spec=arch_spec,
                                config=svc_config,
                                project_root=svc_path,
                            )
                            health.security_score = sec_result.overall_score
                            health.security_risk = sec_result.risk_level
                            health.security_findings = len(sec_result.findings)

                            # Write security.md in spec format for future agents
                            sec_md_lines = [
                                f"# Security specification: {svc_name}\n",
                                f"**Risk level:** {sec_result.risk_level}  "
                                f"**Score:** {sec_result.overall_score:.2f}\n",
                                f"## Summary\n{sec_result.summary}\n",
                                "## Known vulnerabilities and required remediations\n",
                            ]
                            for f in sec_result.findings:
                                sec_md_lines.append(
                                    f"### [{f.severity.upper()}] {f.category} — {f.location}\n"
                                    f"{f.description}\n\n"
                                    f"**Fix:** {f.remediation}\n"
                                )
                            if not sec_result.findings:
                                sec_md_lines.append("No significant vulnerabilities found.\n")

                            sec_md_lines += [
                                "\n## Security NFRs (auto-enforced by speckit)\n",
                                "- All endpoints must validate and sanitize input\n",
                                "- No hardcoded credentials — all secrets via environment variables\n",
                                "- Auth required on all non-public endpoints\n",
                                "- Rate limiting on all public-facing endpoints\n",
                                "- All SQL via parameterised queries or ORM — no string interpolation\n",
                                "- Errors must not expose stack traces or internal paths in responses\n",
                            ]
                            sec_out.write_text("".join(sec_md_lines), encoding="utf-8")
                            health.spec_files_written.append("specs/security.md")
                            self._step(
                                f"  [{svc_name}] Security audit complete",
                                f"risk={sec_result.risk_level} findings={health.security_findings}",
                            )
                        except Exception as e:
                            self._step(f"  [{svc_name}] Security audit failed", str(e))
                            health.error = f"security: {e}"

                # ── Stage 3: Scalability audit ────────────────────────────────
                if not skip_audit:
                    self._step(f"  [{svc_name}] Scalability audit")
                    scale_out = specs_dir / "scalability.md"
                    if scale_out.exists() and not self.force:
                        self._step(f"  [{svc_name}] scalability.md exists — skipping")
                    else:
                        try:
                            scale_files = self._collect_source_files(
                                svc_path, _SCALE_PRIORITY_NAMES
                            )
                            scale_result = audit_scalability(
                                service_name=svc_name,
                                code_files=scale_files,
                                architecture_spec=arch_spec,
                                config=svc_config,
                                project_root=svc_path,
                            )
                            health.scalability_score = scale_result.overall_score
                            health.scalability_concerns = len(scale_result.concerns)

                            scale_lines = [
                                f"# Scalability notes: {svc_name}\n",
                                f"**Score:** {scale_result.overall_score:.2f}\n",
                                f"## Summary\n{scale_result.summary}\n",
                                "## Concerns\n",
                            ]
                            for c in scale_result.concerns:
                                scale_lines.append(
                                    f"### [{c.severity.upper()}] {c.category} — {c.location}\n"
                                    f"{c.description}\n\n"
                                    f"**Fix:** {c.remediation}\n"
                                )
                            if not scale_result.concerns:
                                scale_lines.append("No significant scalability concerns found.\n")

                            scale_out.write_text("".join(scale_lines), encoding="utf-8")
                            health.spec_files_written.append("specs/scalability.md")
                            self._step(
                                f"  [{svc_name}] Scalability audit complete",
                                f"score={scale_result.overall_score:.2f} "
                                f"concerns={health.scalability_concerns}",
                            )
                        except Exception as e:
                            self._step(f"  [{svc_name}] Scalability audit failed", str(e))

                # ── Stage 4: Ambiguity detection ──────────────────────────────
                if not skip_audit:
                    self._step(f"  [{svc_name}] Ambiguity detection")
                    amb_out = specs_dir / "ambiguities.md"
                    if amb_out.exists() and not self.force:
                        self._step(f"  [{svc_name}] ambiguities.md exists — skipping")
                    else:
                        try:
                            # Build module specs summary from what we just wrote
                            mod_summary_parts = []
                            for md_file in sorted(modules_dir.glob("*.md")):
                                text = md_file.read_text(encoding="utf-8")
                                mod_summary_parts.append(
                                    f"### {md_file.stem}\n{text[:300]}"
                                )
                            mod_summary = "\n\n".join(mod_summary_parts)

                            # Sample code: first 1500 chars from a few key files
                            sample_files_for_amb = self._collect_source_files(
                                svc_path, _SECURITY_PRIORITY_NAMES, max_files=3, max_chars=500
                            )
                            sample_code = "\n\n".join(
                                f"// {k}\n{v}" for k, v in sample_files_for_amb.items()
                            )

                            amb_result = detect_ambiguities(
                                service_name=svc_name,
                                architecture_spec=arch_spec,
                                module_specs_summary=mod_summary,
                                sample_code=sample_code,
                                config=svc_config,
                                project_root=svc_path,
                            )
                            health.ambiguities = len(amb_result.ambiguities)

                            amb_lines = [
                                f"# Architectural ambiguities: {svc_name}\n",
                                f"## Summary\n{amb_result.summary}\n",
                                f"## Ambiguities ({len(amb_result.ambiguities)} found)\n",
                            ]
                            for a in amb_result.ambiguities:
                                locs = ", ".join(a.locations) if a.locations else "unspecified"
                                amb_lines.append(
                                    f"### [{a.severity.upper()}] {a.category}\n"
                                    f"**Locations:** {locs}\n\n"
                                    f"{a.description}\n\n"
                                    f"**Recommendation:** {a.recommendation}\n"
                                )
                            if not amb_result.ambiguities:
                                amb_lines.append("No significant ambiguities detected.\n")

                            amb_out.write_text("".join(amb_lines), encoding="utf-8")
                            health.spec_files_written.append("specs/ambiguities.md")
                            self._step(
                                f"  [{svc_name}] Ambiguity detection complete",
                                f"{health.ambiguities} ambiguities found",
                            )
                        except Exception as e:
                            self._step(f"  [{svc_name}] Ambiguity detection failed", str(e))

                # ── Stage 5: Bootstrap sdd.config.yml ────────────────────────
                wrote = self._write_sdd_config(svc_path, svc_name, language)
                if wrote:
                    health.config_written = True
                    health.spec_files_written.append("sdd.config.yml")
                    self._step(f"  [{svc_name}] sdd.config.yml written")

            except Exception as e:
                self._step(f"  [{svc_name}] FAILED", str(e))
                health.error = str(e)
                result.services_failed.append(svc_name)

        # ── Stage 6: Contract extraction ──────────────────────────────────────
        if not skip_contracts:
            self._step("Stage 6 — Extracting service contracts")
            contracts_dir = self.root / "global-specs" / "contracts"
            contracts_dir.mkdir(parents=True, exist_ok=True)

            for svc_path in service_paths:
                svc_name = svc_path.name
                contract_out = contracts_dir / f"{svc_name}.contract.md"
                if contract_out.exists() and not self.force:
                    self._step(f"  [{svc_name}] Contract exists — skipping")
                    continue
                try:
                    route_files = self._collect_route_files(svc_path)
                    if not route_files:
                        self._step(f"  [{svc_name}] No route files found — skipping contract")
                        continue
                    svc_config = self._load_or_build_config(svc_path)
                    contract_md = extract_service_contracts(
                        service_name=svc_name,
                        code_files=route_files,
                        config=svc_config,
                        project_root=svc_path,
                    )
                    contract_out.write_text(contract_md.strip(), encoding="utf-8")
                    result.health[svc_name].contract_written = True
                    self._step(f"  [{svc_name}] Contract written",
                               str(contract_out.relative_to(self.root)))
                except Exception as e:
                    self._step(f"  [{svc_name}] Contract extraction failed", str(e))

        # ── Stage 7: Write global.sdd.config.yml ──────────────────────────────
        self._step("Stage 7 — Writing global.sdd.config.yml")
        global_config_path = self.root / "global.sdd.config.yml"
        if global_config_path.exists() and not self.force:
            self._step("global.sdd.config.yml already exists — skipping")
        else:
            self._write_global_config(service_paths)
            result.global_config_written = True
            self._step("global.sdd.config.yml written")

        # ── Stage 8: Health report ─────────────────────────────────────────────
        self._step("Stage 8 — Writing health report")
        report_path = self._write_health_report(result)
        result.health_report_path = report_path

        cost_md = get_cost_summary_md()
        if cost_md:
            self._log_lines.append(cost_md)
        self._step("Brownfield init complete",
                   f"{len(result.services_discovered)} services | "
                   f"{len(result.services_failed)} failed")

        return result

    # ── helpers ────────────────────────────────────────────────────────────────

    def _load_or_build_config(self, svc_path: Path):
        """Load existing sdd.config.yml or return a synthetic config object."""
        try:
            from speckit.core.config import load_config
            return load_config(svc_path)
        except Exception:
            from speckit.core.config import SpeckitConfig, PathsConfig, AgentConfig, ProjectMode
            return SpeckitConfig(
                project_name=svc_path.name,
                primary_language="unknown",
                repo="",
                mode=ProjectMode.BROWNFIELD_NO_SPECS,
                paths=PathsConfig(specs="./specs", runs="./runs"),
                agent=AgentConfig(
                    model="claude-sonnet-4-6",
                    judge_threshold=0.85,
                    max_judge_iterations=3,
                    max_spec_read_files=6,
                ),
            )

    def _write_global_config(self, service_paths: list[Path]) -> None:
        lines = [
            f"project_name: {self.root.name}\n",
            f"global_specs: ./global-specs\n",
            f"contracts_dir: ./global-specs/contracts\n",
            f"runs_dir: ./runs\n\n",
            f"agent:\n",
            f"  model: claude-sonnet-4-6\n",
            f"  judge_threshold: 0.85\n",
            f"  max_judge_iterations: 3\n\n",
            f"services:\n",
        ]
        for svc_path in service_paths:
            rel = svc_path.relative_to(self.root)
            lines.append(
                f"  - name: {svc_path.name}\n"
                f"    path: ./{rel}\n"
                f"    depends_on: []  # TODO: fill in dependencies\n"
            )
        (self.root / "global.sdd.config.yml").write_text("".join(lines), encoding="utf-8")

    def _write_health_report(self, result: BrownfieldInitResult) -> Path:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines = [
            f"# Speckit brownfield init — health report\n",
            f"**Generated:** {now}  **Root:** {result.root}\n\n",
            "## Service health summary\n",
            "| Service | Security score | Risk | Sec findings | Scale score | Concerns | Ambiguities |",
            "|---------|---------------|------|-------------|-------------|----------|-------------|",
        ]
        for name, h in result.health.items():
            lines.append(
                f"| {name} | {h.security_score:.2f} | {h.security_risk} "
                f"| {h.security_findings} | {h.scalability_score:.2f} "
                f"| {h.scalability_concerns} | {h.ambiguities} |"
            )

        # Prioritised remediation
        lines.append("\n## Prioritised remediation order\n")
        all_items: list[tuple[str, str, str, str]] = []  # (priority_key, service, category, detail)

        for name, h in result.health.items():
            # Parse security findings from written file
            sec_file = h.path / "specs" / "security.md"
            if sec_file.exists():
                text = sec_file.read_text(encoding="utf-8")
                for match in _re.finditer(
                    r"### \[(CRITICAL|HIGH|MEDIUM|LOW)\] (\w+) — (.+)", text
                ):
                    sev, cat, loc = match.groups()
                    priority = {"CRITICAL": "0", "HIGH": "1", "MEDIUM": "2", "LOW": "3"}[sev]
                    all_items.append((priority, name, f"Security: {cat}", loc))

            # Parse scalability concerns from written file
            scale_file = h.path / "specs" / "scalability.md"
            if scale_file.exists():
                text = scale_file.read_text(encoding="utf-8")
                for match in _re.finditer(
                    r"### \[(CRITICAL|HIGH|MEDIUM|LOW)\] (\w+) — (.+)", text
                ):
                    sev, cat, loc = match.groups()
                    priority = {"CRITICAL": "0", "HIGH": "1", "MEDIUM": "2", "LOW": "3"}[sev]
                    all_items.append((priority, name, f"Scalability: {cat}", loc))

        all_items.sort(key=lambda x: x[0])
        if all_items:
            for _, svc, cat, loc in all_items[:20]:
                lines.append(f"1. **[{svc}]** {cat} — {loc}")
        else:
            lines.append("No critical/high issues found across all services.")

        lines += [
            "\n## What to do next\n",
            "1. Review generated specs in `specs/` for each service\n",
            "2. Fix any issues flagged in `specs/security.md` and `specs/scalability.md`\n",
            "3. Fill in `depends_on` in `global.sdd.config.yml`\n",
            "4. Run `speckit index` in each service directory\n",
            "5. Run `speckit orchestrate --name \"your feature\"` from the monorepo root\n",
        ]

        report_path = self._run_dir / "health_report.md"
        report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return report_path
