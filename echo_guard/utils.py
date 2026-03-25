"""Shared utilities used across Echo Guard modules."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


def split_name_tokens(name: str) -> list[str]:
    """Split a function name into lowercase tokens.

    Handles snake_case, camelCase, PascalCase, and mixed conventions
    uniformly. Used by the similarity engine and classifier.

    Examples:
        "reset_session"         → ["reset", "session"]
        "deleteSession"         → ["delete", "session"]
        "XMLParser"             → ["xml", "parser"]
        "_coerce_json"          → ["coerce", "json"]
    """
    stripped = name.lstrip("_")
    if not stripped:
        return []
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", stripped)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)
    return [t.lower() for t in s.split("_") if t]


def find_repo_root() -> Path:
    """Find the repository root by git or marker file detection.

    Tries `git rev-parse --show-toplevel` first. If git isn't available
    or we're not in a repo, walks upward from cwd looking for Echo Guard
    markers (echo-guard.yml, .echo-guard/, .git/).
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
    markers = {".git", "echo-guard.yml", ".echo-guard"}
    current = Path.cwd().resolve()
    for parent in [current, *current.parents]:
        if any((parent / m).exists() for m in markers):
            return parent

    return Path.cwd()
