"""Tests for echo_guard.upload — payload construction and path stripping."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from echo_guard.upload import (
    _strip_feedback_record,
    _strip_training_pair,
    prepare_payload,
)


# ── _strip_feedback_record ───────────────────────────────────────────────


class TestStripFeedbackRecord:
    def test_strips_internal_fields(self):
        record = {
            "id": 42,
            "verdict": "true_positive",
            "similarity_score": 0.95,
            "uploaded_at": None,
            "recorded_at": "2026-03-29T00:00:00",
            "source_language": "python",
        }
        stripped = _strip_feedback_record(record)
        assert "id" not in stripped
        assert "uploaded_at" not in stripped
        assert "recorded_at" not in stripped
        assert stripped["verdict"] == "true_positive"
        assert stripped["source_language"] == "python"


# ── _strip_training_pair ─────────────────────────────────────────────────


class TestStripTrainingPair:
    def test_strips_identifying_fields(self):
        pair = {
            "id": 1,
            "verdict": "clone",
            "language": "python",
            "source_code_a": "def foo(): pass",
            "source_code_b": "def bar(): pass",
            "function_name_a": "foo",
            "function_name_b": "bar",
            "filepath_a": "/src/a.py",
            "filepath_b": "/src/b.py",
            "embedding_score": 0.92,
            "clone_type": "type3",
            "uploaded_at": None,
            "recorded_at": "2026-03-29T00:00:00",
        }
        stripped = _strip_training_pair(pair)
        # Should be removed
        assert "filepath_a" not in stripped
        assert "filepath_b" not in stripped
        assert "function_name_a" not in stripped
        assert "function_name_b" not in stripped
        assert "id" not in stripped
        assert "uploaded_at" not in stripped
        assert "recorded_at" not in stripped
        # Should be kept
        assert stripped["source_code_a"] == "def foo(): pass"
        assert stripped["source_code_b"] == "def bar(): pass"
        assert stripped["verdict"] == "clone"
        assert stripped["language"] == "python"
        assert stripped["embedding_score"] == 0.92


# ── prepare_payload ──────────────────────────────────────────────────────


class TestPreparePayload:
    def _make_config(self, consent="private", model="codesage-small", visibility="public"):
        config = MagicMock()
        config.feedback_consent = consent
        config.repo_visibility = visibility
        config.model = model
        return config

    def test_none_consent_returns_none(self):
        config = self._make_config(consent="none")
        assert prepare_payload(config, [{"verdict": "tp"}], []) is None

    def test_empty_records_returns_none(self):
        config = self._make_config()
        assert prepare_payload(config, [], []) is None

    @patch("echo_guard.__version__", "0.4.1")
    def test_private_tier_excludes_training_pairs(self):
        config = self._make_config(consent="private")
        feedback = [{"verdict": "true_positive", "source_language": "python", "similarity_score": 0.9}]
        pairs = [{"verdict": "clone", "source_code_a": "x", "source_code_b": "y"}]
        payload = prepare_payload(config, feedback, pairs)
        assert payload is not None
        # Only feedback records, no training pairs
        types = [r.get("type") for r in payload["records"]]
        assert "feedback" in types
        assert "training_pair" not in types

    @patch("echo_guard.__version__", "0.4.1")
    def test_public_tier_includes_training_pairs(self):
        config = self._make_config(consent="public")
        feedback = [{"verdict": "true_positive", "source_language": "python", "similarity_score": 0.9}]
        pairs = [{"verdict": "clone", "language": "python", "source_code_a": "x", "source_code_b": "y"}]
        payload = prepare_payload(config, feedback, pairs)
        assert payload is not None
        types = [r.get("type") for r in payload["records"]]
        assert "feedback" in types
        assert "training_pair" in types

    @patch("echo_guard.__version__", "0.4.1")
    def test_public_tier_private_repo_excludes_training_pairs(self):
        config = self._make_config(consent="public", visibility="private")
        feedback = [{"verdict": "true_positive", "source_language": "python", "similarity_score": 0.9}]
        pairs = [{"verdict": "clone", "language": "python", "source_code_a": "x", "source_code_b": "y"}]
        payload = prepare_payload(config, feedback, pairs)
        assert payload is not None
        types = [r.get("type") for r in payload["records"]]
        assert "feedback" in types
        assert "training_pair" not in types

    @patch("echo_guard.__version__", "0.4.1")
    def test_public_tier_unknown_repo_excludes_training_pairs(self):
        config = self._make_config(consent="public", visibility="unknown")
        feedback = [{"verdict": "true_positive", "source_language": "python", "similarity_score": 0.9}]
        pairs = [{"verdict": "clone", "language": "python", "source_code_a": "x", "source_code_b": "y"}]
        payload = prepare_payload(config, feedback, pairs)
        assert payload is not None
        types = [r.get("type") for r in payload["records"]]
        assert "feedback" in types
        assert "training_pair" not in types

    @patch("echo_guard.__version__", "0.4.1")
    def test_payload_metadata(self):
        config = self._make_config(consent="private", model="codesage-small")
        feedback = [{"verdict": "tp", "source_language": "python", "similarity_score": 0.9}]
        payload = prepare_payload(config, feedback, [])
        assert payload["schema_version"] == "1"
        assert payload["echo_guard_version"] == "0.4.1"
        assert payload["model_name"] == "codesage-small"
        assert payload["consent_tier"] == "private"
        assert "upload_timestamp" in payload

    @patch("echo_guard.__version__", "0.4.1")
    def test_language_distribution(self):
        config = self._make_config(consent="private")
        feedback = [
            {"verdict": "tp", "source_language": "python", "similarity_score": 0.9},
            {"verdict": "fp", "source_language": "python", "similarity_score": 0.7},
            {"verdict": "tp", "source_language": "typescript", "similarity_score": 0.8},
        ]
        payload = prepare_payload(config, feedback, [])
        assert payload["language_distribution"] == {"python": 2, "typescript": 1}

    @patch("echo_guard.__version__", "0.4.1")
    def test_public_tier_strips_paths_from_training_pairs(self):
        config = self._make_config(consent="public")
        pairs = [{
            "id": 1,
            "verdict": "clone",
            "language": "python",
            "source_code_a": "def foo(): pass",
            "source_code_b": "def bar(): pass",
            "function_name_a": "foo",
            "function_name_b": "bar",
            "filepath_a": "/src/a.py",
            "filepath_b": "/src/b.py",
            "embedding_score": 0.92,
            "uploaded_at": None,
            "recorded_at": "2026-03-29",
        }]
        payload = prepare_payload(config, [], pairs)
        assert payload is not None
        tp_records = [r for r in payload["records"] if r.get("type") == "training_pair"]
        assert len(tp_records) == 1
        rec = tp_records[0]
        assert "filepath_a" not in rec
        assert "filepath_b" not in rec
        assert "function_name_a" not in rec
        assert "function_name_b" not in rec
        assert rec["source_code_a"] == "def foo(): pass"
