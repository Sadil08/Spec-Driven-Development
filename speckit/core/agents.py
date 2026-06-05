"""
Claude/Gemini agents — one function per pipeline stage.

Backend is selected automatically from env vars (see _get_backend).
System prompts can be overridden via .speckit/prompts/{name}.md.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

import anthropic
from pydantic import BaseModel

if TYPE_CHECKING:
    from speckit.core.config import SpeckitConfig


# ── output models ─────────────────────────────────────────────────────────────

class Classification(BaseModel):
    issue_type: str          # "bug" | "feature" | "unclear"
    affected_modules: list[str]
    severity: str            # "critical" | "high" | "medium" | "low"
    search_keywords: list[str]
    summary: str


class JudgeScore(BaseModel):
    score: float
    approved: bool
    gaps: list[str]
    feedback: str


class CodeChange(BaseModel):
    file: str          # project-relative path
    action: str        # "modify" | "create"
    content: str       # full new file content
    explanation: str   # why this fixes the bug


class CodePlan(BaseModel):
    changes: list[CodeChange]
    test_command: str  # e.g. "pytest tests/" or "npm test"
    summary: str       # one-line summary of all changes


class CompatibilityResult(BaseModel):
    score: float              # 0-1: how well the feature fits the architecture
    conflicts: list[str]      # architecture principles this might violate
    recommendations: list[str]
    breaking_changes: list[str]
    verdict: str              # "approved" | "needs-changes" | "blocked"


class ScanIssue(BaseModel):
    severity: str   # "error" | "warning" | "info"
    message: str
    suggestion: str


class ScanJudgeResult(BaseModel):
    score: float
    issues: list[ScanIssue]
    verdict: str   # "good" | "needs-review" | "poor"


# ── cost tracking ─────────────────────────────────────────────────────────────

_cost_log: list[dict] = []


def reset_cost_log() -> None:
    _cost_log.clear()


def get_cost_summary_md() -> str:
    if not _cost_log:
        return ""
    total_in = sum(e["input_tokens"] for e in _cost_log)
    total_out = sum(e["output_tokens"] for e in _cost_log)
    rows = "\n".join(
        f"| {e['agent']:<32} | {e['input_tokens']:>8,} | {e['output_tokens']:>8,} |"
        for e in _cost_log
    )
    return (
        "\n## Token usage\n\n"
        "| Agent                            | Input tkns | Output tkns |\n"
        "|----------------------------------|-----------|-------------|\n"
        f"{rows}\n"
        f"| **Total**                        | **{total_in:>7,}** | **{total_out:>8,}** |"
    )


# ── backend abstraction ───────────────────────────────────────────────────────

class _AnthropicBackend:
    def __init__(self, client, model: str):
        self._client = client
        self.model = model

    def call(self, system: str, user: str, max_tokens: int, _agent: str = "") -> str:
        try:
            resp = self._client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system.strip(),
                messages=[{"role": "user", "content": user.strip()}],
            )
        except anthropic.BadRequestError as e:
            msg = str(e)
            if "credit balance is too low" in msg or "billing" in msg.lower():
                raise RuntimeError(
                    "Anthropic API credits exhausted.\n"
                    "  Top up at console.anthropic.com/billing.\n"
                    "  Or switch to free Gemini: set GEMINI_VERTEX=true in .env"
                ) from None
            raise
        except anthropic.AuthenticationError:
            raise RuntimeError(
                "Anthropic API key invalid. "
                "Fix at console.anthropic.com/api-keys."
            ) from None
        _cost_log.append({
            "agent": _agent or "unknown",
            "input_tokens": resp.usage.input_tokens,
            "output_tokens": resp.usage.output_tokens,
        })
        return resp.content[0].text.strip()


class _GeminiBackend:
    def __init__(self, client, model: str):
        self._client = client
        self.model = model

    def call(self, system: str, user: str, max_tokens: int, _agent: str = "") -> str:
        from google.genai import types  # type: ignore[import]
        resp = self._client.models.generate_content(
            model=self.model,
            contents=user.strip(),
            config=types.GenerateContentConfig(
                system_instruction=system.strip(),
                max_output_tokens=max_tokens,
                temperature=0.3,
            ),
        )
        try:
            um = resp.usage_metadata
            _cost_log.append({
                "agent": _agent or "unknown",
                "input_tokens": um.prompt_token_count or 0,
                "output_tokens": um.candidates_token_count or 0,
            })
        except Exception:
            pass
        return resp.text.strip()


def _get_backend(config: "SpeckitConfig") -> _AnthropicBackend | _GeminiBackend:
    """
    Return the best available LLM backend.

    Detection order (first match wins):
      1. GEMINI_VERTEX=true          → Gemini 2.0 Flash on Vertex AI
      2. GEMINI_API_KEY              → Gemini via Google AI Studio (free tier)
      3. ANTHROPIC_VERTEX_PROJECT_ID → Claude on Vertex AI
      4. ANTHROPIC_API_KEY           → Anthropic direct API
    """
    from google import genai as ggenai  # type: ignore[import]

    # 1. Gemini on Vertex (reuses GOOGLE_APPLICATION_CREDENTIALS)
    if os.environ.get("GEMINI_VERTEX", "").lower() in ("true", "1", "yes"):
        project = (
            os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID", "")
            or os.environ.get("GEMINI_VERTEX_PROJECT_ID", "")
        )
        if not project:
            raise EnvironmentError(
                "GEMINI_VERTEX=true requires a project ID.\n"
                "  Add to .env: ANTHROPIC_VERTEX_PROJECT_ID=sdd-1-495816"
            )
        region = os.environ.get("GEMINI_VERTEX_REGION", "us-central1")
        client = ggenai.Client(vertexai=True, project=project, location=region)
        model = os.environ.get("GEMINI_MODEL", "publishers/google/models/gemini-2.5-flash-lite")
        return _GeminiBackend(client, model)

    # 2. Gemini via Google AI Studio (free API key)
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    if gemini_key:
        client = ggenai.Client(api_key=gemini_key)
        model = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
        return _GeminiBackend(client, model)

    # 3. Claude on Vertex AI
    vertex_project = os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID", "")
    if vertex_project:
        try:
            from anthropic import AnthropicVertex
        except ImportError:
            raise RuntimeError("Run: pip install 'anthropic[vertex]'") from None
        region = os.environ.get("ANTHROPIC_VERTEX_REGION", "us-east5")
        client = AnthropicVertex(project_id=vertex_project, region=region)
        _MAP = {
            "claude-sonnet-4-6": "claude-sonnet-4-5@20251001",
            "claude-haiku-4-5":  "claude-haiku-4-5@20251001",
        }
        model = _MAP.get(config.agent.model, config.agent.model)
        return _AnthropicBackend(client, model)

    # 4. Anthropic direct API
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if key and key not in ("paste-your-key-here", "sk-ant-..."):
        return _AnthropicBackend(anthropic.Anthropic(api_key=key), config.agent.model)

    raise EnvironmentError(
        "No LLM credentials found. Add one of these to .env:\n\n"
        "  Free — Gemini on your existing Vertex project:\n"
        "    GEMINI_VERTEX=true\n"
        "    ANTHROPIC_VERTEX_PROJECT_ID=sdd-1-495816\n\n"
        "  Free — Gemini via Google AI Studio:\n"
        "    GEMINI_API_KEY=AIza...  (get key at aistudio.google.com)\n\n"
        "  Paid — Anthropic direct API:\n"
        "    ANTHROPIC_API_KEY=sk-ant-..."
    )


def _get_coding_backend(config: "SpeckitConfig") -> _AnthropicBackend | _GeminiBackend:
    """
    Return the backend to use for code-writing steps specifically.

    Controlled by config.agent.coding_backend:
      "auto"       → same as _get_backend() (default)
      "anthropic"  → force Anthropic direct API (needs ANTHROPIC_API_KEY)
      "gemini"     → force Gemini (Vertex or AI Studio key)
      "vertex"     → force Vertex AI (Gemini or Claude depending on env)

    Also respects SPECKIT_CODING_BACKEND env var to override config at runtime.
    """
    choice = (
        os.environ.get("SPECKIT_CODING_BACKEND", "")
        or getattr(config.agent, "coding_backend", "auto")
        or "auto"
    ).lower()

    coding_model = (
        os.environ.get("SPECKIT_CODING_MODEL", "")
        or getattr(config.agent, "coding_model", "")
        or ""
    )

    if choice == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key or key in ("paste-your-key-here", "sk-ant-..."):
            raise EnvironmentError(
                "coding_backend=anthropic requires ANTHROPIC_API_KEY in .env.\n"
                "  Get a key at console.anthropic.com/api-keys\n"
                "  Or switch to 'auto' in sdd.config.yml to use your Vertex AI backend."
            )
        model = coding_model or config.agent.model
        return _AnthropicBackend(anthropic.Anthropic(api_key=key), model)

    if choice == "gemini":
        from google import genai as ggenai  # type: ignore[import]
        key = os.environ.get("GEMINI_API_KEY", "")
        if key:
            model = coding_model or os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
            return _GeminiBackend(ggenai.Client(api_key=key), model)
        # Fall through to Vertex Gemini
        project = (
            os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID", "")
            or os.environ.get("GEMINI_VERTEX_PROJECT_ID", "")
        )
        if project:
            region = os.environ.get("GEMINI_VERTEX_REGION", "us-central1")
            client = ggenai.Client(vertexai=True, project=project, location=region)
            model = coding_model or os.environ.get("GEMINI_MODEL", "publishers/google/models/gemini-2.5-flash-lite")
            return _GeminiBackend(client, model)
        raise EnvironmentError(
            "coding_backend=gemini but no GEMINI_API_KEY or GEMINI_VERTEX project found."
        )

    # "auto" or "vertex" or anything else → use default backend
    backend = _get_backend(config)
    if coding_model and hasattr(backend, 'model'):
        backend.model = coding_model
    return backend


# ── shared helpers ────────────────────────────────────────────────────────────

def _prompt_override(name: str, project_root: Path) -> str | None:
    p = project_root / ".speckit" / "prompts" / f"{name}.md"
    return p.read_text(encoding="utf-8") if p.exists() else None


def _strip_fences(text: str) -> str:
    import re
    text = text.strip()
    # Strip ```json ... ``` or ``` ... ``` fences
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        text = text.rsplit("```", 1)[0]
        return text.strip()
    # Extract first {...} block if there's surrounding prose
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if m:
        return m.group(0)
    return text.strip()


def _parse_json_safe(text: str) -> dict:
    """Parse JSON from LLM output, tolerating common formatting issues."""
    import re

    def _try_loads(s: str):
        try:
            return json.loads(s), True
        except Exception:
            return None, False

    cleaned = _strip_fences(text)

    # Attempt 1: direct parse
    val, ok = _try_loads(cleaned)
    if ok:
        return val

    # Attempt 2: escape bare newlines inside string values
    fixed = re.sub(
        r'"((?:[^"\\]|\\.)*)"',
        lambda m: '"' + m.group(1).replace('\n', '\\n').replace('\r', '') + '"',
        cleaned,
    )
    val, ok = _try_loads(fixed)
    if ok:
        return val

    # Attempt 3: field-by-field extraction
    result: dict = {}
    for key in ("score", "approved", "gaps", "feedback",
                "issue_type", "severity", "affected_modules", "search_keywords", "summary",
                "changes", "test_command", "summary"):
        # Match the value after "key": — handles strings, arrays, booleans, numbers
        m = re.search(
            rf'"{key}"\s*:\s*(\[.*?\]|\{{.*?\}}|"(?:[^"\\]|\\.)*"|true|false|[\d.]+)',
            cleaned, re.DOTALL,
        )
        if not m:
            continue
        raw_val = m.group(1)
        val, ok = _try_loads(raw_val)
        if ok:
            result[key] = val
        else:
            # If it looks like an array/object but failed, try cleaning it
            if raw_val.startswith('[') or raw_val.startswith('{'):
                escaped = re.sub(
                    r'"((?:[^"\\]|\\.)*)"',
                    lambda mv: '"' + mv.group(1).replace('\n', '\\n') + '"',
                    raw_val,
                )
                val, ok = _try_loads(escaped)
                result[key] = val if ok else raw_val
            else:
                result[key] = raw_val.strip('"')

    if result:
        return result
    raise ValueError(f"Could not parse JSON from LLM response: {text[:200]}")


# ── agent 1: classify ─────────────────────────────────────────────────────────

def classify_issue(
    issue_title: str,
    issue_body: str,
    config: "SpeckitConfig",
    project_root: Path,
) -> Classification:
    """~500 input / ~200 output tokens."""
    backend = _get_backend(config)

    system = _prompt_override("classify", project_root) or (
        "You are a software issue classifier. "
        "Read the GitHub issue and return compact, precise JSON. "
        "search_keywords must be terms likely to appear in spec file content. "
        "Return JSON only — no markdown, no explanation."
    )

    user = f"""GitHub issue title: {issue_title}

GitHub issue body:
{issue_body[:3000]}

Return JSON:
{{
  "issue_type": "bug" | "feature" | "unclear",
  "affected_modules": ["module-name"],
  "severity": "critical" | "high" | "medium" | "low",
  "search_keywords": ["kw1", "kw2", "kw3"],
  "summary": "one sentence describing the issue"
}}"""

    raw = backend.call(system, user, max_tokens=800, _agent="classify_issue")
    return Classification(**_parse_json_safe(raw))


# ── agent 2: draft bug report ────────────────────────────────────────────────

def draft_bug_report(
    issue_title: str,
    issue_body: str,
    classification: Classification,
    spec_files: list[dict],
    source_file_contents: dict[str, str],
    global_learnings: str,
    config: "SpeckitConfig",
    project_root: Path,
) -> str:
    """~3 000 input / ~2 000 output tokens."""
    backend = _get_backend(config)

    specs_text = "\n\n---\n\n".join(
        f"### {s.get('path', '')} (module: {s.get('module', '')})\n"
        f"{s.get('content', '')[:1200]}"
        for s in spec_files
    ) or "(no relevant spec files found)"

    source_text = "\n\n---\n\n".join(
        f"### {path}\n```\n{content[:800]}\n```"
        for path, content in source_file_contents.items()
    ) or "(no source files resolved)"

    system = _prompt_override("draft_bug_report", project_root) or """
You are a spec-driven development agent writing a bug report.
RULES:
- Root cause MUST reference a specific file and line number.
- The proposed fix MUST respect every architecture principle in the spec.
- Fill every section completely — no placeholder text.
- If source files aren't available, infer from spec content and say so.
- Return only the markdown document, no preamble.
"""

    user = f"""## Issue
Title: {issue_title}
Type: {classification.issue_type} | Severity: {classification.severity}
Affected modules: {', '.join(classification.affected_modules)}

{issue_body[:1500]}

## Relevant spec files
{specs_text}

## Global learnings (avoid these mistakes, follow these patterns)
{global_learnings[:800] if global_learnings else 'No learnings recorded yet.'}

## Source files (relevant excerpts)
{source_text}

---
Write the complete bug report in this exact structure:

# Bug report: {issue_title}

## Issue summary
[plain-language description of what is broken]

## Root cause analysis
- Location: [file:line or "unknown — needs source access"]
- Type: [logic-error | null-reference | race-condition | type-error | config | dependency | security]
- Triggered by: [exact conditions]
- Root cause: [explanation]

## Architecture impact
- Modules affected: [{', '.join(classification.affected_modules)}]
- Principles at risk: [which rules from architecture spec this touches]
- Data models affected: [entities or "none"]
- Security surface touched: yes/no — [details if yes]

## Proposed fix
[description of the fix approach in plain language]

### Why this approach does not violate architecture
[explicit reasoning against architecture principles]

### Alternative approaches considered
| Approach | Rejected because |
|----------|-----------------|
| [alt] | [reason] |

## Packages / imports required
| Package | Version | Justification | Compatible? |
|---------|---------|---------------|-------------|
[list new packages OR write a single row: "None required" | - | - | - ]

