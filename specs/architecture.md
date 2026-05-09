---
project: speckit
version: 0.1.0
last_updated: 2026-05-09
mode: brownfield-no-specs
---

# Architecture overview

## Purpose
speckit is a spec-driven development CLI that automates bug-fix and feature workflows
using Claude AI. It reads structured spec files to understand a codebase without
scanning every source file, then uses a judge loop to ensure all proposed changes
respect the project's architecture and security rules before any code is written.

## Core principles
1. Spec-first: agents read spec files, not raw source code — keeps token cost low
2. Judge before code: no code is written until a spec is approved by the judge loop
3. Config-driven: all behaviour comes from sdd.config.yml — no hardcoded values
4. Fail loudly: missing API keys, config errors, and bad commands surface immediately
5. No secrets in code: all credentials via environment variables loaded from .env
6. Human in the loop: agents never auto-merge; humans always review and approve PRs

## System design
CLI tool written in Python with Typer. User runs `speckit <command>` in a project
directory. Commands read sdd.config.yml, load .env, and orchestrate agents that
call the Claude API. All run artifacts (classification, bug report, test plan) are
written to the runs/ directory as markdown files for human review.

## Tech stack
| Layer       | Technology          | Version  | Reason chosen                        |
|-------------|---------------------|----------|--------------------------------------|
| CLI         | Typer               | 0.12+    | Type-safe, auto-help, clean UX       |
| UI          | Rich                | 13+      | Terminal panels, tables, progress     |
| AI          | Anthropic SDK       | 0.28+    | Claude claude-sonnet-4-6 for agents  |
| Config      | Pydantic v2         | 2.7+     | Validated config models              |
| Config file | PyYAML              | 6+       | sdd.config.yml parsing               |
| HTTP        | httpx               | 0.27+    | GitHub REST API calls                |
| Env vars    | python-dotenv       | 1.0+     | Load .env without shell exports      |
| Vector DB   | local JSON (BM25)   | built-in | Zero-dep local index, Supabase opt.  |
| Packaging   | setuptools          | 68+      | pyproject.toml, editable installs    |

## Runtime environment
- OS: Ubuntu 22.04 (dev), any Linux/macOS (users)
- Python: 3.11+
- Package manager: pip / uv
- Virtualenv: .venv/ (not committed)

## Design patterns in use
- Command pattern: each CLI command is a standalone function in commands/
- Adapter pattern: GitHub, local index, Supabase all behind the same interface
- Pipeline pattern: BugFixPipeline orchestrates all stages in order
- Strategy pattern: vector_db.py auto-selects adapter based on config + env vars
- Prompt override: agents check .speckit/prompts/{name}.md before using defaults

## Forbidden patterns
- No direct Claude API calls outside speckit/core/agents.py
- No hardcoded API keys, tokens, or credentials anywhere in source
- No auto-merging PRs — humans always review before merge
- No shell commands in agent code except via ShellAdapter with allowlist
- No reading from .env directly — always via os.environ after dotenv.load_dotenv()
- No writing outside runs/ and specs/ directories during a pipeline run

## Module map
| Module                  | Responsibility                       | Spec file                    |
|-------------------------|--------------------------------------|------------------------------|
| cli                     | Entry point, command registration    | specs/modules/cli.md         |
| commands                | One file per CLI command             | specs/modules/commands.md    |
| core/agents             | All Claude API calls                 | specs/modules/agents.md      |
| core/judge              | Judge loop orchestration             | specs/modules/agents.md      |
| core/config             | Config loading + validation          | specs/modules/config.md      |
| core/spec_parser        | Parse spec markdown files            | specs/modules/spec-parser.md |
| adapters/github         | GitHub REST API                      | specs/modules/adapters.md    |
| adapters/local_index    | BM25 local index                     | specs/modules/adapters.md    |
| adapters/supabase_index | Supabase pgvector                    | specs/modules/adapters.md    |
| adapters/vector_db      | Adapter factory + search             | specs/modules/adapters.md    |
| adapters/shell          | Safe test runner                     | specs/modules/adapters.md    |
| modes/bug_fix           | Mode B: bug-fix pipeline             | specs/modules/pipeline.md    |
| templates               | Spec file templates used by init     | specs/modules/templates.md   |

## Security baseline
- Auth model: no user auth in the CLI itself — credentials are env vars only
- Secrets: ANTHROPIC_API_KEY, GITHUB_TOKEN loaded from .env, never committed
- Shell adapter: allowlist of permitted test runner commands only
- No user-supplied strings are executed as shell commands
- GitHub token scope: only `repo` (read issues, create branches/PRs)

## Error handling contract
- Missing ANTHROPIC_API_KEY → EnvironmentError with setup instructions
- Missing sdd.config.yml → FileNotFoundError telling user to run speckit init
- Judge threshold not met → RunResult.approved=False, human review message
- Pipeline failure → FAILED.md written to run directory with full log
