# speckit — Complete User Guide

> **What is speckit?**
> A spec-driven development CLI that makes an AI understand your codebase *before* touching it.
> Instead of asking an AI to "fix this bug", speckit first reads your architecture, finds the
> relevant module specs, drafts a structured bug report, critiques it in a judge loop, writes
> a test plan, then generates code — all grounded in how your project is actually designed.

---

## Contents

1. [Installation & setup](#1-installation--setup)
2. [Greenfield project — building from scratch](#2-greenfield-project--building-from-scratch)
3. [Brownfield project — no existing specs](#3-brownfield-project--no-existing-specs)
4. [Fixing bugs (Mode B)](#4-fixing-bugs-mode-b)
5. [Adding features (Mode C)](#5-adding-features-mode-c)
6. [Automatic GitHub trigger (webhook)](#6-automatic-github-trigger-webhook)
7. [Reference — all commands](#7-reference--all-commands)
8. [Claude Pro MCP integration](#8-claude-pro-mcp-integration)

---

## 1. Installation & setup

### Install speckit

```bash
# From the repo (development install)
cd speckit
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .

# Verify
speckit --help
```

### Configure your LLM backend

Create a `.env` file in your project root. You only need **one** of these:

```bash
# ── Option A: Gemini on Vertex AI (free within GCP quota) ─────────────────
GEMINI_VERTEX=true
ANTHROPIC_VERTEX_PROJECT_ID=your-gcp-project-id
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json

# ── Option B: Gemini via Google AI Studio (free tier) ─────────────────────
GEMINI_API_KEY=AIza...

# ── Option C: Anthropic Claude (paid) ─────────────────────────────────────
ANTHROPIC_API_KEY=sk-ant-...
```

### Configure GitHub (optional but recommended)

Add to `.env` — needed for fetching issues and opening PRs:

```bash
GITHUB_TOKEN=ghp_...        # github.com/settings/tokens → classic → repo scope
GITHUB_REPO=org/repo-name   # e.g. acme/backend-api
```

---

## 2. Greenfield project — building from scratch

Use this when you're starting a new project with no existing code.

**What you get:** vision doc → tech stack proposal → full spec suite (architecture, security,
coding standards, data models, per-module specs). Everything reviewed by a quality judge.

### Step 1 — Initialize speckit

```bash
cd my-new-project
speckit init
```

Answer the prompts:
- Project name
- Mode → choose `greenfield`
- GitHub repo (optional)

This creates `sdd.config.yml` and empty `specs/` and `runs/` directories.

### Step 2 — Run the greenfield builder

```bash
speckit build
```

You'll be asked 8 discovery questions:

```
  What is the project name?
  In one sentence — what does it do and why does it exist?
  Who are the primary users and what is their technical level?
  List the 3-5 core features (comma-separated or one per line):
  Where will it be deployed? (e.g. web app, CLI tool, mobile backend, API)
  Any existing systems it must integrate with? (leave blank if none)
  Any hard technical constraints? (leave blank if none)
  Preferred primary language? (leave blank to let the agent recommend)
```

After each major step, you'll see a preview and be asked to confirm:

```
  Does this vision look right? [Y/n]:
  Does this tech stack look right? [Y/n]:
  Modules [core,api,auth]:
```

To skip all interactive prompts (useful for CI or re-runs):

```bash
speckit build --yes
```

### Step 3 — Review the generated specs

```
specs/
  architecture.md       ← system design, tech stack, module map, security baseline
  security.md           ← auth, authorization, input validation, known risks
  coding-standards.md   ← naming conventions, file structure, git conventions
  data-models.md        ← entities, relationships, validation rules
  modules/
    core.md             ← per-module: purpose, interface, dependencies, errors
    api.md
    auth.md
    ...
  scan_quality_report.md  ← judge's findings per file
```

Open each file. The quality judge has already flagged any vague or incomplete sections —
fix those before proceeding.

### Step 4 — Build the spec index

```bash
speckit index
```

This builds the BM25 search index (`.speckit/index.json`) so agents can find the right spec
files for each issue or feature.

### Step 5 — Start building features

```bash
speckit feature --name "user-authentication" \
  --description "Email/password login with JWT access tokens and 7-day refresh tokens"
```

See [Section 5](#5-adding-features-mode-c) for the full feature workflow.

---

## 3. Brownfield project — no existing specs

Use this when you have an existing codebase but no spec files. speckit will read your source
code and write the specs for you.

### Step 1 — Initialize speckit

```bash
cd your-existing-project
speckit init
```

Choose `brownfield-no-specs` as the mode.

### Step 2 — Scan the codebase

Point `--src` at your source code directory:

```bash
speckit scan --src ./src
```

Or if your source is at the project root:

```bash
speckit scan --src .
```

What happens:
1. speckit walks the directory, groups files by top-level folder (each folder = one module)
2. For each module: reads up to 10 files, sends them to the LLM, gets a spec back
3. Generates `specs/architecture.md` from all modules combined
4. Runs a quality judge on every generated file
5. Writes `specs/scan_quality_report.md` with findings

Output:
```
  ✓  adapters    → specs/modules/adapters.md
  ✓  commands    → specs/modules/commands.md
  ✓  core        → specs/modules/core.md
  ✓  modes       → specs/modules/modes.md
  ✓  architecture.md  → specs/architecture.md

  ● specs/modules/adapters.md   needs-review  (score 0.72)
       ⚠  Error states table is non-empty
          → Add error handling for GitHub 401/403/429 responses
  ✓  specs/architecture.md      good  (score 0.88)
```

#### To force-overwrite existing specs:
```bash
speckit scan --src ./src --force
```

#### To skip the quality judge (faster):
```bash
speckit scan --src ./src --skip-judge
```

### Step 3 — Review and edit the generated specs

**This is the most important step.** The LLM can only see what's in the files. Things it
will get wrong or leave vague:

- Business rules that aren't in the code
- Auth model details (if using a third-party service)
- Performance requirements
- Which modules are "owned by" which team
- Anything in a README but not in code

Open each `.md` file and fill in or correct what the LLM got wrong. Pay attention to
`scan_quality_report.md` — fix every `error` and ideally all `warning` items.

### Step 4 — Index the specs

```bash
speckit index
```

### Step 5 — You're ready

Now use `speckit run` to fix bugs and `speckit feature` to add features.
Both commands will find the right spec files automatically before starting any work.

---

## 4. Fixing bugs (Mode B)

**Prerequisite:** You have spec files and have run `speckit index`.

### With GitHub integration (recommended)

```bash
speckit run --issue 42
```

The pipeline:
1. **Fetches** issue #42 from GitHub (title, body, labels)
2. **Classifies** — type, severity, affected modules, search keywords
3. **Searches** your spec index for the 5 most relevant spec files
4. **Reads** source files referenced in those specs
5. **Drafts** a structured bug report (root cause, proposed fix, security checklist)
6. **Judge loop** — scores the report (0–1), refines it until ≥ 0.85 or 5 iterations
7. **Writes** a test plan (regression, unit, integration, security tests)
8. **Writes** code fix — reads actual source files, applies minimal targeted change
9. **Runs** your tests (e.g. `pytest tests/`)
10. **Retries** code up to 2× if tests fail
11. **Commits** changes, pushes branch, opens a PR with full context

Output files in `runs/bug-fix-42/`:
```
  00_run_log.md       ← every step + token usage
  01_classification.md
  02_bug_report.md    ← judge-approved spec
  03_test_plan.md
  04_code_changes.md  ← diffs applied
  05_test_results.md  ← stdout/stderr from test run
```

### Without GitHub (local testing)

If you don't have a GitHub token or want to test with a hypothetical issue:

```bash
speckit run --issue 1 --no-github
```

You'll be prompted to enter the issue title and body manually.

To lower the judge threshold for testing (so it doesn't loop 5 times):

```bash
SPECKIT_JUDGE_THRESHOLD=0.1 speckit run --issue 1 --no-github
```

### What to do with the result

**If approved (green border):** The PR is already open. Review the diff before merging.
Never let speckit auto-merge — always read the code.

**If needs-review (yellow border):** The judge couldn't reach 0.85 in 5 iterations.
Open `runs/bug-fix-42/02_bug_report.md`, read the judge's gaps, fix the spec manually,
then decide if the proposed fix is still correct.

**If tests failed:** Open `runs/bug-fix-42/05_test_results.md`. The LLM will have tried
2 more times. If still failing, the test output is preserved — fix the code manually.

### Tips

- The spec index quality directly determines fix quality. Good specs → good fixes.
- If the fix looks wrong, check `01_classification.md` — if the module detection is off,
  the wrong spec files were used. Add better keywords to your spec files.
- The test runner must be in the allowlist (pytest, npm, jest, vitest, go, cargo, make).
  Set `testing.backend_runner` in `sdd.config.yml` to match your project.

---

## 5. Adding features (Mode C)

**Prerequisite:** You have spec files and have run `speckit index`.

```bash
speckit feature \
  --name "csv-export" \
  --description "Allow users to export any run artifact as a CSV file from the CLI"
```

The pipeline:
1. **Research** — finds implementation patterns, evaluates packages, flags pitfalls
2. **Compatibility check** — scores how well the feature fits your architecture (0–1)
3. **Draft feature spec** — functional requirements (FRs), non-functional requirements (NFRs),
   data model changes, API contract, security considerations, dependencies
4. **Judge loop** — scores the spec, refines until ≥ 0.85 or 5 iterations
5. **Test plan** — unit, integration, API, security, performance tests
6. **Build plan** — ordered phases with tasks, file targets, and verification steps

Output files in `runs/feature-csv-export/`:
```
  00_run_log.md        ← every step + token usage
  01_research.md       ← patterns, packages, pitfalls
  02_compatibility.md  ← compatibility verdict + score
  03_feature_spec.md   ← judge-approved feature spec
  04_test_plan.md      ← test cases
  05_build_plan.md     ← phased implementation plan
```

### Compatibility verdicts

| Verdict | Meaning | What to do |
|---------|---------|------------|
| `approved` | Fits the architecture cleanly | Proceed |
| `needs-changes` | Fits with adjustments | Read 02_compatibility.md, apply recommendations |
| `blocked` | Significant architecture conflicts | Fix conflicts first, re-run |

### The human checkpoint

After the pipeline, **you** decide when to start building. The `05_build_plan.md` gives
you a phase-by-phase implementation plan. Work through it task by task — speckit has done
the spec work, you do the coding. (Future: `speckit build --execute` will do this too.)

### Using the feature spec in your work

The approved `03_feature_spec.md` becomes your source of truth:
- FRs become your implementation checklist
- NFRs become your acceptance criteria
- The API contract goes directly into your route handlers
- The build plan is your sprint backlog

---

## 6. Automatic GitHub trigger (webhook)

Once set up, speckit automatically starts a pipeline when anyone labels a GitHub issue —
no CLI command needed.

### Step 1 — Install server extras

```bash
pip install 'speckit[server]'
```

### Step 2 — Add webhook secret to .env

```bash
# Generate a random secret
python -c "import secrets; print(secrets.token_hex(32))"
# Add to .env:
GITHUB_WEBHOOK_SECRET=the-generated-secret
```

### Step 3 — Configure GitHub webhook

1. Go to your repo → **Settings** → **Webhooks** → **Add webhook**
2. **Payload URL**: `https://your-server.com/webhook/github`
3. **Content type**: `application/json`
4. **Secret**: same value as `GITHUB_WEBHOOK_SECRET`
5. **Events**: select **Issues** only (not "everything")
6. Click **Add webhook**

### Step 4 — Start the server

```bash
speckit serve
```

Or on a custom port:

```bash
speckit serve --port 9000
```

For production, run behind nginx/caddy with TLS. For local testing, use ngrok:

```bash
ngrok http 8000
# use the ngrok URL as your webhook Payload URL
```

### Step 5 — Trigger a pipeline

Label any issue with `bug` or `fix-needed` → bug-fix pipeline starts automatically.
Label with `feature` or `enhancement` → feature pipeline starts.

Check `runs/` for artifacts. The pipeline runs in a background thread.

### Routing logic

Labels are configurable in `sdd.config.yml`:

```yaml
github:
  bug_labels:
    - bug
    - fix-needed
  feature_labels:
    - feature
    - enhancement
```

---

## 7. Reference — all commands

### `speckit init [PATH]`
Initialize speckit in a project. Creates `sdd.config.yml`, `specs/`, `runs/`, and template files.

```bash
speckit init                 # interactive
speckit init --force         # reinitialize (overwrites config)
```

---

### `speckit scan [PATH] [OPTIONS]`
Generate spec files from an existing codebase (brownfield).

```bash
speckit scan --src ./src              # scan ./src, write to ./specs
speckit scan --src . --out ./specs    # scan project root
speckit scan --src ./src --force      # overwrite existing spec files
speckit scan --src ./src --skip-judge # skip quality review (faster)
```

---

### `speckit build [PATH] [OPTIONS]`
Guided greenfield spec-building session (Mode A).

```bash
speckit build              # interactive (8 questions + approval prompts)
speckit build --yes        # skip all approval prompts (non-interactive)
```

---

### `speckit index [PATH] [OPTIONS]`
Build the spec search index.

```bash
speckit index              # build local BM25 index
speckit index --query "authentication token expiry"  # test search
speckit index --setup-sql  # print Supabase setup SQL
```

---

### `speckit run [PATH] [OPTIONS]`
Run the bug-fix pipeline for a GitHub issue (Mode B).

```bash
speckit run --issue 42               # fetch issue from GitHub, run pipeline
speckit run --issue 1 --no-github    # manual issue entry (no GitHub needed)
```

Environment overrides:
```bash
SPECKIT_JUDGE_THRESHOLD=0.5 speckit run --issue 1  # lower judge bar for testing
```

---

### `speckit feature [PATH] [OPTIONS]`
Run the feature spec pipeline (Mode C).

```bash
speckit feature --name "user-auth" \
  --description "Email/password login with JWT tokens"

speckit feature --name "csv-export"   # prompts for description interactively
```

---

### `speckit serve [PATH] [OPTIONS]`
Start the GitHub webhook server (requires `pip install 'speckit[server]'`).

```bash
speckit serve                        # listen on 0.0.0.0:8000
speckit serve --port 9000            # custom port
speckit serve --reload               # dev mode (auto-reload on file changes)
```

Routes:
- `GET  /health` — liveness check
- `POST /webhook/github` — GitHub issue event handler

---

## Appendix F — Using Anthropic API for code writing

**Claude Pro ≠ Anthropic API.** They are separate products:

| Product | What it is | Can you call it from code? |
|---------|-----------|---------------------------|
| Claude Pro ($20/mo) | claude.ai + VSCode extension (Claude Code) | No — interactive only |
| Claude Max ($100/mo) | Full Claude Code usage | No — interactive only |
| Anthropic API | Pay-per-token API with `ANTHROPIC_API_KEY` | Yes ✅ |
| Vertex AI (Google Cloud) | Gemini + Claude hosted on GCP | Yes ✅ |

speckit currently uses **Vertex AI** (free within GCP quota) for all agents. If you want
to use Anthropic's API for just the code-writing step (highest quality, costs money),
you can mix them:

### Setup: hybrid backend (Vertex for research, Anthropic for coding)

```bash
# .env
GEMINI_VERTEX=true
ANTHROPIC_VERTEX_PROJECT_ID=sdd-1-495816
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json

# Add your Anthropic API key (get at console.anthropic.com)
ANTHROPIC_API_KEY=sk-ant-...
```

```yaml
# sdd.config.yml
agent:
  model: claude-sonnet-4-6
  coding_backend: anthropic    # only write_code uses Anthropic API
  coding_model: claude-sonnet-4-6
```

Now research, judging, and spec writing use free Vertex AI, but code generation uses
Claude Sonnet directly — the best model for code.

### Per-run override (no config change needed)

```bash
speckit run --issue 42 --coding-backend anthropic
speckit feature --name "auth" --coding-backend anthropic --model claude-sonnet-4-6
```

### Available coding backend values

| Value | Uses | Requires |
|-------|------|---------|
| `auto` (default) | Same backend as everything else | Nothing extra |
| `anthropic` | Anthropic direct API | `ANTHROPIC_API_KEY` |
| `gemini` | Gemini (AI Studio or Vertex) | `GEMINI_API_KEY` or `GEMINI_VERTEX` |
| `vertex` | Vertex AI (same as auto for most setups) | GCP credentials |

---

## Appendix G — Slack notifications

Add to your `.env`:
```bash
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T.../B.../...
```

Configure which events trigger notifications in `sdd.config.yml`:
```yaml
notifications:
  slack_channel: "#engineering"
  notify_on:
    - pr_opened          # bug-fix PR opened on GitHub
    - judge_failed       # judge couldn't reach threshold in 5 iterations
    - tests_failed       # tests failed after 3 code attempts
    - pipeline_complete  # any pipeline finished (approved or not)
    - feature_blocked    # compatibility check blocked a feature
```

---

## Appendix H — Multi-project webhook server

Run one webhook server for multiple repos:

```bash
# .env
SPECKIT_PROJECT_MAP='{"acme/backend": "/srv/acme-backend", "acme/mobile": "/srv/acme-mobile"}'
```

Each project path must contain its own `sdd.config.yml` and `.env`. When a webhook arrives
from `acme/backend`, it loads `/srv/acme-backend/sdd.config.yml`. Repos not in the map fall
back to the `--path` argument passed to `speckit serve`.

---

## Appendix A — sdd.config.yml reference

```yaml
project_name: my-project
repo: org/repo-name           # GitHub repo (org/name format)
mode: greenfield | brownfield-with-specs | brownfield-no-specs
primary_language: python      # python | typescript | go | java | etc.

paths:
  specs: ./specs              # where spec files live
  runs: ./runs                # where pipeline artifacts are saved
  src: ./src                  # source code root (used by scan)
  tests: ./tests              # tests directory

agent:
  model: claude-sonnet-4-6    # LLM model (overridden by backend detection)
  judge_threshold: 0.85       # 0-1, how strict the judge is
  max_judge_iterations: 5     # how many refine cycles before giving up
  max_spec_read_files: 5      # token budget: max spec files per run

github:
  default_branch: main
  bug_labels: [bug, fix-needed]
  feature_labels: [feature, enhancement]
  require_spec_update: true   # (future) block PR if specs not updated

testing:
  backend_runner: pytest      # pytest | jest | vitest | go | cargo | make
  frontend_runner: vitest
  coverage_threshold: 80

vector_db:
  provider: none              # none (local BM25) | supabase
  index_name: my-project-specs
  connection_url: ''          # Supabase URL if provider: supabase
```

---

## Appendix B — LLM backend priority

speckit checks env vars in this order and uses the first match:

| Priority | Env var | Backend | Cost |
|----------|---------|---------|------|
| 1 | `GEMINI_VERTEX=true` | Gemini on Vertex AI | Free within GCP quota |
| 2 | `GEMINI_API_KEY` | Gemini via AI Studio | Free tier |
| 3 | `ANTHROPIC_VERTEX_PROJECT_ID` | Claude on Vertex AI | GCP billing |
| 4 | `ANTHROPIC_API_KEY` | Claude direct API | Paid per token |

To use a specific Gemini model:
```bash
GEMINI_MODEL=publishers/google/models/gemini-2.5-flash-lite speckit run --issue 1
```

---

## Appendix C — Overriding system prompts

Every agent's system prompt can be overridden per-project by creating a file in
`.speckit/prompts/`:

```
.speckit/prompts/
  classify_issue.md       ← overrides the classify agent
  judge_bug_report.md     ← overrides the judge
  draft_bug_report.md     ← overrides the bug report writer
  write_code.md           ← overrides the code writer
  research_feature.md
  write_feature_spec.md
  judge_feature.md
  refine_feature.md
  write_build_plan.md
  draft_vision.md
  propose_tech_stack.md
  generate_spec_architecture.md
  generate_spec_security.md
  generate_spec_coding-standards.md
```

This lets you tune tone, verbosity, domain constraints, or output format for your
specific project without touching speckit's source code.

---

## Appendix D — Cost per run (approximate)

All costs are approximate. Gemini 2.5 Flash Lite on Vertex AI is effectively free
within standard quota.

| Operation | Anthropic Sonnet | Gemini Flash Lite |
|-----------|-----------------|-------------------|
| Bug fix (1 judge iter) | ~$0.08 | ~$0.005 |
| Bug fix (3 judge iters) | ~$0.12 | ~$0.008 |
| Feature pipeline | ~$0.25–0.40 | ~$0.015–0.025 |
| Scan (10 modules) | ~$0.15 | ~$0.010 |
| Greenfield build | ~$0.30 | ~$0.020 |

Token usage is logged to `runs/{run}/00_run_log.md` after every pipeline run.

---

## Appendix E — Directory structure

```
your-project/
  sdd.config.yml          ← speckit config (commit this)
  .env                    ← secrets: API keys, tokens (NEVER commit)
  .gitignore              ← should include: .env, *.json, .speckit/

  specs/                  ← your spec files (commit these)
    architecture.md
    security.md
    coding-standards.md
    data-models.md
    global_learnings.md
    scan_quality_report.md
    modules/
      auth.md
      users.md
      payments.md
      ...

  runs/                   ← pipeline artifacts (gitignore or commit — your choice)
    bug-fix-42/
      00_run_log.md
      01_classification.md
      02_bug_report.md
      03_test_plan.md
      04_code_changes.md
      05_test_results.md
    feature-csv-export/
      00_run_log.md
      01_research.md
      02_compatibility.md
      03_feature_spec.md
      04_test_plan.md
      05_build_plan.md
    greenfield-init/
      01_vision.md
      02_tech_stack.md
      00_token_usage.md

  .speckit/               ← speckit internal state (gitignore)
    index.json            ← BM25 search index
    prompts/              ← optional system prompt overrides
```

---

## 8. Claude Pro MCP integration

> **What this is:** Instead of speckit calling an LLM API in the background,
> you run `speckit mcp .` and Claude Pro (your VS Code extension) connects to it.
> Claude Pro becomes the reasoning and coding brain; speckit provides the tools —
> spec search, GitHub, judge, test runner, PR creation.
>
> **This is additive.** Every existing CLI command still works exactly as before.
> MCP is a new way to interact — best for interactive development in VS Code.

### Two modes, same engine

```
speckit CLI / webhook                   speckit MCP
────────────────────────────────────    ────────────────────────────────────
You: speckit run --issue 42             You: "Fix issue #42" (in Claude Pro)

Python calls LLM API                    Claude Pro IS the LLM
  → classify                              → calls get_issue(42)
  → search specs                          → calls search_specs("auth error")
  → draft bug report                      → reads the relevant specs
  → judge loop                            → writes code in your editor
  → write code                            → calls run_tests()
  → create PR                             → calls create_pr(...)

Best for: automation, CI/CD,            Best for: interactive dev, complex
webhooks, running unattended            problems, full editor context
Uses: your API key tokens               Uses: your Claude Pro subscription
```

---

### Step 1 — Install the MCP extra

```bash
pip install 'speckit[mcp]'
```

---

### Step 2 — Configure VS Code

Open VS Code Settings JSON (`Cmd+Shift+P` → "Open User Settings (JSON)") and add:

```json
{
  "claude.mcpServers": {
    "speckit": {
      "command": "speckit",
      "args": ["mcp", "."],
      "cwd": "${workspaceFolder}"
    }
  }
}
```

> If you have speckit installed globally via `pip install speckit`, VS Code will
> find it automatically. If you use a venv, point `command` to the venv binary:
> `"command": "/path/to/project/.venv/bin/speckit"`

Reload VS Code. Claude Pro will now show speckit tools in the tool picker when
you open a conversation.

---

### Step 3 — Verify it's working

In Claude Pro (VS Code), start a new conversation and say:

> *"What speckit project is this and how many specs are indexed?"*

Claude Pro will call `get_project_info` and reply with your project name, mode,
language, and spec count. If it does, speckit MCP is live.

---

### Workflow A — Fix a bug (project with specs)

**You say in Claude Pro:**
> *"Fix GitHub issue #42"*

**Claude Pro does:**
1. `get_issue(42)` — reads title, body, labels
2. `search_specs("auth timeout error")` — finds the 3 most relevant specs
3. `get_spec("specs/modules/auth.md")` — reads the full spec
4. Writes the code fix directly in your editor
5. `run_tests()` — runs pytest, checks output
6. Fixes any failing tests
7. `create_pr("fix/issue-42", "Fix auth timeout", body)` — opens the PR

**What you get:** A GitHub PR, written by Claude Pro, grounded in your actual specs.

---

### Workflow B — Fix a bug (project WITHOUT existing specs)

If your project has no spec files yet, do this first (one time):

```bash
# 1. Initialize speckit
speckit init .

# 2. Generate specs from your codebase
speckit scan --src ./src .

# 3. Review the generated specs (important!)
# Edit specs/modules/*.md — fix anything the LLM got wrong

# 4. Build the search index
speckit index .
```

Then go back to Workflow A. Claude Pro now has your specs to work from.

**Alternatively — let Claude Pro discover on the fly:**

Say: *"Read the relevant source files and fix issue #42 without specs."*

Claude Pro will use its file reading tools (built into VS Code) to read the
actual source code and make the fix. This works but produces less spec-grounded
output. The spec workflow gives better, more consistent results.

---

### Workflow C — Add a feature (project with specs)

**Option 1 — speckit generates the spec, Claude Pro codes it:**

> *"Add OAuth2 Google login as a new feature"*

Claude Pro:
1. `run_feature_pipeline("oauth2-login", "Add OAuth2 Google login so users can sign in with their Google account instead of creating a new password")` — triggers the Python pipeline in the background
2. `get_pipeline_status("feature-oauth2-login")` — polls until complete
3. `get_run_artifact("feature-oauth2-login", "03_feature_spec.md")` — reads the full feature spec
4. `get_run_artifact("feature-oauth2-login", "05_build_plan.md")` — reads the build plan
5. Codes each module from the build plan, files written in your editor
6. `run_tests()` — runs tests
7. `create_pr("feature/oauth2-login", "Add OAuth2 Google login", body)`

**Option 2 — Claude Pro writes the spec itself, then codes:**

> *"Draft a spec for adding OAuth2 login, review it, then implement it"*

Claude Pro:
1. `list_specs()` → `get_spec("specs/architecture.md")` — understands current architecture
2. `search_specs("auth login")` — finds related existing specs
3. Drafts a feature spec in markdown
4. `judge_spec(draft, "feature")` — gets score + specific gaps
5. Revises the draft based on feedback
6. `judge_spec(revised, "feature")` — repeats until score ≥ 0.85
7. `write_spec("specs/features/oauth2-login.md", final_spec)` — saves to disk
8. Codes from the approved spec
9. `run_tests()` → `create_pr(...)`

---

### Workflow D — Add a feature (project WITHOUT specs)

No specs at all? Here's the full path:

**Step 1 — Generate specs (one time)**

```bash
speckit scan --src ./src .   # generates specs/modules/*.md + architecture.md
speckit index .               # builds the search index
```

**Step 2 — Let Claude Pro write the feature spec**

> *"I want to add a dark mode toggle. Draft a spec for it, validate it, save it, then implement it."*

Claude Pro will:
- Read architecture.md to understand the project
- Draft the feature spec
- Use `judge_spec` to iterate until quality is good
- `write_spec` to save it
- Code from the spec
- `run_tests` + `create_pr`

**Step 3 — Subsequent features**

Every feature spec Claude Pro writes gets saved to `specs/features/`. Over time your
spec library grows automatically. Future features benefit from all the prior context.

---

### Workflow E — Greenfield project (brand new, no code yet)

Use the CLI first to build your initial spec suite, then use Claude Pro to code:

```bash
# 1. Initialize
mkdir my-project && cd my-project
git init
speckit init .         # creates sdd.config.yml

# 2. Generate your spec suite interactively
speckit build .        # Q&A → vision → tech stack → all specs

# 3. Review the generated specs
# specs/architecture.md, specs/modules/*.md etc.
# Edit anything that doesn't look right

# 4. Index
speckit index .
```

Then in Claude Pro:

> *"Read the specs and implement the user authentication module"*

Claude Pro:
1. `get_project_info()` — understands it's a greenfield project
2. `list_specs()` → `get_spec("specs/modules/auth.md")` — reads the module spec
3. `get_spec("specs/architecture.md")` + `get_spec("specs/security.md")` — full context
4. Creates files from scratch in your editor
5. `run_tests()` → iterate → `create_pr(...)`

---

### Workflow F — Iterating when Claude Pro is unsure

When Claude Pro encounters ambiguities mid-task, it can:

**Ask speckit's judge for feedback:**
```
Claude Pro: [drafts code change]
            judge_spec(code_context, "module")
            → score: 0.61, gaps: ["Missing error handling for network timeout",
                                   "No rollback on DB failure"]
            [fixes both gaps]
            judge_spec(revised, "module")
            → score: 0.88 ✓ approved
```

**Post a GitHub comment asking you:**
```
Claude Pro: add_issue_comment(42, "Unclear whether the timeout should be configurable
             or hardcoded. The auth.md spec doesn't specify. Please clarify.")
```

**Run tests and fix until green:**
```
Claude Pro: run_tests()
            → passed: false, output: "AssertionError: expected 200, got 401"
            [reads the test output, fixes the code]
            run_tests()
            → passed: true ✓
```

---

### Available tools reference (for prompting Claude Pro)

| Tool | What it does |
|---|---|
| `get_project_info` | Project name, mode, language, spec count, config |
| `search_specs` | BM25 search over all spec files |
| `list_specs` | All spec files with title + summary |
| `get_spec` | Full content of one spec file |
| `write_spec` | Write/update a spec file |
| `judge_spec` | Quality-judge any spec, get score + gaps |
| `get_issue` | Fetch a GitHub issue by number |
| `list_issues` | List open/closed GitHub issues |
| `add_issue_comment` | Post a comment to a GitHub issue |
| `create_pr` | Create a GitHub pull request |
| `run_bug_fix_pipeline` | Run the full automated bug-fix pipeline |
| `run_feature_pipeline` | Run the full automated feature pipeline |
| `get_pipeline_status` | Check pipeline progress, list artifacts |
| `get_run_artifact` | Read a pipeline artifact file |
| `list_runs` | List recent runs + their status |
| `run_tests` | Run pytest/vitest, get pass/fail + output |

**Resources Claude Pro can read directly:**

| URI | Content |
|---|---|
| `speckit://specs/{path}` | Any spec file as a readable resource |
| `speckit://config` | sdd.config.yml |
| `speckit://runs/{run_id}/{artifact}` | Any run artifact |

---

### Does the terminal workflow still work?

**Yes, completely.** MCP doesn't replace anything. You can use both in parallel:

```
Terminal (Python calls LLM API):         VS Code (Claude Pro uses MCP):
  speckit run --issue 42                   "Fix issue #42"
  speckit feature --name oauth2            "Add OAuth2 login"
  speckit build .                          (use build for initial specs, then MCP)
  speckit serve                            (webhook still works for CI/CD)
```

**Which to use when:**

| Situation | Use |
|---|---|
| Automated CI/CD, no human in the loop | Terminal / webhook (`speckit serve`) |
| You have Vertex AI but no Claude Pro | Terminal (`speckit run`, `speckit feature`) |
| You have Claude Pro and are in VS Code | MCP (`speckit mcp`) |
| Future: you get Anthropic API key | Terminal — set `ANTHROPIC_API_KEY` in `.env`, everything works |

---

### Future: Anthropic API key

If you later get an Anthropic API key from [console.anthropic.com](https://console.anthropic.com):

```bash
# .env
ANTHROPIC_API_KEY=sk-ant-...
```

All terminal commands (`speckit run`, `speckit feature`, `speckit build`, `speckit scan`)
will automatically use Claude via the Anthropic API — the same model Claude Pro uses,
but callable from Python. No changes needed to the CLI.

You can also use it as the coding backend while keeping Vertex AI for research:

```yaml
# sdd.config.yml
agent:
  coding_backend: anthropic   # use Anthropic API for code writing
  model: claude-sonnet-4-6    # Vertex AI for research/judging
```

The MCP server also keeps working — you can have both running simultaneously.

---

### Troubleshooting MCP

**Claude Pro doesn't show speckit tools:**
1. Check VS Code settings JSON has the `claude.mcpServers` block
2. Reload VS Code (Cmd+Shift+P → "Reload Window")
3. Run `speckit mcp .` manually in a terminal — if it errors, fix the error first

**"MCP SDK not installed":**
```bash
pip install 'speckit[mcp]'
# If using a venv, activate it first
source .venv/bin/activate && pip install 'speckit[mcp]'
```

**GitHub tools fail:**
Make sure `.env` has `GITHUB_TOKEN` and `GITHUB_REPO`:
```bash
GITHUB_TOKEN=ghp_...
GITHUB_REPO=yourname/yourrepo
```

**Pipeline tools fail (run_bug_fix_pipeline, run_feature_pipeline):**
These call Python LLM agents — you need at least one LLM backend in `.env`:
```bash
GEMINI_VERTEX=true        # OR
GEMINI_API_KEY=AIza...    # OR
ANTHROPIC_API_KEY=sk-ant-...
```
The spec and GitHub tools (`search_specs`, `get_issue`, etc.) work without an LLM backend.
