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
        "| [GPTCloneBench](https://github.com/srlabUsask/GPTCloneBench) | Python, Java | T3-T4 | AI-generated clone pairs |",
        "| [POJ-104](https://github.com/microsoft/CodeXGLUE/tree/main/Code-Code/Clone-detection-POJ-104) | C | T4 (semantic) | Competitive programming solutions |",
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
        "Benchmarks use the same two-tier pipeline as `echo-guard scan`:",
        "",
        "1. All benchmark functions are extracted via tree-sitter (same as `echo-guard index`)",
        "2. All functions are embedded via CodeSage-small (ONNX INT8, 1024-dim vectors)",
        "3. ALL functions are loaded into a single `SimilarityEngine`",
        "4. `find_all_matches()` runs the two-tier pipeline:",
        "   - **Tier 1**: AST hash grouping → Type-1/Type-2 exact clone detection",
        "   - **Tier 2**: Embedding cosine similarity with per-language thresholds → Type-3/Type-4 detection",
        "   - **Intent filters**: Domain-aware false positive suppression",
        "5. Engine output is mapped back to labeled pairs to compute precision/recall/F1",
        "",
        "This matches real-world usage where the engine must find correct matches among",
        "many candidate functions while avoiding false positives from unrelated code.",
        "",
        "### Per-language embedding thresholds",
        "",
        "| Language | Threshold |",
        "|----------|-----------|",
        "| Python | 0.94 |",
        "| Java | 0.81 |",
        "| JavaScript | 0.85 |",
        "| C/C++ | 0.83 |",
        "| Go | 0.81 |",
        "",
        "## Reproducing",
        "",
        "```bash",
        "# Install with language support",
        "pip install -e \".[languages]\"",
        "",
        "# Run all benchmarks",
        "python -m benchmarks.runner",
        "",
        "# Run specific benchmark with per-pair details",
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
    ]

    # Severity distribution
    if result.by_severity:
        sev_parts = []
        for sev in ("high", "medium"):
            count = result.by_severity.get(sev, 0)
            if count > 0:
                sev_parts.append(f"{sev}: {count}")
        if sev_parts:
            lines.append(f"Severity distribution: {', '.join(sev_parts)}")
            lines.append("")

    lines.extend([
        "| Clone Type | Precision | Recall | F1 | TP | FP | TN | FN |",
        "|------------|-----------|--------|----|----|----|----|-----|",
    ])

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
        "## Type-4 (Semantic) Detection Analysis",
        "",
        "Type-4 clones have the same semantics but completely different implementation.",
        "Echo Guard uses CodeSage-small embeddings (1024-dim) with per-language similarity",
        "thresholds to detect these. Performance varies by language and dataset.",
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

    return lines


def generate_readme_section(results: list[BenchmarkResult]) -> str:
    """Generate a concise benchmark section for the README."""
    lines = [
        "## Benchmark Results",
        "",
        "Echo Guard is evaluated against established academic clone detection benchmarks",
        "using the two-tier pipeline (AST hash + UniXcoder embeddings).",
        "Full results: [BENCHMARKS.md](docs/BENCHMARKS.md)",
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
        "**Detection by clone type:**",
        "- Type-1/2 (exact/renamed): 100% via AST hash matching",
        "- Type-3 (modified): Per-language embedding thresholds",
        "- Type-4 (semantic): UniXcoder cosine similarity",
        "- Cross-language: Supported across 9 languages",
        "",
    ])

    return "\n".join(lines)
