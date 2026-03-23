"""Tests for the benchmark infrastructure.

Validates that:
1. All benchmark adapters are registered and load correctly
2. The real scan pipeline (multi-function index + find_all_matches) produces valid metrics
3. Severity levels are tracked correctly
4. Quality gates catch regressions

Requires benchmark datasets to be downloaded. See benchmarks/SETUP.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmarks.base import BenchmarkPair, EvaluationMetrics
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


# ── Dataset availability ─────────────────────────────────────────────────


class TestDatasetAvailability:
    """Verify datasets raise clear errors when not available."""

    def test_missing_dataset_raises_error(self, tmp_path):
        """Adapters should raise FileNotFoundError with setup instructions."""
        adapter = BigCloneBenchAdapter(data_dir=tmp_path)
        with pytest.raises(FileNotFoundError, match="SETUP.md"):
            adapter.load_pairs()

    def test_missing_gcb_raises_error(self, tmp_path):
        adapter = GPTCloneBenchAdapter(data_dir=tmp_path)
        with pytest.raises(FileNotFoundError, match="SETUP.md"):
            adapter.load_pairs()

    def test_missing_poj_raises_error(self, tmp_path):
        adapter = POJ104Adapter(data_dir=tmp_path)
        with pytest.raises(FileNotFoundError, match="SETUP.md"):
            adapter.load_pairs()


# ── Dataset validation ───────────────────────────────────────────────────


def _skip_if_unavailable(adapter):
    if not adapter.is_available():
        pytest.skip(f"{adapter.name} dataset not downloaded")


class TestBigCloneBenchPairs:
    @pytest.fixture(scope="class")
    def adapter(self):
        a = BigCloneBenchAdapter()
        _skip_if_unavailable(a)
        return a

    @pytest.fixture(scope="class")
    def pairs(self, adapter):
        return adapter.load_pairs()

    def test_has_pairs(self, pairs):
        assert len(pairs) >= 100, f"Expected at least 100 pairs, got {len(pairs)}"

    def test_all_java(self, pairs):
        for p in pairs:
            assert p.language_a == "java"
            assert p.language_b == "java"

    def test_has_clone_types(self, pairs):
        types = {p.clone_type for p in pairs}
        assert "type1" in types
        assert "type2" in types

    def test_has_negatives(self, pairs):
        negatives = [p for p in pairs if not p.is_clone]
        assert len(negatives) >= 10

    def test_code_not_empty(self, pairs):
        for p in pairs[:50]:
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
        a = GPTCloneBenchAdapter()
        _skip_if_unavailable(a)
        return a

    @pytest.fixture(scope="class")
    def pairs(self, adapter):
        return adapter.load_pairs()

    def test_has_pairs(self, pairs):
        assert len(pairs) >= 100, f"Expected at least 100 pairs, got {len(pairs)}"

    def test_has_clone_types(self, pairs):
        types = {p.clone_type for p in pairs}
        assert "type3" in types
        assert "type4" in types
        assert "negative" in types

    def test_unique_ids(self, pairs):
        ids = [p.pair_id for p in pairs]
        assert len(ids) == len(set(ids))


class TestPOJ104Pairs:
    @pytest.fixture(scope="class")
    def adapter(self):
        a = POJ104Adapter()
        _skip_if_unavailable(a)
        return a

    @pytest.fixture(scope="class")
    def pairs(self, adapter):
        return adapter.load_pairs()

    def test_has_pairs(self, pairs):
        assert len(pairs) >= 50, f"Expected at least 50 pairs, got {len(pairs)}"

    def test_all_c(self, pairs):
        for p in pairs:
            assert p.language_a == "c", f"{p.pair_id}: unexpected language {p.language_a}"

    def test_mostly_type4(self, pairs):
        type4_pairs = [p for p in pairs if p.clone_type == "type4"]
        assert len(type4_pairs) >= 20, "POJ-104 should have mostly Type-4 pairs"

    def test_has_negatives(self, pairs):
        negatives = [p for p in pairs if not p.is_clone]
        assert len(negatives) >= 10

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


# ── Real pipeline evaluation ─────────────────────────────────────────────


class TestRealPipelineEvaluation:
    """Tests that the benchmark uses the real echo-guard scan pipeline
    (multi-function index + find_all_matches), not pair-by-pair evaluation."""

    def test_adapter_evaluate_runs(self):
        adapter = BigCloneBenchAdapter()
        _skip_if_unavailable(adapter)
        result = adapter.evaluate(threshold=0.50, max_pairs=10)
        assert result.dataset_name == "BigCloneBench"
        assert result.pairs_evaluated > 0
        assert result.overall.total > 0

    def test_result_has_severity_info(self):
        adapter = BigCloneBenchAdapter()
        _skip_if_unavailable(adapter)
        result = adapter.evaluate(threshold=0.50, max_pairs=20)
        assert hasattr(result, "by_severity")
        assert isinstance(result.by_severity, dict)
        if result.overall.tp > 0:
            assert len(result.by_severity) > 0

    def test_severity_values_are_valid(self):
        adapter = BigCloneBenchAdapter()
        _skip_if_unavailable(adapter)
        result = adapter.evaluate(threshold=0.50, max_pairs=20)
        for severity in result.by_severity.keys():
            assert severity in ("high", "medium", "low"), f"Unexpected severity: {severity}"

    def test_multi_function_index(self):
        adapter = GPTCloneBenchAdapter()
        _skip_if_unavailable(adapter)
        result = adapter.evaluate(threshold=0.50, max_pairs=20)
        assert result.overall.total > 0


# ── Quality thresholds ────────────────────────────────────────────────────


class TestBenchmarkQuality:
    """Quality gate tests for Echo Guard's benchmark performance.

    These tests ensure that code changes don't degrade detection quality
    on the benchmark datasets using the real scan pipeline.
    """

    @pytest.fixture(scope="class")
    def bcb_results(self):
        adapter = BigCloneBenchAdapter()
        _skip_if_unavailable(adapter)
        return adapter.evaluate(threshold=0.50)

    @pytest.fixture(scope="class")
    def gcb_results(self):
        adapter = GPTCloneBenchAdapter()
        _skip_if_unavailable(adapter)
        return adapter.evaluate(threshold=0.50)

    @pytest.fixture(scope="class")
    def poj_results(self):
        adapter = POJ104Adapter()
        _skip_if_unavailable(adapter)
        return adapter.evaluate(threshold=0.50)

    def test_bcb_type1_recall(self, bcb_results):
        """BigCloneBench Type-1 recall should be perfect."""
        t1 = bcb_results.by_clone_type.get("type1")
        if t1 and t1.total > 0:
            assert t1.recall >= 0.90, f"BCB Type-1 recall {t1.recall:.1%} < 90%"

    def test_bcb_precision(self, bcb_results):
        """BigCloneBench precision should be high."""
        assert bcb_results.overall.precision >= 0.60, (
            f"BCB precision {bcb_results.overall.precision:.1%} < 60%"
        )

    def test_bcb_has_severity_distribution(self, bcb_results):
        """BigCloneBench should produce matches at different severity levels."""
        if bcb_results.overall.tp > 0:
            assert len(bcb_results.by_severity) > 0, "Should have severity data"

    def test_gcb_overall_f1(self, gcb_results):
        """GPTCloneBench F1 should be reasonable."""
        assert gcb_results.overall.f1 >= 0.40, (
            f"GCB F1 {gcb_results.overall.f1:.1%} < 40%"
        )

    def test_poj_has_type4_results(self, poj_results):
        """POJ-104 should produce Type-4 evaluation results."""
        t4 = poj_results.by_clone_type.get("type4")
        assert t4 is not None, "POJ-104 should have Type-4 results"
        assert t4.total > 0, "POJ-104 should have evaluated Type-4 pairs"

    def test_poj_type4_severity_tracked(self, poj_results):
        """POJ-104 Type-4 detections should have severity info in gap analysis."""
        if poj_results.type4_gap_analysis:
            assert "severity_of_detections" in poj_results.type4_gap_analysis
