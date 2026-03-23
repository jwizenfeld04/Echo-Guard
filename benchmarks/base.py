"""Base classes for benchmark adapters.

Evaluates Echo Guard using the same pipeline as `echo-guard scan`:
1. Write all benchmark functions to temp files (simulating a real codebase)
2. Extract functions via tree-sitter (same as `echo-guard index`)
3. Index ALL functions into a single SimilarityEngine (same as `scan_for_redundancy`)
4. Run `find_all_matches()` — the real batch scan path
5. Check which expected pairs were found, at what severity, and what was missed

This matches real-world usage where N functions coexist in the index and the
engine must find the right matches among many candidates while avoiding false
positives from unrelated functions.
"""

from __future__ import annotations

import tempfile
import time
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from echo_guard.languages import ExtractedFunction, extract_functions_universal
from echo_guard.similarity import SimilarityEngine, SimilarityMatch


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
    by_severity: dict[str, int]  # how many matches at each severity level
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
            "by_severity": self.by_severity,
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

        print("\n  SEVERITY DISTRIBUTION (of true positive matches)")
        for sev in ("high", "medium", "low"):
            count = self.by_severity.get(sev, 0)
            if count > 0:
                print(f"    {sev:<8s}: {count}")

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


def _extract_first_function(
    code: str, language: str, filepath: str
) -> ExtractedFunction | None:
    """Extract the first function from a code snippet using tree-sitter."""
    funcs = extract_functions_universal(filepath, source=code, language=language)
    return funcs[0] if funcs else None


def _build_function_key(func: ExtractedFunction) -> str:
    """Build a stable key for a function (filepath:name)."""
    return f"{func.filepath}::{func.name}"


