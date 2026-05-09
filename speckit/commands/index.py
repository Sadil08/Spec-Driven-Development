"""speckit index — parse and index all spec files for fast agent retrieval."""
from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn
from rich.table import Table

from speckit.core.config import VectorDBProvider, load_config
from speckit.core.spec_parser import discover_spec_files
from speckit.adapters.vector_db import get_adapter

console = Console()


def index_command(
    path: str = typer.Argument(default=".", help="Project root."),
    setup_sql: bool = typer.Option(
        False,
        "--setup-sql",
        help="Print the Supabase setup SQL and exit.",
    ),
    query: str = typer.Option(
        "",
        "--query",
        "-q",
        help="Test a search query against the current index.",
    ),
):
    """
    Index all spec files for fast retrieval during agent runs.

    Parses every .md file in specs/, builds a searchable index, and
    persists it. Uses Supabase pgvector when configured, otherwise a
    local BM25 JSON index (zero external dependencies).

    Run [bold]speckit index --setup-sql[/bold] to get the Supabase SQL
    you need to run once before enabling the Supabase adapter.
    """
    project_root = Path(path).resolve()

    # ── --setup-sql shortcut ─────────────────────────────────────────────────
    if setup_sql:
        from speckit.adapters.supabase_index import SUPABASE_SETUP_SQL
        console.print(
            "\n[bold]Supabase setup SQL[/bold] "
            "[dim]— run this once in your Supabase SQL editor[/dim]\n"
        )
        console.print(SUPABASE_SETUP_SQL)
        raise typer.Exit(0)

    # ── header ───────────────────────────────────────────────────────────────
    console.print(Panel.fit(
        "[bold white]speckit index[/bold white]\n"
        "[dim]Building spec search index[/dim]",
        border_style="bright_blue",
        padding=(1, 4),
    ))

    # ── load config ──────────────────────────────────────────────────────────
    try:
        config = load_config(project_root)
    except FileNotFoundError as e:
        console.print(f"\n  [red]✗[/red]  {e}\n")
        raise typer.Exit(1)

    # ── --query shortcut: search without re-indexing ─────────────────────────
    if query:
        _run_test_query(query, config, project_root)
        raise typer.Exit(0)

    # ── discover spec files ──────────────────────────────────────────────────
    console.print(f"\n  [dim]Scanning:[/dim] [cyan]{config.paths.specs}[/cyan]")
    spec_files = discover_spec_files(project_root, config.paths.specs)

    if not spec_files:
        console.print(
            "\n  [yellow]⚠[/yellow]  No spec files found in "
            f"[cyan]{config.paths.specs}[/cyan].\n"
            "  Run [bold]speckit init[/bold] to scaffold them, or "
            "[bold]speckit scan[/bold] to generate them from existing code.\n"
        )
        raise typer.Exit(1)

    console.print(
        f"  [dim]Found[/dim] [bold]{len(spec_files)}[/bold] "
        "[dim]spec files[/dim]\n"
    )

    # ── resolve adapter ──────────────────────────────────────────────────────
    adapter = get_adapter(config, project_root)
    adapter_name = type(adapter).__name__.replace("Index", "")

    console.print(f"  [dim]Adapter:[/dim] [cyan]{adapter_name}[/cyan]")

    if (
        config.vector_db.provider == VectorDBProvider.SUPABASE
        and adapter_name == "Local"
    ):
        console.print(
            "  [dim]Tip:[/dim] Set [cyan]SPECKIT_VECTOR_DB_URL[/cyan], "
            "[cyan]SPECKIT_VECTOR_DB_KEY[/cyan], and [cyan]OPENAI_API_KEY[/cyan] "
            "in [cyan].env[/cyan] to enable Supabase.\n"
            "  Run [bold]speckit index --setup-sql[/bold] to get the required SQL.\n"
        )

    # ── index with progress bar ──────────────────────────────────────────────
    with Progress(
        SpinnerColumn(),
        TextColumn("  [progress.description]{task.description}"),
        BarColumn(bar_width=30),
        TaskProgressColumn(),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("Indexing spec files…", total=len(spec_files))
        try:
            count = adapter.build(spec_files)
            progress.update(task, completed=count)
        except Exception as e:
            console.print(f"\n  [red]✗[/red]  Indexing failed: {e}\n")
            raise typer.Exit(1)

    # ── summary table ────────────────────────────────────────────────────────
    console.print()
    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("File", style="cyan")
    table.add_column("Module", style="dim")
    table.add_column("Title")

    for spec in spec_files[:15]:
        table.add_row(spec.path, spec.module, (spec.title or "—")[:55])

    if len(spec_files) > 15:
        table.add_row(
            f"[dim]… and {len(spec_files) - 15} more[/dim]", "", ""
        )

    console.print(table)
    console.print()
    console.print(Panel.fit(
        f"  [bold green]✓[/bold green]  Indexed [bold]{count}[/bold] spec files "
        f"via [cyan]{adapter_name}[/cyan]  ",
        border_style="green",
        padding=(0, 2),
    ))
    console.print(
        "\n  [dim]Tip:[/dim] Test search with "
        "[bold]speckit index --query 'auth bug'[/bold]\n"
        "  [dim]Next:[/dim] Run [bold]speckit run --issue N[/bold] "
        "to trigger the full agent pipeline.\n"
    )


# ── internal helpers ─────────────────────────────────────────────────────────

def _run_test_query(
    query: str,
    config,
    project_root: Path,
) -> None:
    """Run a test search query and print results."""
    adapter = get_adapter(config, project_root)
    adapter_name = type(adapter).__name__.replace("Index", "")

    if not getattr(adapter, "is_built", lambda: True)():
        console.print(
            "\n  [yellow]⚠[/yellow]  No local index found. "
            "Run [bold]speckit index[/bold] first.\n"
        )
        return

    console.print(f"\n  [dim]Searching ({adapter_name}):[/dim] [bold]{query}[/bold]\n")
    results = adapter.search(query, top_k=5)

    if not results:
        console.print("  [dim]No results found.[/dim]\n")
        return

    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("#", style="dim", width=3)
    table.add_column("File", style="cyan")
    table.add_column("Module", style="dim")
    table.add_column("Title")

    for i, doc in enumerate(results, 1):
        table.add_row(
            str(i),
            doc.get("path", ""),
            doc.get("module", ""),
            (doc.get("title", "") or "—")[:55],
        )

    console.print(table)
    console.print()
