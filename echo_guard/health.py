"""Codebase health score calculation.

Computes a 0-100 score representing codebase redundancy health.
Higher = less redundant = healthier.

Scoring formula:
  score = 100 - penalty

Where penalty is based on:
  - Number of high-severity matches (×5 per match — Type-1/2 exact, Type-3 strong)
  - Number of medium-severity matches (×2 per match — Type-3 moderate, Type-4 semantic)
  - Normalized by total function count (larger codebases get proportional scaling)

A codebase with zero redundancy scores 100.
A codebase where 20%+ of functions are redundant scores below 50.
"""

from __future__ import annotations

from echo_guard.index import FunctionIndex
from echo_guard.similarity import SimilarityMatch


def compute_health_score(
    matches: list[SimilarityMatch],
    total_functions: int,
) -> dict:
    """Compute a 0-100 health score from scan results.

    Returns a dict with:
      score: 0-100 integer
      grade: A/B/C/D/F letter grade
      breakdown: detailed component scores
      recommendations: list of actionable items
    """
    if total_functions == 0:
        return {
            "score": 100,
            "grade": "A",
            "breakdown": {},
            "recommendations": ["No functions indexed. Run `echo-guard index` first."],
        }

    high = sum(1 for m in matches if m.severity == "high")
    medium = sum(1 for m in matches if m.severity == "medium")

    # Weighted penalty per match
    raw_penalty = (high * 5.0) + (medium * 2.0)

    # Normalize: in a 100-function codebase, 10 high matches = 50 penalty
    # Scale factor prevents small codebases from being unfairly punished
    scale = max(total_functions / 50.0, 1.0)
    normalized_penalty = raw_penalty / scale

    # Clamp to 0-100
    score = max(0, min(100, round(100 - normalized_penalty)))

    # Letter grade
    if score >= 90:
        grade = "A"
    elif score >= 75:
        grade = "B"
    elif score >= 60:
        grade = "C"
    elif score >= 40:
        grade = "D"
    else:
        grade = "F"

    # Redundancy rate
    unique_functions_flagged = set()
    for m in matches:
        unique_functions_flagged.add(m.source_func.qualified_name)
        unique_functions_flagged.add(m.existing_func.qualified_name)
    redundancy_rate = len(unique_functions_flagged) / max(total_functions, 1) * 100

    # Cross-language stats
    cross_lang = sum(1 for m in matches if m.reuse_type == "reference_only")
    same_lang = sum(1 for m in matches if m.reuse_type == "direct_import")
    compatible = sum(1 for m in matches if m.reuse_type == "compatible_import")

    # Scope stats
    private_matches = sum(
        1 for m in matches if m.existing_func.visibility == "private"
    )

    breakdown = {
        "total_redundancies": len(matches),
        "high": high,
        "medium": medium,
        "total_functions": total_functions,
        "redundancy_rate_pct": round(redundancy_rate, 1),
        "same_language_matches": same_lang,
        "compatible_runtime_matches": compatible,
        "cross_language_matches": cross_lang,
        "private_scope_matches": private_matches,
    }

    # Generate recommendations
    recommendations = _generate_recommendations(
        score, high, medium, cross_lang, private_matches,
        redundancy_rate, matches,
    )

    return {
        "score": score,
        "grade": grade,
        "breakdown": breakdown,
        "recommendations": recommendations,
    }


def _generate_recommendations(
    score: int, high: int, medium: int,
    cross_lang: int, private_matches: int,
    redundancy_rate: float, matches: list[SimilarityMatch],
) -> list[str]:
    """Generate actionable recommendations based on the health score."""
    recs = []

    if high > 0:
        # Find the most impactful files
        file_counts: dict[str, int] = {}
        for m in matches:
            if m.severity == "high":
                f = m.source_func.filepath
                file_counts[f] = file_counts.get(f, 0) + 1
        worst_file = max(file_counts, key=lambda f: file_counts[f]) if file_counts else None
        recs.append(
            f"{high} exact structural duplicate(s) found. "
            f"These are the easiest wins — replace with imports."
            + (f" Start with {worst_file} ({file_counts[worst_file]} duplicates)." if worst_file else "")
        )

    if medium > 0:
        recs.append(
            f"{medium} near-duplicate(s) found. Review these — most can likely be "
            f"consolidated into shared utilities."
        )

    if cross_lang > 0:
        recs.append(
            f"{cross_lang} cross-language redundanc{'y' if cross_lang == 1 else 'ies'} detected. "
            f"The same logic is implemented in multiple languages. "
            f"Consider a shared service or pick one canonical language for shared utilities."
        )

    if private_matches > 0:
        recs.append(
            f"{private_matches} match(es) involve private/internal functions. "
            f"Consider making the canonical version public if it's generally useful."
        )

    if redundancy_rate > 20:
        recs.append(
            f"Redundancy rate is {redundancy_rate:.0f}% — consider a dedicated refactoring sprint "
            f"to consolidate shared utilities."
        )

    if score >= 90 and not recs:
        recs.append("Codebase is clean. Keep Echo Guard running as a pre-commit hook to maintain this.")

    return recs


def record_health(index: FunctionIndex, score_data: dict) -> None:
    """Save the health score to history for trend tracking."""
    index.record_health_score(
        score=score_data["score"],
        details=score_data["breakdown"],
    )


def get_trend(index: FunctionIndex, limit: int = 10) -> list[dict]:
    """Get recent health score history."""
    return index.get_health_history(limit)