## Security checklist
- [ ] Fix does not expose new attack surface
- [ ] Input validation unchanged or strengthened
- [ ] No credentials introduced (all via env vars)
- [ ] Auth/authz not weakened
- [ ] No PII added to logs

## Code changes (before/after)
### [filename]
**Before:**
```
[original code or "see source file"]
```
**After:**
```
[proposed fix]
```

## Spec files to update after merge
- [ ] [spec file] — [what to update]
"""

    return backend.call(system, user, max_tokens=2500, _agent="draft_bug_report")


# ── agent 3: judge ────────────────────────────────────────────────────────────

def judge_bug_report(
    bug_report_md: str,
    architecture_spec: str,
    security_spec: str,
    config: "SpeckitConfig",
    project_root: Path,
) -> JudgeScore:
    """~2 500 input / ~400 output tokens."""
    backend = _get_backend(config)

    system = _prompt_override("judge", project_root) or (
        "You are a senior software architect reviewing an AI-generated bug report. "
        "Be rigorous. Return JSON only."
    )

    user = f"""## Architecture specification
{architecture_spec[:2000]}

## Security specification
{security_spec[:1200]}

## Bug report to review
{bug_report_md[:2800]}

---
Score on five dimensions (0–1 each, equal weight, average = final score):

1. **Root cause accuracy** — Is the file:line specific and plausible?
2. **Completeness** — Are all sections filled (no "TODO" or "unknown" without justification)?
3. **Architecture alignment** — Does the fix respect the listed architecture principles?
4. **Security** — Is the checklist complete and the attack surface addressed?
5. **Testability** — Are the before/after code changes concrete enough to implement and verify?

Return JSON only:
{{
  "score": 0.0,
  "approved": false,
  "gaps": ["specific gap 1", "specific gap 2"],
  "feedback": "one paragraph of actionable improvement guidance"
}}

Set approved=true only if score >= {config.agent.judge_threshold}"""

    raw = backend.call(system, user, max_tokens=1500, _agent="judge_bug_report")
    data = _parse_json_safe(raw)
    data["approved"] = float(data.get("score", 0)) >= config.agent.judge_threshold
    return JudgeScore(**data)


# ── agent 4: refine ───────────────────────────────────────────────────────────

def refine_bug_report(
    bug_report_md: str,
    judge_score: JudgeScore,
    config: "SpeckitConfig",
    project_root: Path,
) -> str:
    """~2 500 input / ~2 000 output tokens."""
    backend = _get_backend(config)

    system = _prompt_override("refine", project_root) or """
You are a spec-driven development agent improving a bug report based on reviewer feedback.
RULES:
- Address every gap listed — do not skip any.
- Do not remove or shorten any section that was already good.
- Return the complete revised bug report in markdown (all sections present).
- No preamble, no explanation outside the document.
"""

    gaps_text = "\n".join(f"- {g}" for g in judge_score.gaps)

    user = f"""## Current bug report (score: {judge_score.score:.2f})
{bug_report_md}

## Gaps to address
{gaps_text}

## Reviewer feedback
{judge_score.feedback}

Return the complete improved bug report (maintain all sections, improve weak ones)."""

    return backend.call(system, user, max_tokens=2500, _agent="refine_bug_report")


# ── agent 5: test plan ────────────────────────────────────────────────────────

def write_test_plan(
    bug_report_md: str,
    config: "SpeckitConfig",
    project_root: Path,
) -> str:
    """~2 000 input / ~1 500 output tokens."""
    backend = _get_backend(config)

    system = _prompt_override("test_plan", project_root) or """
You are a QA engineer writing tests for a bug fix.
RULES:
- Every test must have specific inputs and expected outputs.
- Include at minimum: one regression test, one unit test, one security test.
- API tests must be real curl commands (with placeholder values where needed).
- Return only the markdown document.
"""

    user = f"""## Approved bug report
{bug_report_md[:2500]}

---
Write a complete test plan:

# Test plan: [bug title from report]

## Regression tests (prove the bug is fixed)
| ID | Test | Input | Expected output | Pass/Fail |
|----|------|-------|-----------------|-----------|
| R01 | [name] | [input] | [expected] | - |

## Unit tests
| ID | Function/method | Scenario | Expected | Pass/Fail |
|----|----------------|----------|----------|-----------|
| U01 | [fn] | happy path | [expected] | - |
| U02 | [fn] | edge case | [expected] | - |

## API tests
```bash
# R01 — [description]
curl -X [METHOD] [url] \\
  -H "Authorization: Bearer $TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '[body]'
# Expected: [response shape]
```

## Security tests
| ID | Test | Method | Expected | Pass/Fail |
|----|------|--------|----------|-----------|
| S01 | Auth required on affected endpoint | Request without token | 401 | - |
| S02 | Input validation | Send malformed payload | 400 with detail | - |

## Test results summary
- Total: [n] | Passed: - | Failed: -
"""

    return backend.call(system, user, max_tokens=1800, _agent="write_test_plan")


# ── agent 6: generate module spec (scan) ─────────────────────────────────────

def generate_module_spec(
    module_name: str,
    file_contents: dict[str, str],
    language: str,
    config: "SpeckitConfig",
    project_root: Path,
) -> str:
    """~3 000 input / ~1 500 output tokens. Used by speckit scan."""
    backend = _get_backend(config)

    system = _prompt_override("scan_module", project_root) or """
You are a spec writer analysing existing source code.
RULES:
- Be precise — reference real function/class names you see in the code.
- Do NOT invent behaviour that isn't in the files.
- Keep every section concise (2-5 bullet points each).
- Return only the markdown document (frontmatter + body), no preamble.
"""

    files_text = "\n\n".join(
        f"### {path}\n```\n{content[:1000]}\n```"
        for path, content in list(file_contents.items())[:8]
    )

    user = f"""Analyse the source files below and write a spec for the **{module_name}** module of a {language} project.

## Source files
{files_text}

---
Return this exact structure (fill every section):

---
module: {module_name}
affects: []
files: {list(file_contents.keys())}
---

# {module_name.replace("-", " ").title()} Module

## Purpose
[1-3 sentences: what this module does and why it exists]

## Public interfaces
[Bullet list of key public functions / classes / endpoints with brief description]

## Data flow
[How data enters and leaves this module]

## Architecture principles
[Rules this module must follow — constraints, invariants, non-obvious behaviour]

## Dependencies
- Internal: [other modules it imports]
- External: [third-party packages]

## Known gaps / TODOs
[Things missing or unclear from the source — write "none" if all good]
"""

    return backend.call(system, user, max_tokens=1800, _agent="generate_module_spec")


# ── agent 7: generate architecture spec (scan) ───────────────────────────────

def generate_architecture_spec(
    project_name: str,
    language: str,
    modules_summary: str,
    sample_files: dict[str, str],
    config: "SpeckitConfig",
    project_root: Path,
) -> str:
    """~3 000 input / ~2 000 output tokens. Used by speckit scan."""
    backend = _get_backend(config)

    system = _prompt_override("scan_architecture", project_root) or """
You are a senior software architect writing the top-level architecture spec for an existing project.
RULES:
- Ground every statement in the actual code you see — do not add fictional features.
- Infer architecture principles from the code patterns you observe.
- Return only the markdown document, no preamble.
"""

    sample_text = "\n\n".join(
        f"### {path}\n```\n{content[:600]}\n```"
        for path, content in list(sample_files.items())[:6]
    )

    user = f"""Project: **{project_name}** ({language})

## Module summary
{modules_summary}

## Representative source files
{sample_text}

---
Write specs/architecture.md:

# {project_name} — Architecture

## Overview
[2-3 sentences: what the project does]

## Tech stack
| Layer | Technology |
|-------|-----------|
[rows for: language, framework, DB, auth, testing, infra]

## Module map
| Module | Responsibility |
|--------|---------------|
[one row per module from the module summary]

## Architecture principles
[5-8 bullet points: the key design rules observed in the code]

## Cross-cutting concerns
- Logging: [approach]
- Error handling: [approach]
- Configuration: [how config/env vars are managed]
- Testing: [test strategy]

## Security model
- Authentication: [mechanism]
- Authorisation: [mechanism]
- Secrets management: [env vars / vault / etc.]
- Input validation: [where and how]

