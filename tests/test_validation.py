"""Tests for the ground-truth validation harness.

Ensures the validation pipeline itself works correctly and that
Echo-Guard meets minimum quality thresholds on the labeled dataset.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmarks.ground_truth import get_all_pairs, get_pairs_by_type
from benchmarks.validate import run_validation


# ── Ground truth dataset sanity checks ───────────────────────────────────


class TestGroundTruth:
    def test_has_all_clone_types(self):
        types = {p.clone_type for p in get_all_pairs()}
        assert "type1" in types
        assert "type2" in types
        assert "type3" in types
        assert "type4" in types
        assert "cross_lang" in types
        assert "negative" in types

    def test_has_enough_pairs(self):
        pairs = get_all_pairs()
        assert len(pairs) >= 20, f"Need at least 20 pairs, got {len(pairs)}"

    def test_positive_negative_balance(self):
        pairs = get_all_pairs()
        positives = [p for p in pairs if p.is_clone]
        negatives = [p for p in pairs if not p.is_clone]
        assert len(positives) >= 10
        assert len(negatives) >= 5

    def test_unique_ids(self):
        pairs = get_all_pairs()
        ids = [p.id for p in pairs]
        assert len(ids) == len(set(ids)), "Duplicate pair IDs found"

    def test_all_pairs_have_code(self):
        for p in get_all_pairs():
            assert len(p.code_a.strip()) > 10, f"{p.id}: code_a is too short"
            assert len(p.code_b.strip()) > 10, f"{p.id}: code_b is too short"
            assert p.lang_a, f"{p.id}: lang_a is empty"
            assert p.lang_b, f"{p.id}: lang_b is empty"


# ── Validation quality thresholds ────────────────────────────────────────


class TestValidationQuality:
    """These tests enforce minimum quality bars for Echo-Guard.

    If these fail, it means a code change degraded detection quality.
    """

    @pytest.fixture(scope="class")
    def results_at_050(self):
        return run_validation(threshold=0.50)

    def test_no_false_positives_at_050(self, results_at_050):
        """At the default threshold, we should have very few false positives."""
        fp = results_at_050["overall"]["fp"]
        total_neg = sum(
            1
            for d in results_at_050["details"]
            if not next(p for p in get_all_pairs() if p.id == d["id"]).is_clone
        )
        fp_rate = fp / total_neg if total_neg > 0 else 0
        assert (
            fp_rate <= 0.15
        ), f"False positive rate {fp_rate:.1%} exceeds 15% at threshold 0.50"

    def test_type1_perfect_recall(self, results_at_050):
        """Type-1 clones (exact copies) should always be detected."""
        t1 = results_at_050["by_type"].get("type1", {})
        assert t1.get("recall", 0) == 1.0, "Type-1 clones should have 100% recall"

    def test_type2_high_recall(self, results_at_050):
        """Type-2 clones (renamed identifiers) should be well detected."""
        t2 = results_at_050["by_type"].get("type2", {})
        assert (
            t2.get("recall", 0) >= 0.75
        ), f"Type-2 recall {t2.get('recall', 0):.1%} below 75%"

    def test_overall_f1_at_050(self, results_at_050):
        """Overall F1 at the default threshold should be high."""
        f1 = results_at_050["overall"]["f1"]
        assert f1 >= 0.70, f"Overall F1 {f1:.1%} below 70% at threshold 0.50"

    def test_overall_precision_at_050(self, results_at_050):
        """Precision should be high at the default threshold."""
        prec = results_at_050["overall"]["precision"]
        assert prec >= 0.80, f"Precision {prec:.1%} below 80% at threshold 0.50"

    def test_type4_detects_some(self, results_at_050):
        """Type-4 semantic clones are hard, but we should catch some."""
        t4 = results_at_050["by_type"].get("type4", {})
        assert (
            t4.get("tp", 0) >= 1
        ), "Should detect at least 1 Type-4 clone at threshold 0.50"
