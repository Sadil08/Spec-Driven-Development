"""
speckit orchestrate — cross-service feature pipeline.

Usage:
    speckit orchestrate --name "Add payment gateway"
    speckit orchestrate --name "Add payment gateway" --build
    speckit orchestrate --name "Add payment gateway" --global-config ./global.sdd.config.yml
"""
from __future__ import annotations

import os
from pathlib import Path

import typer
from speckit.commands import load_env
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


def orchestrate_command(
    name: str = typer.Option(..., "--name", "-n", help="Feature name."),
    description: str = typer.Option(
        "",
        "--description",
        "-d",
        help="Feature description. Prompted interactively if omitted.",
    ),
    build: bool = typer.Option(
        False,
        "--build",
        help="After planning, write code, run tests, and open PRs per service.",
    ),
    global_config: str = typer.Option(
        "./global.sdd.config.yml",
        "--global-config",
        help="Path to global.sdd.config.yml.",
    ),
    path: str = typer.Argument(default=".", help="Monorepo root."),
):
    """
    Run a cross-service feature pipeline (orchestrator mode).

    Determines which services need to change, scaffolds any new services
    required, runs each service's spec pipeline in dependency order, and
    produces a sequenced cross-service build plan + integration test plan.

    With --build: also writes code, runs tests, and opens a PR per service.

    Requires global.sdd.config.yml at the monorepo root.
    """
    root = Path(path).resolve()
    load_env(root)

    console.print(Panel.fit(
        "[bold white]speckit orchestrate[/bold white]\n"
        "[dim]Cross-service feature pipeline[/dim]",
        border_style="bright_blue",
        padding=(1, 4),
    ))
    console.print()

    # Prompt for description if not provided
    if not description.strip():
        description = typer.prompt(
            "  Feature description (what it does, why it matters, any constraints)"
        )
        console.print()

    # Load global config
    from speckit.core.global_config import load_global_config
    try:
        gc = load_global_config(root)
    except FileNotFoundError as e:
        console.print(f"  [red]✗[/red]  {e}")
        raise typer.Exit(1)

    console.print(
        f"  [dim]Project:[/dim] [bold]{gc.project_name}[/bold]  "
        f"[dim]Services registered:[/dim] [bold]{len(gc.services)}[/bold]\n"
    )

    if not gc.services:
        console.print(
            "  [red]✗[/red]  No services in global.sdd.config.yml.\n"
            "  Add at least one service entry and run again."
        )
        raise typer.Exit(1)

    # Check LLM backend
    has_backend = (
        os.environ.get("GEMINI_VERTEX", "").lower() in ("true", "1", "yes")
        or os.environ.get("GEMINI_API_KEY", "")
        or os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID", "")
        or os.environ.get("ANTHROPIC_API_KEY", "") not in ("", "paste-your-key-here", "sk-ant-...")
    )
    if not has_backend:
        console.print(
            "  [red]✗[/red]  No LLM backend configured in [cyan].env[/cyan]."
        )
        raise typer.Exit(1)

    def on_step(title: str, detail: str = "") -> None:
        detail_str = f"  [dim]{detail}[/dim]" if detail else ""
        console.print(f"  [bright_blue]◆[/bright_blue]  {title}{detail_str}")

    from speckit.modes.orchestrator import OrchestratorPipeline
    pipeline = OrchestratorPipeline(global_config=gc, on_step=on_step)

    try:
        result = pipeline.run(
            feature_name=name,
            feature_description=description,
            build=build,
        )
    except Exception as e:
        console.print(f"\n  [red]✗[/red]  Pipeline failed: {e}")
        raise typer.Exit(1)

    # Print result summary
    console.print()
    _print_result(result, console)


def _print_result(result, console: Console) -> None:
    from speckit.modes.orchestrator import OrchestrateResult

    overall = "[green]approved[/green]" if result.approved else "[yellow]needs review[/yellow]"
    contract = f"[{'green' if result.contract_verdict == 'approved' else 'yellow'}]{result.contract_verdict}[/]"

    console.print(Panel.fit(
        f"  [bold]Orchestration complete[/bold] — {overall}\n\n"
        f"  Contract verdict: {contract}\n"
        f"  Services created: [cyan]{', '.join(result.services_created) or 'none'}[/cyan]\n"
        f"  Services affected: [cyan]{', '.join(result.services_affected) or 'none'}[/cyan]\n"
        f"  Artifacts: [dim]{result.run_dir}[/dim]",
        border_style="green" if result.approved else "yellow",
        padding=(0, 2),
    ))

    if result.service_results:
        console.print()
        table = Table(title="Per-service results", border_style="dim")
        table.add_column("Service")
        table.add_column("Score")
        table.add_column("Approved")
        if result.build_ran:
            table.add_column("Tests")
            table.add_column("PR")
        for name, svc_result in result.service_results.items():
            score = f"{svc_result.judge_score:.2f}"
            approved = "[green]✓[/green]" if svc_result.approved else "[yellow]✗[/yellow]"
            if result.build_ran:
                tests = "[green]pass[/green]" if result.services_built.get(name) else "[red]fail[/red]"
                pr = result.pr_urls.get(name, "—")
                table.add_row(name, score, approved, tests, pr)
            else:
                table.add_row(name, score, approved)
        console.print(table)

    console.print()
    console.print("  [yellow]Next:[/yellow]")
    console.print(f"  1. Review [cyan]{result.run_dir}/05_cross_service_build_plan.md[/cyan]")
    console.print(f"  2. Review [cyan]{result.run_dir}/06_integration_test_plan.md[/cyan]")
    if not result.build_ran:
        console.print(
            f"  3. Re-run with [bold]--build[/bold] to auto-generate code, tests, and PRs"
        )
    console.print()
