"""
speckit serve — start the GitHub webhook server.

Listens for GitHub issue events and automatically triggers the correct pipeline:
  - Issues labeled with bug_labels → bug-fix pipeline (Mode B)
  - Issues labeled with feature_labels → feature pipeline (Mode C)

GitHub webhook setup:
  1. Go to your repo → Settings → Webhooks → Add webhook
  2. Payload URL: https://your-server/webhook/github
  3. Content type: application/json
  4. Secret: same value as GITHUB_WEBHOOK_SECRET in .env
  5. Events: select "Issues" only

Requires: pip install 'speckit[server]'
"""
from __future__ import annotations

import os
from pathlib import Path

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel

console = Console()


def serve_command(
    path: str = typer.Argument(default=".", help="Project root."),
    host: str = typer.Option("0.0.0.0", "--host", help="Host to bind to."),
    port: int = typer.Option(8000, "--port", "-p", help="Port to listen on."),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes (dev mode)."),
):
    """
    Start the GitHub webhook server.

    Receives GitHub issue events and triggers the appropriate pipeline automatically.
    Requires pip install 'speckit[server]'.

    Set GITHUB_WEBHOOK_SECRET in .env to validate webhook payloads.
    """
    project_root = Path(path).resolve()
    load_dotenv(project_root / ".env", override=False)

    try:
        import uvicorn  # noqa: F401
    except ImportError:
        console.print(
            "\n  [red]✗[/red]  uvicorn not installed.\n\n"
            "  Run: [bold]pip install 'speckit[server]'[/bold]\n"
        )
        raise typer.Exit(1)

    console.print(Panel.fit(
        "[bold white]speckit serve[/bold white]\n"
        f"[dim]Webhook server — listening on {host}:{port}[/dim]",
        border_style="bright_blue",
        padding=(1, 4),
    ))
    console.print()

    # Validate config
    from speckit.core.config import load_config
    try:
        config = load_config(project_root)
    except FileNotFoundError as e:
        console.print(f"  [red]✗[/red]  {e}\n")
        raise typer.Exit(1)

    secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPO", "") or config.repo

    if not secret:
        console.print(
            "  [yellow]⚠[/yellow]  GITHUB_WEBHOOK_SECRET not set — "
            "webhook signatures will NOT be validated.\n"
            "  [dim]Add GITHUB_WEBHOOK_SECRET to .env for production use.[/dim]\n"
        )
    else:
        console.print("  [green]✓[/green]  Webhook signature validation enabled")

    if not token:
        console.print("  [yellow]⚠[/yellow]  GITHUB_TOKEN not set — GitHub API calls will fail")
    else:
        console.print(f"  [green]✓[/green]  GitHub token configured  [dim]{repo}[/dim]")

    console.print(f"\n  Routes:")
    console.print(f"    [dim]GET[/dim]   [cyan]http://{host}:{port}/health[/cyan]")
    console.print(f"    [dim]POST[/dim]  [cyan]http://{host}:{port}/webhook/github[/cyan]")
    console.print()
    console.print(f"  Bug labels:     [dim]{config.github.bug_labels}[/dim]")
    console.print(f"  Feature labels: [dim]{config.github.feature_labels}[/dim]")
    console.print()

    from speckit.server.webhook import create_app
    app = create_app(project_root)

    import uvicorn
    uvicorn.run(
        app,
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )
