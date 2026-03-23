from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP


mcp = FastMCP("echo-guard")


def _find_repo_root() -> Path:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
        return Path(result.stdout.strip())
    except Exception:
        return Path.cwd()


def _coerce_repo_root(repo_root: str | None) -> Path:
    if repo_root:
        return Path(repo_root).expanduser().resolve()
    return _find_repo_root()


def _guess_language(code: str, filename: str | None = None) -> str:
    if filename:
        suffix = Path(filename).suffix.lower()
        by_suffix = {
            ".py": "python",
            ".js": "javascript",
            ".jsx": "javascript",
            ".mjs": "javascript",
            ".cjs": "javascript",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".go": "go",
            ".rs": "rust",
            ".java": "java",
            ".rb": "ruby",
            ".c": "c",
            ".h": "c",
            ".cpp": "cpp",
            ".cc": "cpp",
            ".cxx": "cpp",
            ".hpp": "cpp",
        }
        if suffix in by_suffix:
            return by_suffix[suffix]

    code_lower = code.lower()

    heuristics: list[tuple[str, list[str]]] = [
        ("python", ["def ", "import ", "from ", "elif ", "self"]),
        ("javascript", ["function ", "const ", "let ", "=>", "console.log"]),
        ("typescript", ["interface ", "type ", ": string", ": number", "implements "]),
        ("go", ["package ", "func ", "fmt.", "err != nil"]),
        ("rust", ["fn ", "let mut ", "impl ", "pub fn ", "match "]),
        ("java", ["public class ", "public static ", "system.out", "private "]),
        ("ruby", ["def ", "end", "puts ", "class "]),
        ("cpp", ["#include <", "std::", "::", "cout", "vector<"]),
        ("c", ["#include <", "printf(", "malloc(", "free("]),
    ]

    best_language = "python"
    best_score = -1
    for language, markers in heuristics:
        score = sum(1 for marker in markers if marker.lower() in code_lower)
        if score > best_score:
            best_score = score
            best_language = language
    return best_language


def _json_text(payload: Any) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False, default=str)


def _safe_read_text(path: Path, max_chars: int = 4000) -> str:
    try:
        return path.read_text(encoding="utf-8")[:max_chars]
    except Exception:
        return ""


def _function_key(filepath: str, func_name: str) -> tuple[str, str]:
    return (str(Path(filepath)), func_name)


def _serialize_function(func: Any) -> dict[str, Any]:
    return {
        "name": getattr(func, "name", ""),
        "filepath": getattr(func, "filepath", ""),
        "language": getattr(func, "language", ""),
        "lineno": getattr(func, "lineno", None),
        "end_lineno": getattr(func, "end_lineno", None),
        "class_name": getattr(func, "class_name", None),
        "visibility": getattr(func, "visibility", None),
        "calls_made": list(getattr(func, "calls_made", []) or []),
        "source": getattr(func, "source", ""),
    }


def _load_index(repo_root: Path) -> Any:
    from echo_guard.index import FunctionIndex

    return FunctionIndex(repo_root)


@mcp.tool()
def check_before_write(
    code: str,
    language: str | None = None,
    filename: str | None = None,
    threshold: float = 0.50,
    repo_root: str | None = None,
) -> str:
    """
    Check whether code you are about to write already exists in the codebase.

    Use this before creating a new helper or utility function.
    """
    from echo_guard.languages import detect_language, extract_functions_universal
    from echo_guard.similarity import SimilarityEngine

    resolved_repo_root = _coerce_repo_root(repo_root)

    if not code.strip():
        return _json_text({"matches": [], "message": "No code provided to check."})

    detected_language = language
    if detected_language is None and filename:
        try:
            detected_language = detect_language(filename)
        except Exception:
            detected_language = None
    if detected_language is None:
        detected_language = _guess_language(code, filename)

    proposed_filename = filename or "<proposed>"

    try:
        new_functions = extract_functions_universal(
            proposed_filename,
            code,
            detected_language,
        )
    except Exception as exc:
        return _json_text(
            {
                "matches": [],
                "message": "Could not parse functions from provided code.",
                "error": str(exc),
            }
        )

    if not new_functions:
        return _json_text(
            {
                "matches": [],
                "message": "Could not parse any functions from the provided code.",
            }
        )

    try:
        index = _load_index(resolved_repo_root)
        all_functions = index.get_all_functions()
        index.close()
    except Exception:
        return _json_text(
            {
                "matches": [],
                "message": "No Echo Guard index found. Run `echo-guard index` first.",
            }
        )

    if not all_functions:
        return _json_text(
            {
                "matches": [],
                "message": "Index is empty. Run `echo-guard index` first.",
            }
        )

    engine = SimilarityEngine(
        lsh_threshold=0.15,
        similarity_threshold=float(threshold),
    )
    for func in all_functions:
        engine.add_function(func)

    candidate_threshold = min(float(threshold), 0.40)
    all_results: list[dict[str, Any]] = []

    for func in new_functions:
        engine.add_function(func)
        matches = engine.find_similar(func, threshold=candidate_threshold)

        for match in matches:
            existing = match.existing_func
            all_results.append(
                {
                    "proposed_function": getattr(func, "name", ""),
                    "proposed_language": getattr(func, "language", detected_language),
                    "proposed_source": getattr(func, "source", ""),
                    "existing_function": getattr(existing, "name", ""),
                    "existing_filepath": getattr(existing, "filepath", ""),
                    "existing_lineno": getattr(existing, "lineno", None),
                    "existing_language": getattr(existing, "language", ""),
                    "existing_visibility": getattr(existing, "visibility", None),
                    "existing_source": getattr(existing, "source", ""),
                    "similarity_score": round(
                        float(getattr(match, "similarity_score", 0.0)), 4
                    ),
                }
            )

    all_results.sort(key=lambda r: r["similarity_score"], reverse=True)

    suggestions = []
    for result in all_results[:10]:
        if result["similarity_score"] >= threshold:
            suggestions.append(
                {
                    "action": "reuse_or_refactor",
                    "reason": "High-confidence existing implementation found.",
                    "existing_function": result["existing_function"],
                    "existing_filepath": result["existing_filepath"],
                }
            )

    return _json_text(
        {
            "repo_root": str(resolved_repo_root),
            "language": detected_language,
            "threshold": threshold,
            "match_count": len(all_results),
            "matches": all_results[:10],
            "suggestions": suggestions,
        }
    )


@mcp.tool()
def search_functions(
    query: str,
    language: str | None = None,
    repo_root: str | None = None,
) -> str:
    """
    Search the Echo Guard index for functions by name, source text, call name, or class name.
    """
    resolved_repo_root = _coerce_repo_root(repo_root)

    try:
        index = _load_index(resolved_repo_root)
        all_functions = index.get_all_functions()
        index.close()
    except Exception:
        return _json_text({"results": [], "message": "No index found."})

    query_lower = query.lower()
    results: list[dict[str, Any]] = []

    for func in all_functions:
        func_language = getattr(func, "language", None)
        if language and func_language != language:
            continue

        score = 0
        if query_lower in getattr(func, "name", "").lower():
            score += 3
        if query_lower in getattr(func, "source", "").lower():
            score += 1
        if any(
            query_lower in call.lower()
            for call in (getattr(func, "calls_made", []) or [])
        ):
            score += 1
        class_name = getattr(func, "class_name", None)
        if class_name and query_lower in class_name.lower():
            score += 2

        if score > 0:
            results.append(
                {
                    "name": getattr(func, "name", ""),
                    "filepath": getattr(func, "filepath", ""),
                    "language": func_language,
                    "lineno": getattr(func, "lineno", None),
                    "class_name": class_name,
                    "score": score,
                    "source_preview": getattr(func, "source", "")[:300],
                }
            )

    results.sort(key=lambda r: r["score"], reverse=True)

    return _json_text(
        {
            "repo_root": str(resolved_repo_root),
            "query": query,
            "count": len(results),
            "results": results[:20],
        }
    )


@mcp.tool()
def get_index_stats(repo_root: str | None = None) -> str:
    """
    Get statistics about the Echo Guard function index and dependency graph.
    """
    from echo_guard.scanner import _build_dep_graph

    resolved_repo_root = _coerce_repo_root(repo_root)

    try:
        index = _load_index(resolved_repo_root)
        stats = index.get_stats()

        try:
            graph = _build_dep_graph(index)
            stats["dependency_graph"] = graph.get_stats()
        except Exception:
            stats["dependency_graph"] = {"available": False}

        index.close()
        return _json_text(stats)
    except Exception:
        return _json_text({"error": "No index found."})


