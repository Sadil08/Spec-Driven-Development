---
module: commands
affects: []
files: ['speckit/commands/__init__.py', 'speckit/commands/build.py', 'speckit/commands/index.py', 'speckit/commands/init.py', 'speckit/commands/run.py', 'speckit/commands/scan.py']
---

# Commands Module

## Purpose
The `commands` module provides the main entry points for the `speckit` CLI. It exposes various subcommands that allow users to interact with the `speckit` project, such as initializing a new project, indexing spec files, running the agent pipeline, scanning codebases, and building specs.

## Public interfaces
*   `build_command`: A stub for a future command to guide users through building specs from scratch.
*   `index_command`: Parses and indexes spec files for efficient retrieval by the agent.
*   `init_command`: Interactively initializes a new `speckit` project, scaffolding directories and configuration.
*   `run_command`: Triggers the spec-driven agent pipeline for a given GitHub issue.
*   `scan_command`: Generates spec files from an existing codebase by scanning source files.

## Data flow
Data enters the module primarily through command-line arguments and options passed to the public functions. These functions then process this input, often interacting with other `speckit` modules (like `core.config`, `core.spec_parser`, `adapters.vector_db`) to perform their tasks. Results are typically outputted to the console using the `rich` library, or by creating/modifying files on the filesystem.

## Architecture principles
*   Each file in the `commands` module corresponds to a distinct `speckit` CLI subcommand.
*   Commands leverage the `typer` library for argument parsing and CLI definition.
*   User feedback and progress are provided using the `rich` library for enhanced console output.
*   Commands often load project configuration using `speckit.core.config.load_config`.

## Dependencies
- Internal:
    - `speckit.core.config`
    - `speckit.core.spec_parser`
    - `speckit.adapters.vector_db`
    - `speckit.templates.files`
- External:
    - `typer`
    - `rich`
    - `pathlib`
    - `os`
    - `dotenv`
    - `typing`

## Known gaps / TODOs
*   `build_command` is explicitly marked as "coming in Phase 4" and currently only prints a placeholder message.
*   The `index_command` definition is truncated, and its full implementation is not visible.
*   The `run_command` definition is truncated, and its full implementation is not visible.
*   The `scan_command` definition is truncated, and its full implementation is not visible.