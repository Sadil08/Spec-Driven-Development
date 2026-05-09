"""
Claude API agents — one function per pipeline stage.

Each agent receives only the context it needs (token budget control).
System prompts can be overridden by placing a .md file in .speckit/prompts/.
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


# ── data models ───────────────────────────────────────────────────────────────

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


# ── shared helpers ────────────────────────────────────────────────────────────

def _get_client(config: "SpeckitConfig") -> anthropic.Anthropic:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise EnvironmentError(
            "ANTHROPIC_API_KEY not set. Add it to .env:\n  ANTHROPIC_API_KEY=sk-ant-..."
        )
    return anthropic.Anthropic(api_key=key)


def _load_prompt_override(name: str, project_root: Path) -> str | None:
    """Load a user-defined prompt from .speckit/prompts/{name}.md if it exists."""
    p = project_root / ".speckit" / "prompts" / f"{name}.md"
    return p.read_text(encoding="utf-8") if p.exists() else None


def _strip_fences(text: str) -> str:
    """Remove markdown code fences from LLM output before JSON parsing."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        text = text.rsplit("```", 1)[0]
    return text.strip()


def _call(
    client: anthropic.Anthropic,
    model: str,
    system: str,
    user: str,
    max_tokens: int,
) -> str:
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system.strip(),
        messages=[{"role": "user", "content": user.strip()}],
    )
    return response.content[0].text.strip()


# ── agent 1: classify ─────────────────────────────────────────────────────────

def classify_issue(
    issue_title: str,
    issue_body: str,
    config: "SpeckitConfig",
    project_root: Path,
) -> Classification:
    """
    Classify a GitHub issue — extract type, affected modules, severity,
    and search keywords for spec lookup.

    Input tokens:  ~500
    Output tokens: ~200
    """
    client = _get_client(config)

    system = _load_prompt_override("classify", project_root) or (
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

    raw = _call(client, config.agent.model, system, user, max_tokens=400)
    return Classification(**json.loads(_strip_fences(raw)))


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
    """
    Draft a full bug report markdown document.

    Input tokens:  ~3 000
    Output tokens: ~2 000
    """
    client = _get_client(config)

    specs_text = "\n\n---\n\n".join(
        f"### {s.get('path', '')} (module: {s.get('module', '')})\n"
        f"{s.get('content', '')[:1200]}"
        for s in spec_files
    ) or "(no relevant spec files found)"

    source_text = "\n\n---\n\n".join(
        f"### {path}\n```\n{content[:800]}\n```"
        for path, content in source_file_contents.items()
    ) or "(no source files resolved)"

    system = _load_prompt_override("draft_bug_report", project_root) or """
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

    return _call(client, config.agent.model, system, user, max_tokens=2500)


# ── agent 3: judge ────────────────────────────────────────────────────────────

def judge_bug_report(
    bug_report_md: str,
    architecture_spec: str,
    security_spec: str,
    config: "SpeckitConfig",
    project_root: Path,
) -> JudgeScore:
    """
    Score a bug report 0–1 against architecture + security specs.
    Returns score, approval, gaps, and actionable feedback.

    Input tokens:  ~2 500
    Output tokens: ~400
    """
    client = _get_client(config)

    system = _load_prompt_override("judge", project_root) or (
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

    raw = _call(client, config.agent.model, system, user, max_tokens=500)
    data = json.loads(_strip_fences(raw))
    data["approved"] = float(data.get("score", 0)) >= config.agent.judge_threshold
    return JudgeScore(**data)


# ── agent 4: refine ───────────────────────────────────────────────────────────

def refine_bug_report(
    bug_report_md: str,
    judge_score: JudgeScore,
    config: "SpeckitConfig",
    project_root: Path,
) -> str:
    """
    Improve a bug report based on judge feedback.

    Input tokens:  ~2 500
    Output tokens: ~2 000
    """
    client = _get_client(config)

    system = _load_prompt_override("refine", project_root) or """
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

    return _call(client, config.agent.model, system, user, max_tokens=2500)


# ── agent 5: test plan ────────────────────────────────────────────────────────

def write_test_plan(
    bug_report_md: str,
    config: "SpeckitConfig",
    project_root: Path,
) -> str:
    """
    Generate a concrete test plan from an approved bug report.

    Input tokens:  ~2 000
    Output tokens: ~1 500
    """
    client = _get_client(config)

    system = _load_prompt_override("test_plan", project_root) or """
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

    return _call(client, config.agent.model, system, user, max_tokens=1800)
