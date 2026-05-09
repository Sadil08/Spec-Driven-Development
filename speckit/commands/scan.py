"""scan command stub"""
import typer
from rich.console import Console
console = Console()

def scan_command(
    src: str = typer.Option("./src", "--src", help="Source directory to scan."),
    path: str = typer.Argument(default=".", help="Project root."),
):
    """Generate spec files from an existing codebase. (coming in Phase 3)"""
    console.print("\n  [yellow]speckit scan[/yellow] is coming in Phase 3.")
    console.print("  [dim]It will walk your codebase and generate /specs from your existing code.[/dim]\n")
