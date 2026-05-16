"""
speckit MCP server — exposes speckit as a Model Context Protocol server.

Claude Pro (VS Code extension) connects to this server and gains 14 tools:
  - search_specs, list_specs, get_spec, write_spec, judge_spec
  - get_issue, list_issues, add_issue_comment, create_pr
  - run_bug_fix_pipeline, run_feature_pipeline, get_pipeline_status
  - run_tests, get_run_artifact, list_runs, get_project_info

Transport: stdio — Claude VS Code spawns this process and communicates
via JSON-RPC over stdin/stdout. No port or network config needed.

Install extras: pip install 'speckit[mcp]'

VS Code setup (Cmd+Shift+P → "Open User Settings (JSON)"):
  {
    "claude.mcpServers": {
      "speckit": {
        "command": "speckit",
        "args": ["mcp", "."],
        "cwd": "${workspaceFolder}"
      }
    }
  }
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _make_server(project_root: Path):
    """Build and return the configured MCP Server instance."""
    try:
        from mcp.server import Server
        import mcp.types as types
    except ImportError:
        raise ImportError(
            "MCP SDK not installed. Run: pip install 'speckit[mcp]'"
        ) from None

    from dotenv import load_dotenv
    load_dotenv(project_root / ".env", override=False)

    from speckit.core.config import load_config
    config = load_config(project_root)

    server = Server("speckit")

    # ── internal helpers ──────────────────────────────────────────────────────

    def _local_index():
        from speckit.adapters.local_index import LocalIndex
        return LocalIndex(project_root=project_root, project_name=config.project_name)

    def _github():
        from speckit.adapters.github import GitHubAdapter
        return GitHubAdapter(
            repo=os.environ.get("GITHUB_REPO", "") or config.repo,
            token=os.environ.get("GITHUB_TOKEN", ""),
        )

    def _spec_files():
        from speckit.core.spec_parser import discover_spec_files
        return discover_spec_files(project_root, config.paths.specs)

    def _run_dir(run_id: str) -> Path:
        return project_root / config.paths.runs.lstrip("./") / run_id

    def _ok(data: Any) -> list:
        text = (
            json.dumps(data, indent=2, ensure_ascii=False)
            if not isinstance(data, str)
            else data
        )
        return [types.TextContent(type="text", text=text)]

    def _err(msg: str) -> list:
        return [types.TextContent(type="text", text=json.dumps({"error": msg}))]

    # ── tool list ─────────────────────────────────────────────────────────────

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name="get_project_info",
                description=(
                    "Return project name, mode, language, spec count, and config summary. "
                    "Call this first to orient yourself before doing any work."
                ),
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            types.Tool(
                name="search_specs",
                description=(
                    "BM25 full-text search over all spec files. "
                    "Always search before writing or modifying code to find relevant specs."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Keywords, module names, or bug description",
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "Max results (default 5)",
                            "default": 5,
                        },
                    },
                    "required": ["query"],
                },
            ),
            types.Tool(
                name="list_specs",
                description="List all spec files with path, title, and one-line summary.",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            types.Tool(
                name="get_spec",
                description="Read the full content of a spec file by its project-relative path.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Relative path, e.g. 'specs/modules/auth.md'",
                        },
                    },
                    "required": ["path"],
                },
            ),
            types.Tool(
                name="write_spec",
                description=(
                    "Write or update a spec file in the specs/ directory. "
                    "Use after drafting or refining a spec. "
                    "Pair with judge_spec to validate quality before writing."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Relative path, e.g. 'specs/features/oauth2.md'",
                        },
                        "content": {
                            "type": "string",
                            "description": "Full markdown content of the spec",
                        },
                    },
                    "required": ["path", "content"],
                },
            ),
            types.Tool(
                name="judge_spec",
                description=(
                    "Run speckit's quality judge on any spec content. "
                    "Returns score (0-1), approved flag, specific gaps, and feedback. "
                    "Iterate: revise spec → judge_spec → revise → judge_spec until score >= threshold."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": "Markdown spec content to evaluate",
                        },
                        "spec_type": {
                            "type": "string",
                            "description": "Spec type",
                            "enum": ["module", "architecture", "feature", "security", "coding-standards"],
                        },
                        "spec_name": {
                            "type": "string",
                            "description": "Name for reporting, e.g. 'auth.md'",
                            "default": "spec.md",
                        },
                    },
                    "required": ["content", "spec_type"],
                },
            ),
            types.Tool(
                name="get_issue",
                description="Fetch a GitHub issue by number. Returns title, body, labels, state.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "number": {"type": "integer", "description": "GitHub issue number"},
                    },
                    "required": ["number"],
                },
            ),
            types.Tool(
                name="list_issues",
                description="List GitHub issues, optionally filtered by state or label.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "state": {
                            "type": "string",
                            "description": "'open' | 'closed' | 'all'",
                            "default": "open",
                        },
                        "label": {
                            "type": "string",
                            "description": "Filter by label name (optional)",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max issues to return (default 20)",
                            "default": 20,
                        },
                    },
                    "required": [],
                },
            ),
            types.Tool(
                name="add_issue_comment",
                description="Post a markdown comment to a GitHub issue.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "number": {"type": "integer", "description": "Issue number"},
                        "body": {"type": "string", "description": "Comment body (markdown)"},
                    },
                    "required": ["number", "body"],
                },
            ),
            types.Tool(
                name="create_pr",
                description=(
                    "Create a GitHub pull request. "
                    "Call after code is written and tests pass."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "branch": {"type": "string", "description": "Head branch name"},
                        "title": {"type": "string", "description": "PR title"},
                        "body": {"type": "string", "description": "PR description (markdown)"},
                        "base": {
                            "type": "string",
                            "description": "Base branch (defaults to repo default)",
                            "default": "",
                        },
                    },
                    "required": ["branch", "title", "body"],
                },
            ),
            types.Tool(
                name="run_bug_fix_pipeline",
                description=(
                    "Trigger the full automated bug-fix pipeline for a GitHub issue. "
                    "speckit handles: classify → search specs → bug report → judge loop → code → PR. "
                    "Returns run_id immediately — use get_pipeline_status to poll progress."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "issue_number": {"type": "integer", "description": "GitHub issue number"},
                    },
                    "required": ["issue_number"],
                },
            ),
            types.Tool(
                name="run_feature_pipeline",
                description=(
                    "Trigger the full feature pipeline: research → compat check → spec → judge loop → build plan. "
                    "Returns run_id immediately. Once completed, read artifacts with get_run_artifact "
                    "to get the spec and build plan, then code from them."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Feature name (short, kebab-friendly)",
                        },
                        "description": {
                            "type": "string",
                            "description": "What the feature should do",
                        },
                    },
                    "required": ["name", "description"],
                },
            ),
            types.Tool(
                name="get_pipeline_status",
                description=(
                    "Check the status of a pipeline run. "
                    "Returns: status ('starting'|'running'|'completed'|'failed'), "
                    "plus a list of available artifact files."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "run_id": {
                            "type": "string",
                            "description": "Run ID returned by run_bug_fix_pipeline or run_feature_pipeline",
                        },
                    },
                    "required": ["run_id"],
                },
            ),
            types.Tool(
                name="run_tests",
                description=(
                    "Run the project test suite and return output + pass/fail result. "
                    "Uses the test runner from sdd.config.yml (default: pytest)."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "Override test command (optional). Defaults to configured runner.",
                        },
                    },
                    "required": [],
                },
            ),
            types.Tool(
                name="get_run_artifact",
                description="Read a specific artifact file from a pipeline run.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "run_id": {
                            "type": "string",
                            "description": "Run ID, e.g. 'bug-fix-42' or 'feature-oauth2-login'",
                        },
                        "artifact": {
                            "type": "string",
                            "description": "Filename, e.g. '03_feature_spec.md' or '05_build_plan.md'",
                        },
                    },
                    "required": ["run_id", "artifact"],
                },
            ),
            types.Tool(
                name="list_runs",
                description="List recent pipeline runs with status and artifact names.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "Max runs to return (default 10)",
                            "default": 10,
                        },
                    },
                    "required": [],
                },
            ),
        ]

    # ── tool dispatcher ───────────────────────────────────────────────────────

    @server.call_tool()
    async def call_tool(name: str, arguments: dict | None) -> list:
        args = arguments or {}
        try:
            if name == "get_project_info":
                idx = _local_index()
                stats = idx.stats()
                return _ok({
                    "project_name": config.project_name,
                    "mode": config.mode.value,
                    "primary_language": config.primary_language,
                    "description": config.description,
                    "repo": config.repo,
                    "spec_count": stats.get("doc_count", 0),
                    "index_built": stats.get("built", False),
                    "judge_threshold": config.agent.judge_threshold,
                    "max_judge_iterations": config.agent.max_judge_iterations,
                    "specs_dir": config.paths.specs,
                    "runs_dir": config.paths.runs,
                    "default_model": config.agent.model,
                    "coding_backend": config.agent.coding_backend,
                })

            elif name == "search_specs":
                query = args.get("query", "")
                top_k = int(args.get("top_k", 5))
                idx = _local_index()
                if not idx.is_built():
                    return _err("Spec index not built. Run 'speckit index .' first, or ask to rebuild it.")
                results = idx.search(query, top_k=top_k)
                return _ok([
                    {
                        "path": r["path"],
                        "title": r.get("title", ""),
                        "summary": r.get("summary", ""),
                        "module": r.get("module", ""),
                        "affects": r.get("affects", []),
                    }
                    for r in results
                ])

            elif name == "list_specs":
                specs = _spec_files()
                return _ok([
                    {
                        "path": s.path,
                        "title": s.title,
                        "summary": s.summary,
                        "module": s.module,
                        "last_updated": s.last_updated,
                    }
                    for s in specs
                ])

            elif name == "get_spec":
                path = args.get("path", "")
                abs_path = (project_root / path).resolve()
                if not str(abs_path).startswith(str(project_root)):
                    return _err("Path must be inside the project root.")
                if not abs_path.exists():
                    return _err(f"File not found: {path}")
                return _ok(abs_path.read_text(encoding="utf-8"))

            elif name == "write_spec":
                path = args.get("path", "")
                content = args.get("content", "")
                abs_path = (project_root / path).resolve()
                if not str(abs_path).startswith(str(project_root)):
                    return _err("Path must be inside the project root.")
                specs_root = (project_root / config.paths.specs.lstrip("./")).resolve()
                if not str(abs_path).startswith(str(specs_root)):
                    return _err("write_spec may only write inside the specs/ directory.")
                abs_path.parent.mkdir(parents=True, exist_ok=True)
                abs_path.write_text(content, encoding="utf-8")
                return _ok({"written": path, "bytes": len(content.encode("utf-8"))})

            elif name == "judge_spec":
                content = args.get("content", "")
                spec_type = args.get("spec_type", "module")
                spec_name = args.get("spec_name", "spec.md")
                from speckit.core.agents import judge_scan_spec
                result = judge_scan_spec(spec_name, spec_type, content, config, project_root)
                return _ok({
                    "score": round(result.score, 3),
                    "verdict": result.verdict,
                    "approved": result.score >= config.agent.judge_threshold,
                    "judge_threshold": config.agent.judge_threshold,
                    "issues": [
                        {
                            "severity": i.severity,
                            "message": i.message,
                            "suggestion": i.suggestion,
                        }
                        for i in result.issues
                    ],
                })

            elif name == "get_issue":
                number = int(args["number"])
                gh = _github()
                issue = gh.get_issue(number)
                return _ok({
                    "number": issue.number,
                    "title": issue.title,
                    "body": issue.body,
                    "labels": issue.labels,
                    "state": issue.state,
                })

            elif name == "list_issues":
                state = args.get("state", "open")
                label = args.get("label", "")
                limit = int(args.get("limit", 20))
                gh = _github()
                params: dict = {"state": state, "per_page": min(limit, 100)}
                if label:
                    params["labels"] = label
                data = gh._get(f"/repos/{gh.repo}/issues", params=params)
                items = data if isinstance(data, list) else []
                return _ok([
                    {
                        "number": i["number"],
                        "title": i["title"],
                        "state": i["state"],
                        "labels": [lb["name"] for lb in i.get("labels", [])],
                        "created_at": i.get("created_at", ""),
                        "url": i.get("html_url", ""),
                    }
                    for i in items[:limit]
                ])

            elif name == "add_issue_comment":
                number = int(args["number"])
                body = args.get("body", "")
                gh = _github()
                gh.add_comment(number, body)
                return _ok({"commented": True, "issue": number})

            elif name == "create_pr":
                branch = args["branch"]
                title = args["title"]
                body = args.get("body", "")
                base = args.get("base", "")
                gh = _github()
                pr = gh.create_pr(title=title, body=body, head=branch, base=base)
                return _ok({
                    "pr_url": pr.get("html_url", ""),
                    "pr_number": pr.get("number"),
                    "state": pr.get("state", ""),
                })

            elif name == "run_bug_fix_pipeline":
                issue_number = int(args["issue_number"])
                run_id = f"bug-fix-{issue_number}"

                def _worker():
                    try:
                        from speckit.modes.bug_fix import BugFixPipeline
                        token = os.environ.get("GITHUB_TOKEN", "")
                        repo = os.environ.get("GITHUB_REPO", "") or config.repo
                        github_adapter = None
                        if token and repo:
                            from speckit.adapters.github import GitHubAdapter
                            github_adapter = GitHubAdapter(repo=repo, token=token)
                        pipeline = BugFixPipeline(
                            config=config,
                            project_root=project_root,
                            github=github_adapter,
                        )
                        pipeline.run(issue_number=issue_number)
                    except Exception:
                        logger.exception("Bug-fix pipeline failed for issue #%s", issue_number)

                t = threading.Thread(target=_worker, daemon=True, name=f"speckit-bug-{issue_number}")
                t.start()
                return _ok({
                    "run_id": run_id,
                    "status": "started",
                    "message": (
                        f"Bug-fix pipeline started for issue #{issue_number}. "
                        f"Call get_pipeline_status('{run_id}') to check progress."
                    ),
                    "artifacts_dir": str((_run_dir(run_id)).relative_to(project_root))
                    if not _run_dir(run_id).exists()
                    else str(_run_dir(run_id).relative_to(project_root)),
                })

            elif name == "run_feature_pipeline":
                feature_name = args.get("name", "feature")
                description = args.get("description", "")
                import re as _re
                slug = _re.sub(r"[^a-z0-9]+", "-", feature_name.lower()).strip("-")[:40]
                run_id = f"feature-{slug}"

                def _fworker():
                    try:
                        from speckit.modes.feature import FeaturePipeline
                        pipeline = FeaturePipeline(config=config, project_root=project_root)
                        pipeline.run(feature_name=feature_name, feature_description=description)
                    except Exception:
                        logger.exception("Feature pipeline failed for '%s'", feature_name)

                t = threading.Thread(target=_fworker, daemon=True, name=f"speckit-feat-{slug}")
                t.start()
                return _ok({
                    "run_id": run_id,
                    "status": "started",
                    "message": (
                        f"Feature pipeline started for '{feature_name}'. "
                        f"Call get_pipeline_status('{run_id}') to check progress. "
                        f"Once completed, use get_run_artifact('{run_id}', '03_feature_spec.md') "
                        f"and get_run_artifact('{run_id}', '05_build_plan.md')."
                    ),
                })

            elif name == "get_pipeline_status":
                run_id = args.get("run_id", "")
                d = _run_dir(run_id)
                if not d.exists():
                    return _ok({
                        "run_id": run_id,
                        "status": "not_found",
                        "message": "Run directory does not exist yet — the pipeline may still be starting.",
                        "artifacts": [],
                    })
                artifacts = sorted(f.name for f in d.iterdir() if f.is_file())
                failed = "FAILED.md" in artifacts
                done = any(
                    a in artifacts
                    for a in ["05_build_plan.md", "05_test_results.md", "04_code_changes.md"]
                )
                has_log = "00_run_log.md" in artifacts
                status = (
                    "failed" if failed
                    else "completed" if done
                    else "running" if has_log
                    else "starting"
                )
                return _ok({
                    "run_id": run_id,
                    "status": status,
                    "artifacts": artifacts,
                    "run_dir": str(d.relative_to(project_root)),
                })

            elif name == "run_tests":
                command = args.get("command", "")
                if not command:
                    command = config.testing.backend_runner or "pytest"
                result = subprocess.run(
                    command,
                    shell=True,  # noqa: S602 — user-controlled local env
                    cwd=str(project_root),
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                raw_output = (result.stdout or "") + (result.stderr or "")
                return _ok({
                    "passed": result.returncode == 0,
                    "returncode": result.returncode,
                    "command": command,
                    "output": raw_output[-4000:] if len(raw_output) > 4000 else raw_output,
                })

            elif name == "get_run_artifact":
                run_id = args.get("run_id", "")
                artifact = args.get("artifact", "")
                art_path = _run_dir(run_id) / artifact
                if not art_path.exists():
                    available = (
                        sorted(f.name for f in _run_dir(run_id).iterdir() if f.is_file())
                        if _run_dir(run_id).exists()
                        else []
                    )
                    return _err(
                        f"Artifact '{artifact}' not found in run '{run_id}'. "
                        f"Available: {available}"
                    )
                return _ok(art_path.read_text(encoding="utf-8"))

            elif name == "list_runs":
                limit = int(args.get("limit", 10))
                runs_root = project_root / config.paths.runs.lstrip("./")
                if not runs_root.exists():
                    return _ok([])
                runs = []
                for d in sorted(
                    (p for p in runs_root.iterdir() if p.is_dir()),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                ):
                    artifacts = [f.name for f in d.iterdir() if f.is_file()]
                    failed = "FAILED.md" in artifacts
                    done = any(
                        a in artifacts
                        for a in ["05_build_plan.md", "05_test_results.md", "04_code_changes.md"]
                    )
                    status = "failed" if failed else "completed" if done else "in_progress"
                    runs.append({
                        "run_id": d.name,
                        "status": status,
                        "artifacts": sorted(artifacts),
                    })
                    if len(runs) >= limit:
                        break
                return _ok(runs)

            else:
                return _err(f"Unknown tool: '{name}'")

        except Exception as exc:
            logger.exception("Tool '%s' raised an exception", name)
            return _err(f"{type(exc).__name__}: {exc}")

    # ── resources ─────────────────────────────────────────────────────────────

    @server.list_resources()
    async def list_resources() -> list[types.Resource]:
        resources: list[types.Resource] = []

        for spec in _spec_files():
            resources.append(
                types.Resource(
                    uri=f"speckit://specs/{spec.path}",  # type: ignore[arg-type]
                    name=spec.title or spec.path,
                    description=spec.summary or "",
                    mimeType="text/markdown",
                )
            )

        cfg_path = project_root / "sdd.config.yml"
        if cfg_path.exists():
            resources.append(
                types.Resource(
                    uri="speckit://config",  # type: ignore[arg-type]
                    name="sdd.config.yml",
                    description="speckit project configuration",
                    mimeType="text/yaml",
                )
            )

        return resources

    @server.read_resource()
    async def read_resource(uri) -> str:
        uri_str = str(uri)

        if uri_str == "speckit://config":
            cfg = project_root / "sdd.config.yml"
            return cfg.read_text(encoding="utf-8") if cfg.exists() else ""

        if uri_str.startswith("speckit://specs/"):
            rel = uri_str[len("speckit://specs/"):]
            abs_path = (project_root / rel).resolve()
            if not str(abs_path).startswith(str(project_root)):
                raise ValueError("Path outside project root")
            return abs_path.read_text(encoding="utf-8") if abs_path.exists() else f"Not found: {rel}"

        if uri_str.startswith("speckit://runs/"):
            rest = uri_str[len("speckit://runs/"):]
            parts = rest.split("/", 1)
            run_id, artifact = parts[0], parts[1] if len(parts) > 1 else ""
            art_path = project_root / config.paths.runs.lstrip("./") / run_id / artifact
            return art_path.read_text(encoding="utf-8") if art_path.exists() else f"Not found: {uri_str}"

        raise ValueError(f"Unknown resource URI: {uri_str}")

    return server


async def run_server(project_root: Path) -> None:
    """Run the MCP server over stdio (entry point called by CLI command)."""
    try:
        from mcp.server.stdio import stdio_server
    except ImportError:
        raise ImportError(
            "MCP SDK not installed. Run: pip install 'speckit[mcp]'"
        ) from None

    server = _make_server(project_root)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )
