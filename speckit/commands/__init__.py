from __future__ import annotations

from pathlib import Path


def load_env(start: Path) -> None:
    """
    Load the nearest .env file, walking up from `start` toward the filesystem
    root. This lets a monorepo-root .env be shared by all service sub-directories
    without copying it into every service folder.

    Loading order (first file found wins, later files do NOT override):
      1. start/.env          (service-level override, most specific)
      2. start/../.env       (monorepo root)
      3. start/../../.env    … up to filesystem root

    `override=False` means already-set env vars are never clobbered, so a
    shell export always wins over any .env file.
    """
    from dotenv import load_dotenv

    path = start.resolve()
    while True:
        candidate = path / ".env"
        if candidate.is_file():
            load_dotenv(candidate, override=False)
        if path.parent == path:
            break
        path = path.parent
