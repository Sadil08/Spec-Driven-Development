# SDD Master Reference — Templates + Build Plan
> Save this file. It contains every template and the full build plan for the spec-driven development system.

---

# PART 1: DOCUMENT TEMPLATES

## Template 1: `specs/architecture.md`
The root truth file. Every agent reads this first.

```markdown
---
project: {project-name}
version: 1.0.0
last_updated: {date}
mode: greenfield | brownfield
---

# Architecture overview

## Purpose
{one paragraph: what this software does and who it serves}

## Core principles
1. {e.g. API-first: all features exposed via versioned REST/GraphQL endpoints}
2. {e.g. Stateless services: no server-side session state}
3. {e.g. Fail loudly in dev, gracefully in prod}
4. {e.g. No hardcoded credentials — all secrets via environment variables}
5. {e.g. Every external call must have a timeout and retry policy}

## System design
{high-level: what are the main layers/services and how do they relate}

## Tech stack
| Layer       | Technology     | Version  | Reason chosen |
|-------------|----------------|----------|---------------|
| Frontend    | {e.g. Next.js} | {14.x}   | {reason}      |
| Backend     | {e.g. FastAPI} | {0.11x}  | {reason}      |
| Database    | {e.g. Postgres}| {16.x}   | {reason}      |
| Auth        | {e.g. Clerk}   | {latest} | {reason}      |
| Deployment  | {e.g. Railway} | -        | {reason}      |

## Runtime environment
- OS: {e.g. Ubuntu 22.04}
- Package manager: {e.g. pnpm 8.x / pip + uv}
- Container: {e.g. Docker 24.x}
- Node version: {e.g. 20.x LTS} (if applicable)
- Python version: {e.g. 3.11} (if applicable)

## Design patterns in use
- {e.g. Repository pattern for all DB access}
- {e.g. Service layer between routes and repositories}
- {e.g. DTOs for all API boundaries}
- {e.g. Event-driven for async operations}

## Forbidden patterns
- {e.g. No direct DB queries in route handlers}
- {e.g. No any type in TypeScript}
- {e.g. No console.log in production code — use structured logger}
- {e.g. No synchronous file I/O in request path}

## Module map
| Module     | Responsibility              | Spec file                    |
|------------|-----------------------------|------------------------------|
| auth       | {what it owns}              | specs/modules/auth.md        |
| users      | {what it owns}              | specs/modules/users.md       |
| {module}   | {what it owns}              | specs/modules/{module}.md    |

## Security baseline
- Auth model: {e.g. JWT with refresh tokens, 15min access token TTL}
- Authorization: {e.g. RBAC with roles: admin, user, viewer}
- Input validation: {e.g. Zod on all API boundaries}
- CORS: {e.g. allowlist only, no wildcard in production}
- Rate limiting: {e.g. 100 req/min per IP on public routes}
- Secrets: all via environment variables, never committed

## Data flow
{describe how data moves through the system — request in, processed by, stored in, returned as}

## Error handling contract
- {e.g. All errors return {code, message, requestId}}
- {e.g. 4xx = client error, 5xx = server error, never swap}
- {e.g. Validation errors return field-level detail}
```

---

## Template 2: `specs/coding-standards.md`

