---
module: pipeline
affects: [agents, adapters, commands]
files: [speckit/modes/bug_fix.py, speckit/commands/run.py]
last_updated: 2026-05-09
---

# Module: pipeline

## Purpose
Orchestrates all stages of the bug-fix workflow (Mode B). Writes run artifacts to
disk as each stage completes so the user can inspect progress and the log is
preserved even if a later stage fails.

## Public interface
| Name | Type | Description |
|------|------|-------------|
| BugFixPipeline | class | Orchestrates all 8 pipeline stages |
| BugFixPipeline.run(issue_number, issue) | method | Execute pipeline, return RunResult |
| RunResult | dataclass | Final state: approved, score, iterations, artifact paths |

## Pipeline stages (in order)
1. **Fetch issue** — GitHub API or manual Issue object
2. **Classify** — classify_issue agent → 01_classification.md
3. **Search specs** — BM25 or Supabase vector search using classification keywords
4. **Read source files** — local disk or GitHub API, from spec frontmatter `files:`
5. **Draft bug report** — draft_bug_report agent → 02_bug_report.md (draft)
6. **Judge loop** — judge+refine until score >= judge_threshold or max_iterations
7. **Write final report** — 02_bug_report.md (approved / needs-human-review)
8. **Write test plan** — write_test_plan agent → 03_test_plan.md

## Run directory structure
```
runs/bug-fix-{N}/
  00_run_log.md        ← live log, written after every step
  01_classification.md
  02_bug_report.md     ← final (approved or flagged for review)
  03_test_plan.md
  FAILED.md            ← only if pipeline throws an unhandled exception
```

## on_step callback
BugFixPipeline accepts `on_step(title, detail)` callback. The run command wires
this to Rich console output so each stage is printed as it executes.

## Dependencies
- Depends on: core/agents, core/judge, adapters/github, adapters/vector_db,
  core/spec_parser, core/config
- Depended on by: commands/run

## Key decisions
- `run()` accepts optional `issue` parameter so GitHub can be bypassed for local
  testing without needing a token
- Source files are read at most 2 000 chars each to control token cost
- If spec search returns no results, all spec files are used as fallback
- Judge loop uses `on_iteration` callback to write each score to run log in real time

## Error states
| Error | When | How handled |
|-------|------|-------------|
| RuntimeError | GitHub needed but not configured | Raised immediately |
| Any exception | Any stage fails | FAILED.md written, exception re-raised |
| Judge threshold not met | All iterations exhausted | RunResult.approved=False, not an exception |
