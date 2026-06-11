"""
File-safety guards for code generation.

The code-writing agent returns FULL file contents that overwrite the original.
If the model only saw a truncated view of a file, or returns a suspiciously
short rewrite, writing it to disk silently destroys code. These helpers make
that failure mode detectable and refusable.
"""
from __future__ import annotations

from dataclasses import dataclass

# Per-file read budget passed to the coding agent. Large enough to hold most
# real source files in full (~6k tokens); files larger than this are flagged as
# truncated and may NOT be blindly full-file rewritten.
MAX_SOURCE_FILE_CHARS = 24_000

# A file is "non-trivial" (worth guarding) above this many lines.
_NONTRIVIAL_LINES = 15

# Reject a rewrite that drops below this fraction of the original line count.
_MIN_RETAIN_RATIO = 0.6


@dataclass
class ReadResult:
    content: str
    was_truncated: bool
    original_chars: int


def read_capped(text: str, cap: int = MAX_SOURCE_FILE_CHARS) -> ReadResult:
    """Cap file text to `cap` chars and report whether truncation occurred."""
    original_chars = len(text)
    if original_chars > cap:
        return ReadResult(content=text[:cap], was_truncated=True, original_chars=original_chars)
    return ReadResult(content=text, was_truncated=False, original_chars=original_chars)


def validate_rewrite(
    original: str,
    new_content: str,
    was_truncated: bool,
) -> tuple[bool, str]:
    """
    Decide whether `new_content` is a safe full-file replacement for `original`.

    Returns (ok, reason). When ok is False, reason explains why the rewrite is
    rejected so the pipeline can abort the change instead of destroying code.

    Rules:
      1. If the model only saw a truncated view of the file, a full-file rewrite
         is unsafe by definition — it cannot have preserved what it never saw.
      2. A rewrite that shrinks a non-trivial file by more than 40% is almost
         always a truncated/incomplete generation, not an intentional deletion.
      3. Near-empty output for a non-trivial file is rejected outright.
    """
    orig_lines = original.count("\n") + 1 if original else 0
    new_lines = new_content.count("\n") + 1 if new_content else 0

    if was_truncated and orig_lines >= _NONTRIVIAL_LINES:
        return (
            False,
            f"input file was truncated ({orig_lines}+ lines on disk); "
            "refusing a blind full-file rewrite that would drop unseen code",
        )

    if orig_lines >= _NONTRIVIAL_LINES and len(new_content.strip()) < 40:
        return (
            False,
            f"rewrite produced near-empty content for a {orig_lines}-line file",
        )

    if orig_lines >= _NONTRIVIAL_LINES and new_lines < orig_lines * _MIN_RETAIN_RATIO:
        return (
            False,
            f"rewrite shrank file from {orig_lines} to {new_lines} lines "
            f"(>{int((1 - _MIN_RETAIN_RATIO) * 100)}% loss) — likely truncated output",
        )

    return True, ""