class BenchmarkAdapter(ABC):
    """Abstract base class for benchmark dataset adapters.

    The evaluate() method mirrors the real `echo-guard scan` pipeline:
    1. Extract all functions from all benchmark pairs
    2. Index them ALL into a single SimilarityEngine
    3. Run find_all_matches() — the actual batch scan
    4. Map engine output back to expected pairs to compute metrics
    """

    def __init__(self, data_dir: Path | None = None):
        self.data_dir = data_dir or Path(__file__).parent / "data"

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @property
    @abstractmethod
    def dataset_id(self) -> str:
        ...

    @abstractmethod
    def is_available(self) -> bool:
        ...

    @abstractmethod
    def download(self, force: bool = False) -> None:
        ...

    @abstractmethod
    def load_pairs(self, max_pairs: int | None = None) -> list[BenchmarkPair]:
        ...

    def evaluate(
        self,
        threshold: float = 0.50,
        max_pairs: int | None = None,
        verbose: bool = False,
    ) -> BenchmarkResult:
        """Run evaluation using the real echo-guard scan pipeline.

        This is NOT a pair-by-pair evaluation. Instead:
        1. ALL functions are extracted and loaded into one SimilarityEngine
        2. find_all_matches() runs the real batch scan (LSH → TF-IDF → intent filter)
        3. We check which of our expected clone pairs were found by the engine
        4. Unexpected matches between non-clone functions count as false positives
        """
        pairs = self.load_pairs(max_pairs=max_pairs)

        t0 = time.perf_counter()

        # Step 1: Extract all functions and build the index
        # Each pair side gets a unique filepath so we can map matches back
        func_map: dict[str, ExtractedFunction] = {}  # filepath::name → func
        pair_func_keys: dict[str, tuple[str, str]] = {}  # pair_id → (key_a, key_b)
        skipped = 0

        engine = SimilarityEngine(
            lsh_threshold=0.15,  # Same as scan_for_redundancy
            similarity_threshold=threshold,
        )

        for pair in pairs:
            ext_a = _ext(pair.language_a)
            ext_b = _ext(pair.language_b)
            filepath_a = f"bench_a/{pair.pair_id}_a.{ext_a}"
            filepath_b = f"bench_b/{pair.pair_id}_b.{ext_b}"

            func_a = _extract_first_function(pair.code_a, pair.language_a, filepath_a)
            func_b = _extract_first_function(pair.code_b, pair.language_b, filepath_b)

            if func_a is None or func_b is None:
                skipped += 1
                continue

            key_a = _build_function_key(func_a)
            key_b = _build_function_key(func_b)

            func_map[key_a] = func_a
            func_map[key_b] = func_b
            pair_func_keys[pair.pair_id] = (key_a, key_b)

            engine.add_function(func_a)
            engine.add_function(func_b)

        # Step 2: Run the real batch scan — same as echo-guard scan
        all_matches = engine.find_all_matches(threshold=threshold)

        # Step 3: Build a set of matched function-key pairs from engine output
        matched_pairs: dict[tuple[str, str], SimilarityMatch] = {}
        for match in all_matches:
            key_src = _build_function_key(match.source_func)
            key_exist = _build_function_key(match.existing_func)
            # Normalize order for consistent lookup
            pair_key = tuple(sorted([key_src, key_exist]))
            # Keep highest-scoring match if duplicates
            if pair_key not in matched_pairs or match.similarity_score > matched_pairs[pair_key].similarity_score:
                matched_pairs[pair_key] = match

        # Step 4: Evaluate each benchmark pair against engine results
        overall = EvaluationMetrics()
        by_type: dict[str, EvaluationMetrics] = defaultdict(EvaluationMetrics)
        severity_counts: dict[str, int] = defaultdict(int)
        details: list[dict] = []

        for pair in pairs:
            if pair.pair_id not in pair_func_keys:
                continue  # Skipped during extraction

            key_a, key_b = pair_func_keys[pair.pair_id]
            lookup_key = tuple(sorted([key_a, key_b]))
            match = matched_pairs.get(lookup_key)

            predicted = match is not None
            score = match.similarity_score if match else 0.0
            severity = match.severity if match else None
            match_type = match.match_type if match else None

            m = by_type[pair.clone_type]
            if pair.is_clone and predicted:
                overall.tp += 1
                m.tp += 1
                verdict = "TP"
                severity_counts[severity] += 1
            elif pair.is_clone and not predicted:
                overall.fn += 1
                m.fn += 1
                verdict = "FN"
            elif not pair.is_clone and predicted:
                overall.fp += 1
                m.fp += 1
                verdict = "FP"
                severity_counts[severity] += 1
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
                "severity": severity,
                "match_type": match_type,
                "verdict": verdict,
            }
            details.append(detail)

            if verbose:
                sev_str = f" [{severity}]" if severity else ""
                icon = "+" if verdict in ("TP", "TN") else "X"
                print(
                    f"  [{icon}] {verdict} {pair.pair_id:<12s} "
                    f"score={score:.3f}{sev_str}  ({pair.clone_type})"
                )

        # Also check for unexpected matches (engine found matches between
        # functions from different pairs that we didn't label as clones)
        expected_keys = set()
        for pair in pairs:
            if pair.pair_id in pair_func_keys:
                key_a, key_b = pair_func_keys[pair.pair_id]
                expected_keys.add(tuple(sorted([key_a, key_b])))

        unexpected_matches = []
        for pair_key, match in matched_pairs.items():
            if pair_key not in expected_keys:
                unexpected_matches.append(match)

        if unexpected_matches and verbose:
            print(f"\n  UNEXPECTED MATCHES ({len(unexpected_matches)} cross-pair matches)")
            for m in unexpected_matches[:10]:
                print(
                    f"    {m.source_func.qualified_name} <-> {m.existing_func.qualified_name}"
                    f"  score={m.similarity_score:.3f} [{m.severity}]"
                )

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
            by_severity=dict(severity_counts),
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

        # Categorize successful detections by severity
        severity_dist = defaultdict(int)
        for d in t4_successes:
            severity_dist[d.get("severity", "unknown")] += 1

        analysis = {
            "type4_total": t4_metrics.total,
            "type4_detected": t4_metrics.tp,
            "type4_missed": t4_metrics.fn,
            "type4_recall": round(t4_metrics.recall, 4),
            "detection_gap_percentage": round(1.0 - t4_metrics.recall, 4),
            "severity_of_detections": dict(severity_dist),
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
