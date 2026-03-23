"""Tests for the benchmark infrastructure.

Validates that:
1. All benchmark adapters load correctly
2. Curated pairs are well-formed
3. Evaluation pipeline produces valid metrics
4. Type-4 gap analysis works correctly
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmarks.base import BenchmarkPair, EvaluationMetrics, evaluate_pair
from benchmarks.bigclonebench import BigCloneBenchAdapter
from benchmarks.gptclonebench import GPTCloneBenchAdapter
from benchmarks.poj104 import POJ104Adapter
from benchmarks.runner import ADAPTERS, get_adapter


# ── Adapter registry ──────────────────────────────────────────────────────


class TestAdapterRegistry:
    def test_all_adapters_registered(self):
        assert "bigclonebench" in ADAPTERS
        assert "gptclonebench" in ADAPTERS
        assert "poj104" in ADAPTERS

    def test_get_adapter_valid(self):
        for name in ADAPTERS:
            adapter = get_adapter(name)
            assert adapter is not None
            assert adapter.name
            assert adapter.dataset_id == name

    def test_get_adapter_invalid(self):
        with pytest.raises(ValueError, match="Unknown benchmark"):
            get_adapter("nonexistent")


# ── Curated pairs validation ─────────────────────────────────────────────


class TestBigCloneBenchPairs:
    @pytest.fixture(scope="class")
    def adapter(self):
        return BigCloneBenchAdapter()

    @pytest.fixture(scope="class")
    def pairs(self, adapter):
        return adapter.load_pairs()

    def test_has_pairs(self, pairs):
        assert len(pairs) >= 15, f"Expected at least 15 pairs, got {len(pairs)}"

    def test_all_java(self, pairs):
        for p in pairs:
            assert p.language_a == "java", f"{p.pair_id}: language_a should be java"
            assert p.language_b == "java", f"{p.pair_id}: language_b should be java"

    def test_has_all_clone_types(self, pairs):
        types = {p.clone_type for p in pairs}
        assert "type1" in types
        assert "type2" in types
        assert "type3" in types
        assert "type4" in types
        assert "negative" in types

    def test_has_negatives(self, pairs):
        negatives = [p for p in pairs if not p.is_clone]
        assert len(negatives) >= 3

    def test_code_not_empty(self, pairs):
        for p in pairs:
            assert len(p.code_a.strip()) > 10, f"{p.pair_id}: code_a too short"
            assert len(p.code_b.strip()) > 10, f"{p.pair_id}: code_b too short"

    def test_unique_ids(self, pairs):
        ids = [p.pair_id for p in pairs]
        assert len(ids) == len(set(ids)), "Duplicate pair IDs"

    def test_source_dataset(self, pairs):
        for p in pairs:
            assert p.source_dataset == "bigclonebench"


class TestGPTCloneBenchPairs:
    @pytest.fixture(scope="class")
    def adapter(self):
        return GPTCloneBenchAdapter()

    @pytest.fixture(scope="class")
    def pairs(self, adapter):
        return adapter.load_pairs()

    def test_has_pairs(self, pairs):
        assert len(pairs) >= 12, f"Expected at least 12 pairs, got {len(pairs)}"

    def test_has_python_and_java(self, pairs):
        languages = {p.language_a for p in pairs}
        assert "python" in languages, "Should have Python pairs"
        assert "java" in languages, "Should have Java pairs"

    def test_has_all_clone_types(self, pairs):
        types = {p.clone_type for p in pairs}
        assert "type1" in types
        assert "type2" in types
        assert "type3" in types
        assert "type4" in types
        assert "negative" in types

    def test_has_ai_metadata(self, pairs):
        """GPTCloneBench pairs should indicate their AI generation source."""
        ai_pairs = [
            p for p in pairs
            if p.metadata.get("generated_by") in ("gpt-4", "claude", "gpt")
        ]
        assert len(ai_pairs) >= 5, "Should have AI-attributed pairs"

    def test_unique_ids(self, pairs):
        ids = [p.pair_id for p in pairs]
        assert len(ids) == len(set(ids))


class TestPOJ104Pairs:
    @pytest.fixture(scope="class")
    def adapter(self):
        return POJ104Adapter()

    @pytest.fixture(scope="class")
    def pairs(self, adapter):
        return adapter.load_pairs()

    def test_has_pairs(self, pairs):
        assert len(pairs) >= 10, f"Expected at least 10 pairs, got {len(pairs)}"

    def test_all_c_or_cpp(self, pairs):
        for p in pairs:
            assert p.language_a in ("c", "cpp"), f"{p.pair_id}: unexpected language {p.language_a}"

    def test_mostly_type4(self, pairs):
        """POJ-104 is primarily about semantic clones."""
        type4_pairs = [p for p in pairs if p.clone_type == "type4"]
        assert len(type4_pairs) >= 5, "POJ-104 should have mostly Type-4 pairs"

    def test_has_negatives(self, pairs):
        negatives = [p for p in pairs if not p.is_clone]
        assert len(negatives) >= 3

    def test_unique_ids(self, pairs):
        ids = [p.pair_id for p in pairs]
        assert len(ids) == len(set(ids))


# ── Evaluation metrics ────────────────────────────────────────────────────


class TestEvaluationMetrics:
    def test_perfect_precision(self):
        m = EvaluationMetrics(tp=10, fp=0, tn=5, fn=3)
        assert m.precision == 1.0

    def test_perfect_recall(self):
        m = EvaluationMetrics(tp=10, fp=3, tn=5, fn=0)
        assert m.recall == 1.0

    def test_f1_calculation(self):
        m = EvaluationMetrics(tp=8, fp=2, tn=5, fn=2)
        # precision = 8/10 = 0.8, recall = 8/10 = 0.8
        # f1 = 2 * 0.8 * 0.8 / (0.8 + 0.8) = 0.8
        assert abs(m.f1 - 0.8) < 0.01

    def test_zero_division_safe(self):
        m = EvaluationMetrics()
        assert m.precision == 0.0
        assert m.recall == 0.0
        assert m.f1 == 0.0
        assert m.accuracy == 0.0

    def test_to_dict(self):
        m = EvaluationMetrics(tp=5, fp=1, tn=3, fn=1)
        d = m.to_dict()
        assert "precision" in d
        assert "recall" in d
        assert "f1" in d
        assert d["tp"] == 5
        assert d["total"] == 10


# ── Evaluation pipeline ──────────────────────────────────────────────────


class TestEvaluationPipeline:
    def test_evaluate_obvious_clone(self):
        """Two identical Python functions should match."""
        pair = BenchmarkPair(
            pair_id="test_clone",
            code_a="def foo(x):\n    return x + 1\n",
            code_b="def foo(x):\n    return x + 1\n",
            language_a="python",
            language_b="python",
            is_clone=True,
            clone_type="type1",
            source_dataset="test",
        )
        predicted, score, _ = evaluate_pair(pair, threshold=0.50)
        assert predicted is True
        assert score > 0.5

    def test_evaluate_obvious_non_clone(self):
        """Completely different functions should not match."""
        pair = BenchmarkPair(
            pair_id="test_neg",
            code_a="""\
