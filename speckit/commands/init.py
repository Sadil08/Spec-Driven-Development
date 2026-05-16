"""
speckit init — interactive project initialisation.

Asks the user which mode they're in, gathers project details,
then scaffolds the correct directory structure and config.
"""
from __future__ import annotations

import os
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich import print as rprint

from speckit.core.config import (
    AgentConfig,
    GitHubConfig,
    NotificationsConfig,
    PathsConfig,
    ProjectMode,
    SpeckitConfig,
    TestingConfig,
    VectorDBConfig,
    VectorDBProvider,
    config_exists,
    save_config,
    CONFIG_FILENAME,
)
from speckit.templates.files import (
    ARCHITECTURE_MD,
    CODING_STANDARDS_MD,
    DATA_MODELS_MD,
    DOT_ENV_EXAMPLE,
    GITHUB_ACTION_YML,
    GLOBAL_LEARNINGS_MD,
    MODULE_MD,
    SECURITY_MD,
    SPECKIT_GITIGNORE,
    today,
)

console = Console()


# ─── helpers ──────────────────────────────────────────────────────────────────

def _header():
    console.print(Panel.fit(
        "[bold white]speckit init[/bold white]\n"
        "[dim]Spec-Driven Development — project initialisation[/dim]",
        border_style="bright_blue",
        padding=(1, 4),
    ))


def _section(title: str):
    console.print(f"\n[bold bright_blue]{'─' * 2} {title}[/bold bright_blue]")


def _success(msg: str):
    console.print(f"  [bold green]✓[/bold green]  {msg}")


def _info(msg: str):
    console.print(f"  [dim]·[/dim]  [dim]{msg}[/dim]")


def _warn(msg: str):
    console.print(f"  [yellow]⚠[/yellow]  [yellow]{msg}[/yellow]")