@mcp.tool()
def get_codebase_clusters(repo_root: str | None = None) -> str:
    """
    Get dependency-graph clusters showing how the codebase is organized by domain.
    """
    from echo_guard.scanner import _build_dep_graph

    resolved_repo_root = _coerce_repo_root(repo_root)

    try:
        index = _load_index(resolved_repo_root)
        graph = _build_dep_graph(index)
        index.close()

        clusters: dict[str, list[dict[str, Any]]] = {}
        for filepath, node in graph.nodes.items():
            cluster = str(getattr(node, "cluster", "unclustered"))
            clusters.setdefault(cluster, []).append(
                {
                    "filepath": filepath,
                    "functions": sorted(getattr(node, "function_names", []) or []),
                    "keywords": sorted(getattr(node, "keywords", []) or [])[:10],
                }
            )

        return _json_text({"repo_root": str(resolved_repo_root), "clusters": clusters})
    except Exception:
        return _json_text({"error": "No index found."})


def _find_function(
    all_functions: list[Any], filepath: str, function_name: str
) -> Any | None:
    normalized_target = _function_key(filepath, function_name)
    for func in all_functions:
        key = _function_key(getattr(func, "filepath", ""), getattr(func, "name", ""))
        if key == normalized_target:
            return func
    return None


def _find_callers(all_functions: list[Any], target_name: str) -> list[dict[str, Any]]:
    callers: list[dict[str, Any]] = []
    for func in all_functions:
        calls_made = getattr(func, "calls_made", []) or []
        if any(call == target_name or target_name in call for call in calls_made):
            callers.append(
                {
                    "name": getattr(func, "name", ""),
                    "filepath": getattr(func, "filepath", ""),
                    "lineno": getattr(func, "lineno", None),
                    "language": getattr(func, "language", ""),
                }
            )
    return callers[:25]


def _extract_file_context(func: Any) -> dict[str, Any]:
    filepath = Path(getattr(func, "filepath", ""))
    context_text = _safe_read_text(filepath)
    return {
        "filepath": str(filepath),
        "exists": filepath.exists(),
        "file_preview": context_text,
    }


@mcp.tool()
def suggest_refactor(
    filepath_a: str,
    function_a: str,
    filepath_b: str,
    function_b: str,
    repo_root: str | None = None,
) -> str:
    """
    Return full context for consolidating two redundant functions into one.

    Use this after a duplicate has been identified and you want refactoring context.
    """
    from echo_guard.scanner import _build_dep_graph

    resolved_repo_root = _coerce_repo_root(repo_root)

    try:
        index = _load_index(resolved_repo_root)
        all_functions = index.get_all_functions()
        try:
            graph = _build_dep_graph(index)
        except Exception:
            graph = None
        index.close()
    except Exception:
        return _json_text({"error": "No index found. Run `echo-guard index` first."})

    func_a = _find_function(all_functions, filepath_a, function_a)
    func_b = _find_function(all_functions, filepath_b, function_b)

    if func_a is None or func_b is None:
        return _json_text(
            {
                "error": "Could not find one or both functions in the index.",
                "requested": {
                    "a": {"filepath": filepath_a, "function": function_a},
                    "b": {"filepath": filepath_b, "function": function_b},
                },
            }
        )

    cluster_info: dict[str, Any] = {}
    if graph is not None:
        for fp in [filepath_a, filepath_b]:
            node = graph.nodes.get(fp)
            if node is not None:
                cluster_info[fp] = {
                    "cluster": getattr(node, "cluster", None),
                    "keywords": sorted(getattr(node, "keywords", []) or [])[:10],
                    "function_names": sorted(getattr(node, "function_names", []) or []),
                }

    payload = {
        "repo_root": str(resolved_repo_root),
        "function_a": {
            **_serialize_function(func_a),
            "file_context": _extract_file_context(func_a),
            "callers": _find_callers(all_functions, getattr(func_a, "name", "")),
        },
        "function_b": {
            **_serialize_function(func_b),
            "file_context": _extract_file_context(func_b),
            "callers": _find_callers(all_functions, getattr(func_b, "name", "")),
        },
        "cluster_context": cluster_info,
        "refactor_prompt": (
            "Propose a concrete refactor that consolidates these functions, preserves callers, "
            "and minimizes API breakage. Prefer reuse of an existing shared helper when possible."
        ),
    }

    return _json_text(payload)


@mcp.tool()
def ping() -> str:
    """
    Lightweight health check.
    """
    return _json_text({"ok": True})


if __name__ == "__main__":
    mcp.run(transport="stdio")
