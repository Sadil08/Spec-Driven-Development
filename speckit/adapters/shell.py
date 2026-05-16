"""Shell adapter — run test commands safely inside the project directory."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

# Only these executables are permitted. Extend in sdd.config.yml in a future version.
_ALLOWED = frozenset({
    "pytest", "python", "python3",
    "npm", "npx", "yarn", "pnpm",
    "go", "cargo",
    "make",
    "jest", "vitest", "mocha",
})

_DEFAULT_TIMEOUT = 300  # 5 minutes


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


class ShellAdapter:
    """Run test commands in a controlled subprocess."""

    def __init__(self, cwd: Path, timeout: int = _DEFAULT_TIMEOUT):
        self.cwd = cwd
        self.timeout = timeout

    def run_tests(self, command: str) -> TestResult:
        """
        Execute a test command. Raises ValueError if the executable is not
        in the allowlist — preventing arbitrary command execution.
        """
        executable = command.strip().split()[0]
        if executable not in _ALLOWED:
            raise ValueError(
                f"'{executable}' is not in the allowed command list. "
                f"Allowed: {sorted(_ALLOWED)}"
            )

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
            return TestResult(
                passed=False,
                command=command,
                return_code=-1,
                stdout="",
                stderr=f"Command timed out after {self.timeout}s",
            )

        return TestResult(
            passed=proc.returncode == 0,
            command=command,
            return_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )
