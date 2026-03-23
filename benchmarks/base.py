"""Base classes for benchmark adapters.

Provides a common interface for loading datasets, running evaluations,
and reporting results across all benchmark suites.
"""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from echo_guard.languages import ExtractedFunction, extract_functions_universal
from echo_guard.similarity import SimilarityEngine


@dataclass
class BenchmarkPair:
    """A single clone/non-clone pair from an external benchmark."""

    pair_id: str
    code_a: str
    code_b: str
    language_a: str
    language_b: str
    is_clone: bool
    clone_type: str  # "type1", "type2", "type3", "type4", "cross_lang", "negative"
    source_dataset: str  # "bigclonebench", "gptclonebench", "poj104"
    metadata: dict = field(default_factory=dict)


@dataclass
class EvaluationMetrics:
    """Precision/recall/F1 metrics for a benchmark run."""

    tp: int = 0
    fp: int = 0
    tn: int = 0
    fn: int = 0

    @property
    def precision(self) -> float:
        denom = self.tp + self.fp
        return self.tp / denom if denom > 0 else 0.0

    @property
    def recall(self) -> float:
        denom = self.tp + self.fn
        return self.tp / denom if denom > 0 else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) > 0 else 0.0

    @property
    def accuracy(self) -> float:
        total = self.tp + self.fp + self.tn + self.fn
        return (self.tp + self.tn) / total if total > 0 else 0.0

    @property
    def total(self) -> int:
        return self.tp + self.fp + self.tn + self.fn

    def to_dict(self) -> dict:
        return {
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "accuracy": round(self.accuracy, 4),
            "tp": self.tp,
            "fp": self.fp,
            "tn": self.tn,
            "fn": self.fn,
            "total": self.total,
        }


@dataclass
class BenchmarkResult:
    """Complete results from a benchmark run."""

    dataset_name: str
    threshold: float
    elapsed_seconds: float
    total_pairs: int
    pairs_evaluated: int
    pairs_skipped: int
    overall: EvaluationMetrics
    by_clone_type: dict[str, EvaluationMetrics]
    details: list[dict]
    type4_gap_analysis: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "dataset": self.dataset_name,
            "threshold": self.threshold,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "total_pairs": self.total_pairs,
            "pairs_evaluated": self.pairs_evaluated,
            "pairs_skipped": self.pairs_skipped,
            "overall": self.overall.to_dict(),
            "by_clone_type": {
                k: v.to_dict() for k, v in sorted(self.by_clone_type.items())
            },
            "type4_gap_analysis": self.type4_gap_analysis,
        }

    def print_summary(self) -> None:
        """Print a human-readable summary of results."""
        print(f"\n{'=' * 72}")
        print(f"  {self.dataset_name.upper()} BENCHMARK — threshold={self.threshold}")
        print(f"{'=' * 72}")
        print(
            f"\n  Pairs: {self.pairs_evaluated}/{self.total_pairs} evaluated "
            f"({self.pairs_skipped} skipped)  |  Time: {self.elapsed_seconds:.1f}s"
        )

        o = self.overall
        print("\n  OVERALL")
        print(f"    Precision:  {o.precision:.1%}")
        print(f"    Recall:     {o.recall:.1%}")
        print(f"    F1 Score:   {o.f1:.1%}")
        print(f"    Accuracy:   {o.accuracy:.1%}")

        print("\n  BY CLONE TYPE")
        print(
            f"    {'Type':<12s} {'Prec':>7s} {'Recall':>7s} {'F1':>7s}"
            f"  {'TP':>4s} {'FP':>4s} {'TN':>4s} {'FN':>4s}"
        )
        print(f"    {'-' * 12} {'-' * 7} {'-' * 7} {'-' * 7}  {'-' * 4} {'-' * 4} {'-' * 4} {'-' * 4}")
        for ctype, m in sorted(self.by_clone_type.items()):
            print(
                f"    {ctype:<12s} {m.precision:>6.1%} {m.recall:>6.1%} {m.f1:>6.1%}"
                f"  {m.tp:>4d} {m.fp:>4d} {m.tn:>4d} {m.fn:>4d}"
            )

        if self.type4_gap_analysis:
            print("\n  TYPE-4 GAP ANALYSIS")
            for key, value in self.type4_gap_analysis.items():
                print(f"    {key}: {value}")


def extract_function_from_code(
    code: str, language: str, filepath: str
) -> ExtractedFunction | None:
    """Extract the first function from a code snippet using tree-sitter.

    Falls back to creating a synthetic ExtractedFunction if tree-sitter
    can't parse (e.g., snippet without proper function definition).
    """
    funcs = extract_functions_universal(filepath, source=code, language=language)
    if funcs:
        return funcs[0]

    # Fallback: wrap the code as a synthetic function for comparison
    # This handles cases where the code is a bare snippet without a function def
    lines = code.strip().splitlines()
    if len(lines) < 2:
        return None

    return ExtractedFunction(
        name=f"_snippet_{abs(hash(code)) % 10000}",
        filepath=filepath,
        language=language,
        lineno=1,
        end_lineno=len(lines),
        source=code,
    )


