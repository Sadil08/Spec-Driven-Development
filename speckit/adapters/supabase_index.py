"""Supabase pgvector spec index — uses OpenAI text-embedding-3-small."""
from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from speckit.core.spec_parser import SpecFile

# SQL the user must run once in their Supabase SQL editor
SUPABASE_SETUP_SQL = """\
-- Run this once in your Supabase SQL editor.
-- Enables pgvector and creates the speckit_files table + similarity search function.

create extension if not exists vector;

create table if not exists speckit_files (
  id           uuid primary key default gen_random_uuid(),
  project      text not null,
  path         text not null,
  module       text,
  affects      text[],
  source_files text[],
  last_updated text,
  title        text,
  summary      text,
  content      text,
  embedding    vector(1536),
  unique (project, path)
);

create index if not exists speckit_files_embedding_idx
  on speckit_files using hnsw (embedding vector_cosine_ops);

create or replace function search_speckit_files(
  query_embedding vector(1536),
  match_project   text,
  match_count     int default 5
) returns table (
  path         text,
  module       text,
  affects      text[],
  source_files text[],
  content      text,
  title        text,
  summary      text,
  similarity   float
) language sql stable as $$
  select
    path, module, affects, source_files,
    content, title, summary,
    1 - (embedding <=> query_embedding) as similarity
  from speckit_files
  where project = match_project
  order by embedding <=> query_embedding
  limit match_count;
$$;
"""

_EMBED_MODEL = "text-embedding-3-small"
_EMBED_DIMS = 1536
_MAX_EMBED_CHARS = 8000


class SupabaseIndex:
    """
    Supabase pgvector index for spec files.

    Requires:
      - supabase Python package  (pip install 'speckit[supabase]')
      - openai Python package    (pip install 'speckit[supabase]')
      - SPECKIT_VECTOR_DB_URL    env var
      - SPECKIT_VECTOR_DB_KEY    env var
      - OPENAI_API_KEY           env var
    """

    def __init__(
        self,
        project_name: str,
        supabase_url: str = "",
        supabase_key: str = "",
        openai_api_key: str = "",
    ):
        self.project_name = project_name
        self.supabase_url = supabase_url or os.environ.get("SPECKIT_VECTOR_DB_URL", "")
        self.supabase_key = supabase_key or os.environ.get("SPECKIT_VECTOR_DB_KEY", "")
        self.openai_api_key = openai_api_key or os.environ.get("OPENAI_API_KEY", "")
        self._client = None
        self._openai = None

    # ── configuration check ──────────────────────────────────────────────────

    def is_configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_key and self.openai_api_key)

    def missing_vars(self) -> list[str]:
        missing = []
        if not self.supabase_url:
            missing.append("SPECKIT_VECTOR_DB_URL")
        if not self.supabase_key:
            missing.append("SPECKIT_VECTOR_DB_KEY")
        if not self.openai_api_key:
            missing.append("OPENAI_API_KEY")
        return missing

    # ── lazy clients ─────────────────────────────────────────────────────────

    def _get_supabase(self):
        if self._client is None:
            try:
                from supabase import create_client
            except ImportError:
                raise ImportError(
                    "supabase package not installed. Run: pip install 'speckit[supabase]'"
                )
            self._client = create_client(self.supabase_url, self.supabase_key)
        return self._client

    def _get_openai(self):
        if self._openai is None:
            try:
                from openai import OpenAI
            except ImportError:
                raise ImportError(
                    "openai package not installed. Run: pip install 'speckit[supabase]'"
                )
            self._openai = OpenAI(api_key=self.openai_api_key)
        return self._openai

    # ── embeddings ───────────────────────────────────────────────────────────

    def _embed(self, text: str) -> list[float]:
        response = self._get_openai().embeddings.create(
            model=_EMBED_MODEL,
            input=text[:_MAX_EMBED_CHARS],
        )
        return response.data[0].embedding

    def _spec_embed_text(self, spec: "SpecFile") -> str:
        """Compose the text that gets embedded for a spec file."""
        parts = [
            f"Module: {spec.module}",
            f"Title: {spec.title}",
        ]
        if spec.affects:
            parts.append(f"Affects: {', '.join(spec.affects)}")
        if spec.source_files:
            parts.append(f"Files: {', '.join(spec.source_files)}")
        if spec.summary:
            parts.append(spec.summary)
        parts.append(spec.content[:2000])
        return "\n".join(parts)

    # ── build ────────────────────────────────────────────────────────────────

    def build(self, spec_files: list["SpecFile"]) -> int:
        """Upsert all spec files into Supabase. Returns number indexed."""
        client = self._get_supabase()
        count = 0
        for spec in spec_files:
            embedding = self._embed(self._spec_embed_text(spec))
            client.table("speckit_files").upsert(
                {
                    "project": self.project_name,
                    "path": spec.path,
                    "module": spec.module,
                    "affects": spec.affects,
                    "source_files": spec.source_files,
                    "last_updated": spec.last_updated,
                    "title": spec.title,
                    "summary": spec.summary,
                    "content": spec.content,
                    "embedding": embedding,
                },
                on_conflict="project,path",
            ).execute()
            count += 1
        return count

    # ── search ───────────────────────────────────────────────────────────────

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        """Vector similarity search. Returns top_k spec dicts."""
        client = self._get_supabase()
        embedding = self._embed(query)
        result = client.rpc(
            "search_speckit_files",
            {
                "query_embedding": embedding,
                "match_project": self.project_name,
                "match_count": top_k,
            },
        ).execute()
        return result.data or []

    # ── helpers ──────────────────────────────────────────────────────────────

    def setup_sql(self) -> str:
        return SUPABASE_SETUP_SQL