def _write_file(path: Path, content: str, label: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    _success(f"Created [cyan]{path.relative_to(path.parents[len(path.parts) - 2])}[/cyan]  [dim]— {label}[/dim]")


# ─── mode selection ───────────────────────────────────────────────────────────

def _ask_mode() -> ProjectMode:
    _section("Project mode")
    console.print(
        "  [dim]Speckit works in four modes depending on your project's state.[/dim]\n"
    )

    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("Option", style="bold bright_blue", width=4)
    table.add_column("Mode", width=28)
    table.add_column("When to use", style="dim")
    table.add_row("1", "Greenfield", "Starting a brand-new project from scratch")
    table.add_row("2", "Brownfield — specs exist", "Code + specs exist, adding features / fixing bugs")
    table.add_row("3", "Brownfield — no specs", "Existing codebase, no spec files yet")
    console.print(table)
    console.print()

    choice = Prompt.ask(
        "  [bold]Choose a mode[/bold]",
        choices=["1", "2", "3"],
        default="1",
    )

    mode_map = {
        "1": ProjectMode.GREENFIELD,
        "2": ProjectMode.BROWNFIELD_WITH_SPECS,
        "3": ProjectMode.BROWNFIELD_NO_SPECS,
    }
    return mode_map[choice]


# ─── project info ─────────────────────────────────────────────────────────────

def _ask_project_info(project_root: Path) -> dict:
    _section("Project details")

    default_name = project_root.name
    name = Prompt.ask(f"  Project name", default=default_name)

    description = Prompt.ask(
        "  One-line description",
        default="A production-grade application built with speckit.",
    )

    repo = Prompt.ask(
        "  GitHub repo [dim](org/repo-name)[/dim]",
        default="",
    )
    if repo and "/" not in repo:
        _warn("Repo format should be org/repo-name. You can update sdd.config.yml later.")
        repo = ""

    console.print()
    console.print("  [dim]Primary language / stack:[/dim]")
    lang_table = Table(show_header=False, box=None, padding=(0, 2))
    lang_table.add_column(width=4)
    lang_table.add_column()
    lang_table.add_row("1", "Python")
    lang_table.add_row("2", "TypeScript / Node")
    lang_table.add_row("3", "Go")
    lang_table.add_row("4", "Other")
    console.print(lang_table)

    lang_choice = Prompt.ask("  Choose", choices=["1", "2", "3", "4"], default="1")
    lang_map = {"1": "python", "2": "typescript", "3": "go", "4": "other"}
    lang = lang_map[lang_choice]

    pkg_map = {"python": "pip / uv", "typescript": "pnpm", "go": "go modules", "other": "—"}

    return {
        "name": name,
        "description": description,
        "repo": repo,
        "language": lang,
        "package_manager": pkg_map[lang],
    }


# ─── optional features ────────────────────────────────────────────────────────

def _ask_optional_features() -> dict:
    _section("Optional features")
    console.print("  [dim]These can be configured later in sdd.config.yml[/dim]\n")

    use_vector = Confirm.ask(
        "  Enable vector DB for spec search? [dim](improves accuracy, reduces tokens)[/dim]",
        default=False,
    )

    vector_provider = VectorDBProvider.NONE
    if use_vector:
        console.print()
        v_choice = Prompt.ask(
            "  Vector DB provider",
            choices=["supabase", "qdrant"],
            default="supabase",
        )
        vector_provider = VectorDBProvider(v_choice)

    use_slack = Confirm.ask(
        "\n  Enable Slack notifications?",
        default=False,
    )
    slack_channel = ""
    if use_slack:
        slack_channel = Prompt.ask("  Slack channel", default="#engineering")

    return {
        "vector_provider": vector_provider,
        "slack_channel": slack_channel if use_slack else "",
    }


# ─── scaffolding ──────────────────────────────────────────────────────────────

def _scaffold_specs(project_root: Path, info: dict, mode: ProjectMode):
    """Write the /specs directory based on mode."""
    specs_dir = project_root / "specs" / "modules"
    specs_dir.mkdir(parents=True, exist_ok=True)

    common = {
        "project_name": info["name"],
        "date": today(),
        "mode": mode.value,
        "description": info["description"],
        "primary_language": info["language"],
        "package_manager": info["package_manager"],
    }

    if mode == ProjectMode.BROWNFIELD_WITH_SPECS:
        # Specs already exist — don't overwrite
        _info("Specs directory already exists — not overwriting.")
        return

    _write_file(
        project_root / "specs" / "architecture.md",
        ARCHITECTURE_MD.format(**common),
        "system design, principles, tech stack",
    )
    _write_file(
        project_root / "specs" / "coding-standards.md",
        CODING_STANDARDS_MD.format(**common),
        "naming, imports, git conventions",
    )
    _write_file(
        project_root / "specs" / "security.md",
        SECURITY_MD.format(**common),
        "auth, validation, secrets policy",
    )
    _write_file(
        project_root / "specs" / "data-models.md",
        DATA_MODELS_MD.format(**common),
        "entities, relationships, validation",
    )
    _write_file(
        project_root / "specs" / "global_learnings.md",
        GLOBAL_LEARNINGS_MD.format(**common),
        "growing pattern library — append-only",
    )

    if mode == ProjectMode.BROWNFIELD_NO_SPECS:
        _write_file(
            project_root / "specs" / "modules" / ".gitkeep",
            "# module specs will be generated by `speckit scan`\n",
            "placeholder",
        )
    else:
        # Greenfield — empty module placeholder
        _write_file(
            project_root / "specs" / "modules" / ".gitkeep",
            "# module specs will be added as you define modules\n",
            "placeholder",
        )


def _scaffold_runs(project_root: Path):
    runs_dir = project_root / "runs"
    runs_dir.mkdir(exist_ok=True)
    gitkeep = runs_dir / ".gitkeep"
    if not gitkeep.exists():
        gitkeep.write_text(
            "# speckit stores all agent run artifacts here\n"
            "# Each run gets its own directory: runs/bug-fix-{n}/ or runs/feature-{name}/\n"
        )
        _success(f"Created [cyan]runs/[/cyan]  [dim]— agent run artifacts[/dim]")


def _scaffold_github_action(project_root: Path):
    workflows_dir = project_root / ".github" / "workflows"
    workflows_dir.mkdir(parents=True, exist_ok=True)
    action_path = workflows_dir / "speckit.yml"
    if action_path.exists():
        _info(".github/workflows/speckit.yml already exists — not overwriting.")
        return
    action_path.write_text(GITHUB_ACTION_YML)
    _success("Created [cyan].github/workflows/speckit.yml[/cyan]  [dim]— GitHub Actions trigger[/dim]")


def _scaffold_speckit_dir(project_root: Path):
    speckit_dir = project_root / ".speckit"
    prompts_dir = speckit_dir / "prompts"
    hooks_dir = speckit_dir / "hooks"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    hooks_dir.mkdir(parents=True, exist_ok=True)

    readme = speckit_dir / "README.md"
    if not readme.exists():
        readme.write_text(
            "# .speckit\n\n"
            "Internal speckit directory.\n\n"
            "- `prompts/` — agent system prompts (edit to tune agent behaviour)\n"
            "- `hooks/`   — pre/post run hooks (optional shell scripts)\n"
        )
    _success("Created [cyan].speckit/[/cyan]  [dim]— prompts and hooks[/dim]")


def _scaffold_env(project_root: Path):
    env_example = project_root / ".env.example"
    if not env_example.exists():
        env_example.write_text(DOT_ENV_EXAMPLE)
        _success("Created [cyan].env.example[/cyan]  [dim]— environment variable template[/dim]")
    else:
        _info(".env.example already exists — not overwriting.")

    gitignore = project_root / ".gitignore"
    if gitignore.exists():
        content = gitignore.read_text()
        if "speckit" not in content:
            with open(gitignore, "a") as f:
                f.write("\n" + SPECKIT_GITIGNORE)
            _info("Appended speckit entries to .gitignore")
    else:
        gitignore.write_text(SPECKIT_GITIGNORE)
        _success("Created [cyan].gitignore[/cyan]")


# ─── next steps ───────────────────────────────────────────────────────────────

def _print_next_steps(mode: ProjectMode, info: dict):
    _section("Next steps")

    steps = {
        ProjectMode.GREENFIELD: [
            ("1", "Fill in [cyan]specs/architecture.md[/cyan]", "Add your tech stack, principles, module map"),
            ("2", "Run [bold]speckit build[/bold]", "Start the guided spec-building conversation"),
            ("3", "Human review all spec files", "Approve before any code is written"),
            ("4", "Run [bold]speckit index[/bold]", "Embed specs into vector DB (if configured)"),
        ],
        ProjectMode.BROWNFIELD_WITH_SPECS: [
            ("1", "Run [bold]speckit index[/bold]", "Embed your existing specs into the vector DB"),
            ("2", "Open a GitHub issue", "Label it 'bug' or 'feature' — speckit handles the rest"),
            ("3", "Review [cyan]sdd.config.yml[/cyan]", "Check paths match your project structure"),
        ],
        ProjectMode.BROWNFIELD_NO_SPECS: [
            ("1", "Run [bold]speckit scan[/bold]", "Generate spec files from your existing codebase"),
            ("2", "Review generated specs", "Human review before anything is automated"),
            ("3", "Run [bold]speckit index[/bold]", "Embed specs into vector DB"),
            ("4", "Open a GitHub issue", "Label it 'bug' or 'feature'"),
        ],
    }

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(width=3, style="bold bright_blue")
    table.add_column(width=36)
    table.add_column(style="dim")

    for step in steps[mode]:
        table.add_row(*step)

    console.print(table)

    if not info.get("repo"):
        console.print()
        _warn(
            "No GitHub repo set. Add [cyan]repo: org/repo-name[/cyan] "
            "to [cyan]sdd.config.yml[/cyan] before running automations."
        )

    console.print()
    console.print(
        Panel.fit(
            f"  [bold green]speckit is ready[/bold green] in "
            f"[cyan]{info['name']}[/cyan]  "
            f"[dim]({mode.value})[/dim]  ",
            border_style="green",
            padding=(0, 2),
        )
    )


# ─── main command ─────────────────────────────────────────────────────────────

def init_command(
    path: str = typer.Argument(
        default=".",
        help="Project root directory. Defaults to current directory.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Reinitialise even if sdd.config.yml already exists.",
    ),
):
    """
    Initialise speckit in a project.

    Asks which mode you're in, gathers project details, then scaffolds
    the spec directory structure, config file, and GitHub Action.
    """
    project_root = Path(path).resolve()

    _header()

    # Guard: already initialised
    if config_exists(project_root) and not force:
        _warn(
            f"[cyan]{CONFIG_FILENAME}[/cyan] already exists in this directory.\n"
            "  Use [bold]speckit init --force[/bold] to reinitialise."
        )
        raise typer.Exit(1)

    console.print(f"\n  [dim]Initialising in:[/dim] [cyan]{project_root}[/cyan]")

    # ── gather input ──
    mode = _ask_mode()
    info = _ask_project_info(project_root)
    features = _ask_optional_features()

    # ── build config object ──
    config = SpeckitConfig(
        project_name=info["name"],
        repo=info["repo"],
        mode=mode,
        primary_language=info["language"],
        description=info["description"],
        paths=PathsConfig(),
        agent=AgentConfig(),
        vector_db=VectorDBConfig(
            provider=features["vector_provider"],
            index_name=f"{info['name'].lower().replace(' ', '-')}-specs",
        ),
        github=GitHubConfig(),
        testing=TestingConfig(),
        notifications=NotificationsConfig(
            slack_channel=features["slack_channel"] or "#engineering",
        ),
    )

    # ── scaffold files ──
    _section("Scaffolding")
    console.print()

    _scaffold_specs(project_root, info, mode)
    _scaffold_runs(project_root)
    _scaffold_github_action(project_root)
    _scaffold_speckit_dir(project_root)
    _scaffold_env(project_root)

    config_path = save_config(config, project_root)
    _success(f"Created [cyan]{CONFIG_FILENAME}[/cyan]  [dim]— project configuration[/dim]")

    # ── next steps ──
    _print_next_steps(mode, info)
