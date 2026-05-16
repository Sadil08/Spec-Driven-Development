"""
Templates — spec file content for each mode.
All templates use {placeholders} that are filled during init/scan.
"""
from datetime import date


def today() -> str:
    return date.today().isoformat()


ARCHITECTURE_MD = """\
---
project: {project_name}
version: 1.0.0
last_updated: {date}
mode: {mode}
---

# Architecture overview

## Purpose
{description}

## Core principles
1. API-first: all features exposed via versioned endpoints
2. Stateless services: no server-side session state
3. Fail loudly in dev, gracefully in prod
4. No hardcoded credentials — all secrets via environment variables
5. Every external call must have a timeout and retry policy

> TODO: Review and customise these principles for your project.

## System design
> TODO: Describe the main layers/services and how they relate.

## Tech stack
| Layer      | Technology | Version | Reason chosen |
|------------|------------|---------|---------------|
| Backend    | {primary_language} | - | - |
| Database   | -          | -       | - |
| Auth       | -          | -       | - |
| Deployment | -          | -       | - |

> TODO: Fill in your tech stack.

## Runtime environment
- OS: Ubuntu 22.04
- Package manager: {package_manager}
- Node version: (if applicable)
- Python version: (if applicable)

> TODO: Fill in your actual runtime.

## Design patterns in use
> TODO: List the patterns your codebase follows.

## Forbidden patterns
- No direct DB queries in route handlers
- No hardcoded secrets or credentials
- No commented-out code in commits

> TODO: Add project-specific forbidden patterns.

## Module map
| Module | Responsibility | Spec file |
|--------|---------------|-----------|
| (none yet) | - | - |

> TODO: Add modules as you define them.

## Security baseline
- Auth model: TODO
- Authorization: TODO
- Input validation: TODO
- CORS: allowlist only, no wildcard in production
- Secrets: all via environment variables, never committed

## Error handling contract
- All errors return {{code, message, requestId}}
- 4xx = client error, 5xx = server error
- Validation errors return field-level detail
"""

CODING_STANDARDS_MD = """\
---
project: {project_name}
last_updated: {date}
---

# Coding standards

## File and folder structure
> TODO: Describe the actual folder layout with example paths.

## Naming conventions
| Thing     | Convention      | Example             |
|-----------|-----------------|---------------------|
| Files     | kebab-case      | user-service.py     |
| Classes   | PascalCase      | UserService         |
| Functions | snake_case      | get_user_by_id      |
| Constants | SCREAMING_SNAKE | MAX_RETRY_COUNT     |
| DB tables | snake_case      | user_sessions       |
| Env vars  | SCREAMING_SNAKE | DATABASE_URL        |

> TODO: Adjust for your primary language.

## Import rules
- Absolute imports only
- Third-party imports before local imports
- Group: stdlib → third-party → local

## Function rules
- Max function length: 40 lines
- Max parameters: 3 (use dataclass/dict if more needed)
- Pure functions preferred, side effects isolated
- All async functions must handle errors explicitly

## Comments
- No commented-out code in commits
- Docstrings on all public functions
- TODO comments must include issue number: # TODO #123

## Testing standards
- Test file naming: test_<feature>.py
- Coverage target: 80% minimum on services
- No test should depend on another test's state

## Environment variables
- All secrets in .env (never committed)
- .env.example committed with placeholder values
- Validated at startup
- Format: APP_CATEGORY_NAME e.g. APP_DB_HOST

## Git conventions
- Branch naming: fix/issue-{{n}}-{{slug}} | feature/{{name}}
- Commit format: feat: | fix: | chore: | docs:
- PR must: reference issue, include test results, update specs
"""

SECURITY_MD = """\
---
project: {project_name}
last_updated: {date}
threat_model_reviewed: {date}
---

# Security specification

## Authentication
- Mechanism: TODO (e.g. JWT / session / OAuth)
- Token TTL: access 15min, refresh 7 days
- Storage: TODO
- Rotation: refresh token rotated on every use

## Authorization
- Model: TODO (RBAC / ABAC / ownership-based)
- Roles: TODO
- Rule: authorization checked at service layer, not only route layer

## Input validation rules
- All API inputs validated before processing
- Schema library: TODO (Zod / Pydantic / Joi)
- SQL: parameterized queries only, no string concatenation
- Sanitize before rendering on frontend

## Data security
- PII fields: TODO (e.g. email, phone, address)
- PII in logs: never log PII fields
- Data retention: TODO

## API security
- HTTPS only in production
- CORS allowlist: TODO
- Rate limiting: TODO
- API versioning: TODO

## Secrets management
- Provider: environment variables
- Never in: code, git, logs, error messages, API responses
- Rotation policy: TODO

## Security testing requirements
Every bug fix and feature must pass:
- [ ] OWASP top 10 checklist applicable items
- [ ] No new credentials in code
- [ ] Auth/authz not weakened
- [ ] Input validation on all new fields
- [ ] New public endpoints have rate limiting

## Known risks and mitigations
| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| (none documented yet) | - | - | - |
"""

