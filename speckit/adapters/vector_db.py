"""Vector DB factory — auto-routes to Supabase or local JSON index."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Union

from speckit.core.config import SpeckitConfig, VectorDBProvider

if TYPE_CHECKING:
    from speckit.adapters.local_index import LocalIndex
    from speckit.adapters.supabase_index import SupabaseIndex

IndexAdapter = Union["LocalIndex", "SupabaseIndex"]


def get_adapter(config: SpeckitConfig, project_root: Path) -> IndexAdapter:
    """
    Return the best available index adapter.

    Decision order:
      1. If config says Supabase AND all env vars are set → SupabaseIndex
      2. If config says Supabase but env vars are missing → warn, fall back to LocalIndex
      3. Otherwise → LocalIndex (always available, no deps)
    """
    if config.vector_db.provider == VectorDBProvider.SUPABASE:
        from speckit.adapters.supabase_index import SupabaseIndex
        adapter = SupabaseIndex(project_name=config.project_name)
        if adapter.is_configured():
            return adapter

        # Supabase wanted but not fully configured — warn and fall back
        missing = adapter.missing_vars()
        from rich.console import Console
        Console().print(
            f"  [yellow]⚠[/yellow]  Supabase configured but missing env vars: "
            f"{', '.join(missing)}. Using local index instead.\n"
            "  Set them in [cyan].env[/cyan] to enable Supabase."
        )

    from speckit.adapters.local_index import LocalIndex
    return LocalIndex(project_root=project_root, project_name=config.project_name)


def search_specs(
    query: str,
    config: SpeckitConfig,
    project_root: Path,
    top_k: int = 5,
) -> list[dict]:
    """
    Search indexed specs for the given query.

    Returns a list of spec dicts sorted by relevance. Each dict contains:
      path, module, affects, source_files, title, summary, content
    """
    adapter = get_adapter(config, project_root)
    return adapter.search(query, top_k=top_k)