```markdown
---
project: {project-name}
last_updated: {date}
---

# Coding standards

## File and folder structure
{describe the actual folder layout with example paths}

## Naming conventions
| Thing         | Convention      | Example                    |
|---------------|-----------------|----------------------------|
| Files         | kebab-case      | user-service.ts            |
| Classes       | PascalCase      | UserService                |
| Functions     | camelCase       | getUserById                |
| Constants     | SCREAMING_SNAKE | MAX_RETRY_COUNT            |
| DB tables     | snake_case      | user_sessions              |
| Env vars      | SCREAMING_SNAKE | DATABASE_URL               |

## Import rules
- {e.g. Absolute imports only, no ../../../}
- {e.g. Barrel exports from each module index}
- {e.g. Third-party imports before local imports}

## Function rules
- Max function length: {e.g. 40 lines}
- Max parameters: {e.g. 3 — use object if more needed}
- {e.g. Pure functions preferred, side effects isolated}
- {e.g. All async functions must handle errors explicitly}

## Comments
- {e.g. No commented-out code in commits}
- {e.g. JSDoc on all exported functions}
- {e.g. TODO comments must include issue number: // TODO #123}

## Testing standards
- Test file location: {e.g. co-located __tests__/ or /tests at root}
- Naming: {e.g. {feature}.test.ts}
- Coverage target: {e.g. 80% minimum on services, 100% on utils}
- {e.g. No test should depend on another test's state}

## Environment variables
- All secrets in .env (never committed)
- .env.example committed with placeholder values
- Validated at startup via {e.g. Zod schema / pydantic Settings}
- Format: {PREFIX}_{CATEGORY}_{NAME} e.g. APP_DB_HOST

## Git conventions
- Branch naming: fix/issue-{n}-{slug} | feature/{name} | chore/{name}
- Commit format: {e.g. conventional commits: feat:, fix:, chore:}
- PR must: reference issue, include test results, update specs
```

---

## Template 3: `specs/security.md`

```markdown
---
project: {project-name}
last_updated: {date}
threat_model_reviewed: {date}
---

# Security specification

## Authentication
- Mechanism: {e.g. JWT / session / OAuth}
- Token TTL: access {15min}, refresh {7 days}
- Storage: {e.g. httpOnly cookie for web, secure storage for mobile}
- Rotation: {e.g. refresh token rotated on every use}

## Authorization
- Model: {e.g. RBAC / ABAC / ownership-based}
- Roles: {list roles and what each can do}
- Rule: authorization checked at service layer, not only route layer

## Input validation rules
- All API inputs validated before processing
- Schema library: {e.g. Zod / Pydantic / Joi}
- File uploads: {max size, allowed types, virus scan if applicable}
- SQL: parameterized queries only, no string concatenation
- {Frontend}: sanitize before rendering, no dangerouslySetInnerHTML

## Data security
- PII fields: {list — e.g. email, phone, address}
- PII encrypted at rest: {yes/no, which fields}
- PII in logs: never log PII fields
- Data retention: {policy}

## API security
- HTTPS only in production
- CORS allowlist: {list allowed origins}
- Rate limiting: {limits per route type}
- API versioning: {strategy}
- Sensitive endpoints: {list and their extra protections}

## Secrets management
- Provider: {e.g. env vars / Railway secrets / AWS Secrets Manager}
- Never in: code, git, logs, error messages, API responses
- Rotation policy: {how often, how done}

## Security testing requirements
Every bug fix and feature must pass:
- [ ] OWASP top 10 checklist applicable items
- [ ] No new credentials in code (automated secret scanning)
- [ ] Auth/authz not weakened
- [ ] Input validation on all new fields
- [ ] New endpoints have rate limiting if public

## Known risks and mitigations
| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| {risk} | {H/M/L} | {H/M/L} | {what's done} |
```

---

## Template 4: `specs/modules/{module}.md`
One file per domain module.

```markdown
---
module: {module-name}
affects: [{list of other modules this touches}]
files: [{src/path/to/main.ts}, {src/path/to/service.ts}]
last_updated: {date}
---

# Module: {module-name}

## Purpose
{what this module is responsible for — one paragraph}

## Public interface
### Exported functions / classes / routes
| Name | Type | Description |
|------|------|-------------|
| {name} | {function/class/route} | {what it does} |

## Internal structure
{how it's organised internally — key files and their roles}

## Data owned
{which DB tables / collections / stores this module owns}

## Dependencies
- Depends on: {list of other modules}
- Depended on by: {list of other modules}

## Key decisions
{why it's built the way it is — important for anyone changing it}

## Known edge cases
{things that behave unexpectedly or require special handling}

## Error states
| Error | When | How handled |
|-------|------|-------------|
| {error} | {condition} | {handling} |

## Security notes
{anything security-relevant specific to this module}
```

---

## Template 5: `runs/bug-fix-{id}/02_bug_report.md`

