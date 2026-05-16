"""Parse spec markdown files — extract frontmatter and content."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class SpecFile:
    path: str               # relative path from project root
    module: str             # from frontmatter or inferred from filename
    affects: list[str] = field(default_factory=list)
    source_files: list[str] = field(default_factory=list)
    last_updated: str = ""
    title: str = ""
    summary: str = ""
    content: str = ""       # full markdown body (after frontmatter stripped)


def _parse_frontmatter(raw: str) -> tuple[dict, str]:
    """Split YAML frontmatter from markdown body."""
    if not raw.startswith("---"):
        return {}, raw
    parts = raw.split("---", 2)
    if len(parts) < 3:
        return {}, raw
    try:
        fm = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        fm = {}
    return fm, parts[2].strip()


def _first_h1(text: str) -> str:
    m = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    return m.group(1).strip() if m else ""


def _first_paragraph(text: str) -> str:
    """Return first non-header, non-table, non-quote paragraph (max 300 chars)."""
    for line in text.splitlines():
        line = line.strip()
        if (
            line
            and not line.startswith("#")
            and not line.startswith("|")
            and not line.startswith(">")
            and not line.startswith("```")
            and not line.startswith("- ")
            and not line.startswith("* ")
        ):
            return line[:300]
    return ""


def parse_spec_file(file_path: Path, project_root: Path) -> Optional[SpecFile]:
    """Parse a single spec markdown file. Returns None if empty or unparseable."""
    try:
        raw = file_path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None

    if not raw or raw.startswith("# module specs will be") or raw.startswith("# speckit"):
        return None

    fm, body = _parse_frontmatter(raw)
    relative_path = str(file_path.relative_to(project_root))

    # Resolve module name: frontmatter > filename stem
    module = str(fm.get("module", "") or fm.get("project", "") or file_path.stem)

    affects = fm.get("affects", []) or []
    if isinstance(affects, str):
        affects = [a.strip() for a in affects.split(",") if a.strip()]

    source_files = fm.get("files", []) or []
    if isinstance(source_files, str):
        source_files = [f.strip() for f in source_files.split(",") if f.strip()]

    return SpecFile(
        path=relative_path,
        module=module,
        affects=list(affects),
        source_files=list(source_files),
        last_updated=str(fm.get("last_updated", "")),
        title=_first_h1(body) or file_path.stem.replace("-", " ").title(),
        summary=_first_paragraph(body),
        content=body,
    )


def discover_spec_files(project_root: Path, specs_path: str = "./specs") -> list[SpecFile]:
    """Walk the specs directory and parse all .md files."""
    specs_dir = (project_root / specs_path).resolve()
    if not specs_dir.exists():
        return []

    spec_files: list[SpecFile] = []
    for md_file in sorted(specs_dir.rglob("*.md")):
        if md_file.name.startswith("."):
            continue
        spec = parse_spec_file(md_file, project_root)
        if spec:
            spec_files.append(spec)

    return spec_files
