---
project: speckit
last_updated: 2026-05-09
threat_model_reviewed: 2026-05-09
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
