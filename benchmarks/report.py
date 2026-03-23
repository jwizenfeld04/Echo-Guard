"""Benchmark report generation.

Generates markdown reports and README sections from benchmark results.
"""

from __future__ import annotations

from datetime import date

from benchmarks.base import BenchmarkResult


def generate_markdown_report(results: list[BenchmarkResult]) -> str:
    """Generate a full markdown benchmark report."""
    lines = [
        "# Echo Guard Benchmark Results",
        "",
        f"Generated: {date.today().isoformat()}",
        "",
        "## Overview",
        "",
        "Echo Guard is evaluated against three established clone detection benchmarks:",
        "",
        "| Benchmark | Language | Clone Types | Focus |",
        "|-----------|----------|-------------|-------|",
        "| [BigCloneBench](https://github.com/clonebench/BigCloneBench) | Java | T1-T4 | Largest academic benchmark (8M+ pairs) |",
        "| [GPTCloneBench](https://github.com/AluaBa662/GPTCloneBench) | Python, Java | T1-T4 | AI-generated clone pairs |",
        "| [POJ-104](https://github.com/microsoft/CodeXGLUE/tree/main/Code-Code/Clone-detection-POJ-104) | C/C++ | T4 (semantic) | Competitive programming solutions |",
        "",
        "## Results Summary",
        "",
    ]

    # Consolidated table
    lines.append("| Dataset | Precision | Recall | F1 | Type-4 Recall | Pairs |")
    lines.append("|---------|-----------|--------|----|----|-------|")
    for r in results:
        t4 = r.by_clone_type.get("type4")
        t4_recall = f"{t4.recall:.1%}" if t4 and t4.total > 0 else "N/A"
        lines.append(
            f"| {r.dataset_name} | {r.overall.precision:.1%} "
            f"| {r.overall.recall:.1%} | {r.overall.f1:.1%} "
            f"| {t4_recall} | {r.pairs_evaluated} |"
        )
    lines.append("")

    # Per-dataset detailed results
    for r in results:
        lines.extend(_format_dataset_section(r))

    # Type-4 gap analysis
    lines.extend(_format_gap_analysis(results))

    # Methodology
    lines.extend([
        "## Methodology",
        "",
        "- Each pair is evaluated independently using Echo Guard's `SimilarityEngine`",
        "- Function A is indexed, then Function B is queried against it",
        "- LSH threshold set to 0.2 (permissive) to maximize recall for evaluation",
        "- Results measured at the configurable similarity threshold (default 0.50)",
        "- Curated subsets represent the distribution of clone types in the original datasets",
        "",
        "## Reproducing",
        "",
        "```bash",
        "# Run all benchmarks",
        "python -m benchmarks.runner",
        "",
        "# Run specific benchmark",
        "python -m benchmarks.runner --dataset bigclonebench --verbose",
        "",
        "# Threshold sweep",
        "python -m benchmarks.runner --sweep --json sweep_results.json",
        "",
        "# Generate this report",
        "python -m benchmarks.runner --report",
        "```",
        "",
    ])

    return "\n".join(lines)


def _format_dataset_section(result: BenchmarkResult) -> list[str]:
    """Format a detailed section for one dataset."""
    lines = [
        f"### {result.dataset_name}",
        "",
        f"Threshold: {result.threshold} | "
        f"Pairs evaluated: {result.pairs_evaluated} | "
        f"Time: {result.elapsed_seconds:.1f}s",
        "",
        "| Clone Type | Precision | Recall | F1 | TP | FP | TN | FN |",
        "|------------|-----------|--------|----|----|----|----|-----|",
    ]

    for ctype, m in sorted(result.by_clone_type.items()):
        lines.append(
            f"| {ctype} | {m.precision:.1%} | {m.recall:.1%} | {m.f1:.1%} "
            f"| {m.tp} | {m.fp} | {m.tn} | {m.fn} |"
        )
    lines.append("")
    return lines


def _format_gap_analysis(results: list[BenchmarkResult]) -> list[str]:
    """Format the Type-4 gap analysis section."""
    lines = [
        "## Type-4 (Semantic) Detection Gap Analysis",
        "",
        "Type-4 clones have the same semantics but completely different implementation.",
        "This is the hardest clone type to detect with structural/textual methods.",
        "",
    ]

    has_gaps = False
    for r in results:
        if r.type4_gap_analysis:
            has_gaps = True
            gap = r.type4_gap_analysis
            lines.append(f"### {r.dataset_name}")
            lines.append("")
            lines.append(f"- **Total Type-4 pairs:** {gap.get('type4_total', 0)}")
            lines.append(f"- **Detected:** {gap.get('type4_detected', 0)}")
            lines.append(f"- **Missed:** {gap.get('type4_missed', 0)}")
            lines.append(f"- **Recall:** {gap.get('type4_recall', 0):.1%}")
            lines.append(f"- **Detection gap:** {gap.get('detection_gap_percentage', 0):.1%}")

            avg_success = gap.get("avg_score_successes", 0)
            avg_fail = gap.get("avg_score_failures", 0)
            if avg_success > 0:
                lines.append(f"- **Avg score (detected):** {avg_success:.3f}")
            if avg_fail > 0:
                lines.append(f"- **Avg score (missed):** {avg_fail:.3f}")

            rec = gap.get("recommendation", "")
            if rec:
                lines.append(f"- **Recommendation:** {rec}")
            lines.append("")

    if not has_gaps:
        lines.append("No Type-4 pairs were evaluated.")
        lines.append("")

    lines.extend([
        "### Implications for Phase 2",
        "",
        "The Type-4 detection gaps identified above confirm the need for Phase 2's",
        "semantic detection upgrade. Code embeddings (CodeBERT, UniXcoder) are expected",
        "to significantly improve Type-4 recall by capturing semantic similarity that",
        "TF-IDF and structural methods miss.",
        "",
        "Key areas where embeddings would help:",
        "- Recursive vs iterative implementations of the same algorithm",
        "- Different data structure choices for the same operation",
        "- Algorithmic variants (e.g., bubble sort vs insertion sort for sorting)",
        "",
    ])

    return lines


def generate_readme_section(results: list[BenchmarkResult]) -> str:
    """Generate a concise benchmark section for the README."""
    lines = [
        "## Benchmark Results",
        "",
        "Echo Guard is evaluated against established academic clone detection benchmarks.",
        "Full results: [BENCHMARKS.md](BENCHMARKS.md)",
        "",
        "| Benchmark | Precision | Recall | F1 | Type-4 Recall |",
        "|-----------|-----------|--------|----|----|",
    ]

    for r in results:
        t4 = r.by_clone_type.get("type4")
        t4_recall = f"{t4.recall:.1%}" if t4 and t4.total > 0 else "N/A"
        lines.append(
            f"| {r.dataset_name} | {r.overall.precision:.1%} "
            f"| {r.overall.recall:.1%} | {r.overall.f1:.1%} "
            f"| {t4_recall} |"
        )

    lines.extend([
        "",
        "**Clone type detection strength:**",
        "- Type-1 (exact copies): Excellent",
        "- Type-2 (renamed identifiers): Strong",
        "- Type-3 (modified statements): Good",
        "- Type-4 (semantic clones): Limited (Phase 2 will add code embeddings)",
        "- Cross-language: Supported across 9 languages",
        "",
    ])

    return "\n".join(lines)