```markdown
---
issue: #{number}
title: {issue title}
branch: fix/issue-{number}-{slug}
date: {date}
status: draft | judge-approved | coding | testing | done
judge_score: {0.0-1.0}
---

# Bug report

## Issue summary
{plain language description of what is broken and how it manifests}

## Steps to reproduce
1. {step}
2. {step}
3. Expected: {x} | Actual: {y}

## Root cause analysis
- File: {path:line}
- Type: logic-error | null-reference | race-condition | type-error | config | dependency | security
- Triggered by: {exact conditions that cause it}
- Root cause: {explanation}

## Architecture impact
- Modules affected: {from spec front-matter}
- Architecture principles at risk: {which rules from architecture.md}
- Data models affected: {entities}
- Security surface touched: yes/no — {details if yes}

## Proposed fix
{description of the fix approach in plain language}

### Why this approach does not violate architecture
{explicit reasoning against architecture.md principles}

### Alternative approaches considered
| Approach | Rejected because |
|----------|-----------------|
| {alt} | {reason} |

## Dependencies required
| Package | Version | Justification | Runtime compatible | Added to |
|---------|---------|---------------|-------------------|----------|
| {pkg} | {ver} | {why needed} | verified: yes/no | package.json/requirements.txt |

## Environment variables required
| Variable | Purpose | Default | Required in |
|----------|---------|---------|-------------|
| {VAR_NAME} | {purpose} | none | .env.example |

## Code changes (before/after)
### {filename}
**Before:**
```{lang}
{original code}
```
**After:**
```{lang}
{fixed code}
```

## Security checklist
- [ ] Fix does not expose new attack surface
- [ ] Input validation unchanged or strengthened
- [ ] No credentials introduced (all via env vars)
- [ ] Auth/authz not weakened
- [ ] No PII in logs added
- [ ] Rate limiting unaffected

## Test strategy
See: `runs/bug-fix-{id}/03_test_plan.md`

## Spec files to update post-merge
- [ ] specs/modules/{module}.md — {what to update}
- [ ] specs/global_learnings.md — {pattern to add if applicable}
```

---

## Template 6: `runs/bug-fix-{id}/03_test_plan.md`

```markdown
---
issue: #{number}
date: {date}
status: planned | running | passed | failed
---

# Test plan: bug fix #{number}

## Regression test (prove the bug is fixed)
| ID | Test | Input | Expected output | Pass/Fail |
|----|------|-------|-----------------|-----------|
| R01 | {test name} | {input} | {expected} | - |

## Unit tests
| ID | Function/method | Scenario | Expected | Pass/Fail |
|----|----------------|----------|----------|-----------|
| U01 | {function} | {scenario} | {expected} | - |

## Integration tests
| ID | Flow | Steps | Expected | Pass/Fail |
|----|------|-------|----------|-----------|
| I01 | {flow name} | {steps} | {expected} | - |

## API tests (curl / Postman)
```bash
# Test R01 — {description}
curl -X POST {url} \
  -H "Authorization: Bearer $TEST_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"field": "value"}' \
  | jq '{status: .status, data: .data}'
# Expected: {"status": "success", "data": {...}}
```

## Frontend tests (if applicable)
| ID | Component | Action | Expected | Pass/Fail |
|----|-----------|--------|----------|-----------|
| F01 | {component} | {action} | {expected} | - |

## Security tests
| ID | Test | Method | Expected | Pass/Fail |
|----|------|--------|----------|-----------|
| S01 | Auth required on fixed endpoint | Request without token | 401 | - |
| S02 | Input validation | Send malformed input | 400 with detail | - |

## Test results summary
- Total: {n}
- Passed: {n}
- Failed: {n}
- Skipped: {n}

## Failed tests detail
{fill in after running}
```

---

## Template 7: `runs/feature-{name}/03_feature_spec.md`

