"""Shared utilities used across Echo Guard modules."""

from __future__ import annotations

import subprocess
from pathlib import Path


def find_repo_root() -> Path:
    """Find the git repository root, or fall back to cwd."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
        return Path(result.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return Path.cwd()
