"""
speckit scan — generate spec files from an existing (brownfield) codebase.

Walks --src, groups files by module (top-level directory), calls an LLM
to write a spec for each module, then writes specs/architecture.md.

Usage:
    speckit scan --src ./src
    speckit scan --src . --out ./specs
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

from speckit.core.config import load_config

console = Console()

# File extensions to include
_CODE_EXTS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".java", ".rb",
    ".rs", ".cs", ".cpp", ".c", ".h", ".swift", ".kt",
}

# Directories to always skip
_SKIP_DIRS = {
    "node_modules", "__pycache__", ".git", ".venv", "venv", "env",
    "dist", "build", ".next", ".nuxt", "target", "vendor",
    ".speckit", "runs", "specs", ".pytest_cache", "coverage",
    ".mypy_cache", ".ruff_cache",
}


# ── file discovery ────────────────────────────────────────────────────────────

def _discover_files(src: Path) -> dict[str, list[Path]]:
    """Return {module_name: [file_paths]} grouped by top-level directory."""
    modules: dict[str, list[Path]] = {}

    for p in sorted(src.rglob("*")):
        if not p.is_file():
            continue
        if p.suffix not in _CODE_EXTS:
            continue
        # Skip if any part of the path is in _SKIP_DIRS
        if any(part in _SKIP_DIRS for part in p.parts):
            continue

        # Group by first directory component relative to src
        rel = p.relative_to(src)
        parts = rel.parts
        module = parts[0].rstrip("/") if len(parts) > 1 else "_root"
        # Strip file extension from single-file "modules"
        if module == "_root":
            module = "core"
        modules.setdefault(module, []).append(p)

    return modules


def _read_files(paths: list[Path], project_root: Path, max_chars: int = 1000) -> dict[str, str]:
    """Read file contents keyed by project-relative path, truncated to max_chars."""
    result = {}
    for p in paths[:10]:  # cap at 10 files per module
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
            key = str(p.relative_to(project_root))
            result[key] = text[:max_chars]
        except Exception:
            pass
    return result


def _strip_md_fence(text: str) -> str:
    """Remove ```markdown or ``` wrapper that LLMs sometimes add."""
    text = text.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1:]
        if text.endswith("```"):
            text = text[:-3]
    return text.strip()


def _detect_language(src: Path) -> str:
    """Detect primary language from file extension counts."""
    counts: dict[str, int] = {}
    for p in src.rglob("*"):
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


# ── command ───────────────────────────────────────────────────────────────────

def scan_command(
    src: str = typer.Option(".", "--src", "-s", help="Source directory to scan."),
    path: str = typer.Argument(default=".", help="Project root."),
    out: str = typer.Option("./specs", "--out", "-o", help="Output directory for spec files."),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing spec files."),
    skip_judge: bool = typer.Option(False, "--skip-judge", help="Skip quality judge after generating specs."),
):
    """
    Generate spec files from an existing codebase (brownfield mode).

    Walks --src, groups files by module, and uses an LLM to write a
    spec for each module plus an architecture overview.

    Generated files are written to --out (default: ./specs).
    Human review before running speckit index is strongly recommended.
    """
    project_root = Path(path).resolve()
    src_path = (project_root / src).resolve()
    out_path = (project_root / out).resolve()

    load_dotenv(project_root / ".env", override=False)

    console.print(Panel.fit(
        "[bold white]speckit scan[/bold white]\n"
        f"[dim]Generating specs from [cyan]{src_path.relative_to(project_root)}[/cyan][/dim]",
        border_style="bright_blue",
        padding=(1, 4),
    ))
    console.print()

    # ── load config ───────────────────────────────────────────────────────────
    try:
        config = load_config(project_root)
    except FileNotFoundError as e:
        console.print(f"  [red]✗[/red]  {e}")
        raise typer.Exit(1)

    # ── check backend ─────────────────────────────────────────────────────────
    has_backend = (
        os.environ.get("GEMINI_VERTEX", "").lower() in ("true", "1", "yes")
        or os.environ.get("GEMINI_API_KEY", "")
        or os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID", "")
        or os.environ.get("ANTHROPIC_API_KEY", "") not in ("", "paste-your-key-here", "sk-ant-...")
    )
    if not has_backend:
        console.print(
            "  [red]✗[/red]  No LLM backend configured in [cyan].env[/cyan].\n"
            "  Set [dim]GEMINI_VERTEX=true[/dim] or [dim]GEMINI_API_KEY=...[/dim]"
        )
        raise typer.Exit(1)

    if not src_path.exists():
        console.print(f"  [red]✗[/red]  Source directory not found: [cyan]{src_path}[/cyan]")
        raise typer.Exit(1)

    # ── discover files ────────────────────────────────────────────────────────
    console.print(f"  [dim]Scanning[/dim] [cyan]{src_path}[/cyan] …")
    modules = _discover_files(src_path)
    language = _detect_language(src_path)

    if not modules:
        console.print("  [yellow]⚠[/yellow]  No source files found. Check --src path.")
        raise typer.Exit(1)

    total_files = sum(len(v) for v in modules.values())
    console.print(
        f"  [green]✓[/green]  Found [bold]{total_files}[/bold] files "
        f"in [bold]{len(modules)}[/bold] modules  [dim]({language})[/dim]\n"
    )

    # ── prepare output dir ────────────────────────────────────────────────────
    modules_out = out_path / "modules"
    modules_out.mkdir(parents=True, exist_ok=True)

    # ── import agents ─────────────────────────────────────────────────────────
    from speckit.core.agents import generate_module_spec, generate_architecture_spec

    generated: list[str] = []
    modules_summary_lines: list[str] = []
    sample_files: dict[str, str] = {}

    # ── generate per-module specs ─────────────────────────────────────────────
    with Progress(
        SpinnerColumn(),
        TextColumn("  [progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        for module_name, files in sorted(modules.items()):
            out_file = modules_out / f"{module_name}.md"
            if out_file.exists() and not force:
                console.print(
                    f"  [dim]·[/dim]  [dim]{module_name}[/dim]  "
                    f"[dim]already exists — skip (use --force to overwrite)[/dim]"
                )
                modules_summary_lines.append(f"- **{module_name}**: (pre-existing spec)")
                continue

            task = progress.add_task(f"[cyan]{module_name}[/cyan]  [dim]({len(files)} files)[/dim]")
            file_contents = _read_files(files, project_root)

            # Collect sample files for architecture spec
            if len(sample_files) < 6:
                sample_files.update(dict(list(file_contents.items())[:2]))

            try:
                spec_md = generate_module_spec(
                    module_name=module_name,
                    file_contents=file_contents,
                    language=language,
                    config=config,
                    project_root=project_root,
                )
                out_file.write_text(_strip_md_fence(spec_md), encoding="utf-8")
                progress.remove_task(task)
                console.print(
                    f"  [green]✓[/green]  [cyan]{module_name}[/cyan]  "
                    f"[dim]→ specs/modules/{module_name}.md[/dim]"
                )
                generated.append(str(out_file.relative_to(project_root)))
                modules_summary_lines.append(f"- **{module_name}**: {len(files)} files")
            except Exception as e:
                progress.remove_task(task)
                console.print(f"  [red]✗[/red]  [cyan]{module_name}[/cyan]  [red]{e}[/red]")

    # ── generate architecture spec ────────────────────────────────────────────
    arch_out = out_path / "architecture.md"
    if arch_out.exists() and not force:
        console.print(
            "  [dim]·[/dim]  [dim]architecture.md already exists — skip[/dim]"
        )
    else:
        console.print()
        with Progress(
            SpinnerColumn(),
            TextColumn("  [progress.description]{task.description}"),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task("Generating [cyan]architecture.md[/cyan] …")
            try:
                arch_md = generate_architecture_spec(
                    project_name=config.project_name,
                    language=language,
                    modules_summary="\n".join(modules_summary_lines),
                    sample_files=sample_files,
                    config=config,
                    project_root=project_root,
                )
                arch_out.write_text(_strip_md_fence(arch_md), encoding="utf-8")
                progress.remove_task(task)
                console.print("  [green]✓[/green]  [cyan]architecture.md[/cyan]  [dim]→ specs/architecture.md[/dim]")
                generated.append(str(arch_out.relative_to(project_root)))
            except Exception as e:
                progress.remove_task(task)
                console.print(f"  [red]✗[/red]  architecture.md  [red]{e}[/red]")

    # ── quality judge ─────────────────────────────────────────────────────────
    if not skip_judge and generated:
        console.print()
        console.print("  [dim]Running quality judge on generated specs…[/dim]\n")
        _run_scan_judge(generated, out_path, project_root, config)

    # ── done ──────────────────────────────────────────────────────────────────
    console.print()
    console.print(Panel.fit(
        f"  [bold green]Scan complete[/bold green] — {len(generated)} spec file(s) written\n\n"
        "  [yellow]Next:[/yellow]\n"
        "  1. [dim]Review generated specs in[/dim] [cyan]specs/[/cyan] [dim]— fix any flagged issues[/dim]\n"
        "  2. Run [bold]speckit index[/bold] [dim]— build the search index[/dim]\n"
        "  3. Run [bold]speckit run --issue N[/bold] [dim]— fix a real issue[/dim]",
        border_style="green",
        padding=(0, 2),
    ))
    console.print()


def _run_scan_judge(
    generated: list[str],
    out_path: Path,
    project_root: Path,
    config,
) -> None:
    from speckit.core.agents import judge_scan_spec

    _SEVERITY_COLOR = {"error": "red", "warning": "yellow", "info": "dim"}
    _SEVERITY_ICON = {"error": "✗", "warning": "⚠", "info": "·"}

    any_issues = False
    report_lines: list[str] = ["# Scan quality report\n"]

    for rel_path in generated:
        abs_path = project_root / rel_path
        if not abs_path.exists():
            continue

        name = abs_path.name
        # Determine spec type from path
        if name == "architecture.md":
            spec_type = "architecture"
        elif abs_path.parent.name == "modules":
            spec_type = "module"
        elif name == "security.md":
            spec_type = "security"
        elif name == "coding-standards.md":
            spec_type = "coding-standards"
        else:
            continue  # skip unknown types

        try:
            content = abs_path.read_text(encoding="utf-8")
            result = judge_scan_spec(name, spec_type, content, config, project_root)
        except Exception as e:
            console.print(f"  [dim]Judge failed for {name}: {e}[/dim]")
            continue

        verdict_color = {"good": "green", "needs-review": "yellow", "poor": "red"}.get(result.verdict, "dim")
        console.print(
            f"  [{verdict_color}]{'●' if result.issues else '✓'}[/{verdict_color}]  "
            f"[cyan]{rel_path}[/cyan]  "
            f"[{verdict_color}]{result.verdict}[/{verdict_color}]  "
            f"[dim](score {result.score:.2f})[/dim]"
        )

        report_lines.append(f"\n## {rel_path} — {result.verdict} (score {result.score:.2f})\n")

        if result.issues:
            any_issues = True
            for issue in result.issues:
                sev = issue.severity
                color = _SEVERITY_COLOR.get(sev, "dim")
                icon = _SEVERITY_ICON.get(sev, "·")
                console.print(
                    f"       [{color}]{icon}[/{color}]  [{color}]{issue.message}[/{color}]\n"
                    f"          [dim]→ {issue.suggestion}[/dim]"
                )
                report_lines.append(f"- **{sev.upper()}**: {issue.message}\n  → {issue.suggestion}")
        else:
            report_lines.append("No issues found.")

    # Write quality report
    report_path = out_path / "scan_quality_report.md"
    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    if any_issues:
        console.print(
            f"\n  [yellow]⚠[/yellow]  Quality issues found. "
            f"Review [cyan]specs/scan_quality_report.md[/cyan] before indexing.\n"
        )
    else:
        console.print(
            f"\n  [green]✓[/green]  All specs passed quality review.\n"
        )