```markdown
---
feature: {feature-name}
issue: #{number}
branch: feature/{name}
date: {date}
status: research | speccing | judge-approved | building | testing | done
judge_score: {0.0-1.0}
---

# Feature spec: {feature name}

## User story
As a {user type}, I want {capability}, so that {outcome}.

## Problem statement
{what gap this fills, why it matters}

## Research summary
See: `runs/feature-{name}/01_research.md`
- Similar implementations: {list with links}
- Chosen approach: {pattern and justification}
- Key learnings from research: {what informed this spec}

## Compatibility assessment
- Architecture fit: {how this aligns — reference specific principles}
- Potential conflicts: {any architecture rules that need care}
- Breaking changes: none | {list what breaks and migration path}
- Performance impact: {assessment}

## Functional requirements
| ID | Requirement | Priority | Notes |
|----|-------------|----------|-------|
| F01 | {requirement} | must/should/could | {notes} |

## Non-functional requirements
| ID | Category | Requirement | Metric |
|----|----------|-------------|--------|
| NF01 | Performance | {requirement} | {measurable target} |
| NF02 | Security | {requirement} | {measurable target} |
| NF03 | Accessibility | {requirement} | {standard, e.g. WCAG 2.1 AA} |

## Data model changes
### New entities
{entity name, fields, types, validation rules, relationships}

### Modified entities
{entity name, what changes and why}

## API contract
### New endpoints
```
{METHOD} /api/v1/{path}
Auth: required | public
Body: {shape}
Response 200: {shape}
Response 4xx: {error cases}
```

## UI/UX specification
- Figma link: {url or "pending"}
- Key user flows:
  1. {flow}: {steps}
- Component list: {new components needed}
- Design notes: see `runs/feature-{name}/04_design_notes.md`

## Security considerations
- Auth required: yes/no
- New roles/permissions needed: {list}
- New attack surface: {describe}
- Mitigations: {list}
- OWASP concerns: {applicable items}

## Dependencies
| Package | Version | Justification | Runtime compatible |
|---------|---------|---------------|--------------------|
| {pkg} | {ver} | {why} | verified: yes/no |

## Environment variables
| Variable | Purpose | Required in |
|----------|---------|-------------|
| {VAR} | {purpose} | prod, staging |

## Spec files to create/update
- [ ] specs/modules/{module}.md — {what changes}
- [ ] specs/features/{name}.md — create
- [ ] specs/data-models.md — {if data changes}

## Out of scope
{explicit list of what is NOT included in this feature}

## Open questions
- [ ] {question needing human decision}
```

---

## Template 8: `runs/feature-{name}/05_test_plan.md`

```markdown
---
feature: {name}
date: {date}
status: planned | running | passed | failed
---

# Test plan: {feature name}

## Unit tests — backend
| ID | Function | Scenario | Expected | Pass/Fail |
|----|----------|----------|----------|-----------|
| UB01 | {fn} | happy path | {expected} | - |
| UB02 | {fn} | invalid input | 400 + detail | - |
| UB03 | {fn} | unauthenticated | 401 | - |
| UB04 | {fn} | unauthorized role | 403 | - |

## Unit tests — frontend
| ID | Component | Scenario | Expected | Pass/Fail |
|----|-----------|----------|----------|-----------|
| UF01 | {component} | renders correctly | matches snapshot | - |
| UF02 | {component} | user interaction | {expected state} | - |

## Integration tests
| ID | Flow | Steps | Expected | Pass/Fail |
|----|------|-------|----------|-----------|
| I01 | {full flow name} | {steps} | {end state} | - |

## API tests
```bash
# Test I01 — {description}
curl -X POST {base_url}/api/v1/{path} \
  -H "Authorization: Bearer $TEST_TOKEN" \
  -d '{"key": "value"}' \
  | jq .

# Expected response:
# { "id": "...", "status": "created" }
```

## E2E tests (if applicable)
| ID | Scenario | User role | Steps | Expected | Pass/Fail |
|----|----------|-----------|-------|----------|-----------|
| E01 | {scenario} | {role} | {steps} | {expected} | - |

## Security tests
| ID | Test | Method | Expected | Pass/Fail |
|----|------|--------|----------|-----------|
| S01 | Endpoint requires auth | No token | 401 | - |
| S02 | Role enforcement | Wrong role | 403 | - |
| S03 | Input injection | SQL/XSS payload | 400, not executed | - |
| S04 | Rate limit | Exceed limit | 429 | - |

## Performance tests (if NF requirement set)
| ID | Scenario | Load | Expected p95 | Pass/Fail |
|----|----------|------|-------------|-----------|
| P01 | {endpoint} | {rps} | {ms} | - |

## Test results summary
- Total: {n} | Passed: {n} | Failed: {n}
## Failed detail: {fill after run}
```

