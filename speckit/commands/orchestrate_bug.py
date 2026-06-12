"""
speckit orchestrate-bug — cross-service bug-fix pipeline.

Usage:
    speckit orchestrate-bug --issue 42
    speckit orchestrate-bug --issue 42 --build
    speckit orchestrate-bug --no-github --title "Bug title" --body "Bug description"
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


def orchestrate_bug_command(
    issue: int = typer.Option(0, "--issue", "-i", help="GitHub issue number."),
    build: bool = typer.Option(
        False,
        "--build",
        help="After planning, write code, run tests, and open PRs per service.",
    ),
    no_github: bool = typer.Option(
        False,
        "--no-github",
        help="Run without GitHub — provide --title and --body instead.",
    ),
    title: str = typer.Option("", "--title", help="Bug title (used with --no-github)."),
    body: str = typer.Option("", "--body", help="Bug description (used with --no-github)."),
    global_config: str = typer.Option(
        "./global.sdd.config.yml",
        "--global-config",
        help="Path to global.sdd.config.yml.",
    ),
    path: str = typer.Argument(default=".", help="Monorepo root."),
):
    """
    Run a cross-service bug-fix pipeline (orchestrator mode).

    Classifies the bug, determines which services are affected, runs each
    service's bug-fix pipeline in dependency order, and produces a sequenced
    cross-service fix plan + integration test plan.

    With --build: also writes code, runs tests, and opens a PR per service.

    Requires global.sdd.config.yml at the monorepo root.
    """
    root = Path(path).resolve()
    load_env(root)

    if not no_github and issue == 0:
        console.print(
            "  [red]✗[/red]  Provide --issue <number> or use --no-github with --title and --body."
        )
        raise typer.Exit(1)

    if no_github and not title.strip():
        title = typer.prompt("  Bug title")
    if no_github and not body.strip():
        body = typer.prompt("  Bug description")

    console.print(Panel.fit(
        "[bold white]speckit orchestrate-bug[/bold white]\n"
        "[dim]Cross-service bug-fix pipeline[/dim]",
        border_style="bright_blue",
        padding=(1, 4),
    ))
    console.print()

    from speckit.core.global_config import load_global_config
    try:
        gc = load_global_config(root)
    except FileNotFoundError as e:
        console.print(f"  [red]✗[/red]  {e}")
        raise typer.Exit(1)

    console.print(
        f"  [dim]Project:[/dim] [bold]{gc.project_name}[/bold]  "
        f"[dim]Services:[/dim] [bold]{len(gc.services)}[/bold]\n"
    )

    if not gc.services:
        console.print("  [red]✗[/red]  No services in global.sdd.config.yml.")
        raise typer.Exit(1)

    has_backend = (
        os.environ.get("GEMINI_VERTEX", "").lower() in ("true", "1", "yes")
        or os.environ.get("GEMINI_API_KEY", "")
        or os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID", "")
        or os.environ.get("ANTHROPIC_API_KEY", "") not in ("", "paste-your-key-here", "sk-ant-...")
    )
    if not has_backend:
        console.print("  [red]✗[/red]  No LLM backend configured in [cyan].env[/cyan].")
        raise typer.Exit(1)

    def on_step(title_s: str, detail: str = "") -> None:
        detail_str = f"  [dim]{detail}[/dim]" if detail else ""
        console.print(f"  [bright_blue]◆[/bright_blue]  {title_s}{detail_str}")

    from speckit.modes.orchestrator import BugOrchestratePipeline
    pipeline = BugOrchestratePipeline(global_config=gc, on_step=on_step)

    try:
        result = pipeline.run(
            issue_number=issue if not no_github else 0,
            issue_title=title,
            issue_body=body,
            build=build,
        )
    except Exception as e:
        console.print(f"\n  [red]✗[/red]  Pipeline failed: {e}")
        raise typer.Exit(1)

    console.print()
    _print_bug_result(result, console)


def _print_bug_result(result, console: Console) -> None:
    overall = "[green]approved[/green]" if result.approved else "[yellow]needs review[/yellow]"
    contract = f"[{'green' if result.contract_verdict == 'approved' else 'yellow'}]{result.contract_verdict}[/]"

    console.print(Panel.fit(
        f"  [bold]Bug orchestration complete[/bold] — {overall}\n\n"
        f"  Issue: [cyan]#{result.issue_number}[/cyan]\n"
        f"  Contract verdict: {contract}\n"
        f"  Services affected: [cyan]{', '.join(result.services_affected) or 'none'}[/cyan]\n"
        f"  Artifacts: [dim]{result.run_dir}[/dim]",
        border_style="green" if result.approved else "yellow",
        padding=(0, 2),
    ))

    if result.service_results:
        console.print()
        table = Table(title="Per-service results", border_style="dim")
        table.add_column("Service")
        table.add_column("Approved")
        if result.build_ran:
            table.add_column("Tests")
            table.add_column("PR")
        for name, svc_result in result.service_results.items():
            approved = "[green]✓[/green]" if svc_result.approved else "[yellow]✗[/yellow]"
            if result.build_ran:
                tests = "[green]pass[/green]" if result.services_built.get(name) else "[red]fail[/red]"
                pr = result.pr_urls.get(name, "—")
                table.add_row(name, approved, tests, pr)
            else:
                table.add_row(name, approved)
        console.print(table)

    console.print()
    console.print("  [yellow]Next:[/yellow]")
    console.print(f"  1. Review [cyan]{result.run_dir}/05_cross_service_fix_plan.md[/cyan]")
    console.print(f"  2. Review [cyan]{result.run_dir}/06_integration_test_plan.md[/cyan]")
    if not result.build_ran:
        console.print(
            "  3. Re-run with [bold]--build[/bold] to auto-generate code, tests, and PRs"
        )
    console.print()
