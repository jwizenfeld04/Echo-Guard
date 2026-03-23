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


def _normalize_path(filepath: str) -> str:
    """Normalize a filepath so ./src/foo.py and src/foo.py match."""
    return str(Path(filepath))


def _function_key(filepath: str, func_name: str) -> tuple[str, str]:
    return (_normalize_path(filepath), func_name)


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
def check_for_duplicates(
    code: str,
    language: str | None = None,
    filename: str | None = None,
    threshold: float = 0.50,
    repo_root: str | None = None,
) -> str:
    """
    Check code for duplicates against the existing codebase.

    WHEN TO CALL THIS:
    - Before writing a utility/helper function that might already exist
    - After completing a task, pass all new code to check in one batch
    - When you're about to create something that "feels" like it could exist

    You do NOT need to call this for every function you write. Use your
    judgment — call it when there's a reasonable chance of duplication.

    Each finding includes a finding_id. After reviewing, call resolve_finding
    to record your decision (fixed, acknowledged, or false_positive).
    Previously resolved findings are automatically excluded.
    """
    from echo_guard.languages import detect_language, extract_functions_universal
    from echo_guard.similarity import SimilarityEngine

    resolved_repo_root = _coerce_repo_root(repo_root)

    if not code.strip():
        return _json_text({"duplicates": [], "message": "No code provided."})

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
            proposed_filename, code, detected_language,
        )
    except Exception as exc:
        return _json_text({"duplicates": [], "error": str(exc)})

    if not new_functions:
        return _json_text({"duplicates": [], "message": "No functions parsed."})

    try:
        index = _load_index(resolved_repo_root)
        try:
            all_functions = index.get_all_functions()
        finally:
            index.close()
    except Exception:
        return _json_text({"duplicates": [], "message": "No index. Run `echo-guard index`."})

    if not all_functions:
        return _json_text({"duplicates": [], "message": "Index empty."})

    # Set up embedding infrastructure for Tier 2 detection
    from echo_guard.scanner import _setup_embeddings
    index_obj = _load_index(resolved_repo_root)
    try:
        index_dir = resolved_repo_root / ".echo-guard"
        embedding_store, embedding_model, embedding_rows = _setup_embeddings(
            index_obj, all_functions, index_dir,
        )
    finally:
        index_obj.close()

    engine = SimilarityEngine(
        similarity_threshold=float(threshold),
        embedding_store=embedding_store,
        embedding_model=embedding_model,
    )
    for func in all_functions:
        emb_row = embedding_rows.get(func.qualified_name)
        engine.add_function(func, embedding_row=emb_row)

    # Compute embeddings for proposed functions
    new_emb_rows: dict[str, int] = {}
    if embedding_model is not None and embedding_store is not None:
        embeddings = embedding_model.embed_functions(new_functions)
        rows = embedding_store.add_embeddings(embeddings)
        for func, row in zip(new_functions, rows):
            new_emb_rows[func.qualified_name] = row

    # Load previously resolved findings to skip
    from echo_guard.index import FunctionIndex as _FI
    resolved_ids: set[str] = set()
    try:
        res_index = _load_index(resolved_repo_root)
        try:
            resolved_ids = res_index.get_resolved_finding_ids()
        finally:
            res_index.close()
    except Exception:
        pass

    duplicates: list[dict[str, Any]] = []

    for func in new_functions:
        emb_row = new_emb_rows.get(func.qualified_name)
        engine.add_function(func, embedding_row=emb_row)
        matches = engine.find_similar(func, threshold=float(threshold))

        for match in matches:
            existing = match.existing_func

            # Generate stable finding ID
            finding_id = _FI.make_finding_id(
                func.filepath, func.name,
                existing.filepath, existing.name,
            )

            # Skip previously resolved findings
            if finding_id in resolved_ids:
                continue

            duplicate: dict[str, Any] = {
                "finding_id": finding_id,
                "clone_type": match.clone_type,
                "severity": match.severity,
                "similarity": round(float(match.similarity_score), 2),
                "your_function": func.name,
                "existing_function": existing.name,
                "existing_file": f"{existing.filepath}:{existing.lineno}",
                "existing_source": existing.source,
                "action": _mcp_action_guidance(match),
            }
            if match.import_suggestion and match.reuse_type not in ("reference_only",):
                duplicate["fix"] = match.import_suggestion

            duplicates.append(duplicate)

    duplicates.sort(key=lambda r: r["similarity"], reverse=True)

    if not duplicates:
        return _json_text({"duplicates": [], "message": "No duplicates found. Safe to proceed."})

    return _json_text({
        "duplicate_count": len(duplicates),
        "duplicates": duplicates[:10],
    })