## Data flow (top level)
[Brief description of the main request/data path through the system]
"""

    return backend.call(system, user, max_tokens=2000, _agent="generate_architecture_spec")


# ── agent 8: write code fix ───────────────────────────────────────────────────

def write_code(
    bug_report_md: str,
    source_file_contents: dict[str, str],
    test_runner: str,
    config: "SpeckitConfig",
    project_root: Path,
    previous_test_output: str = "",
) -> CodePlan:
    """~4 000 input / ~3 000 output tokens."""
    backend = _get_coding_backend(config)

    system = _prompt_override("write_code", project_root) or """
You are a senior software engineer implementing a bug fix.
RULES:
- Make the MINIMAL change that fixes the root cause described in the bug report.
- Output FULL file contents for every modified file — not diffs or snippets.
- Never introduce new dependencies unless the bug report explicitly requires them.
- Never remove existing functionality unrelated to the fix.
- Return JSON only — no markdown, no explanation outside the JSON.
"""

    source_text = "\n\n".join(
        f"### {path}\n```\n{content}\n```"
        for path, content in list(source_file_contents.items())[:6]
    ) or "(no source files available — infer from bug report)"

    retry_section = ""
    if previous_test_output:
        retry_section = f"""
## Previous attempt failed
The tests failed with this output. Fix the remaining issues:
```
{previous_test_output[:1500]}
```
"""

    user = f"""## Approved bug report
{bug_report_md[:3000]}

## Current source files
{source_text}
{retry_section}
---
Return JSON with this exact structure:
{{
  "changes": [
    {{
      "file": "relative/path/to/file.py",
      "action": "modify",
      "content": "FULL new file content here",
      "explanation": "one sentence: what changed and why"
    }}
  ],
  "test_command": "{test_runner}",
  "summary": "one-line summary of all changes"
}}

Rules for the JSON:
- "file" must be a project-relative path (e.g. "src/auth/middleware.py")
- "action" is "modify" for existing files, "create" for new files
- "content" is the COMPLETE file — not a diff, not a snippet
- Include ALL changed files in the array
"""

    raw = backend.call(system, user, max_tokens=4000, _agent="write_code")
    data = _parse_json_safe(raw)

    # Normalise: content strings sometimes come back escaped
    changes = []
    for c in data.get("changes", []):
        changes.append(CodeChange(
            file=c.get("file", ""),
            action=c.get("action", "modify"),
            content=c.get("content", ""),
            explanation=c.get("explanation", ""),
        ))

    return CodePlan(
        changes=changes,
        test_command=data.get("test_command", test_runner),
        summary=data.get("summary", "Code fix applied"),
    )


# ── agent 9: research feature ────────────────────────────────────────────────

def research_feature(
    feature_name: str,
    feature_description: str,
    architecture_spec: str,
    language: str,
    config: "SpeckitConfig",
    project_root: Path,
) -> str:
    """~2 500 input / ~2 000 output tokens."""
    backend = _get_backend(config)

    system = _prompt_override("research_feature", project_root) or """
You are a senior engineer researching how to implement a new feature.
RULES:
- Ground recommendations in the project's actual tech stack and architecture.
- List real packages with accurate version info.
- Identify the most common implementation pitfalls for this type of feature.
- Return only the markdown document, no preamble.
"""

    user = f"""## Feature request
Name: {feature_name}
Description: {feature_description}

## Project architecture
Language: {language}
{architecture_spec[:2000]}

---
Write a research document:

# Research: {feature_name}

## Implementation patterns found
### Pattern A: [name]
- Approach: [description]
- Pros: [list]
- Cons: [list]
- Fits this project: yes/no — [reason referencing architecture]

### Pattern B: [name]
...

