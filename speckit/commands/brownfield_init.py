"""
speckit brownfield-init — bootstrap speckit for an existing multi-service codebase.

Discovers all services in a monorepo, generates specs for each one, runs security
and scalability audits, extracts inter-service contracts, and writes global.sdd.config.yml
so that speckit orchestrate can immediately be used.

Usage:
    speckit brownfield-init                        # at monorepo root
    speckit brownfield-init /path/to/monorepo
    speckit brownfield-init --skip-audit           # skip security/scalability audit
    speckit brownfield-init --skip-contracts       # skip contract extraction
    speckit brownfield-init --force                # overwrite existing spec files
"""
from __future__ import annotations

import os
from pathlib import Path

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

_RISK_COLOR = {"critical": "red", "high": "yellow", "medium": "cyan", "low": "green", "unknown": "dim"}
_SCORE_COLOR = lambda s: "green" if s >= 0.8 else ("yellow" if s >= 0.5 else "red")


def brownfield_init_command(
    path: str = typer.Argument(default=".", help="Monorepo root directory."),
    skip_audit: bool = typer.Option(
        False, "--skip-audit", help="Skip security and scalability audits."
    ),
    skip_contracts: bool = typer.Option(
        False, "--skip-contracts", help="Skip contract extraction."
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Overwrite existing spec files."
    ),
    service: list[str] = typer.Option(
        None, "--service", "-s",
        help="Only process this service (directory name). Repeat for multiple: -s backend -s frontend.",
    ),
):
    """
    Bootstrap speckit for an existing multi-service codebase (no specs yet).

    \b
    What it does:
      1. Discovers all services by scanning for go.mod / package.json / requirements.txt
      2. Generates module specs + architecture.md per service
      3. Security audit (OWASP Top 10) per service → specs/security.md
      4. Scalability audit per service → specs/scalability.md
      5. Architectural ambiguity detection → specs/ambiguities.md
      6. Extracts inter-service API contracts → global-specs/contracts/
      7. Writes global.sdd.config.yml at the monorepo root
      8. Writes a health report with prioritised remediation list

    After this runs, use:
      speckit orchestrate --name "your feature"
      speckit orchestrate-bug --issue 42
    """
    root = Path(path).resolve()
    load_dotenv(root / ".env", override=False)

    console.print(Panel.fit(
        "[bold white]speckit brownfield-init[/bold white]\n"
        f"[dim]Bootstrapping speckit for [cyan]{root}[/cyan][/dim]",
        border_style="bright_blue",
        padding=(1, 4),
    ))
    console.print()

    # ── backend check ─────────────────────────────────────────────────────────
    from speckit.core.agents import _find_claude_cli
    has_backend = (
        os.environ.get("SPECKIT_USE_CLAUDE_CLI", "").lower() in ("true", "1", "yes")
        or os.environ.get("GEMINI_VERTEX", "").lower() in ("true", "1", "yes")
        or os.environ.get("GEMINI_API_KEY", "")
        or os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID", "")
        or os.environ.get("ANTHROPIC_API_KEY", "") not in ("", "paste-your-key-here", "sk-ant-...")
        or bool(_find_claude_cli())
    )
    if not has_backend:
        console.print(
            "  [red]✗[/red]  No LLM backend configured in [cyan].env[/cyan].\n"
            "  Set [dim]ANTHROPIC_API_KEY=...[/dim] or [dim]GEMINI_VERTEX=true[/dim]\n"
            "  Or use your Claude Pro subscription: [dim]SPECKIT_USE_CLAUDE_CLI=true[/dim]"
        )
        raise typer.Exit(1)

    if not root.exists():
        console.print(f"  [red]✗[/red]  Directory not found: [cyan]{root}[/cyan]")
        raise typer.Exit(1)

    if service:
        console.print(f"  [yellow]⚠[/yellow]  --service filter: only processing [cyan]{', '.join(service)}[/cyan]\n")
    if skip_audit:
        console.print("  [yellow]⚠[/yellow]  Skipping security/scalability/ambiguity audits\n")
    if skip_contracts:
        console.print("  [yellow]⚠[/yellow]  Skipping contract extraction\n")
    if force:
        console.print("  [yellow]⚠[/yellow]  --force: overwriting existing spec files\n")

    # ── run pipeline ──────────────────────────────────────────────────────────
    from speckit.modes.brownfield import BrownfieldInitPipeline

    step_count = [0]

    def _on_step(title: str, detail: str = "") -> None:
        step_count[0] += 1
        if detail:
            console.print(f"  [dim]{title}:[/dim] [cyan]{detail}[/cyan]")
        else:
            console.print(f"  [dim]{title}[/dim]")

    pipeline = BrownfieldInitPipeline(root=root, on_step=_on_step, force=force, only_services=service or None)

    try:
        result = pipeline.run(skip_audit=skip_audit, skip_contracts=skip_contracts)
    except Exception as e:
        console.print(f"\n  [red]✗  Pipeline failed: {e}[/red]")
        raise typer.Exit(1)

    if result.error:
        console.print(f"\n  [red]✗  {result.error}[/red]")
        raise typer.Exit(1)

    # ── results table ─────────────────────────────────────────────────────────
    console.print()
    console.print("  [bold]Service health summary[/bold]\n")

    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("Service", style="cyan")
    if not skip_audit:
        table.add_column("Security", justify="center")
        table.add_column("Risk", justify="center")
        table.add_column("Findings", justify="center")
        table.add_column("Scale", justify="center")
        table.add_column("Concerns", justify="center")
        table.add_column("Ambiguities", justify="center")
    table.add_column("Specs written", justify="right", style="dim")
    table.add_column("Status")

    for name, h in result.health.items():
        status = "[red]✗ failed[/red]" if h.error else "[green]✓[/green]"
        spec_count = len(h.spec_files_written)
        row_data = [name]
        if not skip_audit:
            sc = h.security_score
            sc_col = _SCORE_COLOR(sc)
            rc = _RISK_COLOR.get(h.security_risk, "dim")
            ss = h.scalability_score
            ss_col = _SCORE_COLOR(ss)
            row_data += [
                f"[{sc_col}]{sc:.2f}[/{sc_col}]",
                f"[{rc}]{h.security_risk}[/{rc}]",
                str(h.security_findings),
                f"[{ss_col}]{ss:.2f}[/{ss_col}]",
                str(h.scalability_concerns),
                str(h.ambiguities),
            ]
        row_data += [str(spec_count), status]
        table.add_row(*row_data)

    console.print(table)
    console.print()

    # ── summary ───────────────────────────────────────────────────────────────
    lines: list[str] = [
        f"  [green]✓[/green]  [bold]{len(result.services_discovered)}[/bold] service(s) processed",
    ]
    if result.services_failed:
        lines.append(
            f"  [red]✗[/red]  {len(result.services_failed)} failed: "
            + ", ".join(result.services_failed)
        )
    if result.global_config_written:
        lines.append("  [green]✓[/green]  [cyan]global.sdd.config.yml[/cyan] written")
    if result.health_report_path:
        rel = result.health_report_path.relative_to(root)
        lines.append(f"  [green]✓[/green]  Health report: [cyan]{rel}[/cyan]")

    for line in lines:
        console.print(line)

    console.print()
    console.print(Panel.fit(
        "[bold green]Brownfield init complete[/bold green]\n\n"
        "[yellow]Next steps:[/yellow]\n"
        "  1. [dim]Review generated specs in each service's[/dim] [cyan]specs/[/cyan] [dim]directory[/dim]\n"
        "  2. [dim]Fix any CRITICAL/HIGH issues in[/dim] [cyan]specs/security.md[/cyan]\n"
        "  3. [dim]Fill in[/dim] [cyan]depends_on[/cyan] [dim]in[/dim] "
        "[cyan]global.sdd.config.yml[/cyan]\n"
        "  4. [dim]Run[/dim] [bold]speckit index[/bold] [dim]in each service directory[/dim]\n"
        "  5. [dim]Run[/dim] [bold]speckit orchestrate --name \"your feature\"[/bold]\n"
        "  6. [dim]Or fix a bug:[/dim] [bold]speckit orchestrate-bug --issue 42[/bold]",
        border_style="green",
        padding=(0, 2),
    ))
    console.print()
