module: core
affects: []
files: ['speckit/core/__init__.py', 'speckit/core/agents.py', 'speckit/core/config.py', 'speckit/core/judge.py', 'speckit/core/spec_parser.py']
---

# Core Module

## Purpose
The core module provides the fundamental logic and data structures for the speckit tool. It handles configuration loading, agent interactions for spec generation and refinement, and parsing of spec files. This module is central to speckit's spec-driven development workflow.

## Public interfaces
- `speckit.core.config.SpeckitConfig`: Pydantic model for loading and validating project configuration from `sdd.config.yml`.
- `speckit.core.agents.Classification`: Pydantic model representing the classification of an issue.
- `speckit.core.agents.JudgeScore`: Pydantic model representing the score and approval status from a judge agent.
- `speckit.core.judge.run_judge_loop`: Function to iteratively refine a spec based on agent feedback until a threshold is met or max iterations are reached.
- `speckit.core.spec_parser.SpecFile`: Pydantic model representing a parsed spec file, including frontmatter and content.
- `speckit.core.spec_parser._parse_frontmatter`: Internal utility to separate YAML frontmatter from markdown content.

## Data flow
Data enters the core module primarily through configuration files (`sdd.config.yml`) loaded by `SpeckitConfig`. Agent functions process text inputs (like code or existing specs) and return structured data (e.g., `Classification`, `JudgeScore`) or refined text. `run_judge_loop` orchestrates this agent interaction. `SpecFile` objects represent parsed spec files, enabling structured access to spec content.

## Architecture principles
- **Configuration Driven**: Behavior is heavily influenced by `sdd.config.yml` settings.
- **Agent Abstraction**: Agents for LLM interactions are designed to be pluggable or selectable via configuration.
- **Spec File Structure**: Adheres to a markdown format with YAML frontmatter for spec metadata.
- **Iterative Refinement**: The `run_judge_loop` embodies the iterative nature of spec development.

## Dependencies
- Internal: None (this is the core module).
- External: `anthropic`, `pydantic`, `typer`, `rich`, `PyYAML`, `pathlib`, `dataclasses`, `enum`, `os`, `re`, `typing`.

## Known gaps / TODOs
- The `_AnthropicBackend` class in `agents.py` is incomplete.
- The `VectorDBConfig` class in `config.py` is incomplete.
- The `speckit/core/__init__.py` is empty.