---

## Template 9: `specs/global_learnings.md`
Grows over time. Never deleted, only appended.

```markdown
---
project: {project-name}
last_updated: {date}
total_patterns: {n}
---

# Global learnings

This file is append-only. Every time a fix or feature reveals a reusable pattern
or a recurring mistake, it is added here. Agents read this file at the start of
every run to avoid repeating past mistakes.

---

## Pattern library

### GL-001 — {pattern title}
- Discovered: {date} during {issue/feature}
- Context: {when this applies}
- Pattern: {what to do}
- Anti-pattern: {what NOT to do}
- Example:
```{lang}
{code example}
```

### GL-002 — {pattern title}
...

---

## Recurring mistakes to avoid

### RM-001 — {mistake title}
- Seen: {date}, {date}
- Mistake: {what was done wrong}
- Correct approach: {what to do instead}

---

## Environment-specific notes
{gotchas about this specific project's environment that trip up agents}
```

---

## Template 10: `specs/data-models.md`

```markdown
---
project: {project-name}
last_updated: {date}
---

# Data models

## Entity index
| Entity | Table/Collection | Module owner | Spec section |
|--------|-----------------|--------------|--------------|
| User | users | auth | #user |
| {entity} | {table} | {module} | #{anchor} |

---

## User {#user}
```prisma
model User {
  id        String   @id @default(cuid())
  email     String   @unique
  role      Role     @default(USER)
  createdAt DateTime @default(now())
  updatedAt DateTime @updatedAt
}
enum Role { ADMIN USER VIEWER }
```
### Validation rules
- email: valid email format, max 255 chars, lowercase enforced
- {field}: {rule}

### Business rules
- {rule: e.g. email cannot be changed after verification}

### Relationships
- {User has many Posts via userId}

---
{repeat per entity}
```

---

## Template 11: `runs/feature-{name}/01_research.md`

```markdown
---
feature: {name}
date: {date}
researcher: ai-agent
---

# Research: {feature name}

## Research questions
1. How do similar products implement {feature}?
2. What are the known pitfalls?
3. What packages/libraries exist for this?
4. What are the security considerations?

## Sources consulted
| Source | URL | Relevance |
|--------|-----|-----------|
| {source} | {url} | {what it informed} |

## Implementation patterns found
### Pattern A: {name}
- Used by: {products/repos}
- Approach: {description}
- Pros: {list}
- Cons: {list}
- Suitable for this project: yes/no — {reason}

### Pattern B: {name}
...

## Recommended approach
{which pattern, and why it fits this project's architecture.md}

## Packages evaluated
| Package | Stars | Maintained | License | Compatible | Notes |
|---------|-------|-----------|---------|-----------|-------|
| {pkg} | {n}k | yes/no | MIT/... | yes/no | {notes} |

## Recommended packages
{final list with justification}

## Security findings
{any security issues found in research relevant to this feature}

## Open questions after research
- [ ] {question}
```

---

## Template 12: `sdd.config.yml`
Per-project configuration file.

```yaml
# SDD Configuration
project:
  name: {project-name}
  repo: {org/repo-name}
  mode: greenfield | brownfield-with-specs | brownfield-no-specs

paths:
  specs: ./specs
  runs: ./runs
  src: ./src           # root of source code
  tests: ./tests       # or co-located

agent:
  model: claude-sonnet-4-6
  judge_threshold: 0.85
  max_judge_iterations: 5
  max_spec_read_files: 5    # token budget control

vector_db:
  provider: supabase | qdrant | pgvector
  index_name: {project-name}-specs
  embedding_model: text-embedding-3-small

github:
  default_branch: main
  branch_prefix:
    bug: fix/issue-{n}-{slug}
    feature: feature/{name}
  labels:
    trigger: ["bug", "fix-needed"]
    feature: ["feature", "enhancement"]
  require_spec_update: true   # block PR if specs not updated

testing:
  backend_runner: pytest | jest | vitest
  frontend_runner: vitest | jest | playwright
  api_test_tool: curl | postman
  coverage_threshold: 80

notifications:
  slack_channel: "#engineering"
  notify_on: [pr_opened, judge_failed, tests_failed]

security:
  secret_scan: true
  owasp_checklist: true
  dependency_audit: true    # npm audit / pip-audit on every run
```

