#!/usr/bin/env python3
"""Unified benchmark runner for Echo Guard.

Runs all benchmark suites and produces consolidated results.

Usage:
    python -m benchmarks.runner                          # Run all benchmarks
    python -m benchmarks.runner --dataset bigclonebench   # Single benchmark
    python -m benchmarks.runner --sweep                   # Threshold sweep
    python -m benchmarks.runner --report                  # Generate report
    python -m benchmarks.runner --json results.json       # Export JSON
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmarks.base import BenchmarkAdapter, BenchmarkResult
from benchmarks.bigclonebench import BigCloneBenchAdapter
from benchmarks.gptclonebench import GPTCloneBenchAdapter
from benchmarks.poj104 import POJ104Adapter
from benchmarks.report import generate_markdown_report, generate_readme_section


# Registry of all available benchmark adapters
ADAPTERS: dict[str, type[BenchmarkAdapter]] = {
    "bigclonebench": BigCloneBenchAdapter,
    "gptclonebench": GPTCloneBenchAdapter,
    "poj104": POJ104Adapter,
}


def get_adapter(name: str, data_dir: Path | None = None) -> BenchmarkAdapter:
    """Get a benchmark adapter by name."""
    if name not in ADAPTERS:
        raise ValueError(f"Unknown benchmark: {name}. Available: {list(ADAPTERS.keys())}")
    return ADAPTERS[name](data_dir=data_dir)


def run_all_benchmarks(
    threshold: float = 0.50,
    max_pairs: int | None = None,
    verbose: bool = False,
    data_dir: Path | None = None,
) -> list[BenchmarkResult]:
    """Run all benchmark suites and return results."""
    results = []
    for name, adapter_cls in ADAPTERS.items():
        print(f"\n{'=' * 72}")
        print(f"  Running {name}...")
        print(f"{'=' * 72}")
        adapter = adapter_cls(data_dir=data_dir)
        result = adapter.evaluate(
            threshold=threshold,
            max_pairs=max_pairs,
            verbose=verbose,
        )
        result.print_summary()
        results.append(result)

    _print_consolidated_summary(results, threshold)
    return results


def run_threshold_sweep(
    datasets: list[str] | None = None,
    thresholds: list[float] | None = None,
    max_pairs: int | None = None,
    data_dir: Path | None = None,
) -> dict[str, list[BenchmarkResult]]:
    """Sweep thresholds across one or more datasets."""
    if thresholds is None:
        thresholds = [0.30, 0.40, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95]
    if datasets is None:
        datasets = list(ADAPTERS.keys())

    all_results: dict[str, list[BenchmarkResult]] = {}

    for name in datasets:
        adapter = get_adapter(name, data_dir=data_dir)
        results: list[BenchmarkResult] = []

        print(f"\n{'=' * 72}")
        print(f"  Threshold sweep: {name}")
        print(f"{'=' * 72}")

        for t in thresholds:
            result = adapter.evaluate(threshold=t, max_pairs=max_pairs, verbose=False)
            results.append(result)

        # Print sweep table
        print(
            f"\n    {'Threshold':>9s} {'Prec':>7s} {'Recall':>7s} {'F1':>7s} {'Acc':>7s}"
            f"  {'TP':>4s} {'FP':>4s} {'TN':>4s} {'FN':>4s}"
        )
        print(
            f"    {'-' * 9} {'-' * 7} {'-' * 7} {'-' * 7} {'-' * 7}"
            f"  {'-' * 4} {'-' * 4} {'-' * 4} {'-' * 4}"
        )

        best_f1 = 0.0
        best_t = 0.0
        for r in results:
            o = r.overall
            if o.f1 > best_f1:
                best_f1 = o.f1
                best_t = r.threshold
            print(
                f"    {r.threshold:>9.2f} {o.precision:>6.1%} {o.recall:>6.1%} "
                f"{o.f1:>6.1%} {o.accuracy:>6.1%}"
                f"  {o.tp:>4d} {o.fp:>4d} {o.tn:>4d} {o.fn:>4d}"
            )

        print(f"\n    Best F1: {best_f1:.1%} at threshold={best_t}")
        all_results[name] = results

    return all_results


def _print_consolidated_summary(
    results: list[BenchmarkResult], threshold: float
) -> None:
    """Print a consolidated summary across all benchmarks."""
    print(f"\n{'=' * 72}")
    print(f"  CONSOLIDATED RESULTS — threshold={threshold}")
    print(f"{'=' * 72}")

    print(
        f"\n    {'Dataset':<20s} {'Prec':>7s} {'Recall':>7s} {'F1':>7s}"
        f"  {'T4 Recall':>9s}  {'Pairs':>6s}"
    )
    print(
        f"    {'-' * 20} {'-' * 7} {'-' * 7} {'-' * 7}"
        f"  {'-' * 9}  {'-' * 6}"
    )

    for r in results:
        t4 = r.by_clone_type.get("type4")
        t4_recall = f"{t4.recall:.1%}" if t4 and t4.total > 0 else "N/A"
        print(
            f"    {r.dataset_name:<20s} {r.overall.precision:>6.1%} {r.overall.recall:>6.1%} "
            f"{r.overall.f1:>6.1%}  {t4_recall:>9s}  {r.pairs_evaluated:>6d}"
        )

    # Overall Type-4 gap analysis
    print("\n  TYPE-4 DETECTION GAP SUMMARY")
    for r in results:
        if r.type4_gap_analysis:
            gap = r.type4_gap_analysis
            print(
                f"    {r.dataset_name}: {gap.get('type4_detected', 0)}/{gap.get('type4_total', 0)} "
                f"detected (recall={gap.get('type4_recall', 0):.1%})"
            )
            print(f"      → {gap.get('recommendation', '')}")


def main():
    parser = argparse.ArgumentParser(
        description="Echo Guard benchmark runner — evaluate against academic datasets"
    )
    parser.add_argument(
        "--dataset",
        choices=list(ADAPTERS.keys()),
        help="Run a specific benchmark (default: all)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.50,
        help="Similarity threshold (default: 0.50)",
    )
    parser.add_argument(
        "--max-pairs",
        type=int,
        help="Limit number of pairs to evaluate (for quick testing)",
    )
    parser.add_argument(
        "--sweep",
        action="store_true",
        help="Sweep thresholds from 0.30 to 0.95",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show per-pair results",
    )
    parser.add_argument(
        "--json",
        type=str,
        metavar="PATH",
        help="Export results to JSON file",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Generate markdown benchmark report",
    )
    parser.add_argument(
        "--readme-section",
        action="store_true",
        help="Generate README benchmark section",
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        help="Directory for benchmark datasets",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir) if args.data_dir else None
    datasets = [args.dataset] if args.dataset else None

    if args.sweep:
        all_results = run_threshold_sweep(
            datasets=datasets,
            max_pairs=args.max_pairs,
            data_dir=data_dir,
        )
        if args.json:
            _export_sweep_json(all_results, Path(args.json))
        if args.report:
            # Use results at default threshold for report
            best_results = []
            for ds_results in all_results.values():
                # Find best F1 result
                best = max(ds_results, key=lambda r: r.overall.f1)
                best_results.append(best)
            report = generate_markdown_report(best_results)
            report_path = Path(__file__).parent.parent / "docs" / "BENCHMARKS.md"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(report)
            print(f"\n  Report written to {report_path}")
        return

    if args.dataset:
        adapter = get_adapter(args.dataset, data_dir=data_dir)
        result = adapter.evaluate(
            threshold=args.threshold,
            max_pairs=args.max_pairs,
            verbose=args.verbose,
        )
        result.print_summary()
        results = [result]
    else:
        results = run_all_benchmarks(
            threshold=args.threshold,
            max_pairs=args.max_pairs,
            verbose=args.verbose,
            data_dir=data_dir,
        )

    if args.json:
        _export_results_json(results, Path(args.json))

    if args.report:
        report = generate_markdown_report(results)
        report_path = Path(__file__).parent.parent / "docs" / "BENCHMARKS.md"
        report_path.write_text(report)
        print(f"\n  Report written to {report_path}")

    if args.readme_section:
        section = generate_readme_section(results)
        print(f"\n{section}")


def _export_results_json(results: list[BenchmarkResult], path: Path) -> None:
    data = [r.to_dict() for r in results]
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"\n  Results exported to {path}")


def _export_sweep_json(
    all_results: dict[str, list[BenchmarkResult]], path: Path
) -> None:
    data = {
        name: [r.to_dict() for r in results]
        for name, results in all_results.items()
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"\n  Sweep results exported to {path}")


if __name__ == "__main__":
    main()
