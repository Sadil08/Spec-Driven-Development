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


# ── backend abstraction ───────────────────────────────────────────────────────

class _AnthropicBackend:
    def __init__(self, client, model: str):
        self._client = client
        self.model = model

    def call(self, system: str, user: str, max_tokens: int) -> str:
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
        return resp.content[0].text.strip()


class _GeminiBackend:
    def __init__(self, client, model: str):
        self._client = client
        self.model = model

    def call(self, system: str, user: str, max_tokens: int) -> str:
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
    cleaned = _strip_fences(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # Replace literal newlines inside strings with \\n
    # Find all string values and escape newlines within them
    fixed = re.sub(
        r'"((?:[^"\\]|\\.)*)"',
        lambda m: '"' + m.group(1).replace('\n', '\\n').replace('\r', '') + '"',
        cleaned,
    )
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass
    # Last resort: extract key fields individually
    result: dict = {}
    for key in ("score", "approved", "gaps", "feedback",
                "issue_type", "severity", "affected_modules", "search_keywords", "summary"):
        m = re.search(rf'"{key}"\s*:\s*("(?:[^"\\]|\\.)*"|\[.*?\]|true|false|[\d.]+)', cleaned, re.DOTALL)
        if m:
            try:
                result[key] = json.loads(m.group(1))
            except Exception:
                result[key] = m.group(1).strip('"')
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

    raw = backend.call(system, user, max_tokens=800)
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

    return backend.call(system, user, max_tokens=2500)


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

    raw = backend.call(system, user, max_tokens=1500)
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

    return backend.call(system, user, max_tokens=2500)


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

    return backend.call(system, user, max_tokens=1800)


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

    return backend.call(system, user, max_tokens=1800)


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

    return backend.call(system, user, max_tokens=2000)
