"""
speckit — Spec-Driven Development CLI
"""
import typer
from rich.console import Console

from speckit.commands.init import init_command
from speckit.commands.scan import scan_command
from speckit.commands.build import build_command
from speckit.commands.run import run_command
from speckit.commands.feature import feature_command
from speckit.commands.index import index_command
from speckit.commands.serve import serve_command
from speckit.commands.mcp import mcp_command
from speckit.commands.orchestrate import orchestrate_command
from speckit.commands.orchestrate_bug import orchestrate_bug_command
from speckit.commands.brownfield_init import brownfield_init_command

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
app.command("feature", help="Run the spec-driven feature pipeline.")(feature_command)
app.command("index", help="Embed spec files into the vector database.")(index_command)
app.command("serve", help="Start the GitHub webhook server.")(serve_command)
app.command("mcp", help="Start the MCP server for Claude Pro integration.")(mcp_command)
app.command("orchestrate", help="Run a cross-service feature pipeline.")(orchestrate_command)
app.command("orchestrate-bug", help="Run a cross-service bug-fix pipeline.")(orchestrate_bug_command)
app.command(
    "brownfield-init",
    help="Bootstrap speckit for an existing multi-service codebase (no specs yet).",
)(brownfield_init_command)


def main():
    app()


if __name__ == "__main__":
    main()
