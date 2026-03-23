"""Shared utilities used across Echo Guard modules."""

from __future__ import annotations

import subprocess
from pathlib import Path


def find_repo_root() -> Path:
    """Find the repository root by git or marker file detection.

    Tries `git rev-parse --show-toplevel` first. If git isn't available
    or we're not in a repo, walks upward from cwd looking for Echo Guard
    markers (.echoguard.yml, .echo-guard/, .git/).
    Falls back to cwd if nothing is found.
    """
    # Try git first
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
        return Path(result.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    # Walk upward looking for project markers
    markers = {".git", ".echoguard.yml", ".echo-guard"}
    current = Path.cwd().resolve()
    for parent in [current, *current.parents]:
        if any((parent / m).exists() for m in markers):
            return parent

    return Path.cwd()