def _mcp_action_guidance(match: Any) -> str:
    """Generate concise, actionable guidance for an AI agent.

    Returns a single sentence telling the agent exactly what to do.
    Optimized for minimal tokens while being unambiguous.
    """
    clone_type = match.clone_type
    reuse_type = getattr(match, "reuse_type", "")

    if clone_type == "type1_type2":
        if reuse_type == "same_file_refactor":
            return "EXACT DUPLICATE in same file. Delete one copy."
        if reuse_type == "cross_service_reference":
            return "EXACT DUPLICATE across services. Extract to shared library."
        return "EXACT DUPLICATE. Import the existing function instead of rewriting it."

    if clone_type == "type3":
        if reuse_type == "extract_utility":
            return "NEAR DUPLICATE differing only in constants. Extract a shared helper with parameters."
        if reuse_type == "same_file_refactor":
            return "NEAR DUPLICATE in same file. Consolidate into one function."
        if reuse_type == "cross_service_reference":
            return "NEAR DUPLICATE across services. Extract shared logic to a library."
        return "NEAR DUPLICATE with minor modifications. Reuse the existing function or refactor both into one."

    # type4
    if reuse_type == "cross_service_reference":
        return "SAME INTENT, different implementation across services. Consider a shared library if logic should be unified."
    return "SAME INTENT, different implementation. Evaluate whether to reuse the existing function or keep both."


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

    if not query.strip():
        return _json_text({"results": [], "message": "No query provided."})

    try:
        index = _load_index(resolved_repo_root)
        try:
            all_functions = index.get_all_functions()
        finally:
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
        try:
            stats = index.get_stats()

            try:
                graph = _build_dep_graph(index)
                stats["dependency_graph"] = graph.get_stats()
            except Exception:
                stats["dependency_graph"] = {"available": False}

            return _json_text(stats)
        finally:
            index.close()
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
        try:
            graph = _build_dep_graph(index)
        finally:
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
        if any(call == target_name for call in calls_made):
            callers.append(
                {
                    "name": getattr(func, "name", ""),
                    "filepath": getattr(func, "filepath", ""),
                    "lineno": getattr(func, "lineno", None),
                    "language": getattr(func, "language", ""),
                }
            )
    return callers[:25]


def _extract_file_context(func: Any, repo_root: Path | None = None) -> dict[str, Any]:
    rel_path = getattr(func, "filepath", "")
    if repo_root:
        abs_path = repo_root / rel_path
    else:
        abs_path = Path(rel_path)
    context_text = _safe_read_text(abs_path)
    return {
        "filepath": rel_path,
        "exists": abs_path.exists(),
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

    # Normalize paths so ./src/foo.py and src/foo.py match
    filepath_a = _normalize_path(filepath_a)
    filepath_b = _normalize_path(filepath_b)

    try:
        index = _load_index(resolved_repo_root)
        try:
            all_functions = index.get_all_functions()
            try:
                graph = _build_dep_graph(index)
            except Exception:
                graph = None
        finally:
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
            "file_context": _extract_file_context(func_a, resolved_repo_root),
            "callers": _find_callers(all_functions, getattr(func_a, "name", "")),
        },
        "function_b": {
            **_serialize_function(func_b),
            "file_context": _extract_file_context(func_b, resolved_repo_root),
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
def resolve_finding(
    finding_id: str,
    verdict: str,
    note: str = "",
    repo_root: str | None = None,
) -> str:
    """
    Record your decision on a duplicate finding from check_for_duplicates.

    Call this once per finding after you've reviewed it:
    - "fixed": You refactored or consolidated the duplicate
    - "acknowledged": Intentional duplication, don't flag again
    - "false_positive": Not a real duplicate, don't flag again

    Resolved findings are automatically excluded from future scans.
    """
    resolved_repo_root = _coerce_repo_root(repo_root)

    if verdict not in ("fixed", "acknowledged", "false_positive"):
        return _json_text({
            "error": f"Invalid verdict: {verdict}. Use: fixed, acknowledged, false_positive"
        })

    try:
        index = _load_index(resolved_repo_root)
        try:
            # Parse finding_id to extract function info
            parts = finding_id.split("||")
            if len(parts) == 2:
                a_parts = parts[0].rsplit(":", 1)
                b_parts = parts[1].rsplit(":", 1)
                source_filepath = a_parts[0] if len(a_parts) == 2 else ""
                source_function = a_parts[1] if len(a_parts) == 2 else parts[0]
                existing_filepath = b_parts[0] if len(b_parts) == 2 else ""
                existing_function = b_parts[1] if len(b_parts) == 2 else parts[1]
            else:
                source_filepath = ""
                source_function = ""
                existing_filepath = ""
                existing_function = ""

            index.resolve_finding(
                finding_id=finding_id,
                verdict=verdict,
                source_filepath=source_filepath,
                source_function=source_function,
                source_lineno=None,
                existing_filepath=existing_filepath,
                existing_function=existing_function,
                existing_lineno=None,
                note=note,
            )

            # For acknowledged/false_positive, also write to
            # .echoguardignore-findings so CI skips this finding
            if verdict in ("acknowledged", "false_positive"):
                ignore_path = resolved_repo_root / ".echoguardignore-findings"
                existing_ids: set[str] = set()
                if ignore_path.exists():
                    for line in ignore_path.read_text().splitlines():
                        stripped = line.split("#")[0].strip()
                        if stripped:
                            existing_ids.add(stripped)
                if finding_id not in existing_ids:
                    with open(ignore_path, "a") as f:
                        if not existing_ids:
                            f.write("# Echo Guard — acknowledged findings\n")
                            f.write("# Remove a line to re-enable checking.\n\n")
                        comment = f"  # {verdict}: {note}" if note else f"  # {verdict}"
                        f.write(f"{finding_id}{comment}\n")

            return _json_text({
                "resolved": True,
                "finding_id": finding_id,
                "verdict": verdict,
            })
        finally:
            index.close()
    except Exception as exc:
        return _json_text({"error": str(exc)})


@mcp.tool()
def get_finding_resolutions(repo_root: str | None = None) -> str:
    """
    Get all finding resolutions for observability. Shows which findings
    have been fixed, acknowledged, or marked as false positives.
    """
    resolved_repo_root = _coerce_repo_root(repo_root)

    try:
        index = _load_index(resolved_repo_root)
        try:
            stats = index.get_resolution_stats()
            resolutions = index.get_all_resolutions()

            return _json_text({
                "stats": stats,
                "resolutions": resolutions[:50],
            })
        finally:
            index.close()
    except Exception:
        return _json_text({"error": "No index found."})


@mcp.tool()
def ping() -> str:
    """
    Lightweight health check.
    """
    return _json_text({"ok": True})


if __name__ == "__main__":
    mcp.run(transport="stdio")
