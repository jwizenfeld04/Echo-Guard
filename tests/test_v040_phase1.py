"""Tests for v0.4.0 Phase 1 changes:
- AST-hash-based finding IDs
- Structured acknowledged format (intentional/dismissed) with re-surfacing
- Verdict rename (resolved/intentional/dismissed)
- Daemon JSON-RPC protocol
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from echo_guard.index import FunctionIndex
from echo_guard.config import EchoGuardConfig


# ── Finding ID tests ────────────────────────────────────────────────────


def test_finding_id_uses_ast_hash_not_lineno():
    """Finding IDs should be stable across line number changes."""
    fid1 = FunctionIndex.make_finding_id(
        "src/utils.py", "format_date",
        "src/api.py", "format_date",
        source_hash="abcd1234",
        existing_hash="ef567890",
    )
    # Same call but different (hypothetical) linenos would have produced different
    # IDs in the old scheme. Now IDs are hash-based so they're stable.
    fid2 = FunctionIndex.make_finding_id(
        "src/utils.py", "format_date",
        "src/api.py", "format_date",
        source_hash="abcd1234",
        existing_hash="ef567890",
    )
    assert fid1 == fid2


def test_finding_id_is_order_independent():
    """Same pair in different order should produce identical ID."""
    fid_ab = FunctionIndex.make_finding_id(
        "src/a.py", "foo",
        "src/b.py", "foo",
        source_hash="aaaa1111",
        existing_hash="bbbb2222",
    )
    fid_ba = FunctionIndex.make_finding_id(
        "src/b.py", "foo",
        "src/a.py", "foo",
        source_hash="bbbb2222",
        existing_hash="aaaa1111",
    )
    assert fid_ab == fid_ba


def test_finding_id_changes_when_hash_changes():
    """When a function's AST changes, its finding ID should change."""
    fid_before = FunctionIndex.make_finding_id(
        "src/utils.py", "format_date",
        "src/api.py", "format_date",
        source_hash="abcd1234",
        existing_hash="ef567890",
    )
    fid_after = FunctionIndex.make_finding_id(
        "src/utils.py", "format_date",
        "src/api.py", "format_date",
        source_hash="NEW11111",  # function structure changed
        existing_hash="ef567890",
    )
    assert fid_before != fid_after


def test_finding_id_empty_hashes():
    """Empty hashes should not crash — just produce a valid ID."""
    fid = FunctionIndex.make_finding_id(
        "src/a.py", "foo",
        "src/b.py", "bar",
        source_hash="",
        existing_hash="",
    )
    assert "||" in fid
    assert "src/a.py:foo:" in fid or "src/b.py:bar:" in fid


# ── Config: structured acknowledged format + re-surfacing ───────────────


def test_config_suppressed_ids():
    """get_suppressed_ids returns set of finding IDs from structured entries."""
    config = EchoGuardConfig()
    config.acknowledged = [
        {"id": "abc||def", "verdict": "intentional", "source_hash": "abc", "existing_hash": "def"},
        {"id": "ghi||jkl", "verdict": "dismissed"},
    ]
    ids = config.get_suppressed_ids()
    assert "abc||def" in ids
    assert "ghi||jkl" in ids


def test_is_suppressed_dismissed_always():
    """dismissed verdict is permanently suppressed regardless of hashes."""
    config = EchoGuardConfig()
    fid = "src/a.py:foo:aaaa1111||src/b.py:bar:bbbb2222"
    config.acknowledged = [{"id": fid, "verdict": "dismissed"}]
    # Even if hashes are completely different, dismissed is permanent
    assert config.is_suppressed(fid, "DIFFERENT1", "DIFFERENT2")


def test_is_suppressed_intentional_matching_hashes():
    """intentional is suppressed when AST hashes still match."""
    config = EchoGuardConfig()
    fid = "src/a.py:foo:aaaa1111||src/b.py:bar:bbbb2222"
    config.acknowledged = [
        {"id": fid, "verdict": "intentional", "source_hash": "aaaa1111", "existing_hash": "bbbb2222"}
    ]
    assert config.is_suppressed(fid, "aaaa1111", "bbbb2222")


