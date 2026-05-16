---
project: speckit
last_updated: 2026-05-09
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