---

# PART 2: THE BUILD PLAN

## Phase 0 — Foundation (Week 1)
**Goal:** Repo structure, config system, and one working end-to-end run for Mode B (bug fix) on a test project.

### Step 0.1 — Create the tool repo
```
sdd-runner/
  src/
    core/
      pipeline.py          # main orchestrator
      agents.py            # all agent calls to Claude API
      judge.py             # judge loop logic
    adapters/
      github.py            # GitHub MCP wrapper
      filesystem.py        # file read/write
      vector_db.py         # Supabase/Qdrant
      shell.py             # test runner
    modes/
      bug_fix.py           # Mode B pipeline
      feature.py           # Mode C pipeline
      scan.py              # Mode D: generate specs from code
      greenfield.py        # Mode A pipeline
    templates/             # all .md templates as Python string constants
    config.py              # sdd.config.yml loader + validator
  cli.py                   # entry point: sdd run / sdd init / sdd scan
  tests/
  sdd.config.yml           # config for sdd-runner itself
```

### Step 0.2 — Config loader
Build `config.py` first. Everything reads from `sdd.config.yml`. Validate with Pydantic. If the config is invalid, fail immediately with a clear error. This is the foundation — every other module depends on it.

### Step 0.3 — Agent runner (the most important module)
`agents.py` wraps the Claude API. Key design decisions:
- Each agent is a function that takes structured input, returns structured output
- Context is assembled per-call — no shared state between agents
- Every call logs tokens used (input + output) to a run log for cost tracking
- System prompts are loaded from `/prompts/{agent-name}.md` files so they can be tuned without code changes

```python
def classify_issue(issue: Issue, config: Config) -> Classification:
    # reads: issue text only
    # writes: classification (module, type, keywords, severity)
    # tokens: ~500 input, ~200 output

def draft_bug_report(issue, specs, files, learnings, config) -> BugReport:
    # reads: issue + 2-4 spec files + relevant source files + global_learnings.md
    # writes: bug_report.md draft
    # tokens: ~3000 input, ~1500 output

def judge_spec(spec, architecture, security, config) -> JudgeScore:
    # reads: spec draft + architecture.md + security.md
    # writes: score + gaps list
    # tokens: ~2000 input, ~500 output

def refine_spec(spec, gaps, config) -> BugReport:
    # reads: spec + judge gaps
    # writes: improved spec
    # tokens: ~2500 input, ~1500 output
```

### Step 0.4 — Judge loop
`judge.py` is just:
```python
def run_judge_loop(spec, context, config):
    for i in range(config.max_judge_iterations):
        score = judge_spec(spec, context, config)
        log_run(f"Judge iteration {i+1}: score={score.value}")
        if score.value >= config.judge_threshold:
            return spec, score
        spec = refine_spec(spec, score.gaps, config)
    raise JudgeThresholdNotMet(spec, score, config.max_judge_iterations)
```

### Step 0.5 — Test on a real dummy project
Create a test repo with a simple bug. Run the pipeline end to end manually (no GitHub webhook yet — just CLI). Verify the bug report is generated correctly and judge loop terminates.

---

## Phase 1 — Mode B complete (Week 2)
**Goal:** Full bug fix pipeline working CLI-to-PR on a real project.

### Step 1.1 — GitHub adapter
Implement using the GitHub MCP or PyGithub. Required operations:
- `get_issue(number)` → Issue
- `create_branch(name)` → Branch
- `get_file(path)` → str
- `list_files(directory)` → list[str]
- `commit_files(branch, files, message)` → Commit
- `create_pr(branch, title, body)` → PR
- `add_comment(issue_number, body)` → Comment

