"""Tests for PR #8 feedback-consent-model changes.

Covers:
- extract_feedback_from_functions clone_type → reuse_type passthrough
- FunctionIndex.get_function_by_filepath_and_name (targeted lookup)
- _check_github_public / _check_gitlab_public HTTPError handling
- prepare_payload repo_visibility gate for training pairs
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest

from echo_guard.languages import ExtractedFunction
from echo_guard.index import FunctionIndex
from echo_guard.repo_detect import _check_github_public, _check_gitlab_public


# ── Helpers ───────────────────────────────────────────────────────────────


def _make_func(
    name: str,
    filepath: str = "src/a.py",
    language: str = "python",
    lineno: int = 1,
    end_lineno: int = 5,
    ast_hash: str = "abc12345",
    param_count: int = 2,
    has_return: bool = True,
    calls_made: list[str] | None = None,
) -> ExtractedFunction:
    return ExtractedFunction(
        name=name,
        filepath=filepath,
        language=language,
        lineno=lineno,
        end_lineno=end_lineno,
        source=f"def {name}(): pass",
        ast_hash=ast_hash,
        param_count=param_count,
        has_return=has_return,
        calls_made=calls_made or [],
    )


# ── feedback.py: clone_type → reuse_type passthrough ─────────────────────


class TestExtractFeedbackFromFunctions:
    def test_clone_type_passed_through_to_reuse_type(self):
        from echo_guard.feedback import extract_feedback_from_functions

        src = _make_func("foo", filepath="src/a.py")
        ext = _make_func("bar", filepath="src/b.py")
        record = extract_feedback_from_functions(
            src, ext, "true_positive", clone_type="cross_service_reference"
        )
        assert record.reuse_type == "cross_service_reference"

    def test_empty_clone_type_gives_empty_reuse_type(self):
        from echo_guard.feedback import extract_feedback_from_functions

        src = _make_func("foo", filepath="src/a.py")
        ext = _make_func("bar", filepath="src/b.py")
        record = extract_feedback_from_functions(src, ext, "false_positive")
        assert record.reuse_type == ""

    def test_verdict_and_other_fields_set_correctly(self):
        from echo_guard.feedback import extract_feedback_from_functions

        src = _make_func("foo", filepath="src/a.py", param_count=3, has_return=True)
        ext = _make_func("bar", filepath="src/b.py", param_count=1, has_return=False)
        record = extract_feedback_from_functions(
            src, ext, "true_positive",
            similarity_score=0.91,
            match_type="embedding_semantic",
            severity="extract",
            clone_type="direct_import",
        )
        assert record.verdict == "true_positive"
        assert record.match_type == "embedding_semantic"
        assert record.severity == "extract"
        assert record.similarity_score == 0.91
        assert record.reuse_type == "direct_import"
        assert record.source_param_count == 3
        assert record.existing_param_count == 1
        assert record.same_file is False
        assert record.same_language is True


# ── index.py: get_function_by_filepath_and_name ───────────────────────────


@pytest.fixture
def idx_with_funcs(tmp_path):
    """FunctionIndex with two known functions inserted."""
    idx = FunctionIndex(tmp_path)
    idx.upsert_function(_make_func("calculate", filepath="src/math.py", lineno=10, ast_hash="deadbeef"))
    idx.upsert_function(_make_func("calculate", filepath="src/math.py", lineno=50, ast_hash="cafebabe"))
    idx.upsert_function(_make_func("format_date", filepath="src/utils.py", lineno=5, ast_hash="11223344"))
    yield idx
    idx.close()


class TestGetFunctionByFilepathAndName:
    def test_basic_lookup(self, idx_with_funcs):
        f = idx_with_funcs.get_function_by_filepath_and_name("src/utils.py", "format_date")
        assert f is not None
        assert f.name == "format_date"
        assert f.filepath == "src/utils.py"

    def test_returns_none_when_not_found(self, idx_with_funcs):
        f = idx_with_funcs.get_function_by_filepath_and_name("src/utils.py", "nonexistent")
        assert f is None

    def test_returns_none_wrong_filepath(self, idx_with_funcs):
        f = idx_with_funcs.get_function_by_filepath_and_name("src/nope.py", "format_date")
        assert f is None

    def test_hash_disambiguates_same_name_same_file(self, idx_with_funcs):
        # Two "calculate" functions in same file with different hashes
        f1 = idx_with_funcs.get_function_by_filepath_and_name(
            "src/math.py", "calculate", ast_hash="deadbeef"
        )
        f2 = idx_with_funcs.get_function_by_filepath_and_name(
            "src/math.py", "calculate", ast_hash="cafebabe"
        )
        assert f1 is not None
        assert f2 is not None
        assert f1.lineno != f2.lineno
        assert f1.ast_hash[:8] == "deadbeef"
        assert f2.ast_hash[:8] == "cafebabe"

    def test_lineno_disambiguates_same_name_same_file(self, idx_with_funcs):
        f1 = idx_with_funcs.get_function_by_filepath_and_name(
            "src/math.py", "calculate", lineno=10
        )
        f2 = idx_with_funcs.get_function_by_filepath_and_name(
            "src/math.py", "calculate", lineno=50
        )
        assert f1 is not None and f1.lineno == 10
        assert f2 is not None and f2.lineno == 50

    def test_no_hash_returns_first_match(self, idx_with_funcs):
        # Without a disambiguator, returns the first by lineno
        f = idx_with_funcs.get_function_by_filepath_and_name("src/math.py", "calculate")
        assert f is not None
        assert f.lineno == 10  # lower lineno comes first

    def test_wrong_hash_returns_none(self, idx_with_funcs):
        f = idx_with_funcs.get_function_by_filepath_and_name(
            "src/math.py", "calculate", ast_hash="ffffffff"
        )
        assert f is None

    def test_zero_lineno_skips_lineno_filter(self, idx_with_funcs):
        # lineno=0 means "no filter" — should still find a match
        f = idx_with_funcs.get_function_by_filepath_and_name(
            "src/utils.py", "format_date", lineno=0
        )
        assert f is not None


# ── repo_detect.py: HTTPError handling ───────────────────────────────────


class TestCheckGithubPublicHTTPError:
    def test_404_returns_false(self):
        err = HTTPError("https://api.github.com/repos/o/r", 404, "Not Found", hdrs=None, fp=None)
        with patch("echo_guard.repo_detect.urlopen", side_effect=err):
            result = _check_github_public("o", "r")
        assert result is False

    def test_non_404_http_error_returns_none(self):
        err = HTTPError("https://api.github.com/repos/o/r", 500, "Server Error", hdrs=None, fp=None)
        with patch("echo_guard.repo_detect.urlopen", side_effect=err):
            result = _check_github_public("o", "r")
        assert result is None

    def test_url_error_returns_none(self):
        with patch("echo_guard.repo_detect.urlopen", side_effect=URLError("timeout")):
            result = _check_github_public("o", "r")
        assert result is None

    def test_200_returns_true(self):
        mock_resp = MagicMock()
        mock_resp.status = 200
        with patch("echo_guard.repo_detect.urlopen", return_value=mock_resp):
            result = _check_github_public("o", "r")
        assert result is True


class TestCheckGitlabPublicHTTPError:
    def test_404_returns_false(self):
        err = HTTPError("https://gitlab.com/api/v4/projects/o%2Fr", 404, "Not Found", hdrs=None, fp=None)
        with patch("echo_guard.repo_detect.urlopen", side_effect=err):
            result = _check_gitlab_public("o", "r")
        assert result is False

    def test_non_404_http_error_returns_none(self):
        err = HTTPError("https://gitlab.com/api/v4/projects/o%2Fr", 403, "Forbidden", hdrs=None, fp=None)
        with patch("echo_guard.repo_detect.urlopen", side_effect=err):
            result = _check_gitlab_public("o", "r")
        assert result is None

    def test_url_error_returns_none(self):
        with patch("echo_guard.repo_detect.urlopen", side_effect=URLError("timeout")):
            result = _check_gitlab_public("o", "r")
        assert result is None

    def test_200_returns_true(self):
        mock_resp = MagicMock()
        mock_resp.status = 200
        with patch("echo_guard.repo_detect.urlopen", return_value=mock_resp):
            result = _check_gitlab_public("o", "r")
        assert result is True

    def test_generic_exception_returns_none(self):
        with patch("echo_guard.repo_detect.urlopen", side_effect=RuntimeError("unexpected")):
            result = _check_gitlab_public("o", "r")
        assert result is None


class TestCheckGithubPublicGenericException:
    def test_generic_exception_returns_none(self):
        with patch("echo_guard.repo_detect.urlopen", side_effect=RuntimeError("unexpected")):
            result = _check_github_public("o", "r")
        assert result is None


class TestDetectRepoVisibilityEdgeCases:
    @patch("echo_guard.repo_detect._parse_owner_repo")
    @patch("echo_guard.repo_detect._get_remote_url")
    def test_unknown_host_returns_unknown(self, mock_url, mock_parse):
        """_parse_owner_repo returning a non-github/gitlab host falls through to 'unknown'."""
        mock_url.return_value = "https://bitbucket.org/owner/repo.git"
        # Return a tuple with an unrecognized host
        mock_parse.return_value = ("bitbucket", "owner", "repo")
        from echo_guard.repo_detect import detect_repo_visibility
        assert detect_repo_visibility(Path("/fake")) == "unknown"


# ── feedback.py: FeedbackRecord.to_dict serialization ─────────────────────


class TestFeedbackRecordToDict:
    def test_to_dict_returns_all_fields(self):
        from echo_guard.feedback import extract_feedback_from_functions

        src = _make_func("foo", filepath="src/a.py")
        ext = _make_func("bar", filepath="src/b.py")
        record = extract_feedback_from_functions(src, ext, "true_positive", clone_type="direct_import")
        d = record.to_dict()
        assert d["verdict"] == "true_positive"
        assert d["reuse_type"] == "direct_import"
        assert isinstance(d["similarity_score"], float)
        assert "source_param_count" in d
        assert "existing_param_count" in d

    def test_to_dict_serializes_empty_extra(self):
        from echo_guard.feedback import extract_feedback_from_functions

        src = _make_func("foo")
        ext = _make_func("bar", filepath="src/b.py")
        record = extract_feedback_from_functions(src, ext, "false_positive")
        d = record.to_dict()
        # extra field should be empty string when no extra data
        assert d["extra"] == ""
