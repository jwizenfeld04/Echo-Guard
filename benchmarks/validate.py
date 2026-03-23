#!/usr/bin/env python3
"""Validation harness: measures Echo-Guard precision/recall/F1 against ground truth.

Runs each labeled clone pair through the SimilarityEngine and compares
the engine's decision (match/no-match at a given threshold) against the
known label. Reports metrics overall and per clone type.

Usage:
    python -m benchmarks.validate                       # default threshold 0.50
    python -m benchmarks.validate --threshold 0.6       # sweep a specific threshold
    python -m benchmarks.validate --sweep               # sweep thresholds 0.3–0.95
    python -m benchmarks.validate --dataset gptclonebench  # use GPTCloneBench
    python -m benchmarks.validate --dataset gptclonebench --sweep
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from echo_guard.languages import ExtractedFunction, extract_functions_universal
from echo_guard.similarity import SimilarityEngine, _tokenize_code

from benchmarks.ground_truth import ClonePair, get_all_pairs

try:
    from benchmarks.gptclonebench import load_gptclonebench
except ImportError:
    load_gptclonebench = None


# ── Metrics ──────────────────────────────────────────────────────────────


@dataclass
class Metrics:
    tp: int = 0  # true positive: is_clone=True and engine found match
    fp: int = 0  # false positive: is_clone=False but engine found match
    tn: int = 0  # true negative: is_clone=False and engine found no match
    fn: int = 0  # false negative: is_clone=True but engine found no match

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) > 0 else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) > 0 else 0.0

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


def _extract_first_function(
    code: str, language: str, filepath: str
) -> ExtractedFunction | None:
    """Extract the first function from a code snippet using tree-sitter."""
    funcs = extract_functions_universal(filepath, source=code, language=language)
    return funcs[0] if funcs else None


def _evaluate_pair(
    pair: ClonePair,
    threshold: float,
) -> tuple[bool, float, str | None]:
    """Evaluate a single pair. Returns (predicted_is_clone, score, match_type)."""
    func_a = _extract_first_function(pair.code_a, pair.lang_a, pair.file_a)
    func_b = _extract_first_function(pair.code_b, pair.lang_b, pair.file_b)

    if func_a is None or func_b is None:
        # If we can't parse, treat as no match
        return False, 0.0, None

    # Build a mini engine with just func_a, then query with func_b
    engine = SimilarityEngine(
        lsh_threshold=0.2,  # low LSH threshold to maximize recall for evaluation
        similarity_threshold=threshold,
        num_perm=128,
    )
    engine.add_function(func_a)

    # Use find_similar with a very low threshold to get the raw score,
    # then we decide match/no-match at the evaluation threshold
    try:
        matches = engine.find_similar(func_b, threshold=0.1)
    except (ValueError, Exception):
        # Can happen with empty vocabularies or degenerate code
        return False, 0.0, None

    if matches:
        best = matches[0]
        return (
            best.similarity_score >= threshold,
            best.similarity_score,
            best.match_type,
        )
    return False, 0.0, None


def run_validation(
    threshold: float = 0.50,
    verbose: bool = False,
    dataset: str = "synthetic",
    max_pairs: int = 300,
) -> dict:
    """Run validation against ground-truth pairs at a given threshold.

    Args:
        dataset: "synthetic" (built-in 25 pairs) or "gptclonebench" (real dataset)
        max_pairs: Max pairs for GPTCloneBench (positives + negatives)
    """
    if dataset == "gptclonebench":
        if load_gptclonebench is None:
            raise ImportError("GPTCloneBench adapter not available")
        pos_count = int(max_pairs * 0.67)
        neg_count = max_pairs - pos_count
        pairs = load_gptclonebench(
            language="python",
            max_positive_pairs=pos_count,
            max_negative_pairs=neg_count,
        )
    else:
        pairs = get_all_pairs()
    overall = Metrics()
    by_type: dict[str, Metrics] = defaultdict(Metrics)
    details: list[dict] = []

    t0 = time.perf_counter()

    for pair in pairs:
        predicted_clone, score, match_type = _evaluate_pair(pair, threshold)

        # Update metrics
        m = by_type[pair.clone_type]
        if pair.is_clone and predicted_clone:
            overall.tp += 1
            m.tp += 1
            verdict = "TP"
        elif pair.is_clone and not predicted_clone:
            overall.fn += 1
            m.fn += 1
            verdict = "FN"
        elif not pair.is_clone and predicted_clone:
            overall.fp += 1
            m.fp += 1
            verdict = "FP"
        else:
            overall.tn += 1
            m.tn += 1
            verdict = "TN"

        detail = {
            "id": pair.id,
            "clone_type": pair.clone_type,
            "is_clone": pair.is_clone,
            "predicted": predicted_clone,
            "score": round(score, 4),
            "match_type": match_type,
            "verdict": verdict,
        }
        details.append(detail)

        if verbose:
            icon = {"TP": "+", "TN": "+", "FP": "X", "FN": "X"}[verdict]
            print(
                f"  [{icon}] {verdict} {pair.id:<8s} "
                f"score={score:.3f} {'>' if predicted_clone else '<'}= {threshold:.2f}  "
                f"({pair.clone_type}) {pair.description[:60]}"
            )

    elapsed = time.perf_counter() - t0

    result = {
        "threshold": threshold,
        "elapsed_s": round(elapsed, 3),
        "total_pairs": len(pairs),
        "overall": {
            "precision": round(overall.precision, 4),
            "recall": round(overall.recall, 4),
            "f1": round(overall.f1, 4),
            "accuracy": round(overall.accuracy, 4),
            "tp": overall.tp,
            "fp": overall.fp,
            "tn": overall.tn,
            "fn": overall.fn,
        },
        "by_type": {},
        "details": details,
    }

    for ctype, m in sorted(by_type.items()):
        result["by_type"][ctype] = {
            "precision": round(m.precision, 4),
            "recall": round(m.recall, 4),
            "f1": round(m.f1, 4),
            "tp": m.tp,
            "fp": m.fp,
            "tn": m.tn,
            "fn": m.fn,
            "total": m.total,
        }

    return result


def print_results(result: dict) -> None:
    """Pretty-print validation results."""
    threshold = result["threshold"]
    o = result["overall"]

    print(f"\n{'='*72}")
    print(f"  ECHO-GUARD VALIDATION — threshold={threshold}")
    print(f"{'='*72}")
    print(f"\n  Pairs: {result['total_pairs']}  |  Time: {result['elapsed_s']}s")
    print(f"\n  OVERALL")
    print(
        f"    Precision:  {o['precision']:.1%}  ({o['tp']} TP / {o['tp'] + o['fp']} predicted clones)"
    )
    print(
        f"    Recall:     {o['recall']:.1%}  ({o['tp']} TP / {o['tp'] + o['fn']} actual clones)"
    )
    print(f"    F1 Score:   {o['f1']:.1%}")
    print(f"    Accuracy:   {o['accuracy']:.1%}")
    print(f"\n    Confusion Matrix:")
    print(f"                  Predicted Clone  Predicted Not-Clone")
    print(f"      Actual Clone      {o['tp']:>4d}              {o['fn']:>4d}")
    print(f"      Actual Not-Clone  {o['fp']:>4d}              {o['tn']:>4d}")

    print(f"\n  BY CLONE TYPE")
    print(
        f"    {'Type':<12s} {'Prec':>7s} {'Recall':>7s} {'F1':>7s}  {'TP':>3s} {'FP':>3s} {'TN':>3s} {'FN':>3s}"
    )
    print(f"    {'-'*12} {'-'*7} {'-'*7} {'-'*7}  {'-'*3} {'-'*3} {'-'*3} {'-'*3}")
    for ctype, m in sorted(result["by_type"].items()):
        print(
            f"    {ctype:<12s} {m['precision']:>6.1%} {m['recall']:>6.1%} {m['f1']:>6.1%}"
            f"  {m['tp']:>3d} {m['fp']:>3d} {m['tn']:>3d} {m['fn']:>3d}"
        )

    # Show failures
    failures = [d for d in result["details"] if d["verdict"] in ("FP", "FN")]
    if failures:
        print(f"\n  FAILURES ({len(failures)})")
        for d in failures:
            print(
                f"    {d['verdict']} {d['id']:<8s} score={d['score']:.3f}  "
                f"type={d['clone_type']}"
            )


def sweep_thresholds(
    thresholds: list[float] | None = None,
    verbose: bool = False,
    dataset: str = "synthetic",
    max_pairs: int = 300,
) -> list[dict]:
    """Run validation across multiple thresholds to find the optimal one."""
    if thresholds is None:
        thresholds = [
            0.30,
            0.40,
            0.50,
            0.55,
            0.60,
            0.65,
            0.70,
            0.75,
            0.80,
            0.85,
            0.90,
            0.95,
        ]

    results = []
    for t in thresholds:
        r = run_validation(
            threshold=t, verbose=False, dataset=dataset, max_pairs=max_pairs
        )
        results.append(r)

    # Print sweep table
    print(f"\n{'='*72}")
    print(f"  THRESHOLD SWEEP")
    print(f"{'='*72}")
    print(
        f"\n    {'Threshold':>9s} {'Prec':>7s} {'Recall':>7s} {'F1':>7s} {'Acc':>7s}  {'TP':>3s} {'FP':>3s} {'TN':>3s} {'FN':>3s}"
    )
    print(
        f"    {'-'*9} {'-'*7} {'-'*7} {'-'*7} {'-'*7}  {'-'*3} {'-'*3} {'-'*3} {'-'*3}"
    )

    best_f1 = 0.0
    best_threshold = 0.0
    for r in results:
        o = r["overall"]
        marker = ""
        if o["f1"] > best_f1:
            best_f1 = o["f1"]
            best_threshold = r["threshold"]
        print(
            f"    {r['threshold']:>9.2f} {o['precision']:>6.1%} {o['recall']:>6.1%} "
            f"{o['f1']:>6.1%} {o['accuracy']:>6.1%}"
            f"  {o['tp']:>3d} {o['fp']:>3d} {o['tn']:>3d} {o['fn']:>3d}"
        )

    print(f"\n    Best F1: {best_f1:.1%} at threshold={best_threshold}")

    # Print per-type recall at best threshold
    best_result = next(r for r in results if r["threshold"] == best_threshold)
    print(f"\n    Per-type recall at best threshold ({best_threshold}):")
    for ctype, m in sorted(best_result["by_type"].items()):
        bar = "#" * int(m["recall"] * 20)
        print(f"      {ctype:<12s} {m['recall']:>6.1%} |{bar}")

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Echo-Guard validation against ground truth"
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.50,
        help="Similarity threshold (default: 0.50)",
    )
    parser.add_argument(
        "--sweep", action="store_true", help="Sweep thresholds 0.3–0.95"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Show each pair result"
    )
    parser.add_argument("--json", action="store_true", help="Output JSON results")
    parser.add_argument(
        "--dataset",
        choices=["synthetic", "gptclonebench"],
        default="synthetic",
        help="Dataset to validate against (default: synthetic)",
    )
    parser.add_argument(
        "--max-pairs",
        type=int,
        default=300,
        help="Max pairs for GPTCloneBench (default: 300)",
    )
    args = parser.parse_args()

    if args.sweep:
        results = sweep_thresholds(
            verbose=args.verbose,
            dataset=args.dataset,
            max_pairs=args.max_pairs,
        )
        if args.json:
            output_path = Path(__file__).parent.parent / "validation_results.json"
            with open(output_path, "w") as f:
                json.dump(results, f, indent=2)
            print(f"\n  Full results: {output_path}")
    else:
        result = run_validation(
            threshold=args.threshold,
            verbose=args.verbose,
            dataset=args.dataset,
            max_pairs=args.max_pairs,
        )
        print_results(result)
        if args.json:
            output_path = Path(__file__).parent.parent / "validation_results.json"
            with open(output_path, "w") as f:
                json.dump(result, f, indent=2)
            print(f"\n  Full results: {output_path}")


if __name__ == "__main__":
    main()
