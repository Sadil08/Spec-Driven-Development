"""build command stub"""
import typer
from rich.console import Console
console = Console()

def build_command(path: str = typer.Argument(default=".", help="Project root.")):
    """Start a guided greenfield spec-building session. (coming in Phase 4)"""
    console.print("\n  [yellow]speckit build[/yellow] is coming in Phase 4.")
    console.print("  [dim]It will guide you through the full greenfield spec-building conversation.[/dim]\n")
