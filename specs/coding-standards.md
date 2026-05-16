---
project: speckit
last_updated: 2026-05-09
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
- Branch naming: fix/issue-{n}-{slug} | feature/{name}
- Commit format: feat: | fix: | chore: | docs:
- PR must: reference issue, include test results, update specs