### Step 1.2 — Vector DB adapter
- Embed all spec files on first run (`sdd index`)
- Store embeddings with metadata: `{file_path, module, affects[], last_updated}`
- `search(keywords, top_k=5)` → list[SpecFile]
- `upsert(spec_file)` → updates embedding after spec changes
- Re-index only changed files (compare file hash)

### Step 1.3 — Shell adapter
- `run_tests(command)` → TestResult (passed, failed, output)
- Timeout after 5 minutes
- Capture stdout/stderr, parse pass/fail counts
- Return structured output the agent can read

### Step 1.4 — File writer
- Agent writes code changes → shell adapter commits them
- Must validate: no secrets in diff (basic regex scan), files exist before editing

### Step 1.5 — Mode B end to end
Wire all adapters into `bug_fix.py`:
1. `classify_issue`
2. `create_branch`
3. `vector_search` → get relevant spec files
4. `read_source_files` (from spec `files` front-matter)
5. `draft_bug_report`
6. `judge_loop`
7. `write_tests`
8. `write_code`
9. `run_tests`
10. If pass: `commit`, `create_pr`, `update_specs`, `re_embed_specs`
11. If fail: retry up to 2 times, then raise for human review

---

## Phase 2 — Mode C (Week 3)
**Goal:** Feature addition pipeline.

