"""
speckit build — Mode A: guided greenfield spec-building session.

Flow:
  1.  Discovery Q&A (6-8 questions)
  2.  Draft vision doc → runs/greenfield-init/01_vision.md
  3.  Human reviews + approves vision
  4.  Propose tech stack → runs/greenfield-init/02_tech_stack.md
  5.  Human approves tech stack (edit file to tweak, then continue)
  6.  Human names modules (or accept auto-suggestions)
  7.  Generate all spec files:
        specs/architecture.md
        specs/security.md
        specs/coding-standards.md
        specs/data-models.md
        specs/modules/{module}.md  (per module)
  8.  Judge each spec for quality
  9.  Write scan_quality_report.md
  10. Run `speckit index` to embed everything

Requires at least one LLM backend in .env.
"""
from __future__ import annotations

import os
from pathlib import Path

import typer
from speckit.commands import load_env
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from speckit.core.config import load_config

console = Console()

_DISCOVERY_QUESTIONS = [
    ("project_name",    "What is the project name?"),
    ("purpose",         "In one sentence — what does it do and why does it exist?"),
    ("users",           "Who are the primary users and what is their technical level?"),
    ("features",        "List the 3-5 core features (comma-separated or one per line):"),
    ("deploy_target",   "Where will it be deployed? (e.g. web app, CLI tool, mobile backend, API)"),
    ("integrations",    "Any existing systems it must integrate with? (leave blank if none)"),
    ("constraints",     "Any hard technical constraints? (e.g. must use Python, must run on-prem)"),
    ("language_pref",   "Preferred primary language? (leave blank to let the agent recommend)"),
]


def _make_step_callback():
    def on_step(title: str, detail: str = "") -> None:
        detail_str = f"  [dim]{detail}[/dim]" if detail else ""
        console.print(f"  [bright_blue]◆[/bright_blue]  {title}{detail_str}")
    return on_step


