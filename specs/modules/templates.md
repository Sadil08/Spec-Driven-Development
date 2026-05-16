---
module: templates
affects: []
files: ['speckit/templates/__init__.py', 'speckit/templates/files.py']
---

# Templates Module

## Purpose
This module provides predefined template strings for generating project documentation and configuration files. These templates use placeholders that are dynamically filled during project initialization or scanning.

## Public interfaces
- `speckit.templates.files.ARCHITECTURE_MD`: A multi-line string template for generating an architecture overview document in Markdown format. It includes placeholders for project name, date, mode, description, primary language, and other technical details.
- `speckit.templates.files.today()`: A utility function that returns the current date in ISO format.

## Data flow
Templates are defined as string constants within the module. The `today()` function provides dynamic data (current date). Placeholders within the templates are intended to be replaced by external processes or functions during project setup.

## Architecture principles
- Templates should be self-contained strings.
- Placeholders should be clearly delimited using curly braces `{}`.
- Utility functions like `today()` should be simple and focused.

## Dependencies
- Internal: None
- External: `datetime` (standard library)

## Known gaps / TODOs
- The `ARCHITECTURE_MD` template is incomplete, with placeholders for database technology and other details missing.
- No templates are provided for other common project files (e.g., `README.md`, `LICENSE`, `setup.py`).
- The module currently lacks any mechanism to *apply* these templates; it only defines them.