"""Feature-based duplicate classifier for Echo Guard.

Replaces hand-tuned threshold filters with a logistic regression model
that combines multiple signals: AST structure, embedding score, name/body
similarity, control flow, parameter signatures, and code context.

The model is a logistic regression with 14 features — shipped as a small
JSON weights file. No sklearn needed at runtime; inference is pure NumPy.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np

from echo_guard.ast_distance import normalized_ast_similarity
from echo_guard.languages import ExtractedFunction
from echo_guard.utils import split_name_tokens as _split_name_tokens


# Common tokens that appear in almost every function — low signal
_COMMON_TOKENS = frozenset({
    "self", "cls", "return", "if", "else", "for", "in", "not", "and", "or",
    "true", "false", "none", "null", "undefined", "const", "let", "var",
    "async", "await", "def", "function", "class", "import", "from",
    "try", "except", "catch", "finally", "throw", "raise", "new",
    "this", "err", "error", "data", "result", "value", "item", "key",
    "i", "j", "k", "n", "x", "y", "s", "f", "e", "r", "v", "t",
})

_IDENTIFIER_PATTERN = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]{2,})\b")
_STRING_LITERAL_PATTERN = re.compile(r"""(?:'([^'\\]*(?:\\.[^'\\]*)*)'|"([^"\\]*(?:\\.[^"\\]*)*)")""")


def _extract_body_identifiers(source: str) -> set[str]:
    """Extract meaningful identifiers from function body, excluding common tokens."""
    tokens = set(_IDENTIFIER_PATTERN.findall(source))
    return tokens - _COMMON_TOKENS


def _extract_literals(source: str) -> set[str]:
    """Extract normalized string literals from source code."""
    lits = set()
    for m in _STRING_LITERAL_PATTERN.finditer(source):
        val = m.group(1) or m.group(2)
        if val and len(val) > 1:  # Skip empty and single-char strings
            lits.add(val.lower().strip())
    return lits


def _extract_call_tokens(func: ExtractedFunction) -> set[str]:
    """Extract called function/method names, falling back to source parsing."""
    calls = set(func.calls_made) if func.calls_made else set()
    if not calls:
        # Fallback: extract from source using pattern matching
        call_pattern = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_.]*)\s*\(")
        calls = {m.group(1).split(".")[-1] for m in call_pattern.finditer(func.source)}
        calls.discard(func.name)  # Don't count self-calls
    return {c.lower() for c in calls if c and len(c) > 1}


def _jaccard(a: set, b: set) -> float:
    """Jaccard similarity between two sets."""
    if not a and not b:
        return 0.0
    union = a | b
    return len(a & b) / len(union) if union else 0.0


# ── Control flow extraction ──────────────────────────────────────────

_CONTROL_FLOW_PATTERNS = {
    "if": re.compile(r"\b(if|elif|else if)\b"),
    "loop": re.compile(r"\b(for|while|\.forEach|\.map|\.filter)\b"),
    "try": re.compile(r"\b(try)\b"),
    "return": re.compile(r"\breturn\b"),
    "match": re.compile(r"\b(switch|match|case)\b"),
}


def _control_flow_vector(source: str) -> np.ndarray:
    """Extract a small control flow vector: [ifs, loops, tries, returns, switches]."""
    return np.array([
        len(_CONTROL_FLOW_PATTERNS["if"].findall(source)),
        len(_CONTROL_FLOW_PATTERNS["loop"].findall(source)),
        len(_CONTROL_FLOW_PATTERNS["try"].findall(source)),
        len(_CONTROL_FLOW_PATTERNS["return"].findall(source)),
        len(_CONTROL_FLOW_PATTERNS["match"].findall(source)),
    ], dtype=np.float32)


def _control_flow_similarity(source_a: str, source_b: str) -> float:
    """Cosine similarity of control flow vectors."""
    va = _control_flow_vector(source_a)
    vb = _control_flow_vector(source_b)
    dot = float(np.dot(va, vb))
    norm_a = float(np.linalg.norm(va))
    norm_b = float(np.linalg.norm(vb))
    if norm_a < 1e-9 or norm_b < 1e-9:
        # Both have no control flow → similar (trivial functions)
        return 1.0 if norm_a < 1e-9 and norm_b < 1e-9 else 0.0
    return dot / (norm_a * norm_b)


# ── Return shape classification ──────────────────────────────────────

def _return_shape(func: ExtractedFunction) -> str:
    """Classify what kind of value a function returns."""
    source = func.source
    if func.language in ("javascript", "typescript") and re.search(r"</?[A-Za-z][\w.:-]*\b|<>|</>", source):
        return "jsx"
    if re.search(r"return\s+\{", source):
        return "dict"
    if re.search(r"return\s+\[", source):
        return "list"
    if re.search(r"return\s+(True|False|true|false)\b", source):
        return "boolean"
    if re.search(r"return\s+(None|null|undefined)\b", source):
        return "null"
    if func.has_return:
        return "value"
    return "void"


# ── Parameter signature similarity ───────────────────────────────────

def _param_signature_similarity(func_a: ExtractedFunction, func_b: ExtractedFunction) -> float:
    """Compare parameter signatures: count match + name overlap."""
    count_sim = 1.0 - abs(func_a.param_count - func_b.param_count) / max(func_a.param_count, func_b.param_count, 1)

    # Extract param names from source (first line / signature)
    param_pattern = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*[,:)\]=]")
    first_line_a = func_a.source.split("\n")[0]
    first_line_b = func_b.source.split("\n")[0]
    params_a = set(m.group(1).lower() for m in param_pattern.finditer(first_line_a)) - _COMMON_TOKENS
    params_b = set(m.group(1).lower() for m in param_pattern.finditer(first_line_b)) - _COMMON_TOKENS

    name_sim = _jaccard(params_a, params_b)
    return (count_sim + name_sim) / 2.0


# ── Feature extraction ────────────────────────────────────────────────

FEATURE_NAMES = [
    "ast_similarity",
    "embedding_score",
    "name_token_overlap",
    "body_identifier_overlap",
    "call_token_overlap",
    "literal_overlap",
    "control_flow_similarity",
    "parameter_signature_similarity",
    "return_shape_similarity",
    "same_file",
    "async_match",
    "line_count_min",
    "line_count_ratio",
    "is_exact_structure",
]


def extract_features(
    func_a: ExtractedFunction,
    func_b: ExtractedFunction,
    match_type: str,
    raw_score: float,
    similarity_score: float,
) -> np.ndarray:
    """Extract 14-element feature vector for a candidate pair.

    Features combine structural (AST), semantic (embeddings), lexical
    (identifiers, literals, calls), and contextual (file, params, control
    flow) signals into a single vector for the classifier.
    """
    emb_score = raw_score if raw_score > 0 else similarity_score
    same_file = func_a.filepath == func_b.filepath
    is_exact = match_type == "exact_structure"

    # 1. AST similarity — skip expensive computation for obvious rejects
    a_name_tokens = set(_split_name_tokens(func_a.name))
    b_name_tokens = set(_split_name_tokens(func_b.name))
    union = a_name_tokens | b_name_tokens
    name_overlap = len(a_name_tokens & b_name_tokens) / len(union) if union else 0.0

    if name_overlap < 0.1 and same_file and not is_exact and emb_score < 0.98:
        ast_sim = 0.5  # Placeholder for obvious rejects
    else:
        ast_sim = normalized_ast_similarity(
            getattr(func_a, "ast_tokens", "") or "",
            getattr(func_b, "ast_tokens", "") or "",
        )

    # 2. Body identifier overlap (excluding common tokens)
    ids_a = _extract_body_identifiers(func_a.source)
    ids_b = _extract_body_identifiers(func_b.source)
    body_id_overlap = _jaccard(ids_a, ids_b)

    # 3. Call token overlap
    calls_a = _extract_call_tokens(func_a)
    calls_b = _extract_call_tokens(func_b)
    call_overlap = _jaccard(calls_a, calls_b)

    # 4. Literal overlap
    lits_a = _extract_literals(func_a.source)
    lits_b = _extract_literals(func_b.source)
    lit_overlap = _jaccard(lits_a, lits_b)

    # 5. Control flow similarity
    cf_sim = _control_flow_similarity(func_a.source, func_b.source)

    # 6. Parameter signature similarity
    param_sim = _param_signature_similarity(func_a, func_b)

    # 7. Return shape similarity
    ret_a = _return_shape(func_a)
    ret_b = _return_shape(func_b)
    ret_sim = 1.0 if ret_a == ret_b else 0.0

    # 8. Async match
    a_async = "async " in func_a.source[:50]
    b_async = "async " in func_b.source[:50]
    async_match = float(a_async == b_async)

    # Line counts
    a_lines = func_a.end_lineno - func_a.lineno + 1
    b_lines = func_b.end_lineno - func_b.lineno + 1
    line_min = min(a_lines, b_lines)
    line_ratio = line_min / max(a_lines, b_lines) if max(a_lines, b_lines) > 0 else 1.0

    return np.array([
        ast_sim,
        emb_score,
        name_overlap,
        body_id_overlap,
        call_overlap,
        lit_overlap,
        cf_sim,
        param_sim,
        ret_sim,
        float(same_file),
        async_match,
        float(line_min),
        line_ratio,
        float(is_exact),
    ], dtype=np.float32)


# ── Classifier inference ──────────────────────────────────────────────

_WEIGHTS_PATH = Path(__file__).parent / "data" / "classifier_weights.json"
_cached_weights: dict | None = None


def _load_weights() -> dict:
    """Load classifier weights from JSON file."""
    global _cached_weights
    if _cached_weights is not None:
        return _cached_weights

    if not _WEIGHTS_PATH.exists():
        # No trained model — use hand-tuned defaults for 14 features
        _cached_weights = {
            "coef": [
                0.6,   # ast_similarity
                0.4,   # embedding_score
                0.8,   # name_token_overlap
                0.5,   # body_identifier_overlap
                0.4,   # call_token_overlap
                0.3,   # literal_overlap
                0.2,   # control_flow_similarity
                0.3,   # parameter_signature_similarity
                0.2,   # return_shape_similarity
                -0.4,  # same_file
                0.0,   # async_match (neutral)
                0.02,  # line_count_min
                0.1,   # line_count_ratio
                0.5,   # is_exact_structure
            ],
            "intercept": -1.5,
            "version": "default-v2-14feat",
        }
        return _cached_weights

    with open(_WEIGHTS_PATH) as f:
        loaded: dict = json.load(f)
    _cached_weights = loaded
    return loaded


def predict_duplicate(features: np.ndarray) -> float:
    """Predict probability that a pair is a true duplicate.

    Returns a float in [0, 1]. Values > 0.5 indicate likely duplicate.
    Uses logistic regression: sigmoid(features @ coef + intercept).
    """
    weights = _load_weights()
    coef = np.array(weights["coef"], dtype=np.float32)
    intercept = weights["intercept"]

    logit = float(features @ coef + intercept)
    logit = max(-20.0, min(20.0, logit))
    return 1.0 / (1.0 + np.exp(-logit))


def reset_cache() -> None:
    """Clear cached weights (for testing)."""
    global _cached_weights
    _cached_weights = None