def test_is_suppressed_intentional_resurfaces_on_hash_change():
    """intentional resurfaces when either function's AST hash changes."""
    config = EchoGuardConfig()
    fid = "src/a.py:foo:aaaa1111||src/b.py:bar:bbbb2222"
    config.acknowledged = [
        {"id": fid, "verdict": "intentional", "source_hash": "aaaa1111", "existing_hash": "bbbb2222"}
    ]
    # Source function was modified (new hash)
    assert not config.is_suppressed(fid, "NEWWWWWW", "bbbb2222")


def test_is_suppressed_intentional_both_hashes_changed():
    """intentional resurfaces when both hashes change."""
    config = EchoGuardConfig()
    fid = "src/a.py:foo:aaaa1111||src/b.py:bar:bbbb2222"
    config.acknowledged = [
        {"id": fid, "verdict": "intentional", "source_hash": "aaaa1111", "existing_hash": "bbbb2222"}
    ]
    assert not config.is_suppressed(fid, "NEW11111", "NEW22222")


def test_is_suppressed_unknown_finding_id():
    """Unknown finding ID is not suppressed."""
    config = EchoGuardConfig()
    config.acknowledged = [
        {"id": "some||other", "verdict": "dismissed"}
    ]
    assert not config.is_suppressed("completely||different", "hash1", "hash2")


def test_add_suppressed_intentional():
    """add_suppressed writes structured entry with hashes for intentional."""
    config = EchoGuardConfig()
    config._config_path = None  # prevent file write in unit test

    # Patch _save_acknowledged to avoid file I/O
    with patch.object(config, "_save_acknowledged"):
        config.add_suppressed("abc||def", "intentional", "abc12345", "def67890")

    assert len(config.acknowledged) == 1
    entry = config.acknowledged[0]
    assert entry["id"] == "abc||def"
    assert entry["verdict"] == "intentional"
    assert entry["source_hash"] == "abc12345"[:8]
    assert entry["existing_hash"] == "def67890"[:8]


def test_add_suppressed_dismissed_no_hashes():
    """add_suppressed for dismissed does not store hashes."""
    config = EchoGuardConfig()
    with patch.object(config, "_save_acknowledged"):
        config.add_suppressed("abc||def", "dismissed", "abc12345", "def67890")

    entry = config.acknowledged[0]
    assert entry["verdict"] == "dismissed"
    assert "source_hash" not in entry
    assert "existing_hash" not in entry


def test_add_suppressed_replaces_existing():
    """add_suppressed replaces an existing entry for the same finding_id."""
    config = EchoGuardConfig()
    config.acknowledged = [
        {"id": "abc||def", "verdict": "intentional", "source_hash": "old", "existing_hash": "old"}
    ]
    with patch.object(config, "_save_acknowledged"):
        config.add_suppressed("abc||def", "dismissed")

    assert len(config.acknowledged) == 1
    assert config.acknowledged[0]["verdict"] == "dismissed"


def test_config_load_drops_old_string_format(tmp_path):
    """Old plain-string acknowledged entries are silently dropped on load."""
    config_file = tmp_path / "echo-guard.yml"
    config_file.write_text(
        "threshold: 0.5\n"
        "acknowledged:\n"
        "  - 'old_format_finding_id_without_dict'\n"
    )
    config = EchoGuardConfig.load(tmp_path)
    # Old format dropped, acknowledged is empty
    assert config.acknowledged == []


def test_config_load_structured_format(tmp_path):
    """New structured acknowledged format is loaded correctly."""
    config_file = tmp_path / "echo-guard.yml"
    config_file.write_text(
        "threshold: 0.5\n"
        "acknowledged:\n"
        "  - id: 'abc||def'\n"
        "    verdict: intentional\n"
        "    source_hash: 'abc12345'\n"
        "    existing_hash: 'def67890'\n"
    )
    config = EchoGuardConfig.load(tmp_path)
    assert len(config.acknowledged) == 1
    assert config.acknowledged[0]["id"] == "abc||def"
    assert config.acknowledged[0]["verdict"] == "intentional"


def test_config_feedback_consent_default():
    """feedback_consent defaults to 'private'."""
    config = EchoGuardConfig()
    assert config.feedback_consent == "private"


def test_config_feedback_consent_load(tmp_path):
    """feedback_consent is loaded from config file."""
    config_file = tmp_path / "echo-guard.yml"
    config_file.write_text("feedback_consent: none\n")
    config = EchoGuardConfig.load(tmp_path)
    assert config.feedback_consent == "none"