def fibonacci(n):
    if n <= 1:
        return n
    return fibonacci(n - 1) + fibonacci(n - 2)
""",
            code_b="""\
def send_email(to, subject, body):
    import smtplib
    server = smtplib.SMTP('localhost')
    server.sendmail('from@test.com', to, body)
    server.quit()
""",
            language_a="python",
            language_b="python",
            is_clone=False,
            clone_type="negative",
            source_dataset="test",
        )
        predicted, score, _ = evaluate_pair(pair, threshold=0.50)
        assert predicted is False

    def test_adapter_evaluate_runs(self):
        """Smoke test: running evaluation should not crash."""
        adapter = BigCloneBenchAdapter()
        result = adapter.evaluate(threshold=0.50, max_pairs=5)
        assert result.dataset_name == "BigCloneBench"
        assert result.pairs_evaluated > 0
        assert result.overall.total > 0


# ── Quality thresholds ────────────────────────────────────────────────────


class TestBenchmarkQuality:
    """Quality gate tests for Echo Guard's benchmark performance.

    These tests ensure that code changes don't degrade detection quality
    on the benchmark datasets.
    """

    @pytest.fixture(scope="class")
    def bcb_results(self):
        adapter = BigCloneBenchAdapter()
        return adapter.evaluate(threshold=0.50)

    @pytest.fixture(scope="class")
    def gcb_results(self):
        adapter = GPTCloneBenchAdapter()
        return adapter.evaluate(threshold=0.50)

    @pytest.fixture(scope="class")
    def poj_results(self):
        adapter = POJ104Adapter()
        return adapter.evaluate(threshold=0.50)

    def test_bcb_type1_recall(self, bcb_results):
        """BigCloneBench Type-1 recall should be perfect."""
        t1 = bcb_results.by_clone_type.get("type1")
        if t1 and t1.total > 0:
            assert t1.recall >= 0.90, f"BCB Type-1 recall {t1.recall:.1%} < 90%"

    def test_bcb_precision(self, bcb_results):
        """BigCloneBench precision should be high."""
        assert bcb_results.overall.precision >= 0.70, (
            f"BCB precision {bcb_results.overall.precision:.1%} < 70%"
        )

    def test_gcb_overall_f1(self, gcb_results):
        """GPTCloneBench F1 should be reasonable."""
        assert gcb_results.overall.f1 >= 0.50, (
            f"GCB F1 {gcb_results.overall.f1:.1%} < 50%"
        )

    def test_gcb_low_false_positives(self, gcb_results):
        """GPTCloneBench should have few false positives."""
        negatives = [
            d for d in gcb_results.details if not d["is_clone"]
        ]
        if negatives:
            fp_count = sum(1 for d in negatives if d["predicted"])
            fp_rate = fp_count / len(negatives)
            assert fp_rate <= 0.25, f"GCB FP rate {fp_rate:.1%} > 25%"

    def test_poj_has_type4_results(self, poj_results):
        """POJ-104 should produce Type-4 evaluation results."""
        t4 = poj_results.by_clone_type.get("type4")
        assert t4 is not None, "POJ-104 should have Type-4 results"
        assert t4.total > 0, "POJ-104 should have evaluated Type-4 pairs"
