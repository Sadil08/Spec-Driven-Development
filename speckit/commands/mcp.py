"""
speckit mcp — start the Model Context Protocol server.

Claude Pro (VS Code extension) connects to this server and gains full
speckit capabilities: spec search, GitHub integration, pipeline triggering,
test running, and PR creation — all driven by Claude Pro's own reasoning.

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

Then reload VS Code — Claude Pro will show speckit tools in the tool picker.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import typer
from dotenv import load_dotenv
from rich.console import Console

console = Console(stderr=True)  # MCP uses stdout for JSON-RPC; all UI goes to stderr


def mcp_command(
    path: str = typer.Argument(default=".", help="Project root directory."),
    log_level: str = typer.Option(
        "WARNING",
        "--log-level",
        help="Logging level: DEBUG | INFO | WARNING | ERROR",
    ),
):
    """
    Start the speckit MCP server (stdio transport).

    Claude Pro (VS Code extension) connects to this server automatically
    when configured in VS Code MCP settings. All speckit tools become
    available inside Claude Pro conversations.

    Requires: pip install 'speckit[mcp]'
    """
    # ── check mcp installed ───────────────────────────────────────────────────
    try:
        import mcp  # noqa: F401
    except ImportError:
        console.print(
            "\n  [red]✗[/red]  MCP SDK not installed.\n\n"
            "  Fix: [bold]pip install 'speckit\\[mcp]'[/bold]\n",
        )
        raise typer.Exit(1)

    project_root = Path(path).resolve()
    load_dotenv(project_root / ".env", override=False)

    # ── validate config exists ────────────────────────────────────────────────
    from speckit.core.config import load_config
    try:
        config = load_config(project_root)
    except FileNotFoundError as e:
        console.print(f"\n  [red]✗[/red]  {e}\n")
        raise typer.Exit(1)

    # ── configure logging to stderr ───────────────────────────────────────────
    import logging
    numeric = getattr(logging, log_level.upper(), logging.WARNING)
    logging.basicConfig(
        level=numeric,
        format="%(levelname)s [speckit-mcp] %(name)s: %(message)s",
        stream=sys.stderr,
    )

    # ── startup banner (stderr only — stdout is reserved for JSON-RPC) ────────
    console.print(
        f"\n  [bright_blue]◆[/bright_blue]  speckit MCP server starting\n"
        f"  [dim]Project:[/dim]  {config.project_name}  [dim]({config.mode.value})[/dim]\n"
        f"  [dim]Root:[/dim]     {project_root}\n"
        f"  [dim]Repo:[/dim]     {config.repo or '(not set)'}\n"
    )

    github_ok = bool(
        os.environ.get("GITHUB_TOKEN", "") and
        (os.environ.get("GITHUB_REPO", "") or config.repo)
    )
    llm_ok = (
        os.environ.get("GEMINI_VERTEX", "").lower() in ("true", "1", "yes")
        or bool(os.environ.get("GEMINI_API_KEY", ""))
        or bool(os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID", ""))
        or os.environ.get("ANTHROPIC_API_KEY", "") not in ("", "paste-your-key-here", "sk-ant-...")
    )

    console.print(
        f"  GitHub:   {'[green]✓ connected[/green]' if github_ok else '[yellow]⚠ not configured (GITHUB_TOKEN/GITHUB_REPO missing)[/yellow]'}\n"
        f"  LLM:      {'[green]✓ backend available[/green]' if llm_ok else '[yellow]⚠ no LLM backend (pipeline tools need one)[/yellow]'}\n"
        f"\n  [dim]Waiting for Claude Pro to connect…[/dim]\n"
        f"  [dim]Add to VS Code settings:[/dim]\n"
        f'  [dim]  {{"claude.mcpServers": {{"speckit": {{"command": "speckit", "args": ["mcp", "{project_root}"]}}}}}}}}[/dim]\n'
    )

    # ── run server ────────────────────────────────────────────────────────────
    from speckit.server.mcp_server import run_server
    asyncio.run(run_server(project_root))
