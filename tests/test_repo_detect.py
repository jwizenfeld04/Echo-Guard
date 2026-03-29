"""Tests for echo_guard.repo_detect — repository visibility detection."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from echo_guard.repo_detect import (
    _parse_owner_repo,
    default_consent_for_visibility,
    detect_repo_visibility,
)


# ── _parse_owner_repo ────────────────────────────────────────────────────


class TestParseOwnerRepo:
    def test_github_https(self):
        result = _parse_owner_repo("https://github.com/jwizenfeld04/Echo-Guard.git")
        assert result == ("github", "jwizenfeld04", "Echo-Guard")

    def test_github_https_no_git_suffix(self):
        result = _parse_owner_repo("https://github.com/owner/repo")
        assert result == ("github", "owner", "repo")

    def test_github_ssh(self):
        result = _parse_owner_repo("git@github.com:owner/repo.git")
        assert result == ("github", "owner", "repo")

    def test_github_ssh_no_git_suffix(self):
        result = _parse_owner_repo("git@github.com:owner/repo")
        assert result == ("github", "owner", "repo")

    def test_github_ssh_alias(self):
        result = _parse_owner_repo("git@github-personal:jwizenfeld04/Echo-Guard.git")
        assert result == ("github", "jwizenfeld04", "Echo-Guard")

    def test_github_ssh_alias_no_suffix(self):
        result = _parse_owner_repo("git@github-work:owner/repo")
        assert result == ("github", "owner", "repo")

    def test_gitlab_ssh_alias(self):
        result = _parse_owner_repo("git@my-gitlab:owner/repo.git")
        assert result == ("gitlab", "owner", "repo")

    def test_gitlab_https(self):
        result = _parse_owner_repo("https://gitlab.com/owner/repo.git")
        assert result == ("gitlab", "owner", "repo")

    def test_gitlab_ssh(self):
        result = _parse_owner_repo("git@gitlab.com:owner/repo.git")
        assert result == ("gitlab", "owner", "repo")

    def test_unknown_host(self):
        result = _parse_owner_repo("https://bitbucket.org/owner/repo.git")
        assert result is None

    def test_invalid_url(self):
        result = _parse_owner_repo("not-a-url")
        assert result is None


# ── default_consent_for_visibility ───────────────────────────────────────


class TestDefaultConsentForVisibility:
    def test_public_repo(self):
        assert default_consent_for_visibility("public") == "public"

    def test_private_repo(self):
        assert default_consent_for_visibility("private") == "private"

    def test_unknown_repo(self):
        assert default_consent_for_visibility("unknown") == "private"


# ── detect_repo_visibility ───────────────────────────────────────────────


class TestDetectRepoVisibility:
    @patch("echo_guard.repo_detect._get_remote_url")
    def test_no_remote(self, mock_get_url):
        mock_get_url.return_value = None
        assert detect_repo_visibility(Path("/fake")) == "unknown"

    @patch("echo_guard.repo_detect._check_github_public")
    @patch("echo_guard.repo_detect._get_remote_url")
    def test_public_github_repo(self, mock_get_url, mock_check):
        mock_get_url.return_value = "https://github.com/owner/repo.git"
        mock_check.return_value = True
        assert detect_repo_visibility(Path("/fake")) == "public"

    @patch("echo_guard.repo_detect._check_github_public")
    @patch("echo_guard.repo_detect._get_remote_url")
    def test_private_github_repo(self, mock_get_url, mock_check):
        mock_get_url.return_value = "https://github.com/owner/repo.git"
        mock_check.return_value = False
        assert detect_repo_visibility(Path("/fake")) == "private"

    @patch("echo_guard.repo_detect._check_github_public")
    @patch("echo_guard.repo_detect._get_remote_url")
    def test_network_error_returns_unknown(self, mock_get_url, mock_check):
        mock_get_url.return_value = "https://github.com/owner/repo.git"
        mock_check.return_value = None
        assert detect_repo_visibility(Path("/fake")) == "unknown"

    @patch("echo_guard.repo_detect._get_remote_url")
    def test_unparseable_url(self, mock_get_url):
        mock_get_url.return_value = "https://bitbucket.org/owner/repo.git"
        assert detect_repo_visibility(Path("/fake")) == "unknown"

    @patch("echo_guard.repo_detect._check_gitlab_public")
    @patch("echo_guard.repo_detect._get_remote_url")
    def test_public_gitlab_repo(self, mock_get_url, mock_check):
        mock_get_url.return_value = "https://gitlab.com/owner/repo.git"
        mock_check.return_value = True
        assert detect_repo_visibility(Path("/fake")) == "public"