# ── Daemon JSON-RPC protocol tests ─────────────────────────────────────


def test_daemon_ok_response_format():
    """_ok produces correct JSON-RPC 2.0 success response."""
    from echo_guard.daemon import _ok
    resp = _ok(1, {"ready": True})
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 1
    assert resp["result"] == {"ready": True}
    assert "error" not in resp


def test_daemon_err_response_format():
    """_err produces correct JSON-RPC 2.0 error response."""
    from echo_guard.daemon import _err
    resp = _err(2, -32600, "Invalid Request")
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 2
    assert resp["error"]["code"] == -32600
    assert resp["error"]["message"] == "Invalid Request"
    assert "result" not in resp


def test_daemon_unknown_method(tmp_path):
    """Daemon returns error for unknown RPC method."""
    from echo_guard.daemon import EchoGuardDaemon
    daemon = EchoGuardDaemon(tmp_path)
    with pytest.raises(ValueError, match="Unknown method"):
        daemon._dispatch("nonexistent_method", {})


def test_daemon_resolve_finding_invalid_verdict(tmp_path):
    """Daemon raises ValueError for invalid verdict."""
    from echo_guard.daemon import EchoGuardDaemon
    daemon = EchoGuardDaemon(tmp_path)
    daemon._config = EchoGuardConfig()
    with pytest.raises(ValueError, match="Invalid verdict"):
        daemon._handle_resolve_finding({"finding_id": "abc||def", "verdict": "bogus"})


def test_daemon_resolve_finding_missing_id(tmp_path):
    """Daemon raises ValueError when finding_id is missing."""
    from echo_guard.daemon import EchoGuardDaemon
    daemon = EchoGuardDaemon(tmp_path)
    daemon._config = EchoGuardConfig()
    with pytest.raises(ValueError, match="finding_id"):
        daemon._handle_resolve_finding({"verdict": "intentional"})


def test_daemon_get_findings_empty(tmp_path):
    """get_findings returns empty list when no findings cached."""
    from echo_guard.daemon import EchoGuardDaemon
    daemon = EchoGuardDaemon(tmp_path)
    result = daemon._handle_get_findings({})
    assert result["findings"] == []
    assert result["total"] == 0


def test_daemon_get_findings_by_file(tmp_path):
    """get_findings filters by filepath when 'file' param provided."""
    from echo_guard.daemon import EchoGuardDaemon
    daemon = EchoGuardDaemon(tmp_path)
    daemon._findings = {
        "src/a.py": [{"finding_id": "a1", "severity": "extract"}],
        "src/b.py": [{"finding_id": "b1", "severity": "review"}],
    }
    result = daemon._handle_get_findings({"file": "src/a.py"})
    assert result["total"] == 1
    assert result["findings"][0]["finding_id"] == "a1"


def test_daemon_lockfile_written_and_removed(tmp_path):
    """Daemon writes lockfile on start and removes it on exit."""
    import io
    from echo_guard.daemon import EchoGuardDaemon

    daemon = EchoGuardDaemon(tmp_path)
    # Simulate stdin with only a shutdown request
    shutdown_req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "shutdown", "params": {}}) + "\n"

    with (
        patch("sys.stdin", io.StringIO(shutdown_req)),
        patch("sys.stdout") as mock_stdout,
    ):
        daemon.start()

    lock_path = tmp_path / ".echo-guard" / "daemon.lock"
    assert not lock_path.exists(), "Lockfile should be removed after shutdown"


# ── Verdict rename sanity checks ────────────────────────────────────────


def test_resolve_finding_accepts_new_verdicts(tmp_path):
    """FunctionIndex.resolve_finding accepts resolved/intentional/dismissed."""
    idx = FunctionIndex(tmp_path)
    try:
        for verdict in ("resolved", "intentional", "dismissed"):
            idx.resolve_finding(
                finding_id=f"a||b_{verdict}",
                verdict=verdict,
                source_filepath="src/a.py",
                source_function="foo",
                source_lineno=None,
                existing_filepath="src/b.py",
                existing_function="bar",
                existing_lineno=None,
            )
        stats = idx.get_resolution_stats()
        assert stats["total"] == 3
        assert set(stats["by_verdict"].keys()) == {"resolved", "intentional", "dismissed"}
    finally:
        idx.close()
