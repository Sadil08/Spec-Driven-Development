---
module: adapters
affects: [pipeline, commands]
files: [speckit/adapters/vector_db.py, speckit/adapters/local_index.py, speckit/adapters/supabase_index.py, speckit/adapters/github.py, speckit/adapters/shell.py]
last_updated: 2026-05-09
---

# Module: adapters

## Purpose
External integrations behind stable interfaces. The pipeline code never imports
adapter implementations directly — it calls through vector_db.py (factory) and
the GitHubAdapter class.

## Public interface
| Name | File | Description |
|------|------|-------------|
| get_adapter(config, project_root) | vector_db.py | Returns best available index adapter |
| search_specs(query, config, project_root, top_k) | vector_db.py | One-call spec search |
| LocalIndex | local_index.py | BM25 index stored at .speckit/index.json |
| SupabaseIndex | supabase_index.py | pgvector index via Supabase + OpenAI embeddings |
| GitHubAdapter | github.py | GitHub REST v3: issues, branches, file contents, PRs |
| ShellAdapter | shell.py | Safe subprocess runner with executable allowlist |

## Adapter selection (vector_db.py)
1. If sdd.config.yml sets `vector_db.provider: supabase` AND
   SPECKIT_VECTOR_DB_URL + SPECKIT_VECTOR_DB_KEY + OPENAI_API_KEY are all set → SupabaseIndex
2. Otherwise → LocalIndex (always available, zero external deps)
A Rich warning is printed when Supabase is configured but env vars are missing.

## LocalIndex (BM25)
- Index stored at: `.speckit/index.json`
- Build: `adapter.build(spec_files)` — tokenise, compute TF, compute IDF, persist JSON
- Search: BM25 scoring (k1=1.5, b=0.75) against tokenised query
- Frontmatter fields weighted higher than body (module×5, title×3, affects×3, body×1)
- `is_built()` returns True if index.json exists

## SupabaseIndex
- Requires: `pip install 'speckit[supabase]'` (adds supabase + openai packages)
- Embedding model: text-embedding-3-small (1536 dims)
- Table: speckit_files with pgvector column
- Setup SQL: `speckit index --setup-sql`
- Upsert on (project, path) — re-indexing is idempotent

## GitHubAdapter
- Auth: Bearer token from GITHUB_TOKEN env var
- Repo: GITHUB_REPO env var (format: org/repo-name)
- Key methods: get_issue, add_comment, create_branch, get_file_contents, list_files, create_pr
- All HTTP via httpx with 20s timeout

## ShellAdapter
- Allowed executables: pytest, python, python3, npm, npx, yarn, pnpm, go, cargo,
  make, jest, vitest, mocha
- Any other executable raises ValueError — no arbitrary command execution
- Default timeout: 300s (5 min)

## Dependencies
- Depends on: core/config (SpeckitConfig, VectorDBProvider)
- Depended on by: commands/index, modes/bug_fix

## Security notes
- GitHub token never logged
- ShellAdapter allowlist prevents command injection via untrusted spec content
- SupabaseIndex keys loaded from env vars only, never from config file
