"""
speckit — Spec-Driven Development CLI
"""
import typer
from rich.console import Console

from speckit.commands.init import init_command
from speckit.commands.scan import scan_command
from speckit.commands.build import build_command
from speckit.commands.run import run_command
from speckit.commands.index import index_command

app = typer.Typer(
    name="speckit",
    help="Spec-Driven Development — build software the right way.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
console = Console()

app.command("init", help="Initialize speckit in a project.")(init_command)
app.command("scan", help="Generate spec files from an existing codebase.")(scan_command)
app.command("build", help="Start a guided greenfield spec-building session.")(build_command)
app.command("run", help="Manually trigger a bug-fix or feature run.")(run_command)
app.command("index", help="Embed spec files into the vector database.")(index_command)


def main():
    app()


if __name__ == "__main__":
    main()