def evaluate_pair(
    pair: BenchmarkPair,
    threshold: float,
) -> tuple[bool, float, str | None]:
    """Evaluate a single pair using the SimilarityEngine.

    Returns (predicted_is_clone, similarity_score, match_type).
    """
    func_a = extract_function_from_code(pair.code_a, pair.language_a, f"a/{pair.pair_id}.{_ext(pair.language_a)}")
    func_b = extract_function_from_code(pair.code_b, pair.language_b, f"b/{pair.pair_id}.{_ext(pair.language_b)}")

    if func_a is None or func_b is None:
        return False, 0.0, None

    engine = SimilarityEngine(
        lsh_threshold=0.2,
        similarity_threshold=threshold,
        num_perm=128,
    )
    engine.add_function(func_a)

    try:
        matches = engine.find_similar(func_b, threshold=0.1)
    except Exception:
        return False, 0.0, None

    if matches:
        best = matches[0]
        return (
            best.similarity_score >= threshold,
            best.similarity_score,
            best.match_type,
        )
    return False, 0.0, None


def _ext(language: str) -> str:
    """Get file extension for a language."""
    extensions = {
        "python": "py",
        "java": "java",
        "javascript": "js",
        "typescript": "ts",
        "go": "go",
        "rust": "rs",
        "ruby": "rb",
        "c": "c",
        "cpp": "cpp",
    }
    return extensions.get(language, "txt")


class BenchmarkAdapter(ABC):
    """Abstract base class for benchmark dataset adapters."""

    def __init__(self, data_dir: Path | None = None):
        self.data_dir = data_dir or Path(__file__).parent / "data"

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name of the benchmark."""
        ...

    @property
    @abstractmethod
    def dataset_id(self) -> str:
        """Machine-readable identifier (e.g., 'bigclonebench')."""
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Check if the dataset is downloaded and ready."""
        ...

    @abstractmethod
    def download(self, force: bool = False) -> None:
        """Download the dataset if not already present."""
        ...

    @abstractmethod
    def load_pairs(self, max_pairs: int | None = None) -> list[BenchmarkPair]:
        """Load clone pairs from the dataset."""
        ...

    def evaluate(
        self,
        threshold: float = 0.50,
        max_pairs: int | None = None,
        verbose: bool = False,
    ) -> BenchmarkResult:
        """Run evaluation against the dataset."""
        pairs = self.load_pairs(max_pairs=max_pairs)
        overall = EvaluationMetrics()
        by_type: dict[str, EvaluationMetrics] = defaultdict(EvaluationMetrics)
        details: list[dict] = []
        skipped = 0

        t0 = time.perf_counter()

        for i, pair in enumerate(pairs):
            predicted, score, match_type = evaluate_pair(pair, threshold)

            m = by_type[pair.clone_type]
            if pair.is_clone and predicted:
                overall.tp += 1
                m.tp += 1
                verdict = "TP"
            elif pair.is_clone and not predicted:
                overall.fn += 1
                m.fn += 1
                verdict = "FN"
            elif not pair.is_clone and predicted:
                overall.fp += 1
                m.fp += 1
                verdict = "FP"
            else:
                overall.tn += 1
                m.tn += 1
                verdict = "TN"

            detail = {
                "pair_id": pair.pair_id,
                "clone_type": pair.clone_type,
                "is_clone": pair.is_clone,
                "predicted": predicted,
                "score": round(score, 4),
                "match_type": match_type,
                "verdict": verdict,
            }
            details.append(detail)

            if verbose:
                icon = "+" if verdict in ("TP", "TN") else "X"
                print(
                    f"  [{icon}] {verdict} {pair.pair_id:<12s} "
                    f"score={score:.3f}  ({pair.clone_type})"
                )

            # Progress indicator for large datasets
            if (i + 1) % 100 == 0 and not verbose:
                elapsed = time.perf_counter() - t0
                print(f"  ... {i + 1}/{len(pairs)} pairs evaluated ({elapsed:.1f}s)")

        elapsed = time.perf_counter() - t0

        # Type-4 gap analysis
        type4_analysis = self._analyze_type4_gaps(details, by_type)

        return BenchmarkResult(
            dataset_name=self.name,
            threshold=threshold,
            elapsed_seconds=elapsed,
            total_pairs=len(pairs),
            pairs_evaluated=len(pairs) - skipped,
            pairs_skipped=skipped,
            overall=overall,
            by_clone_type=dict(by_type),
            details=details,
            type4_gap_analysis=type4_analysis,
        )

    def _analyze_type4_gaps(
        self,
        details: list[dict],
        by_type: dict[str, EvaluationMetrics],
    ) -> dict:
        """Analyze Type-4 detection failures to guide Phase 2."""
        t4_metrics = by_type.get("type4", EvaluationMetrics())
        if t4_metrics.total == 0:
            return {}

        t4_failures = [
            d for d in details if d["clone_type"] == "type4" and d["verdict"] == "FN"
        ]
        t4_successes = [
            d for d in details if d["clone_type"] == "type4" and d["verdict"] == "TP"
        ]

        analysis = {
            "type4_total": t4_metrics.total,
            "type4_detected": t4_metrics.tp,
            "type4_missed": t4_metrics.fn,
            "type4_recall": round(t4_metrics.recall, 4),
            "detection_gap_percentage": round(1.0 - t4_metrics.recall, 4),
            "avg_score_successes": (
                round(sum(d["score"] for d in t4_successes) / len(t4_successes), 4)
                if t4_successes
                else 0.0
            ),
            "avg_score_failures": (
                round(sum(d["score"] for d in t4_failures) / len(t4_failures), 4)
                if t4_failures
                else 0.0
            ),
            "recommendation": (
                "Phase 2 code embeddings needed"
                if t4_metrics.recall < 0.5
                else "Current TF-IDF approach handles basic Type-4 well"
            ),
        }
        return analysis
