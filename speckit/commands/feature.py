"""
speckit feature — spec-driven feature pipeline.

Pipeline (Mode C):
  research → compatibility check → draft feature spec → judge loop → test plan → build plan

Requires at least one LLM backend configured in .env.
"""
from __future__ import annotations

import os
from pathlib import Path

import typer
from speckit.commands import load_env
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from speckit.core.config import load_config

console = Console()


def _make_step_callback():
    def on_step(title: str, detail: str = "") -> None:
        detail_str = f"  [dim]{detail}[/dim]" if detail else ""
        console.print(f"  [bright_blue]◆[/bright_blue]  {title}{detail_str}")
    return on_step


def feature_command(
    name: str = typer.Option(..., "--name", "-n", help="Feature name (short, kebab-friendly)."),
    description: str = typer.Option(
        "",
        "--description",
        "-d",
        help="Feature description. Prompted interactively if omitted.",
    ),
    path: str = typer.Argument(default=".", help="Project root."),
    model: str = typer.Option(
        "",
        "--model",
        "-m",
        help="Override LLM model for this run.",
    ),
    coding_backend: str = typer.Option(
        "",
        "--coding-backend",
        help="Backend for code writing: auto | anthropic | gemini | vertex.",
    ),
):
    """
    Run the spec-driven feature pipeline.

    Stages: research → compatibility → feature spec → judge loop → test plan → build plan.

    All artifacts are saved to runs/feature-{name}/.
    Requires ANTHROPIC_API_KEY or GEMINI_API_KEY / GEMINI_VERTEX in .env.
    """
    project_root = Path(path).resolve()
    load_env(project_root)

    console.print(Panel.fit(
        "[bold white]speckit feature[/bold white]\n"
        f"[dim]{name} — feature spec pipeline[/dim]",
        border_style="bright_blue",
        padding=(1, 4),
    ))
    console.print()

    # ── load config ───────────────────────────────────────────────────────────
    try:
        config = load_config(project_root)
    except FileNotFoundError as e:
        console.print(f"  [red]✗[/red]  {e}\n")
        raise typer.Exit(1)

    if model:
        config.agent.model = model
    if coding_backend:
        config.agent.coding_backend = coding_backend

    # ── check at least one LLM backend ────────────────────────────────────────
    has_gemini_vertex = os.environ.get("GEMINI_VERTEX", "").lower() in ("true", "1", "yes")
    has_gemini_key = bool(os.environ.get("GEMINI_API_KEY", ""))
    has_anthropic = bool(os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID", "")) or (
        os.environ.get("ANTHROPIC_API_KEY", "") not in ("", "paste-your-key-here", "sk-ant-...")
    )
    if not (has_gemini_vertex or has_gemini_key or has_anthropic):
        console.print(
            "  [red]✗[/red]  No LLM backend configured.\n\n"
            "  Add one of these to [cyan].env[/cyan]:\n"
            "    [dim]GEMINI_VERTEX=true  (uses GOOGLE_APPLICATION_CREDENTIALS)[/dim]\n"
            "    [dim]GEMINI_API_KEY=AIza...  (Google AI Studio)[/dim]\n"
            "    [dim]ANTHROPIC_API_KEY=sk-ant-...  (paid)[/dim]\n"
        )
        raise typer.Exit(1)

    # ── auto-index if missing ─────────────────────────────────────────────────
    from speckit.adapters.local_index import LocalIndex
    local_idx = LocalIndex(project_root=project_root, project_name=config.project_name)
    if not local_idx.is_built():
        console.print("  [dim]No spec index found — building it now…[/dim]")
        from speckit.core.spec_parser import discover_spec_files
        specs = discover_spec_files(project_root, config.paths.specs)
        if specs:
            local_idx.build(specs)
            console.print(f"  [green]✓[/green]  Indexed {len(specs)} spec files\n")
        else:
            console.print(
                "  [yellow]⚠[/yellow]  No spec files found. "
                "Run [bold]speckit scan[/bold] first.\n"
            )

    # ── prompt for description if not provided ────────────────────────────────
    if not description:
        console.print("  [dim]Describe the feature in a few sentences:[/dim]\n")
        description = typer.prompt("  Description")
        console.print()

    # ── run pipeline ──────────────────────────────────────────────────────────
    from speckit.modes.feature import FeaturePipeline

    pipeline = FeaturePipeline(
        config=config,
        project_root=project_root,
        on_step=_make_step_callback(),
    )

    try:
        result = pipeline.run(feature_name=name, feature_description=description)
    except Exception as e:
        console.print(f"\n  [red]✗[/red]  Pipeline failed: {e}")
        import re
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40]
        run_dir = project_root / config.paths.runs.lstrip("./") / f"feature-{slug}"
        if run_dir.exists():
            console.print(
                f"  [dim]Partial artifacts saved to[/dim] "
                f"[cyan]{run_dir.relative_to(project_root)}[/cyan]"
            )
        raise typer.Exit(1)

    _print_result(result, project_root, config)


def _print_result(result, project_root: Path, config) -> None:
    console.print()

    # Artifacts table
    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("Artifact", style="cyan")
    table.add_column("Status")
    for artifact in result.artifacts:
        table.add_row(artifact, "[green]✓ written[/green]")
    console.print(table)
    console.print()

    rel_dir = result.run_dir.relative_to(project_root)
    score_str = f"{result.judge_score:.2f}"
    approval = (
        f"[green]approved[/green] (score {score_str})"
        if result.approved
        else f"[yellow]needs human review[/yellow] (score {score_str} < {config.agent.judge_threshold})"
    )

    compat_line = (
        f"\n  Compat:     [{'green' if result.compatibility_verdict == 'approved' else 'yellow'}]"
        f"{result.compatibility_verdict}[/{'green' if result.compatibility_verdict == 'approved' else 'yellow'}] "
        f"(score {result.compatibility_score:.2f})"
    )

    if result.compatibility_verdict == "blocked":
        next_line = (
            f"\n  [red]Blocked:[/red] Address conflicts in "
            f"[cyan]{rel_dir}/02_compatibility.md[/cyan] before proceeding."
        )
        border = "red"
    elif result.approved:
        next_line = (
            f"\n  [yellow]Next:[/yellow] Implement using "
            f"[cyan]{rel_dir}/05_build_plan.md[/cyan]"
        )
        border = "green"
    else:
        next_line = (
            f"\n  [yellow]Action:[/yellow] Open [cyan]{rel_dir}/03_feature_spec.md[/cyan] "
            "and address the judge's gaps before building."
        )
        border = "yellow"

    from speckit.core.agents import get_cost_summary_md as _cost_md
    cost_text = _cost_md()
    total_tokens = ""
    if cost_text:
        import re as _re
        m = _re.search(r"\*\*(\d[\d,]+)\*\*\s*\|\s*\*\*(\d[\d,]+)\*\*", cost_text)
        if m:
            total_tokens = f"\n  Tokens:     [dim]{m.group(1)} in / {m.group(2)} out[/dim]"

    console.print(Panel.fit(
        f"  Judge:      {approval}\n"
        f"  Iterations: {result.judge_iterations}"
        + compat_line
        + total_tokens
        + f"\n  Artifacts:  [cyan]{rel_dir}/[/cyan]"
        + next_line,
        border_style=border,
        padding=(0, 2),
    ))
    console.print()