## Recommended approach
[Which pattern and why it fits this project's architecture]

## Packages to evaluate
| Package | Stars/Popularity | Maintained | License | Fits stack | Notes |
|---------|-----------------|-----------|---------|-----------|-------|
[at least 2-3 options]

## Recommended packages
[Final list with justification]

## Security considerations
[Auth, input validation, data exposure risks for this feature type]

## Known pitfalls
[Common mistakes when implementing this type of feature]

## Open questions
- [ ] [question needing human decision before building]
"""

    return backend.call(system, user, max_tokens=2500, _agent="research_feature")


# ── agent 10: check compatibility ────────────────────────────────────────────

def check_compatibility(
    feature_name: str,
    feature_description: str,
    architecture_spec: str,
    module_specs_summary: str,
    config: "SpeckitConfig",
    project_root: Path,
) -> CompatibilityResult:
    """~2 500 input / ~600 output tokens."""
    backend = _get_backend(config)

    system = _prompt_override("check_compatibility", project_root) or (
        "You are a software architect checking if a proposed feature fits the project's "
        "architecture. Be rigorous. Return JSON only."
    )

    user = f"""## Proposed feature
Name: {feature_name}
Description: {feature_description}

## Architecture specification
{architecture_spec[:2000]}

## Existing module summary
{module_specs_summary[:1500]}

---
Evaluate compatibility and return JSON only:
{{
  "score": 0.0,
  "conflicts": ["architecture principle this might violate"],
  "recommendations": ["what to do to make it fit cleanly"],
  "breaking_changes": ["what existing behaviour might break"],
  "verdict": "approved"
}}

Scoring guide:
- 0.9-1.0: fits perfectly, no concerns
- 0.7-0.9: fits with minor adjustments
- 0.5-0.7: fits but needs careful design
- below 0.5: significant conflicts — verdict should be "blocked"

verdict must be one of: "approved" | "needs-changes" | "blocked"
"""

    raw = backend.call(system, user, max_tokens=800, _agent="check_compatibility")
    data = _parse_json_safe(raw)
    return CompatibilityResult(
        score=float(data.get("score", 0.5)),
        conflicts=data.get("conflicts", []),
        recommendations=data.get("recommendations", []),
        breaking_changes=data.get("breaking_changes", []),
        verdict=data.get("verdict", "needs-changes"),
    )


# ── agent 11: write feature spec ─────────────────────────────────────────────

def write_feature_spec(
    feature_name: str,
    feature_description: str,
    research_md: str,
    compatibility_md: str,
    architecture_spec: str,
    security_spec: str,
    config: "SpeckitConfig",
    project_root: Path,
) -> str:
    """~4 000 input / ~2 500 output tokens."""
    backend = _get_backend(config)

    system = _prompt_override("write_feature_spec", project_root) or """
You are a product engineer writing a feature specification.
RULES:
- Every functional requirement must be testable (starts with "System shall" or "User can").
- Every NFR must have a measurable target (e.g. "p95 < 200ms", "WCAG 2.1 AA").
- Security section must address auth, input validation, and new attack surface.
- Out of scope section must exist and be explicit.
- Return only the markdown document (with YAML frontmatter), no preamble.
"""

    slug = feature_name.lower().replace(" ", "-")

    user = f"""## Feature request
{feature_description}

## Research summary
{research_md[:1500]}

## Compatibility assessment
{compatibility_md[:800]}

## Architecture spec
{architecture_spec[:1500]}

## Security spec
{security_spec[:800]}

---
Write the complete feature spec:

---
feature: {slug}
status: draft
date: {__import__('datetime').date.today()}
---

# Feature spec: {feature_name}

## User story
As a [user type], I want [capability], so that [outcome].

## Problem statement
[What gap this fills and why it matters]

## Functional requirements
| ID | Requirement | Priority |
|----|-------------|----------|
| F01 | System shall [requirement] | must |
[at least 3 requirements]

## Non-functional requirements
| ID | Category | Requirement | Metric |
|----|----------|-------------|--------|
| NF01 | Performance | [requirement] | [measurable target e.g. p95 < 200ms, throughput > 500 rps] |
| NF02 | Security | [requirement] | [standard e.g. OWASP Top 10 compliant, TLS 1.2+] |
| NF03 | Scalability | [requirement] | [measurable target e.g. supports 10k concurrent users, horizontal scale-out] |
| NF04 | Reliability | [requirement] | [measurable target e.g. 99.9% uptime, RTO < 5 min, zero data loss] |
| NF05 | Maintainability | [requirement] | [measurable target e.g. cyclomatic complexity < 10, test coverage > 80%] |
| NF06 | Observability | [requirement] | [measurable target e.g. all errors logged with trace ID, p99 latency tracked] |

## Data model changes
### New entities (if any)
[entity name, fields, types, relationships — or write "None"]

### Modified entities (if any)
[what changes — or write "None"]

## API contract (if applicable)
[New endpoints with method, path, auth, request/response shape — or write "None"]

## Security considerations
- Auth required: yes/no
- New attack surface: [describe]
- Input validation: [what needs validating]
- OWASP concerns: [applicable items]

## Dependencies
| Package | Version | Justification |
|---------|---------|---------------|
[from research recommendations — or "None required"]

## Spec files to create/update
- [ ] specs/modules/[module].md — [what changes]

## Out of scope
[Explicit list of what is NOT included]

## Open questions
- [ ] [question needing human decision]
"""

    return backend.call(system, user, max_tokens=2800, _agent="write_feature_spec")


# ── agent 12: judge feature spec ─────────────────────────────────────────────

def judge_feature_spec(
    feature_spec_md: str,
    architecture_spec: str,
    security_spec: str,
    config: "SpeckitConfig",
    project_root: Path,
) -> JudgeScore:
    """~3 000 input / ~600 output tokens."""
    backend = _get_backend(config)

    system = _prompt_override("judge_feature", project_root) or (
        "You are a senior product and architecture reviewer. "
        "Be rigorous. Return JSON only."
    )

    user = f"""## Architecture specification
{architecture_spec[:1500]}

## Security specification
{security_spec[:800]}

## Feature spec to review
{feature_spec_md[:2500]}

---
Score on six dimensions (0-1 each, equal weight):

1. **Completeness** — All sections filled, no TODOs or "TBD" without justification
2. **Testability** — Every FR starts with "System shall" or "User can" and is verifiable
3. **Architecture alignment** — Feature fits the principles without conflicts
4. **Security** — Auth, input validation, and attack surface are addressed
5. **Measurability** — Every NFR row has a concrete, numeric target (not vague words like "fast" or "reliable")
6. **NFR coverage** — The NFR table contains rows for ALL of: Performance, Security, Scalability, Reliability, Maintainability, and Observability — missing any of these is an automatic gap

Return JSON only:
{{
  "score": 0.0,
  "approved": false,
  "gaps": ["specific gap 1", "specific gap 2"],
  "feedback": "one paragraph of actionable guidance"
}}

Set approved=true only if score >= {config.agent.judge_threshold}
"""

    raw = backend.call(system, user, max_tokens=1500, _agent="judge_feature_spec")
    data = _parse_json_safe(raw)
    data["approved"] = float(data.get("score", 0)) >= config.agent.judge_threshold
    return JudgeScore(**data)


# ── agent 13: refine feature spec ────────────────────────────────────────────

def refine_feature_spec(
    feature_spec_md: str,
    judge_score: JudgeScore,
    config: "SpeckitConfig",
    project_root: Path,
) -> str:
    """~3 000 input / ~2 500 output tokens."""
    backend = _get_backend(config)

    system = _prompt_override("refine_feature", project_root) or """
You are a product engineer improving a feature spec based on reviewer feedback.
RULES:
- Address every gap listed — do not skip any.
- Do not remove sections that were already good.
- Return the complete revised feature spec in markdown (all sections present).
- No preamble, no explanation outside the document.
"""

    gaps_text = "\n".join(f"- {g}" for g in judge_score.gaps)

    user = f"""## Current feature spec (score: {judge_score.score:.2f})
{feature_spec_md}

## Gaps to address
{gaps_text}

## Reviewer feedback
{judge_score.feedback}

Return the complete improved feature spec (maintain all sections, improve weak ones)."""

    return backend.call(system, user, max_tokens=3000, _agent="refine_feature_spec")


# ── agent 14: write build plan ───────────────────────────────────────────────

def write_build_plan(
    feature_spec_md: str,
    architecture_spec: str,
    config: "SpeckitConfig",
    project_root: Path,
) -> str:
    """~3 000 input / ~2 000 output tokens."""
    backend = _get_backend(config)

    system = _prompt_override("write_build_plan", project_root) or """
You are a senior engineer writing a step-by-step build plan for a feature.
RULES:
- Each task must be small enough to implement and test in one sitting (~1-2 hours).
- Tasks must be ordered by dependency — no task should depend on a later one.
- Every phase must end with a test step.
- Return only the markdown document.
"""

    user = f"""## Approved feature spec
{feature_spec_md[:2500]}

## Architecture
{architecture_spec[:1000]}

---
Write a build plan:

# Build plan: [feature name from spec]

## Phase 1 — Foundation
| Step | Task | Files affected | Test |
|------|------|---------------|------|
| 1.1 | [task] | [files] | [how to verify] |
[group into logical phases, 2-5 tasks each]

## Phase 2 — Core implementation
...

## Phase N — Integration & polish
...

## Definition of done
- [ ] All functional requirements from spec are implemented
- [ ] All NFR targets are met
- [ ] Tests pass
- [ ] Spec files updated
- [ ] No regressions in existing functionality
"""

    return backend.call(system, user, max_tokens=2000, _agent="write_build_plan")


# ── agent 15: judge scan specs ────────────────────────────────────────────────

def judge_scan_spec(
    spec_name: str,
    spec_type: str,   # "architecture" | "module" | "security" | "coding-standards"
    spec_content: str,
    config: "SpeckitConfig",
    project_root: Path,
) -> ScanJudgeResult:
    """Score a generated spec file for quality. ~1 500 input / ~600 output tokens."""
    backend = _get_backend(config)

    rubric = {
        "architecture": (
            "1. All standard sections present (purpose, tech stack, design patterns, security baseline, module map)\n"
            "2. Tech stack table has real versions, not placeholders\n"
            "3. Core principles are specific, not generic platitudes\n"
            "4. Security baseline has concrete policies (not 'TBD')\n"
            "5. Module map references real spec files\n"
            "6. Cross-cutting concerns address performance, scalability, reliability, and observability with measurable targets — not blank or 'TBD'"
        ),
        "module": (
            "1. Purpose is a single clear paragraph (not vague)\n"
            "2. Public interface section lists actual functions/routes with descriptions\n"
            "3. Dependencies listed (both directions: depends-on and depended-on-by)\n"
            "4. Error states table is non-empty\n"
            "5. Security notes section exists and is specific"
        ),
        "security": (
            "1. Auth mechanism specified with concrete TTLs and storage strategy\n"
            "2. Authorization model named (RBAC/ABAC/ownership) with roles listed\n"
            "3. Input validation schema library named\n"
            "4. API security section with HTTPS, CORS, rate limiting details\n"
            "5. Known risks table has at least one entry"
        ),
        "coding-standards": (
            "1. File/folder structure described with example paths\n"
            "2. Naming conventions table covers all entity types\n"
            "3. Testing standards specify file location and coverage target\n"
            "4. Git conventions define branch naming and commit format\n"
            "5. No copy-paste placeholder text remaining"
        ),
    }.get(spec_type, "1. Content is specific and non-generic\n2. No placeholder text\n3. All sections complete")

    system = _prompt_override("judge_scan_spec", project_root) or (
        "You are a spec quality reviewer. Be rigorous and specific. Return JSON only."
    )

    user = f"""## Spec file: {spec_name} (type: {spec_type})

{spec_content[:3000]}

---
Review against this rubric:
{rubric}

Return JSON only:
{{
  "score": 0.0,
  "issues": [
    {{"severity": "error|warning|info", "message": "specific issue", "suggestion": "how to fix it"}}
  ],
  "verdict": "good|needs-review|poor"
}}

Scoring:
- 0.85-1.0: all rubric items met → verdict "good"
- 0.6-0.84: some gaps → verdict "needs-review"
- below 0.6: significant problems → verdict "poor"

Only flag real issues — not stylistic preferences.
"""

    raw = backend.call(system, user, max_tokens=800, _agent="judge_scan_spec")
    data = _parse_json_safe(raw)

    raw_issues = data.get("issues", [])
    issues = []
    for item in raw_issues:
        if isinstance(item, dict):
            issues.append(ScanIssue(
                severity=item.get("severity", "warning"),
                message=item.get("message", ""),
                suggestion=item.get("suggestion", ""),
            ))

    return ScanJudgeResult(
        score=float(data.get("score", 0.5)),
        issues=issues,
        verdict=data.get("verdict", "needs-review"),
    )


# ── agent 16: draft vision ────────────────────────────────────────────────────

def draft_vision(
    answers: dict,   # keys: project_name, purpose, users, features, constraints, deploy_target, integrations
    config: "SpeckitConfig",
    project_root: Path,
) -> str:
    """~1 500 input / ~2 000 output tokens."""
    backend = _get_backend(config)

    system = _prompt_override("draft_vision", project_root) or """
You are a product architect writing a vision document from a discovery conversation.
RULES:
- Be specific — no generic filler. Every sentence should be about THIS project.
- Derive technical implications from the answers.
- Flag ambiguities as open questions.
- Return only the markdown document, no preamble.
"""

    answers_text = "\n".join(f"**{k}:** {v}" for k, v in answers.items() if v)

    user = f"""## Discovery answers

{answers_text}

---
Write the vision document:

# Vision: {answers.get('project_name', 'Untitled Project')}

## What it is
[One paragraph — what the software does and why it exists]

## Who it serves
[Primary users, their goals, their technical level]

## Core capabilities
[The 3-5 things it must do — each as a bullet with a brief "so that" rationale]

## Success metrics
[How we know this is working — measurable outcomes]

## Technical implications
[What the answers above imply about architecture, scale, security requirements]

## Constraints
[Hard limits from the discovery answers]

## Open questions
- [ ] [Ambiguity or decision that needs human input before speccing begins]
"""

    return backend.call(system, user, max_tokens=2000, _agent="draft_vision")


# ── agent 17: propose tech stack ─────────────────────────────────────────────

def propose_tech_stack(
    vision_md: str,
    deploy_target: str,
    primary_language: str,
    config: "SpeckitConfig",
    project_root: Path,
) -> str:
    """~2 000 input / ~1 500 output tokens."""
    backend = _get_backend(config)

    system = _prompt_override("propose_tech_stack", project_root) or """
You are a senior architect proposing a tech stack.
RULES:
- Recommend specific versions, not "latest".
- Justify every choice in terms of the project's stated requirements.
- Flag known risks or tradeoffs for each choice.
- Prefer battle-tested choices over trendy ones unless the project explicitly requires cutting-edge.
- Return only the markdown document.
"""

    user = f"""## Project vision
{vision_md[:2000]}

## Deploy target: {deploy_target}
## Primary language preference: {primary_language or 'no preference'}

---
Propose a complete tech stack:

# Tech stack proposal

## Summary
[One sentence: the chosen stack and why it's right for this project]

## Stack table
| Layer | Technology | Version | Why chosen | Risk |
|-------|-----------|---------|------------|------|
| Frontend | ... | ... | ... | ... |
| Backend | ... | ... | ... | ... |
| Database | ... | ... | ... | ... |
| Auth | ... | ... | ... | ... |
| Deployment | ... | ... | ... | ... |
[Add rows for caching, queuing, etc. if relevant]

## Package manager
[Which one and why]

## Development tooling
[Linter, formatter, test runner — specific versions]

## Alternatives considered
| Layer | Alternative | Rejected because |
|-------|------------|-----------------|
[What you evaluated but didn't pick]

## Open questions
- [ ] [Decision that needs human input]
"""

    return backend.call(system, user, max_tokens=1800, _agent="propose_tech_stack")


# ── agent 18: generate spec from vision ───────────────────────────────────────

def generate_spec_from_vision(
    spec_type: str,   # "architecture" | "security" | "coding-standards" | "data-models"
    vision_md: str,
    tech_stack_md: str,
    module_list: list[str],
    config: "SpeckitConfig",
    project_root: Path,
) -> str:
    """~3 000 input / ~2 500 output tokens."""
    backend = _get_coding_backend(config)

    today = __import__("datetime").date.today()

    if spec_type == "architecture":
        doc_instructions = f"""Write the complete architecture.md spec:

---
project: {config.project_name}
version: 1.0.0
last_updated: {today}
mode: greenfield
---

# Architecture overview

## Purpose
[From vision — one paragraph]

## Core principles
[5 specific principles derived from the vision and tech stack — not generic]

## System design
[High-level: main layers/services and how they relate]

## Tech stack
[Full table from tech stack proposal — include all layers with versions]

## Runtime environment
[OS, package manager, container, language version]

## Design patterns in use
[Patterns that fit the chosen stack]

## Forbidden patterns
[Anti-patterns specifically relevant to this project]

## Module map
| Module | Responsibility | Spec file |
|--------|---------------|-----------|
{chr(10).join(f'| {m} | [derive from vision] | specs/modules/{m}.md |' for m in module_list)}

## Security baseline
[Auth model, authorization, input validation, CORS, rate limiting, secrets management]

## Data flow
[How data moves through the system]

## Error handling contract
[Error response format, status code rules, validation error shape]"""

    elif spec_type == "security":
        doc_instructions = f"""Write the complete security.md spec:

---
project: {config.project_name}
last_updated: {today}
threat_model_reviewed: {today}
---

# Security specification

[All sections — make every requirement specific and measurable.
Derive from the tech stack (auth library, framework, deployment platform).]

## Authentication
## Authorization
## Input validation rules
## Data security
## API security
## Secrets management
## Security testing requirements
## Known risks and mitigations"""

    elif spec_type == "coding-standards":
        doc_instructions = f"""Write the complete coding-standards.md spec:

---
project: {config.project_name}
last_updated: {today}
---

# Coding standards

[All sections — specific to the chosen tech stack.
Include actual file path examples, actual tool names, actual version targets.]

## File and folder structure
## Naming conventions
## Import rules
## Function rules
## Comments
## Testing standards
## Environment variables
## Git conventions"""

    else:
        doc_instructions = f"""Write the complete data-models.md spec:

---
project: {config.project_name}
last_updated: {today}
---

# Data models

[Derive entities from the vision's core capabilities.
Use the ORM/schema syntax for the chosen database/ORM.
Include: entity index, per-entity model, validation rules, business rules, relationships.]"""

    system = _prompt_override(f"generate_spec_{spec_type}", project_root) or """
You are a senior architect generating a spec file from a project vision and tech stack.
RULES:
- Be specific — every value must be real, not a placeholder like '{version}' or '{TBD}'.
- Derive all content from the vision and tech stack documents provided.
- Return only the markdown document with YAML frontmatter.
- No preamble, no explanation outside the document.
"""

    user = f"""## Project vision
{vision_md[:2000]}

## Tech stack
{tech_stack_md[:1500]}

---
{doc_instructions}
"""

    return backend.call(system, user, max_tokens=2800, _agent=f"generate_spec_{spec_type}")


# ── agent 19: sync spec file ──────────────────────────────────────────────────

def sync_spec_file(
    spec_name: str,
    current_spec_content: str,
    changes_summary: str,
    config: "SpeckitConfig",
    project_root: Path,
) -> str:
    """
    Update a spec file to reflect changes that just landed (bug fix or feature).
    ~3 000 input / ~2 500 output tokens.
    """
    backend = _get_backend(config)

    system = _prompt_override("sync_spec", project_root) or """
You are a technical writer keeping a spec file up to date after a code change.
RULES:
- Update only what actually changed — do not rewrite unaffected sections.
- If a new function/route/class was added, add it to the public interface section.
- If error handling changed, update the error states table.
- If dependencies changed, update the dependencies section.
- Keep the same YAML frontmatter structure; update last_updated date.
- Return the complete updated spec document (not a diff).
- No preamble, no explanation outside the document.
"""

    today = __import__("datetime").date.today()

    user = f"""## Spec file to update: {spec_name}

{current_spec_content}

---
## Changes that just landed

{changes_summary}

---
Today's date: {today}

Return the complete updated spec file.
If nothing in the spec needs changing, return the original unchanged (update last_updated date only).
"""

    return backend.call(system, user, max_tokens=2800, _agent="sync_spec_file")


# ── agent 20: generate design doc ─────────────────────────────────────────────

def generate_design_doc(
    vision_md: str,
    tech_stack_md: str,
    architecture_spec: str,
    module_list: list[str],
    config: "SpeckitConfig",
    project_root: Path,
) -> str:
    """
    Generate a design_notes.md covering UI/UX flows, component list, API surface.
    ~3 500 input / ~2 500 output tokens.
    """
    backend = _get_coding_backend(config)

    today = __import__("datetime").date.today()
    deploy_target_hint = "CLI tool" if "cli" in architecture_spec.lower()[:500] else "web application"

    system = _prompt_override("generate_design_doc", project_root) or """
You are a product designer writing a design document from a technical vision.
RULES:
- Be specific about user flows — list every step the user takes.
- Every screen or component must have a clear purpose and data requirements.
- API surface must be consistent with the architecture spec.
- Accessibility and error states must be addressed.
- Return only the markdown document.
"""

    user = f"""## Project vision
{vision_md[:2000]}

## Tech stack
{tech_stack_md[:1000]}

## Architecture
{architecture_spec[:1000]}

## Modules: {', '.join(module_list)}

---
Write the complete design document:

---
project: {config.project_name}
last_updated: {today}
figma: pending
---

# Design notes: {config.project_name}

## Deployment type
{deploy_target_hint}

## User flows

### Flow 1: [primary flow name]
**Actor:** [user type]
**Goal:** [what they're trying to do]
**Steps:**
1. [step]
2. [step]
3. [expected outcome]
**Error states:** [what can go wrong and how it's shown]

### Flow 2: [second flow]
...

## Screen / component inventory
| Name | Type | Route/Location | Data required | Notes |
|------|------|---------------|--------------|-------|
[screen or component name, its type (page/component/dialog), where it lives, what data it needs]

## API surface summary
| Method | Path | Auth | Purpose |
|--------|------|------|---------|
[every endpoint the frontend/CLI needs — derived from module list]

## State management
[How application state is managed — local state, global store, server state, etc.]

## Design system / style guide
- Component library: [from tech stack — e.g. shadcn/ui, MUI, Tailwind, none]
- Color palette: [primary, secondary, error, success — or "TBD — link Figma"]
- Typography: [font family, scale]
- Spacing: [grid system or scale]

## Accessibility requirements
- Minimum standard: WCAG 2.1 AA
- Keyboard navigation: [which flows must be fully keyboard-navigable]
- Screen reader: [key ARIA requirements]
- Color contrast: [minimum ratio]

## Responsive / platform requirements
[Which breakpoints / platforms need to be supported]

## Empty states and loading
| Screen | Empty state | Loading state | Error state |
|--------|-------------|---------------|-------------|

## Open design questions
- [ ] [decision needing human input — e.g. "should we use a modal or page for X?"]

## Figma link
[Paste Figma URL here once designs are created]
"""

    return backend.call(system, user, max_tokens=2800, _agent="generate_design_doc")


# ── orchestrator output models ────────────────────────────────────────────────

class ServiceImpact(BaseModel):
    service_name: str
    reason: str
    scope: str          # "full" | "partial" | "none"
    affected_contracts: list[str]


class ServiceImpactResult(BaseModel):
    impacts: list[ServiceImpact]


class CrossServiceCompatResult(BaseModel):
    verdict: str                    # "approved" | "needs-changes" | "blocked"
    conflicts: list[str]
    breaking_changes: list[str]
    recommendations: list[str]


class ServiceSubFeature(BaseModel):
    service_name: str
    sub_feature_name: str
    sub_feature_description: str
    contract_constraints: str       # what contracts this service must honour


class ServiceDecomposition(BaseModel):
    sub_features: list[ServiceSubFeature]


class NewServiceSpec(BaseModel):
    name: str
    purpose: str
    tech_stack_hint: str
    depends_on: list[str]


class TopologyPlan(BaseModel):
    services_to_create: list[NewServiceSpec]
    services_to_modify: list[str]
    reasoning: str


# ── agent 21: analyze service impact ─────────────────────────────────────────

def analyze_service_impact(
    feature_name: str,
    feature_description: str,
    services_summary: dict[str, str],
    config: "SpeckitConfig",
    project_root: Path,
) -> ServiceImpactResult:
    """~2 000 input / ~600 output tokens."""
    backend = _get_backend(config)

    system = _prompt_override("analyze_service_impact", project_root) or (
        "You are a senior architect analysing which microservices a proposed feature will touch. "
        "Be precise — only include services that genuinely need to change. Return JSON only."
    )

    services_text = "\n\n".join(
        f"### {name}\n{summary[:800]}"
        for name, summary in services_summary.items()
    )

    user = f"""## Feature request
Name: {feature_name}
Description: {feature_description}

## Existing services
{services_text}

---
For each service, determine whether it needs to change.
Return JSON only:
{{
  "impacts": [
    {{
      "service_name": "name",
      "reason": "why this service is affected",
      "scope": "full",
      "affected_contracts": ["contract-file-name.md"]
    }}
  ]
}}

scope must be one of: "full" (major work), "partial" (minor addition), "none" (no change needed).
Only include services with scope "full" or "partial".
affected_contracts lists contract spec files (e.g. "backend-api__payments.md") that this change touches.
"""

    raw = backend.call(system, user, max_tokens=1000, _agent="analyze_service_impact")
    return ServiceImpactResult(**_parse_json_safe(raw))


# ── agent 22: check contract compatibility ────────────────────────────────────

def check_contract_compatibility(
    feature_name: str,
    impacted_services: list[ServiceImpact],
    contract_specs: dict[str, str],
    config: "SpeckitConfig",
    project_root: Path,
) -> CrossServiceCompatResult:
    """~3 000 input / ~800 output tokens."""
    backend = _get_backend(config)

    system = _prompt_override("check_contract_compatibility", project_root) or (
        "You are a senior architect checking whether a cross-service feature will break "
        "existing inter-service contracts. Be rigorous. Return JSON only."
    )

    impacts_text = "\n".join(
        f"- {i.service_name} ({i.scope}): {i.reason}"
        for i in impacted_services
    )

    contracts_text = "\n\n".join(
        f"### {name}\n{content[:1000]}"
        for name, content in contract_specs.items()
    ) or "(no contract specs found — contracts/ directory is empty)"

    user = f"""## Feature: {feature_name}

## Services that will change
{impacts_text}

## Current inter-service contracts
{contracts_text}

---
Will this feature break any existing contracts between services?
Return JSON only:
{{
  "verdict": "approved",
  "conflicts": ["specific contract violation"],
  "breaking_changes": ["what existing behaviour will break"],
  "recommendations": ["what to do to avoid breakage"]
}}

verdict: "approved" if no conflicts, "needs-changes" if fixable, "blocked" if fundamental conflict.
"""

    raw = backend.call(system, user, max_tokens=1200, _agent="check_contract_compatibility")
    return CrossServiceCompatResult(**_parse_json_safe(raw))


# ── agent 23: decompose cross-service feature ─────────────────────────────────

def decompose_cross_service_feature(
    feature_name: str,
    feature_description: str,
    impacted_services: list[ServiceImpact],
    contract_specs: dict[str, str],
    config: "SpeckitConfig",
    project_root: Path,
) -> ServiceDecomposition:
    """~3 000 input / ~1 500 output tokens."""
    backend = _get_backend(config)

    system = _prompt_override("decompose_cross_service_feature", project_root) or """
You are a senior architect decomposing a cross-service feature into per-service sub-features.
RULES:
- Each sub-feature must be scoped ONLY to what that service needs to build.
- sub_feature_description must be detailed enough to pass to a spec-writing agent as a standalone feature.
- contract_constraints must explicitly name the API/event contracts that service must honour.
- Do not invent work — only what the feature requires.
- Return JSON only.
"""

    impacts_text = "\n".join(
        f"- {i.service_name} ({i.scope}): {i.reason}"
        for i in impacted_services
    )

    contracts_text = "\n\n".join(
        f"### {name}\n{content[:600]}"
        for name, content in contract_specs.items()
    ) or "(no existing contracts)"

    user = f"""## Cross-service feature
Name: {feature_name}
Description: {feature_description}

## Services involved
{impacts_text}

## Existing contracts
{contracts_text}

---
Break this feature into one sub-feature per affected service.
Return JSON only:
{{
  "sub_features": [
    {{
      "service_name": "frontend",
      "sub_feature_name": "Add payment UI",
      "sub_feature_description": "Detailed description of exactly what this service must build...",
      "contract_constraints": "Must call POST /api/payments with {{amount, currency, token}}. Expects {{success, transaction_id}} response."
    }}
  ]
}}
"""

    raw = backend.call(system, user, max_tokens=2500, _agent="decompose_cross_service_feature")
    return ServiceDecomposition(**_parse_json_safe(raw))


# ── agent 24: write cross-service build plan ──────────────────────────────────

def write_cross_service_build_plan(
    feature_name: str,
    service_build_plans: dict[str, str],
    dependency_order: list[str],
    config: "SpeckitConfig",
    project_root: Path,
) -> str:
    """~4 000 input / ~2 000 output tokens."""
    backend = _get_backend(config)

    system = _prompt_override("cross_service_build_plan", project_root) or """
You are a senior engineer writing a sequenced build plan across multiple services.
RULES:
- Services must be built in dependency order — foundational services first.
- Include an integration phase between each service phase.
- Every phase must end with a verification step (run tests, check contract).
- Return only the markdown document.
"""

    plans_text = "\n\n---\n\n".join(
        f"### {name} build plan\n{plan[:1200]}"
        for name, plan in service_build_plans.items()
    )

    order_text = " → ".join(dependency_order)

    user = f"""## Cross-service feature: {feature_name}

## Dependency order: {order_text}

## Per-service build plans
{plans_text}

---
Write the complete cross-service build plan:

# Cross-service build plan: {feature_name}

## Build sequence
**Dependency order:** {order_text}

## Phase 1 — {dependency_order[0] if dependency_order else '[first service]'}: Foundation
| Step | Task | Files | Verify |
|------|------|-------|--------|
[steps from that service's build plan]

## Phase 1 → 2 Integration check
- [ ] [contract between phase 1 and phase 2 service is working]

## Phase 2 — [next service]
...

## Final integration phase
| Step | Task | Verify |
|------|------|--------|
| I01 | End-to-end test: full feature flow | All services return expected responses |
| I02 | Contract regression test | No existing consumers broken |
| I03 | Performance test across service boundary | Latency within NFR target |

## Definition of done
- [ ] All per-service definitions of done met
- [ ] Integration tests pass
- [ ] No contract regressions
- [ ] All NFR targets met end-to-end
"""

    return backend.call(system, user, max_tokens=2500, _agent="write_cross_service_build_plan")


# ── agent 25: write integration test plan ────────────────────────────────────

def write_integration_test_plan(
    feature_name: str,
    service_feature_specs: dict[str, str],
    contract_specs: dict[str, str],
    config: "SpeckitConfig",
    project_root: Path,
) -> str:
    """~3 500 input / ~2 000 output tokens."""
    backend = _get_backend(config)

    system = _prompt_override("integration_test_plan", project_root) or """
You are a QA engineer writing integration tests that span service boundaries.
RULES:
- Every test must cross at least one service boundary.
- Include contract regression tests for each inter-service contract.
- Include end-to-end user journey tests.
- Include failure/degradation tests (what happens when one service is slow/down).
- Return only the markdown document.
"""

    specs_text = "\n\n---\n\n".join(
        f"### {name} feature spec\n{spec[:800]}"
        for name, spec in service_feature_specs.items()
    )

    contracts_text = "\n\n".join(
        f"### {name}\n{content[:600]}"
        for name, content in contract_specs.items()
    ) or "(no contracts defined)"

    user = f"""## Feature: {feature_name}

## Per-service feature specs
{specs_text}

## Inter-service contracts
{contracts_text}

---
Write the complete integration test plan:

# Integration test plan: {feature_name}

## End-to-end user journey tests
| ID | Scenario | Services touched | Steps | Expected outcome |
|----|----------|-----------------|-------|-----------------|
| E01 | Happy path | [services] | [steps] | [outcome] |
| E02 | [edge case] | ... | ... | ... |

## Contract regression tests
For each inter-service contract:
| ID | Contract | Producer | Consumer | Test | Expected |
|----|----------|---------|---------|------|---------|

## Failure and degradation tests
| ID | Failure scenario | Expected behaviour |
|----|-----------------|-------------------|
| F01 | [service] is unavailable | [graceful degradation] |
| F02 | [service] responds slowly (>2s) | [timeout + retry behaviour] |

## Performance tests (cross-boundary)
| ID | Scenario | SLA target | Test method |
|----|----------|-----------|------------|

## Test environment requirements
[What infrastructure/mocks are needed to run these tests]
"""

    return backend.call(system, user, max_tokens=2500, _agent="write_integration_test_plan")


# ── agent 26: plan service topology ──────────────────────────────────────────

def plan_service_topology(
    feature_name: str,
    feature_description: str,
    existing_services_summary: dict[str, str],
    system_architecture: str,
    config: "SpeckitConfig",
    project_root: Path,
) -> TopologyPlan:
    """~2 500 input / ~1 000 output tokens."""
    backend = _get_backend(config)

    system = _prompt_override("plan_service_topology", project_root) or (
        "You are a senior architect deciding what services need to exist for a feature. "
        "Be conservative — only create a new service when the feature genuinely can't be "
        "built inside an existing one. Return JSON only."
    )

    services_text = "\n".join(
        f"- {name}: {summary[:400]}"
        for name, summary in existing_services_summary.items()
    ) or "(no services registered yet)"

    user = f"""## Feature request
Name: {feature_name}
Description: {feature_description}

## System architecture
{system_architecture[:1500]}

## Existing services
{services_text}

---
Decide which existing services need to change and whether any NEW services must be created.

Return JSON only:
{{
  "services_to_create": [
    {{
      "name": "payments-service",
      "purpose": "Handle payment processing, refunds, and transaction history",
      "tech_stack_hint": "Python FastAPI — matches existing backend stack",
      "depends_on": ["auth-service"]
    }}
  ],
  "services_to_modify": ["backend-api", "frontend"],
  "reasoning": "One paragraph explaining the topology decision"
}}

services_to_create is empty if no new service is needed.
services_to_modify lists existing service names that need feature work.
"""

    raw = backend.call(system, user, max_tokens=1500, _agent="plan_service_topology")
    return TopologyPlan(**_parse_json_safe(raw))


# ── agent 27: analyze bug service impact ─────────────────────────────────────

def analyze_bug_service_impact(
    issue_title: str,
    issue_body: str,
    classification: "Classification",
    services_summary: dict[str, str],
    config: "SpeckitConfig",
    project_root: Path,
) -> ServiceImpactResult:
    """~2 000 input / ~600 output tokens."""
    backend = _get_backend(config)

    system = _prompt_override("analyze_bug_service_impact", project_root) or (
        "You are a senior architect determining which microservices are affected by a bug. "
        "Only include services that need a code change to fix the bug. Return JSON only."
    )

    services_text = "\n\n".join(
        f"### {name}\n{summary[:600]}"
        for name, summary in services_summary.items()
    )

    user = f"""## Bug report
Title: {issue_title}
Type: {classification.issue_type} | Severity: {classification.severity}
Affected modules: {', '.join(classification.affected_modules)}

{issue_body[:1500]}

## Services
{services_text}

---
Which services need a code change to fix this bug?
Return JSON only:
{{
  "impacts": [
    {{
      "service_name": "backend-api",
      "reason": "the null pointer bug is in the payment handler in this service",
      "scope": "partial",
      "affected_contracts": ["backend-api__payments.md"]
    }}
  ]
}}

scope: "full" for major changes, "partial" for small targeted fix.
Only include services that genuinely need a code change.
"""

    raw = backend.call(system, user, max_tokens=800, _agent="analyze_bug_service_impact")
    return ServiceImpactResult(**_parse_json_safe(raw))