GLOBAL_LEARNINGS_MD = """\
---
project: {project_name}
last_updated: {date}
total_patterns: 0
---

# Global learnings

This file is append-only. Every time a fix or feature reveals a reusable
pattern or a recurring mistake, it is added here. Agents read this at the
start of every run to avoid repeating past mistakes.

---

## Pattern library

> Patterns will be added here automatically as the agent discovers them.
> Format: GL-{{n}} — {{title}}, discovered during {{issue/feature}}.

---

## Recurring mistakes to avoid

> Mistakes will be logged here when they recur across runs.

---

## Environment-specific notes

> Add any gotchas about this project's environment that trip up agents.
"""

DATA_MODELS_MD = """\
---
project: {project_name}
last_updated: {date}
---

# Data models

## Entity index
| Entity | Table/Collection | Module owner | Section |
|--------|-----------------|--------------|---------|
| (none yet) | - | - | - |

> TODO: Add entities as you define them.
> Each entity gets its own section below.

---

## Example entity template

```
Entity: User
Table: users
Fields:
  id: uuid, primary key
  email: string, unique, max 255, lowercase
  role: enum(admin, user, viewer), default user
  created_at: timestamp, auto
  updated_at: timestamp, auto

Validation rules:
  email: valid format, max 255 chars, lowercase enforced

Business rules:
  email cannot be changed after verification

Relationships:
  User has many Posts via user_id
```
"""

MODULE_MD = """\
---
module: {module_name}
affects: []
files: []
last_updated: {date}
---

# Module: {module_name}

## Purpose
> TODO: Describe what this module is responsible for.

## Public interface
| Name | Type | Description |
|------|------|-------------|
| (none documented) | - | - |

## Internal structure
> TODO: Describe key files and their roles.

## Data owned
> TODO: Which DB tables/collections this module owns.

## Dependencies
- Depends on: (none)
- Depended on by: (none)

## Key decisions
> TODO: Why it's built the way it is.

## Known edge cases
> TODO: Things that behave unexpectedly or need special handling.

## Error states
| Error | When | How handled |
|-------|------|-------------|
| (none documented) | - | - |

## Security notes
> TODO: Anything security-relevant specific to this module.
"""

GITHUB_ACTION_YML = """\
name: speckit

on:
  issues:
    types: [opened, labeled]

jobs:
  speckit:
    runs-on: ubuntu-latest
    if: |
      contains(github.event.issue.labels.*.name, 'bug') ||
      contains(github.event.issue.labels.*.name, 'fix-needed') ||
      contains(github.event.issue.labels.*.name, 'feature') ||
      contains(github.event.issue.labels.*.name, 'enhancement')

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install speckit
        run: pip install speckit  # TODO: replace with your install method

      - name: Run speckit
        env:
          ANTHROPIC_API_KEY: ${{{{ secrets.ANTHROPIC_API_KEY }}}}
          GITHUB_TOKEN: ${{{{ secrets.GITHUB_TOKEN }}}}
          SPECKIT_VECTOR_DB_URL: ${{{{ secrets.SPECKIT_VECTOR_DB_URL }}}}
        run: |
          speckit run --issue ${{{{ github.event.issue.number }}}}
"""

SPECKIT_GITIGNORE = """\
# speckit
.speckit/.env
runs/*/00_run_log.md
"""

DOT_ENV_EXAMPLE = """\
# speckit environment variables
# Copy to .env and fill in real values. Never commit .env.

ANTHROPIC_API_KEY=sk-ant-...

# GitHub (only needed for local runs — Actions uses GITHUB_TOKEN automatically)
GITHUB_TOKEN=ghp_...
GITHUB_REPO=org/repo-name

# Vector DB (optional — leave blank to skip embedding)
SPECKIT_VECTOR_DB_URL=
SPECKIT_VECTOR_DB_KEY=

# Notifications (optional)
SLACK_WEBHOOK_URL=
"""
