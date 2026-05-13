"""
speckit run — trigger the spec-driven agent pipeline for a GitHub issue.

Pipeline (Mode B):
  classify → search specs → draft bug report → judge loop → test plan

Requires ANTHROPIC_API_KEY in .env.
GitHub steps require GITHUB_TOKEN + GITHUB_REPO (or set in sdd.config.yml).
"""
from __future__ import annotations

import os
from pathlib import Path

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from speckit.core.config import load_config

console = Console()


# ── step printer ─────────────────────────────────────────────────────────────

def _make_step_callback():
    """Return a callback that prints each pipeline step to the console."""
    def on_step(title: str, detail: str = "") -> None:
        icon = "◆"
        detail_str = f"  [dim]{detail}[/dim]" if detail else ""
        console.print(f"  [bright_blue]{icon}[/bright_blue]  {title}{detail_str}")
    return on_step


# ── command ───────────────────────────────────────────────────────────────────

def run_command(
    issue: int = typer.Option(..., "--issue", "-i", help="GitHub issue number."),
    path: str = typer.Argument(default=".", help="Project root."),
    no_github: bool = typer.Option(
        False,
        "--no-github",
        help="Skip GitHub — enter issue details manually. Useful for local testing.",
    ),
):
    """
    Run the full spec-driven bug-fix pipeline for a GitHub issue.

    Stages: classify → search specs → draft bug report → judge loop → test plan.

    All artifacts are saved to runs/bug-fix-{N}/.
    Requires ANTHROPIC_API_KEY in .env (or environment).
    GitHub steps require GITHUB_TOKEN and GITHUB_REPO.
    """
    project_root = Path(path).resolve()

    # Load .env from project root (does not override already-set env vars)
    load_dotenv(project_root / ".env", override=False)

    console.print(Panel.fit(
        "[bold white]speckit run[/bold white]\n"
        f"[dim]Issue #{issue} — bug fix pipeline[/dim]",
        border_style="bright_blue",
        padding=(1, 4),
    ))
    console.print()

    # ── load config ──────────────────────────────────────────────────────────
    try:
        config = load_config(project_root)
    except FileNotFoundError as e:
        console.print(f"  [red]✗[/red]  {e}\n")
        raise typer.Exit(1)

    # ── check at least one LLM backend is configured ─────────────────────────
    has_gemini_vertex = os.environ.get("GEMINI_VERTEX", "").lower() in ("true", "1", "yes")
    has_gemini_key = bool(os.environ.get("GEMINI_API_KEY", ""))
    has_anthropic = bool(os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID", "")) or (
        os.environ.get("ANTHROPIC_API_KEY", "") not in ("", "paste-your-key-here", "sk-ant-...")
    )
    if not (has_gemini_vertex or has_gemini_key or has_anthropic):
        console.print(
            "  [red]✗[/red]  No LLM backend configured.\n\n"
            "  Add one of these to [cyan].env[/cyan]:\n"
            "    [dim]GEMINI_VERTEX=true  (free, uses GOOGLE_APPLICATION_CREDENTIALS)[/dim]\n"
            "    [dim]GEMINI_API_KEY=AIza...  (free tier via Google AI Studio)[/dim]\n"
            "    [dim]ANTHROPIC_API_KEY=sk-ant-...  (paid)[/dim]\n"
        )
        raise typer.Exit(1)

    # ── setup GitHub adapter ──────────────────────────────────────────────────
    github = None
    if not no_github:
        token = os.environ.get("GITHUB_TOKEN", "")
        repo = os.environ.get("GITHUB_REPO", "") or config.repo
        if not token or not repo:
            console.print(
                "  [yellow]⚠[/yellow]  GitHub not fully configured "
                "(need GITHUB_TOKEN + GITHUB_REPO in .env or sdd.config.yml).\n"
                "  Switching to manual issue entry.\n"
            )
            no_github = True
        else:
            try:
                from speckit.adapters.github import GitHubAdapter
                github = GitHubAdapter(repo=repo, token=token)
                github.verify_token()
                console.print(f"  [green]✓[/green]  GitHub connected  [dim]{repo}[/dim]")
            except EnvironmentError as e:
                console.print(f"\n  [red]✗[/red]  {e}\n")
                raise typer.Exit(1)
            except Exception as e:
                console.print(f"  [yellow]⚠[/yellow]  GitHub check failed: {e}\n  Switching to manual entry.\n")
                no_github = True

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
                "Run [bold]speckit init[/bold] or [bold]speckit scan[/bold] first.\n"
            )

    # ── get issue (GitHub or manual) ──────────────────────────────────────────
    issue_obj = None
    if no_github:
        console.print("  [dim]Enter issue details manually:[/dim]\n")
        issue_title = typer.prompt("  Issue title")
        issue_body = typer.prompt("  Issue body (describe the bug)", default="")
        from speckit.adapters.github import Issue as GHIssue
        issue_obj = GHIssue(
            number=issue,
            title=issue_title,
            body=issue_body,
            labels=["bug"],
            state="open",
        )
        console.print()

    # ── run pipeline ──────────────────────────────────────────────────────────
    from speckit.modes.bug_fix import BugFixPipeline

    pipeline = BugFixPipeline(
        config=config,
        project_root=project_root,
        github=github,
        on_step=_make_step_callback(),
    )

    try:
        result = pipeline.run(issue_number=issue, issue=issue_obj)
    except Exception as e:
        err = str(e)
        if "401" in err:
            console.print(
                "\n  [red]✗[/red]  GitHub returned 401 Unauthorized.\n\n"
                "  Your GITHUB_TOKEN is invalid or expired.\n"
                "  Fix: github.com/settings/tokens → new classic token → repo scope\n"
                "  Then update GITHUB_TOKEN in [cyan].env[/cyan]\n"
            )
        else:
            console.print(f"\n  [red]✗[/red]  Pipeline failed: {e}")
        run_dir = project_root / config.paths.runs.lstrip("./") / f"bug-fix-{issue}"
        if run_dir.exists():
            console.print(
                f"  [dim]Partial artifacts saved to[/dim] "
                f"[cyan]{run_dir.relative_to(project_root)}[/cyan]"
            )
        raise typer.Exit(1)

    # ── print result ──────────────────────────────────────────────────────────
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

    # Summary panel
    rel_dir = result.run_dir.relative_to(project_root)
    score_str = f"{result.judge_score:.2f}"
    approval = (
        f"[green]approved[/green] (score {score_str})"
        if result.approved
        else f"[yellow]needs human review[/yellow] (score {score_str} < {config.agent.judge_threshold})"
    )

    console.print(Panel.fit(
        f"  Judge: {approval}\n"
        f"  Iterations: {result.judge_iterations}\n"
        f"  Artifacts:  [cyan]{rel_dir}/[/cyan]\n"
        + (
            f"\n  [yellow]Next:[/yellow] Review [cyan]{rel_dir}/02_bug_report.md[/cyan] "
            "before implementing the fix."
            if result.approved
            else f"\n  [yellow]Action:[/yellow] Open [cyan]{rel_dir}/02_bug_report.md[/cyan] "
            "and address the judge's gaps before proceeding."
        ),
        border_style="green" if result.approved else "yellow",
        padding=(0, 2),
    ))
    console.print()
