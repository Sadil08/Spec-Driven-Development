---
module: config
affects: [all modules]
files: [speckit/core/config.py, sdd.config.yml]
last_updated: 2026-05-09
---

# Module: config

## Purpose
Single source of truth for project configuration. All modules receive a
SpeckitConfig object — nothing reads sdd.config.yml directly except this module.

## Public interface
| Name | Type | Description |
|------|------|-------------|
| SpeckitConfig | Pydantic model | Full project config |
| AgentConfig | Pydantic model | model, judge_threshold, max_judge_iterations, max_spec_read_files |
| VectorDBConfig | Pydantic model | provider, index_name, connection_url |
| GitHubConfig | Pydantic model | default_branch, require_spec_update, bug_labels, feature_labels |
| PathsConfig | Pydantic model | specs, runs, src, tests paths |
| ProjectMode | Enum | greenfield, brownfield-with-specs, brownfield-no-specs |
| VectorDBProvider | Enum | supabase, qdrant, none |
| load_config(project_root) | function | Load + validate sdd.config.yml, raise FileNotFoundError if missing |
| save_config(config, project_root) | function | Write SpeckitConfig to sdd.config.yml |
| config_exists(project_root) | function | Check if sdd.config.yml exists |

## Key defaults
| Setting | Default | Notes |
|---------|---------|-------|
| agent.model | claude-sonnet-4-6 | Override per-project in sdd.config.yml |
| agent.judge_threshold | 0.85 | Score required for spec approval |
| agent.max_judge_iterations | 5 | Max refinement loops |
| agent.max_spec_read_files | 5 | Max spec files loaded per run |
| vector_db.provider | none | Set to supabase to enable cloud index |

## Dependencies
- Depends on: pydantic, pyyaml
- Depended on by: every other module

## Key decisions
- Enums (ProjectMode, VectorDBProvider) serialised as string values in YAML for readability
- Field validators ensure repo format is org/repo-name
- All sub-configs have defaults so speckit init only needs to fill project-level fields
