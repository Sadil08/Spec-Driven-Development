---
module: modes
affects: []
files: ['speckit/modes/__init__.py', 'speckit/modes/bug_fix.py']
---

# Modes Module

## Purpose
The `modes` module defines different pipelines or workflows for the `speckit` tool. These modes represent distinct operational sequences, such as fixing bugs or potentially other future functionalities. Each mode orchestrates a series of steps to achieve a specific outcome.

## Public interfaces
* `speckit.modes.bug_fix.RunResult`: A dataclass to store the results of a bug fix pipeline run, including the issue number and the directory where the run artifacts are stored.

## Data flow
* Data enters the `bug_fix` mode primarily through an `Issue` object (or its identifier) from GitHub.
* Intermediate data, such as classified information, search results, and draft reports, are generated and stored within the `run_dir`.
* The final output of the `bug_fix` mode includes an approved bug report and a test plan, saved to files within the `run_dir`.

## Architecture principles
* The `bug_fix` mode is designed as a sequential pipeline with distinct stages.
* The pipeline includes a "judge loop" for iterative refinement and approval of the bug report.
* Future stages for code fixing and testing are indicated but not yet implemented.

## Dependencies
- Internal:
    - `speckit.adapters.github` (for `GitHubAdapter` and `Issue`)
    - `speckit.core.config` (for `SpeckitConfig`)
- External:
    - `dataclasses`
    - `datetime`
    - `pathlib`
    - `typing`

## Known gaps / TODOs
* The `RunResult` dataclass is incomplete, missing the definition for `approved`.
* Stages 9-11 in the `bug_fix` mode (Write code fix, Run tests, Create PR) are marked as "[future]" and are not implemented.