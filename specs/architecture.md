# speckit — Architecture

## Overview
Speckit is a Python-based Command Line Interface (CLI) tool designed to facilitate Spec-Driven Development. It provides commands to initialize, scan, build, run, and index project specifications, aiming to guide developers through the process of building software based on defined specifications.

## Tech stack
| Layer | Technology |
|-----------|-----------|
| Language | Python |
| Framework | Typer (for CLI), Rich (for console output) |
| Database | None observed |
| Authentication | GitHub Token (for GitHubAdapter) |
| Testing | None observed |
| Infrastructure | None observed |

## Module map
| Module | Responsibility |
|--------|---------------|
| adapters | Provides interfaces to external services, such as GitHub. |
| commands | Implements the core functionality exposed through the CLI. |
| core | Contains fundamental logic and data structures for speckit. |
| modes | Defines different operational modes or states for speckit. |
| prompts | Handles user interaction and prompt generation. |
| templates | Manages project or specification templates. |

## Architecture principles
- **Modularity**: The project is divided into distinct modules (adapters, commands, core, etc.), each with a specific responsibility, promoting separation of concerns.
- **CLI-first Design**: The primary interface is a command-line tool built using Typer, with commands clearly defined and exposed.
- **External Service Abstraction**: The `adapters` module abstracts interactions with external services like GitHub, isolating this logic.
- **Rich Console Output**: The `rich` library is used extensively for enhanced and user-friendly console output, including colored text and formatting.
- **Data-Centricity (Implicit)**: The presence of dataclasses (e.g., `Issue` in `github.py`) suggests a focus on representing data structures clearly.
- **Command Stubbing**: Commands like `build_command` are present but marked as "coming in Phase 4," indicating a phased development approach.
- **Environment Variable Configuration**: Configuration for external services (e.g., GitHub token and repo) is expected to be managed via environment variables.

## Cross-cutting concerns
- Logging: No explicit logging framework is observed. Console output is handled by the `rich.console.Console` object.
- Error handling: No explicit error handling strategies are detailed in the provided snippets. Standard Python exceptions are likely used.
- Configuration: Configuration for external services, specifically the GitHub adapter, is managed through environment variables (`GITHUB_TOKEN`, `GITHUB_REPO`).
- Testing: No test files or testing frameworks are visible in the provided snippets.

## Security model
- Authentication: For the `GitHubAdapter`, authentication is handled via a `GITHUB_TOKEN` environment variable.
- Authorisation: No explicit authorization mechanisms are observed beyond what the GitHub token might provide.
- Secrets management: Secrets like the `GITHUB_TOKEN` are expected to be managed via environment variables.
- Input validation: Basic type hinting is used for command arguments (e.g., `path: str`). Further input validation is not explicitly detailed.

## Data flow (top level)
User interaction begins with the `speckit` CLI. Typer parses the command-line arguments and dispatches to the appropriate command function (e.g., `init_command`, `scan_command`). These command functions, located in the `commands` module, orchestrate the core logic, potentially interacting with other modules like `core` for data processing or `adapters` for external service calls. User feedback and results are displayed to the console using the `rich` library.