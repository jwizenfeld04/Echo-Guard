"""User feedback collection for match quality improvement.

Stores anonymized structural metadata about user verdicts on matches.
No source code, file paths, or function names are stored — only
numerical/categorical features that describe the match pair.

This data can be used to:
- Fine-tune embedding models on real labeled pairs (future)
- Fine-tune small code embedding models
- Identify systematic false positive patterns

Privacy: all records are stored locally in .echo-guard/index.duckdb.
Optional anonymous export via `echo-guard export-feedback`.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from typing import Any


@dataclass
class FeedbackRecord:
    """Anonymized structural features of a match pair + user verdict.

    No source code, file paths, or function names are stored.
    """

    # ── User verdict ──
    verdict: str  # "true_positive", "false_positive", "ignore"

    # ── Match metadata ──
    match_type: str  # "exact_structure", "embedding_semantic"
    similarity_score: float
    severity: str  # "extract", "review"
    reuse_type: str  # "direct_import", "reference_only", etc.

    # ── Structural features (source function) ──
    source_language: str
    source_param_count: int
    source_has_return: bool
    source_line_count: int
    source_call_count: int
    source_visibility: str
    source_is_nested: bool
    source_has_class: bool

    # ── Structural features (existing function) ──
    existing_language: str
    existing_param_count: int
    existing_has_return: bool
    existing_line_count: int
    existing_call_count: int
    existing_visibility: str
    existing_is_nested: bool
    existing_has_class: bool

    # ── Pair relationship features ──
    same_language: bool
    same_file: bool
    same_class: bool
    same_cluster: bool
    crosses_service_boundary: bool
    ast_hash_match: bool
    name_similarity: float  # 0.0-1.0, edit distance ratio
    param_count_diff: int
    shared_calls_ratio: float  # 0.0-1.0
    line_count_ratio: float  # smaller/larger

    # ── Optional context ──
    dismissed_reason: str = ""  # user-provided reason for false positive
    filter_matched: str = ""  # which intent filter would have caught this
    extra: dict[str, Any] = field(default_factory=dict)

    # ── Cluster context (for mixed-verdict analysis) ──
    cluster_id: str = ""  # hash linking findings from the same cluster
    cluster_size: int = 0  # total copies in cluster at resolution time

    def to_dict(self) -> dict[str, Any]:
        """Serialize for storage/export."""
        d = asdict(self)
        d["extra"] = json.dumps(d["extra"]) if d["extra"] else ""
        return d


def extract_feedback_features(
    match: Any,
    verdict: str,
    dismissed_reason: str = "",
    cluster_info: dict[str, str] | None = None,
    service_boundaries: list[str] | None = None,
) -> FeedbackRecord:
    """Extract anonymized features from a SimilarityMatch + user verdict.

    Args:
        match: A SimilarityMatch instance.
        verdict: One of "true_positive", "false_positive", "ignore".
        dismissed_reason: Optional reason the user dismissed the match.
        cluster_info: Optional dict mapping filepath -> cluster name.
        service_boundaries: Optional list of service boundary paths.
    """
    src = match.source_func
    ext = match.existing_func

    # Name similarity (edit distance ratio, no actual names stored)
    name_sim = SequenceMatcher(None, src.name, ext.name).ratio()

    # Shared calls ratio
    src_calls = set(getattr(src, "calls_made", []) or [])
    ext_calls = set(getattr(ext, "calls_made", []) or [])
    all_calls = src_calls | ext_calls
    shared_calls = len(src_calls & ext_calls) / len(all_calls) if all_calls else 0.0

    # Line counts
    src_lines = src.end_lineno - src.lineno + 1
    ext_lines = ext.end_lineno - ext.lineno + 1
    line_ratio = min(src_lines, ext_lines) / max(src_lines, ext_lines) if max(src_lines, ext_lines) > 0 else 1.0

    # Cluster comparison
    same_cluster = False
    cluster_id = ""
    cluster_size = 0
    if cluster_info:
        src_cluster = cluster_info.get(src.filepath, "")
        ext_cluster = cluster_info.get(ext.filepath, "")
        same_cluster = bool(src_cluster) and src_cluster == ext_cluster
        if same_cluster:
            cluster_id = src_cluster
            cluster_size = sum(1 for cid in cluster_info.values() if cid == src_cluster)

    # Service boundary
    crosses_boundary = False
    if service_boundaries:
        from echo_guard.similarity import _get_service
        svc_a = _get_service(src.filepath, service_boundaries)
        svc_b = _get_service(ext.filepath, service_boundaries)
        crosses_boundary = svc_a is not None and svc_b is not None and svc_a != svc_b

    return FeedbackRecord(
        verdict=verdict,
        match_type=match.match_type,
        similarity_score=round(match.similarity_score, 4),
        severity=match.severity,
        reuse_type=getattr(match, "reuse_type", ""),
        source_language=src.language,
        source_param_count=src.param_count,
        source_has_return=src.has_return,
        source_line_count=src_lines,
        source_call_count=len(src_calls),
        source_visibility=getattr(src, "visibility", "public"),
        source_is_nested=getattr(src, "is_nested", False),
        source_has_class=bool(src.class_name),
        existing_language=ext.language,
        existing_param_count=ext.param_count,
        existing_has_return=ext.has_return,
        existing_line_count=ext_lines,
        existing_call_count=len(ext_calls),
        existing_visibility=getattr(ext, "visibility", "public"),
        existing_is_nested=getattr(ext, "is_nested", False),
        existing_has_class=bool(ext.class_name),
        same_language=src.language == ext.language,
        same_file=src.filepath == ext.filepath,
        same_class=(src.class_name or "") == (ext.class_name or "") and bool(src.class_name),
        same_cluster=same_cluster,
        crosses_service_boundary=crosses_boundary,
        ast_hash_match=src.ast_hash == ext.ast_hash and bool(src.ast_hash),
        name_similarity=round(name_sim, 4),
        param_count_diff=abs(src.param_count - ext.param_count),
        shared_calls_ratio=round(shared_calls, 4),
        line_count_ratio=round(line_ratio, 4),
        dismissed_reason=dismissed_reason,
        cluster_id=cluster_id,
        cluster_size=cluster_size,
    )


def export_feedback(records: list[FeedbackRecord]) -> list[dict[str, Any]]:
    """Export feedback records as a list of dicts for JSONL output.

    All records are already anonymized — no source code or paths.
    """
    return [r.to_dict() for r in records]