def build_command(
    path: str = typer.Argument(default=".", help="Project root."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Accept generated artifacts without interactive approval."),
    model: str = typer.Option("", "--model", "-m", help="Override LLM model for this run."),
    coding_backend: str = typer.Option(
        "",
        "--coding-backend",
        help="Backend for spec generation: auto | anthropic | gemini | vertex.",
    ),
):
    """
    Start a guided greenfield spec-building session (Mode A).

    Walks through a discovery conversation, generates a vision doc, proposes
    a tech stack, then produces all spec files ready for review and indexing.

    All artifacts are saved to runs/greenfield-init/ and specs/.
    Requires ANTHROPIC_API_KEY or GEMINI_API_KEY / GEMINI_VERTEX in .env.
    """
    project_root = Path(path).resolve()
    load_env(project_root)

    console.print(Panel.fit(
        "[bold white]speckit build[/bold white]\n"
        "[dim]Mode A — guided greenfield spec builder[/dim]",
        border_style="bright_blue",
        padding=(1, 4),
    ))
    console.print()

    # ── load config ───────────────────────────────────────────────────────────
    try:
        config = load_config(project_root)
    except FileNotFoundError as e:
        console.print(f"  [red]✗[/red]  {e}\n")
        raise typer.Exit(1)

    if model:
        config.agent.model = model
    if coding_backend:
        config.agent.coding_backend = coding_backend

    # ── check backend ─────────────────────────────────────────────────────────
    has_gemini_vertex = os.environ.get("GEMINI_VERTEX", "").lower() in ("true", "1", "yes")
    has_gemini_key = bool(os.environ.get("GEMINI_API_KEY", ""))
    has_anthropic = bool(os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID", "")) or (
        os.environ.get("ANTHROPIC_API_KEY", "") not in ("", "paste-your-key-here", "sk-ant-...")
    )
    if not (has_gemini_vertex or has_gemini_key or has_anthropic):
        console.print(
            "  [red]✗[/red]  No LLM backend configured.\n\n"
            "  Add one of these to [cyan].env[/cyan]:\n"
            "    [dim]GEMINI_VERTEX=true[/dim]\n"
            "    [dim]GEMINI_API_KEY=AIza...[/dim]\n"
            "    [dim]ANTHROPIC_API_KEY=sk-ant-...[/dim]\n"
        )
        raise typer.Exit(1)

    # ── setup run directory ───────────────────────────────────────────────────
    runs_path = config.paths.runs.lstrip("./")
    run_dir = project_root / runs_path / "greenfield-init"
    run_dir.mkdir(parents=True, exist_ok=True)
    specs_dir = project_root / config.paths.specs.lstrip("./")
    modules_dir = specs_dir / "modules"
    specs_dir.mkdir(parents=True, exist_ok=True)
    modules_dir.mkdir(parents=True, exist_ok=True)

    from speckit.core.agents import (
        draft_vision, propose_tech_stack, generate_spec_from_vision,
        generate_design_doc, judge_scan_spec, reset_cost_log, get_cost_summary_md,
    )

    reset_cost_log()

    # ── step 1: discovery Q&A ─────────────────────────────────────────────────
    console.print("  [bold]Step 1 of 4[/bold] — Discovery\n")
    console.print(
        "  Answer these questions to build your project vision.\n"
        "  [dim]Press Enter to skip optional fields.[/dim]\n"
    )

    answers: dict[str, str] = {}
    for key, question in _DISCOVERY_QUESTIONS:
        required = key in ("project_name", "purpose", "users", "features", "deploy_target")
        prompt_suffix = "" if required else " [dim](optional)[/dim]"
        while True:
            val = typer.prompt(f"  {question}{prompt_suffix}", default="")
            if val.strip() or not required:
                answers[key] = val.strip()
                break
            console.print("  [red]This field is required.[/red]")

    console.print()

    # ── step 2: draft vision ──────────────────────────────────────────────────
    console.print("  [bold]Step 2 of 4[/bold] — Vision doc\n")
    console.print("  [dim]Drafting vision document…[/dim]")

    vision_md = draft_vision(answers, config, project_root)
    vision_path = run_dir / "01_vision.md"
    vision_path.write_text(vision_md, encoding="utf-8")
    console.print(f"  [green]✓[/green]  Vision doc saved → [cyan]{vision_path.relative_to(project_root)}[/cyan]\n")

    if not yes:
        console.print(Panel(Markdown(vision_md[:2000] + ("\n\n..." if len(vision_md) > 2000 else "")),
                            title="Vision preview", border_style="dim", padding=(0, 2)))
        console.print()
        ok = typer.confirm("  Does this vision look right?", default=True)
        if not ok:
            console.print(
                f"\n  [yellow]Edit[/yellow] [cyan]{vision_path.relative_to(project_root)}[/cyan] "
                "then re-run [bold]speckit build[/bold].\n"
            )
            raise typer.Exit(0)

    # ── step 3: tech stack proposal ───────────────────────────────────────────
    console.print("  [bold]Step 3 of 4[/bold] — Tech stack\n")
    console.print("  [dim]Researching and proposing tech stack…[/dim]")

    tech_stack_md = propose_tech_stack(
        vision_md=vision_md,
        deploy_target=answers.get("deploy_target", "web app"),
        primary_language=answers.get("language_pref", config.primary_language),
        config=config,
        project_root=project_root,
    )
    stack_path = run_dir / "02_tech_stack.md"
    stack_path.write_text(tech_stack_md, encoding="utf-8")
    console.print(f"  [green]✓[/green]  Tech stack saved → [cyan]{stack_path.relative_to(project_root)}[/cyan]\n")

    if not yes:
        console.print(Panel(Markdown(tech_stack_md[:2000] + ("\n\n..." if len(tech_stack_md) > 2000 else "")),
                            title="Tech stack preview", border_style="dim", padding=(0, 2)))
        console.print()
        ok = typer.confirm("  Does this tech stack look right?", default=True)
        if not ok:
            console.print(
                f"\n  [yellow]Edit[/yellow] [cyan]{stack_path.relative_to(project_root)}[/cyan] "
                "then re-run [bold]speckit build[/bold] with [bold]--yes[/bold] to skip prompts.\n"
            )
            raise typer.Exit(0)

    # ── step 4: module list ───────────────────────────────────────────────────
    console.print("  [bold]Step 4 of 4[/bold] — Spec generation\n")

    # Auto-suggest modules from features
    features_raw = answers.get("features", "")
    suggested_modules = _suggest_modules(features_raw, answers.get("deploy_target", ""))

    console.print(
        f"  Suggested modules: [cyan]{', '.join(suggested_modules)}[/cyan]\n"
        "  [dim]Enter custom list (comma-separated) or press Enter to accept:[/dim]"
    )
    custom = typer.prompt("  Modules", default="").strip()
    if custom:
        module_list = [m.strip().lower().replace(" ", "-") for m in custom.split(",") if m.strip()]
    else:
        module_list = suggested_modules

    console.print(f"\n  Generating specs for [bold]{len(module_list) + 4}[/bold] files…\n")

    generated: list[str] = []
    _step = _make_step_callback()

    # Generate root spec files
    for spec_type in ("architecture", "security", "coding-standards", "data-models"):
        fname = f"{spec_type}.md"
        out_path = specs_dir / fname
        _step(f"Generating {fname}")
        try:
            content = generate_spec_from_vision(
                spec_type=spec_type,
                vision_md=vision_md,
                tech_stack_md=tech_stack_md,
                module_list=module_list,
                config=config,
                project_root=project_root,
            )
            out_path.write_text(content.strip(), encoding="utf-8")
            generated.append(str(out_path.relative_to(project_root)))
        except Exception as e:
            console.print(f"  [red]✗[/red]  {fname}: {e}")

    # Generate per-module specs (reuse scan's generate_module_spec with empty files)
    from speckit.core.agents import generate_module_spec
    for module in module_list:
        fname = f"modules/{module}.md"
        out_path = modules_dir / f"{module}.md"
        _step(f"Generating {fname}")
        try:
            # Pass vision + tech_stack as "file context" so the agent has enough context
            content = generate_module_spec(
                module_name=module,
                file_contents={"vision.md": vision_md[:1500], "tech_stack.md": tech_stack_md[:800]},
                language=answers.get("language_pref", config.primary_language) or config.primary_language,
                config=config,
                project_root=project_root,
            )
            out_path.write_text(content.strip(), encoding="utf-8")
            generated.append(str(out_path.relative_to(project_root)))
        except Exception as e:
            console.print(f"  [red]✗[/red]  {fname}: {e}")

    # ── design doc ───────────────────────────────────────────────────────────
    _step("Generating design_notes.md")
    try:
        arch_content = (specs_dir / "architecture.md").read_text(encoding="utf-8") if (specs_dir / "architecture.md").exists() else ""
        design_md = generate_design_doc(
            vision_md=vision_md,
            tech_stack_md=tech_stack_md,
            architecture_spec=arch_content,
            module_list=module_list,
            config=config,
            project_root=project_root,
        )
        design_path = run_dir / "03_design_notes.md"
        design_path.write_text(design_md, encoding="utf-8")
        console.print(f"  [green]✓[/green]  Design doc saved → [cyan]{design_path.relative_to(project_root)}[/cyan]")
    except Exception as e:
        console.print(f"  [yellow]⚠[/yellow]  Design doc skipped: {e}")

    # ── quality judge ─────────────────────────────────────────────────────────
    console.print()
    console.print("  [dim]Running quality judge…[/dim]\n")
    _run_quality_judge(generated, specs_dir, project_root, config)

    # ── cost summary ──────────────────────────────────────────────────────────
    cost_md = get_cost_summary_md()
    if cost_md:
        (run_dir / "00_token_usage.md").write_text(cost_md, encoding="utf-8")
        console.print(f"\n  [dim]Token usage saved → runs/greenfield-init/00_token_usage.md[/dim]")

    # ── done ──────────────────────────────────────────────────────────────────
    console.print()
    console.print(Panel.fit(
        f"  [bold green]Greenfield specs generated![/bold green]\n\n"
        f"  [bold]{len(generated)}[/bold] spec files written to [cyan]specs/[/cyan]\n\n"
        "  [yellow]Next steps:[/yellow]\n"
        "  1. [dim]Review specs — fix quality judge warnings[/dim]\n"
        "  2. [dim]Review[/dim] [cyan]runs/greenfield-init/03_design_notes.md[/cyan] [dim]— add Figma link[/dim]\n"
        "  3. Run [bold]speckit index[/bold] [dim]— build the search index[/dim]\n"
        "  4. Run [bold]speckit feature --name <feature>[/bold] [dim]— spec your first feature[/dim]",
        border_style="green",
        padding=(0, 2),
    ))
    console.print()


def _suggest_modules(features: str, deploy_target: str) -> list[str]:
    """Heuristically suggest module names from feature descriptions."""
    import re

    deploy_target = deploy_target.lower()
    modules = []

    # Common modules based on deploy target
    if "web" in deploy_target or "api" in deploy_target:
        modules += ["api", "auth", "users"]
    elif "cli" in deploy_target:
        modules += ["cli", "core"]
    else:
        modules += ["core"]

    # Extract nouns from feature list as potential modules
    words = re.findall(r'\b[a-z]{4,}\b', features.lower())
    _NOISE = {"will", "that", "this", "with", "from", "have", "been", "user", "able",
              "each", "into", "them", "they", "also", "then", "when", "what", "some"}
    noun_candidates = [w for w in dict.fromkeys(words) if w not in _NOISE][:4]

    for candidate in noun_candidates:
        slug = candidate.rstrip("s")  # crude singularize
        if slug not in modules and len(slug) >= 3:
            modules.append(slug)

    return modules[:6]  # cap at 6 modules


def _run_quality_judge(
    generated: list[str],
    specs_dir: Path,
    project_root: Path,
    config,
) -> None:
    from speckit.core.agents import judge_scan_spec

    _SEVERITY_COLOR = {"error": "red", "warning": "yellow", "info": "dim"}
    _SEVERITY_ICON = {"error": "✗", "warning": "⚠", "info": "·"}
    any_issues = False
    report_lines = ["# Spec quality report\n"]

    for rel_path in generated:
        abs_path = project_root / rel_path
        if not abs_path.exists():
            continue

        name = abs_path.name
        if name == "architecture.md":
            spec_type = "architecture"
        elif abs_path.parent.name == "modules":
            spec_type = "module"
        elif name == "security.md":
            spec_type = "security"
        elif name == "coding-standards.md":
            spec_type = "coding-standards"
        else:
            continue

        try:
            content = abs_path.read_text(encoding="utf-8")
            result = judge_scan_spec(name, spec_type, content, config, project_root)
        except Exception as e:
            console.print(f"  [dim]Judge failed for {name}: {e}[/dim]")
            continue

        verdict_color = {"good": "green", "needs-review": "yellow", "poor": "red"}.get(result.verdict, "dim")
        console.print(
            f"  [{verdict_color}]{'●' if result.issues else '✓'}[/{verdict_color}]  "
            f"[cyan]{rel_path}[/cyan]  [{verdict_color}]{result.verdict}[/{verdict_color}]  "
            f"[dim](score {result.score:.2f})[/dim]"
        )
        report_lines.append(f"\n## {rel_path} — {result.verdict} (score {result.score:.2f})\n")
        if result.issues:
            any_issues = True
            for issue in result.issues:
                sev = issue.severity
                color = _SEVERITY_COLOR.get(sev, "dim")
                icon = _SEVERITY_ICON.get(sev, "·")
                console.print(
                    f"       [{color}]{icon}[/{color}]  [{color}]{issue.message}[/{color}]\n"
                    f"          [dim]→ {issue.suggestion}[/dim]"
                )
                report_lines.append(f"- **{sev.upper()}**: {issue.message}\n  → {issue.suggestion}")
        else:
            report_lines.append("No issues found.")

    report_path = specs_dir / "scan_quality_report.md"
    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    if any_issues:
        console.print(
            f"\n  [yellow]⚠[/yellow]  Quality issues found — "
            f"review [cyan]specs/scan_quality_report.md[/cyan] before indexing.\n"
        )
    else:
        console.print("\n  [green]✓[/green]  All specs passed quality review.\n")