### Step 2.1 — Research agent
New agent: `research_feature(feature_request, config)`.
- Uses web search tool (Claude's built-in or Tavily)
- Writes `01_research.md`
- Returns: patterns found, recommended approach, packages

### Step 2.2 — Compatibility agent
`check_compatibility(feature_spec, architecture, existing_modules)`.
- Reads: feature spec + architecture.md + all module specs (index only, not full)
- Outputs: compatibility score, conflicts, recommendations

### Step 2.3 — Feature spec writer + judge
Same judge loop as Mode B, but against the feature spec template. Judge checks:
- All FRs are testable
- NFRs have measurable targets
- Security considerations addressed
- No architecture violations
- Data model changes are explicit

### Step 2.4 — Build plan agent
`write_build_plan(feature_spec)` → ordered task list with phases. The code writer uses this as its instruction set — one task at a time, test after each.

### Step 2.5 — Mode C end to end
Wire: `research` → `compatibility` → `feature_spec` → `judge_loop` → `test_plan` → `build_plan` → (human checkpoint) → `build_phase_by_phase` → `run_tests` → `pr` → `spec_sync`

---

## Phase 3 — Mode D: Brownfield scan (Week 4)
**Goal:** Generate spec files from an existing codebase.

### Step 3.1 — Codebase scanner
`sdd scan --src ./src` does:
1. Walk the directory tree
2. Group files by domain (heuristic: folder names, import clusters)
3. For each group: read files, generate module spec using template
4. Generate architecture.md from the overall structure
5. Generate data-models.md from DB schema files / ORM models

### Step 3.2 — Scan quality judge
After generating specs, a judge reads them and flags:
- Ambiguities (vague descriptions)
- Missing files references
- Module boundaries that don't make sense
- Security gaps (no auth section, etc.)

Human reviews and edits. Then `sdd index` embeds them.

### Step 3.3 — Mode D pipeline
`sdd scan` → human review → `sdd index` → Mode B or C runs normally.

---

## Phase 4 — Mode A: Greenfield (Week 5-6)
**Goal:** Conversational spec-building from scratch.

### Step 4.1 — Discovery chat loop
CLI interactive mode. Agent asks questions, human answers, agent builds `high_level_vision.md` progressively. Not automated — designed as a guided conversation.

### Step 4.2 — Tech stack researcher
Given the vision doc, agent researches and proposes tech stack. Human approves/modifies.

### Step 4.3 — Full spec generator
From approved vision + tech stack, generates the full `/specs` directory using all templates. Flags every ambiguity and asks for human input before proceeding.

### Step 4.4 — Design doc generator
Generates `design_notes.md` with component list, user flows, layout rules. Human links Figma file.

### Step 4.5 — Test-first build
Writes `test_plan.md` per feature first. Human approves. Then builds feature by feature, testing as it goes.

---

## Phase 5 — GitHub webhook + multi-project (Week 7)
**Goal:** Fully automated trigger from GitHub issue to PR with no manual step.

### Step 5.1 — Webhook server
Simple FastAPI server with one route: `POST /webhook/github`. Validates signature, parses issue, routes to correct mode (Mode B or C based on labels), kicks off pipeline in background task.

### Step 5.2 — Multi-project routing
Config map: `{repo_full_name} → sdd.config.yml path`. Single webhook handles all projects.

### Step 5.3 — Run dashboard (optional)
Simple web UI showing: active runs, judge scores, test results, cost per run. Read from the `/runs` directory.

---

## Phase 6 — Hardening (Week 8)
**Goal:** Production-ready reliability.

### Step 6.1 — Cost tracking
Every agent call logs: model, input tokens, output tokens, cost. Per-run summary in `runs/{id}/00_run_log.md`. Alert if run exceeds cost threshold.

### Step 6.2 — Failure handling
- Judge never converges → human notification, branch kept, report saved
- Tests never pass → same: notify, don't merge, keep branch
- GitHub API errors → retry with backoff
- All failures write a `FAILED.md` to the run directory explaining what happened

### Step 6.3 — Security hardening of the tool itself
- No secrets in config files
- GitHub token scoped to minimum permissions
- Shell adapter uses allowlist of permitted commands
- Webhook endpoint validates signature on every request

### Step 6.4 — `sdd init` template command
`sdd init --name my-project --mode brownfield-no-specs` scaffolds the full directory structure, writes `sdd.config.yml`, installs the GitHub Action, and gives the human a checklist of what to fill in.

---

## Human checkpoints (never automated away)

| Mode | Checkpoint | What human does |
|------|-----------|-----------------|
| A | After vision doc | Approve before architecture phase |
| A | After spec generation | Review all spec files, fix ambiguities |
| A | After design doc | Approve UI/UX before build |
| A | After test plan | Approve tests before coding begins |
| B | After judge fails 5x | Review spec, unblock manually |
| B | After tests fail 2x | Review code, unblock manually |
| C | After feature spec | Approve before build starts |
| C | After test plan | Approve before coding begins |
| D | After scan | Review generated specs before indexing |
| All | PR review | Human merges — AI never auto-merges |

---

## File artifact produced per mode

### Mode B (bug fix)
```
runs/bug-fix-{n}/
  01_classification.md
  02_bug_report.md       ← judge-approved
  03_test_plan.md
  04_security_check.md
  05_code_changes.md
  06_test_results.md
```

### Mode C (feature)
```
runs/feature-{name}/
  01_research.md
  02_compatibility.md
  03_feature_spec.md     ← judge-approved
  04_design_notes.md
  05_test_plan.md        ← human-approved
  06_build_plan.md
  07_test_results.md
```

### Mode D (scan)
```
specs/                   ← generated from scan, human-reviewed
  architecture.md
  coding-standards.md
  security.md
  data-models.md
  modules/*.md
```

### Mode A (greenfield)
```
specs/                   ← built conversationally, human-approved
  + all of Mode D files
runs/greenfield-init/
  01_vision.md
  02_requirements.md
  03_tech_stack_decision.md
  04_design_notes.md     ← with Figma link
  05_test_plan.md        ← per feature
  06_build_plan.md
```

---

## Cost estimate per run (approximate)

| Operation | Input tokens | Output tokens | Cost (Sonnet) |
|-----------|-------------|---------------|----------------|
| Classify issue | ~500 | ~200 | ~$0.001 |
| Spec search (vector) | ~200 | ~100 | ~$0.001 |
| Draft bug report | ~3,000 | ~1,500 | ~$0.02 |
| Judge (per iteration) | ~2,000 | ~500 | ~$0.008 |
| Write tests | ~2,500 | ~1,000 | ~$0.015 |
| Write code | ~3,000 | ~2,000 | ~$0.025 |
| Spec update | ~1,500 | ~500 | ~$0.008 |
| **Total Mode B (1 judge iter)** | | | **~$0.08** |
| **Total Mode B (3 judge iters)** | | | **~$0.10** |
| **Total Mode C** | | | **~$0.25-0.40** |
```
