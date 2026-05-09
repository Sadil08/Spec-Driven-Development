---
module: agents
affects: [judge, pipeline, commands]
files: [speckit/core/agents.py, speckit/core/judge.py]
last_updated: 2026-05-09
---

# Module: agents

## Purpose
All Claude API calls live here. Each agent function receives structured input,
calls Claude claude-sonnet-4-6, and returns structured output. No agent shares state
with another — context is assembled fresh per call for token budget control.

## Public interface
| Name | Type | Description |
|------|------|-------------|
| classify_issue | function | Extracts type, modules, severity, keywords from issue text |
| draft_bug_report | function | Writes full bug report md from issue + specs + source |
| judge_bug_report | function | Scores a bug report 0-1, returns gaps and feedback |
| refine_bug_report | function | Improves a bug report based on judge score and gaps |
| write_test_plan | function | Generates test plan md from an approved bug report |
| Classification | Pydantic model | Output of classify_issue |
| JudgeScore | Pydantic model | Output of judge_bug_report |
| run_judge_loop | function (judge.py) | Iterates judge+refine until threshold or max iterations |

## Internal structure
- `_get_client(config)` — creates Anthropic client from ANTHROPIC_API_KEY env var
- `_load_prompt_override(name, project_root)` — checks .speckit/prompts/{name}.md
- `_strip_fences(text)` — removes markdown code fences before JSON parsing
- `_call(client, model, system, user, max_tokens)` — single unified API call wrapper

## Token budgets per agent
| Agent | Input tokens | Output tokens |
|-------|-------------|---------------|
| classify_issue | ~500 | ~200 |
| draft_bug_report | ~3 000 | ~2 000 |
| judge_bug_report | ~2 500 | ~400 |
| refine_bug_report | ~2 500 | ~2 000 |
| write_test_plan | ~2 000 | ~1 500 |

## Prompt override system
Place a markdown file at `.speckit/prompts/{agent-name}.md` to replace the default
system prompt for that agent. Names: classify, draft_bug_report, judge, refine, test_plan.
This lets users tune agent behaviour per-project without code changes.

## Dependencies
- Depends on: config (SpeckitConfig, AgentConfig)
- Depended on by: modes/bug_fix, core/judge

## Key decisions
- JSON output extracted by stripping markdown fences then json.loads() — avoids
  tool_use / structured output API to keep compatibility with all model versions
- Each agent gets a fresh Anthropic client — no shared singleton — safe for future
  parallel execution

## Error states
| Error | When | How handled |
|-------|------|-------------|
| EnvironmentError | ANTHROPIC_API_KEY not set | Raised immediately with setup instructions |
| json.JSONDecodeError | Claude returns non-JSON | Bubbles up to pipeline, written to FAILED.md |
| anthropic.APIError | Rate limit or server error | Bubbles up — retry logic is future work |

## Security notes
- API key never logged or included in error messages
- Prompt content (which may include issue text) is not persisted beyond the run
