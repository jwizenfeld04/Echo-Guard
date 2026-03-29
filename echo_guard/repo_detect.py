"""Repository visibility detection for feedback consent defaults.

Detects whether a GitHub/GitLab repository is public or private by
parsing the git remote URL and making an unauthenticated API call.
Uses only stdlib (subprocess + urllib) — no new dependencies.
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 3

# Patterns for extracting owner/repo from git remote URLs.
# SSH patterns use [^:]+ for the host to match SSH config aliases
# (e.g. git@github-personal:owner/repo where github-personal is an
# alias for github.com in ~/.ssh/config).
_GITHUB_SSH = re.compile(r"git@[^:]*github[^:]*:(.+?)/(.+?)(?:\.git)?$")
_GITHUB_HTTPS = re.compile(r"https?://github\.com/(.+?)/(.+?)(?:\.git)?$")
_GITLAB_SSH = re.compile(r"git@[^:]*gitlab[^:]*:(.+?)/(.+?)(?:\.git)?$")
_GITLAB_HTTPS = re.compile(r"https?://gitlab\.com/(.+?)/(.+?)(?:\.git)?$")


def _get_remote_url(repo_root: Path) -> str | None:
    """Get the origin remote URL from git."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            cwd=str(repo_root),
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _parse_owner_repo(url: str) -> tuple[str, str, str] | None:
    """Parse owner/repo and host from a git remote URL.

    Returns (host, owner, repo) or None if unparseable.
    """
    for pattern, host in [
        (_GITHUB_SSH, "github"),
        (_GITHUB_HTTPS, "github"),
        (_GITLAB_SSH, "gitlab"),
        (_GITLAB_HTTPS, "gitlab"),
    ]:
        match = pattern.match(url)
        if match:
            return host, match.group(1), match.group(2)
    return None


def _check_github_public(owner: str, repo: str) -> bool | None:
    """Check if a GitHub repo is public via unauthenticated HEAD request.

    Returns True if public, False if private/not-found, None on error.
    """
    url = f"https://api.github.com/repos/{owner}/{repo}"
    req = Request(url, method="HEAD")
    req.add_header("User-Agent", "echo-guard")
    try:
        response = urlopen(req, timeout=_TIMEOUT_SECONDS)  # noqa: S310
        return response.status == 200
    except URLError:
        return None
    except Exception:
        return None


def _check_gitlab_public(owner: str, repo: str) -> bool | None:
    """Check if a GitLab repo is public via unauthenticated HEAD request.

    Returns True if public, False if private/not-found, None on error.
    """
    # GitLab API uses URL-encoded path (owner/repo → owner%2Frepo)
    from urllib.parse import quote

    project_path = quote(f"{owner}/{repo}", safe="")
    url = f"https://gitlab.com/api/v4/projects/{project_path}"
    req = Request(url, method="HEAD")
    req.add_header("User-Agent", "echo-guard")
    try:
        response = urlopen(req, timeout=_TIMEOUT_SECONDS)  # noqa: S310
        return response.status == 200
    except URLError:
        return None
    except Exception:
        return None


def detect_repo_visibility(repo_root: Path) -> str:
    """Return 'public', 'private', or 'unknown'.

    Detection strategy:
    1. Parse owner/repo from git remote URL (GitHub/GitLab, HTTPS or SSH)
    2. Unauthenticated HEAD to API — 200 = public, 404 = private
    3. 3-second timeout, fallback to 'unknown'
    """
    url = _get_remote_url(repo_root)
    if not url:
        return "unknown"

    parsed = _parse_owner_repo(url)
    if not parsed:
        return "unknown"

    host, owner, repo = parsed

    if host == "github":
        result = _check_github_public(owner, repo)
    elif host == "gitlab":
        result = _check_gitlab_public(owner, repo)
    else:
        return "unknown"

    if result is True:
        return "public"
    elif result is False:
        return "private"
    else:
        return "unknown"


def default_consent_for_visibility(visibility: str) -> str:
    """Return the smart-default consent tier for a given repo visibility.

    'public' repos default to 'public' consent (code pairs + verdicts).
    'private' and 'unknown' repos default to 'private' consent (features only).
    """
    if visibility == "public":
        return "public"
    return "private"
