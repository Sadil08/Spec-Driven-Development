---
module: adapters
affects: []
files: ['speckit/adapters/__init__.py', 'speckit/adapters/github.py', 'speckit/adapters/local_index.py', 'speckit/adapters/shell.py', 'speckit/adapters/supabase_index.py', 'speckit/adapters/vector_db.py']
---

# Adapters Module

## Purpose
This module provides interfaces to external services and local resources that Speckit interacts with. It abstracts away the complexities of specific APIs and storage mechanisms, allowing Speckit's core logic to remain independent of implementation details. Adapters include interfaces for GitHub, local file indexing, shell commands, and vector databases.

## Public interfaces
*   `GitHubAdapter`: A class to interact with the GitHub REST API v3 for issues, branches, file contents, and pull requests.
*   `LocalIndex`: A class to manage a local JSON-based BM25 spec index.
*   `ShellAdapter`: A class to execute shell commands safely within the project directory.
*   `SupabaseIndex`: A class to interact with a Supabase instance using pgvector for spec indexing.
*   `get_adapter`: A factory function that returns the appropriate index adapter (Supabase or LocalIndex) based on configuration and environment variables.

## Data flow
Data enters the adapters module from Speckit's core components (e.g., `SpecFile` objects) or from external services (e.g., GitHub API responses). For indexing adapters (`LocalIndex`, `SupabaseIndex`), data flows into the adapter for storage and retrieval. For the `GitHubAdapter`, data flows out as API requests and in as API responses. The `ShellAdapter` takes commands as input and returns their execution results. The `get_adapter` function acts as a gateway, directing data flow to the chosen underlying index adapter.

## Architecture principles
*   **Abstraction**: Each adapter provides a consistent interface for its functionality, hiding the underlying implementation details.
*   **Configuration-driven**: The choice of index adapter is determined by `SpeckitConfig` and environment variables.
*   **Error Handling**: Adapters are expected to handle potential errors from external services or local operations (e.g., missing environment variables, API errors, command execution failures).
*   **Dependency Management**: Adapters should minimize direct dependencies on other Speckit modules, relying on clear interfaces.

## Dependencies
- Internal:
    - `speckit.core.config`
    - `speckit.core.spec_parser` (type hinting)
- External:
    - `httpx` (for `GitHubAdapter`)
    - `json`, `math`, `re`, `collections`, `datetime`, `pathlib` (standard library)
    - `subprocess` (standard library)

## Known gaps / TODOs
*   The `GitHubAdapter` is not fully implemented; it raises an `EnvironmentError` in its `__init__` and lacks methods for actual API interaction.
*   The `LocalIndex`'s `_build_term_frequencies` method is incomplete, indicated by `_tokeni`.
*   The `ShellAdapter`'s `__init__` method is incomplete, indicated by `Ru`.
*   The `SupabaseIndex`'s `SUPABASE_SETUP_SQL` string is incomplete, indicated by `create o`.
*   The `vector_db.py` module's `get_adapter` function has an incomplete import for `SupabaseIndex` and an incomplete assignment for `adapter`.