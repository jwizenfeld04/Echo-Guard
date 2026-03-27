from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger("echo_guard.mcp")

from mcp.server.fastmcp import FastMCP


mcp = FastMCP("echo-guard")


def _find_repo_root() -> Path:
    from echo_guard.utils import find_repo_root

    return find_repo_root()


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


# ── Daemon proxy helpers ─────────────────────────────────────────────────


def _get_daemon_socket(repo_root: Path) -> str | None:
    """Return the Unix socket path if a live daemon is running, else None."""
    import os
    import signal

    lock_path = repo_root / ".echo-guard" / "daemon.lock"
    if not lock_path.exists():
        return None
    try:
        data = json.loads(lock_path.read_text())
        pid = data.get("pid")
        sock = data.get("socket")
        if not pid or not sock:
            return None
        # Verify the process is still alive
        os.kill(pid, 0)
        # Verify socket file exists
        if not Path(sock).exists():
            return None
        return sock
    except Exception:
        return None


def _call_daemon(socket_path: str, method: str, params: dict) -> Any:
    """Send one JSON-RPC request to the daemon socket and return the result.

    Raises RuntimeError on RPC error or connection failure.
    """
    import socket as _socket

    sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    sock.settimeout(30.0)
    try:
        sock.connect(socket_path)
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}) + "\n"
        sock.sendall(request.encode())
        data = b""
        while b"\n" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
        response = json.loads(data.split(b"\n", 1)[0])
        if "error" in response:
            raise RuntimeError(response["error"].get("message", "Daemon RPC error"))
        return response.get("result")
    finally:
        sock.close()


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

    SEVERITY MODEL (based on DRY principles):
    - HIGH: 3+ copies exist — extract to shared module immediately
    - MEDIUM: 2 copies — worth noting, import existing or defer per Rule of Three
    - LOW: Semantic similarity — review for relevance

    Each finding includes a finding_id. After reviewing, call resolve_finding
    to record your decision (resolved, intentional, or dismissed).
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
            proposed_filename,
            code,
            detected_language,
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
        return _json_text(
            {"duplicates": [], "message": "No index. Run `echo-guard index`."}
        )

    if not all_functions:
        return _json_text({"duplicates": [], "message": "Index empty."})

    # Set up embedding infrastructure for Tier 2 detection
    from echo_guard.scanner import _setup_embeddings

    index_obj = _load_index(resolved_repo_root)
    try:
        index_dir = resolved_repo_root / ".echo-guard"
        embedding_store, embedding_model, embedding_rows = _setup_embeddings(
            index_obj,
            all_functions,
            index_dir,
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

    # Note: proposed functions are NOT persisted to the embedding store.
    # find_similar() computes their embeddings on the fly for comparison.

    # Load previously suppressed findings to skip
    from echo_guard.index import FunctionIndex as _FI
    from echo_guard.config import EchoGuardConfig

    _cfg = EchoGuardConfig.load(resolved_repo_root)
    resolved_ids: set[str] = _cfg.get_suppressed_ids()
    try:
        res_index = _load_index(resolved_repo_root)
        try:
            resolved_ids |= res_index.get_resolved_finding_ids()
        finally:
            res_index.close()
    except Exception:
        pass

    duplicates: list[dict[str, Any]] = []

    for func in new_functions:
        engine.add_function(func)
        matches = engine.find_similar(func, threshold=float(threshold))

        for match in matches:
            existing = match.existing_func

            # Generate stable finding ID (AST-hash-based, stable across line shifts)
            finding_id = _FI.make_finding_id(
                func.filepath,
                func.name,
                existing.filepath,
                existing.name,
                source_hash=func.ast_hash or "",
                existing_hash=existing.ast_hash or "",
            )

            # Skip previously resolved findings
            if finding_id in resolved_ids:
                continue

            # Count how many copies of this function exist in the codebase
            existing_name = existing.name
            copy_count = (
                sum(
                    1
                    for f in all_functions
                    if f.ast_hash == existing.ast_hash
                )
                if existing.ast_hash
                else 1
            )

            # DRY-based priority (cross-service is a tag, not a separate tier)
            if copy_count >= 3:
                priority = "extract_now"
                severity = "high"
            else:
                priority = "worth_noting"
                severity = match.severity

            duplicate: dict[str, Any] = {
                "finding_id": finding_id,
                "clone_type": match.clone_type,
                "severity": severity,
                "priority": priority,
                "copies_in_codebase": copy_count,
                "similarity": round(float(match.similarity_score), 2),
                "your_function": func.name,
                "existing_function": existing.name,
                "existing_file": f"{existing.filepath}:{existing.lineno}",
                "action": _mcp_action_guidance(match),
            }
            if match.import_suggestion and match.reuse_type not in ("reference_only",):
                duplicate["fix"] = match.import_suggestion

            duplicates.append(duplicate)

    # Sort: extract_now first, then worth_noting; cross-service is a tag on findings
    priority_order = {"extract_now": 0, "worth_noting": 1}
    duplicates.sort(
        key=lambda r: (priority_order.get(r["priority"], 9), -r["similarity"])
    )

    if not duplicates:
        return _json_text(
            {"duplicates": [], "message": "No duplicates found. Safe to proceed."}
        )

    extract_now = [d for d in duplicates if d["priority"] == "extract_now"]
    worth_noting = [d for d in duplicates if d["priority"] == "worth_noting"]

    response: dict[str, Any] = {
        "duplicate_count": len(duplicates),
        "summary": {
            "extract_now": len(extract_now),
            "worth_noting": len(worth_noting),
        },
        "duplicates": duplicates[:15],
    }

    # Occasionally include a low-confidence probe for training data collection.
    # Probes are NOT findings — they're candidates below the detection threshold
    # that we want the agent to evaluate for model improvement.
    import random

    if random.random() < 0.2 and embedding_store is not None:  # 20% of calls
        probe = _generate_probe(
            engine,
            new_functions,
            embedding_store,
            embedding_rows,
            resolved_repo_root,
            embedding_model=embedding_model,
        )
        if probe:
            response["probe"] = probe

    return _json_text(response)


def _generate_probe(
    engine: Any,
    new_functions: list[Any],
    embedding_store: Any,
    embedding_rows: dict[str, int],
    repo_root: Path,
    embedding_model: Any = None,
) -> dict[str, Any] | None:
    """Generate a low-confidence probe for training data collection.

    Finds a pair below the detection threshold but above a minimum score,
    and returns it as a probe for the agent to evaluate. The agent's verdict
    is stored as training data for future model fine-tuning.
    """
    import numpy as np
    from echo_guard.embeddings import get_embedding_threshold

    if not new_functions or embedding_store is None:
        return None

    # Look for a candidate just below the detection threshold
    for func in new_functions[:3]:  # Check first few proposed functions
        if not hasattr(func, "qualified_name"):
            continue

        if embedding_model is None:
            continue
        try:
            query = embedding_model.embed_function(func)
        except Exception:
            continue

        lang_threshold = get_embedding_threshold(func.language)
        # Probe range: 60-90% of the language threshold (below detection, above noise)
        probe_min = lang_threshold * 0.60
        probe_max = lang_threshold * 0.95

        results = embedding_store.search(
            query=query,
            k=5,
            threshold=probe_min,
        )

        row_to_key = {v: k for k, v in embedding_rows.items()}
        for row_idx, score in results:
            if score >= lang_threshold:
                continue  # Already above threshold — not a probe
            if score < probe_min:
                continue

            # Find the function for this row
            neighbor_key = row_to_key.get(row_idx)
            if neighbor_key is None or neighbor_key not in engine._functions:
                continue

            neighbor = engine._functions[neighbor_key]
            if neighbor.filepath == func.filepath:
                continue  # Same file — not interesting for probes

            return {
                "probe": True,
                "message": (
                    "LOW-CONFIDENCE match below threshold. "
                    "Does this existing function serve the same purpose? "
                    "Call respond_to_probe with your verdict."
                ),
                "probe_id": f"{func.filepath}:{func.name}:{func.lineno}||{neighbor.filepath}:{neighbor.name}:{neighbor.lineno}",
                "your_function": func.name,
                "your_source": func.source[:500],
                "your_language": func.language,
                "existing_function": neighbor.name,
                "existing_file": f"{neighbor.filepath}:{neighbor.lineno}",
                "existing_source": neighbor.source[:500],
                "embedding_score": round(score, 3),
            }

    return None


def _mcp_action_guidance(match: Any) -> str:
    """Generate concise, actionable guidance for an AI agent.

    Uses DRY-based severity model:
    - 2 copies: MEDIUM — worth noting, import existing or defer per Rule of Three
    - Cross-service: flag for architectural decision
    - Parameterized: extract shared helper with parameters

    Returns a single sentence telling the agent exactly what to do.
    """
    clone_type = match.clone_type
    reuse_type = getattr(match, "reuse_type", "")

    if reuse_type == "cross_service_reference":
        return "Cross-service duplicate. Direct import NOT possible. Consider extracting to a shared library package."

    if reuse_type == "same_file_refactor":
        return "Same-file duplicate. Consolidate into one function or acknowledge as intentional."

    if reuse_type == "extract_utility":
        return "Parameterized duplicate — identical except for constants. Extract a shared helper that takes the varying parts as parameters."

    if reuse_type == "reference_only":
        return "Cross-language duplicate — direct import is not possible. Consider exposing shared logic via a REST/gRPC API, a shared data contract, or reimplementing from a single source of truth."

    if clone_type == "type1_type2":
        return "Exact duplicate. Import the existing function instead of rewriting it."

    if clone_type == "type3":
        return "Near duplicate with minor modifications. Reuse the existing function or refactor both into a shared helper."

    # type4
    return "Similar intent, different implementation. Evaluate whether to reuse the existing function or keep both."


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
        "refactor_guidance": {
            "prompt": (
                "Propose a concrete refactor that consolidates these functions, preserves callers, "
                "and minimizes API breakage. Prefer reuse of an existing shared helper when possible."
            ),
            "dry_principle": (
                "Only extract if the knowledge is truly shared — if these functions change for the "
                "same reasons. If they serve different domains (e.g., get_user_by_id vs get_team_by_id), "
                "the duplication may be intentional per-entity implementation."
            ),
        },
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
    - "resolved": You refactored or consolidated the duplicate
    - "intentional": Intentional duplication (e.g., same-file CRUD pattern), don't flag again
    - "dismissed": Not a real duplicate, don't flag again

    Resolved findings are automatically excluded from future scans.
    The decision is saved to echo-guard.yml and the local index.
    """
    resolved_repo_root = _coerce_repo_root(repo_root)

    if verdict not in ("resolved", "intentional", "dismissed"):
        return _json_text(
            {
                "error": f"Invalid verdict: {verdict}. Use: resolved, intentional, dismissed"
            }
        )

    # Route through daemon when running — keeps VS Code diagnostics in sync
    daemon_socket = _get_daemon_socket(resolved_repo_root)
    if daemon_socket:
        try:
            result = _call_daemon(
                daemon_socket,
                "resolve_finding",
                {"finding_id": finding_id, "verdict": verdict, "note": note},
            )
            # No rescan needed — resolve_finding already updated in-memory
            # findings and sends a finding_resolved notification to the
            # extension. The suppression is written to config so subsequent
            # scans will filter it correctly.
            return _json_text(result)
        except Exception as exc:
            log.warning("Daemon routing failed for resolve_finding, falling back: %s", exc)

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

            # Collect training data from the resolution
            try:
                import hashlib

                all_funcs = index.get_all_functions()
                code_a = code_b = ""
                lang = "unknown"
                for f in all_funcs:
                    if f.filepath == source_filepath and f.name == source_function:
                        code_a = f.source
                        lang = f.language
                    if f.filepath == existing_filepath and f.name == existing_function:
                        code_b = f.source

                # Compute cluster context — how many copies share the same representative
                cluster_id = hashlib.md5(
                    f"{existing_filepath}:{existing_function}".encode()
                ).hexdigest()[:12]
                cluster_size = sum(
                    1 for f in all_funcs
                    if f.name == existing_function
                )

                if code_a and code_b:
                    train_verdict = (
                        "not_clone" if verdict == "dismissed" else "clone"
                    )
                    index.record_training_pair(
                        verdict=train_verdict,
                        language=lang,
                        source_code_a=code_a,
                        source_code_b=code_b,
                        function_name_a=source_function,
                        function_name_b=existing_function,
                        filepath_a=source_filepath,
                        filepath_b=existing_filepath,
                        clone_type="resolution",
                        probe_type="resolution",
                        cluster_id=cluster_id,
                        cluster_size=cluster_size,
                    )
            except Exception:
                import logging as _log

                _log.getLogger("echo_guard").debug(
                    "Training data collection failed", exc_info=True
                )

            # For intentional/dismissed, save to echo-guard.yml
            if verdict in ("intentional", "dismissed"):
                from echo_guard.config import EchoGuardConfig

                config = EchoGuardConfig.load(resolved_repo_root)
                # Extract hashes from finding_id to support re-surfacing logic
                src_hash = existing_hash_str = ""
                try:
                    for part in finding_id.split("||"):
                        colon_parts = part.rsplit(":", 1)
                        if len(colon_parts) == 2:
                            if not src_hash:
                                src_hash = colon_parts[1]
                            else:
                                existing_hash_str = colon_parts[1]
                except Exception:
                    pass
                config.add_suppressed(finding_id, verdict, src_hash, existing_hash_str)

            # Trigger daemon rescan so extension picks up the change
            _trigger_daemon_rescan(resolved_repo_root)

            return _json_text(
                {
                    "resolved": True,
                    "finding_id": finding_id,
                    "verdict": verdict,
                }
            )
        finally:
            index.close()
    except Exception as exc:
        return _json_text({"error": str(exc)})


def _trigger_daemon_rescan(repo_root: Path) -> None:
    """Best-effort: tell the daemon to rescan so the extension refreshes."""
    daemon_socket = _get_daemon_socket(repo_root)
    if daemon_socket:
        try:
            _call_daemon(daemon_socket, "scan", {})
        except Exception:
            pass  # Non-critical — periodic reindex catches it


@mcp.tool()
def get_finding_resolutions(repo_root: str | None = None) -> str:
    """
    Get all finding resolutions for observability. Shows which findings
    have been resolved, marked intentional, or dismissed.
    """
    resolved_repo_root = _coerce_repo_root(repo_root)

    try:
        index = _load_index(resolved_repo_root)
        try:
            stats = index.get_resolution_stats()
            resolutions = index.get_all_resolutions()

            return _json_text(
                {
                    "stats": stats,
                    "resolutions": resolutions[:50],
                }
            )
        finally:
            index.close()
    except Exception:
        return _json_text({"error": "No index found."})


@mcp.tool()
def respond_to_probe(
    probe_id: str,
    verdict: str,
    your_source: str = "",
    language: str = "",
    repo_root: str | None = None,
) -> str:
    """
    Respond to a low-confidence probe from check_for_duplicates.

    Probes are NOT findings — they are candidates below the detection
    threshold that Echo Guard wants your judgment on. Your response is
    stored as training data to improve future detection.

    Pass your_source and language from the probe response to ensure
    the training pair is complete.

    Verdicts:
    - "clone": Yes, these functions serve the same purpose
    - "not_clone": No, these are different functions
    """
    resolved_repo_root = _coerce_repo_root(repo_root)

    if verdict not in ("clone", "not_clone"):
        return _json_text({"error": "Use 'clone' or 'not_clone'"})

    try:
        index = _load_index(resolved_repo_root)
        try:
            parts = probe_id.split("||")
            if len(parts) != 2:
                return _json_text({"error": "Invalid probe_id"})

            # Format: filepath:name:lineno||filepath:name:lineno
            # (legacy format without lineno is also accepted)
            a_parts = parts[0].rsplit(":", 2)
            b_parts = parts[1].rsplit(":", 2)

            if len(a_parts) == 3:
                filepath_a, name_a, lineno_a = a_parts[0], a_parts[1], int(a_parts[2])
            elif len(a_parts) == 2:
                filepath_a, name_a, lineno_a = a_parts[0], a_parts[1], 0
            else:
                filepath_a, name_a, lineno_a = "", parts[0], 0

            if len(b_parts) == 3:
                filepath_b, name_b, lineno_b = b_parts[0], b_parts[1], int(b_parts[2])
            elif len(b_parts) == 2:
                filepath_b, name_b, lineno_b = b_parts[0], b_parts[1], 0
            else:
                filepath_b, name_b, lineno_b = "", parts[1], 0

            # Get the existing function's source from the index
            code_b = ""
            for f in index.get_all_functions():
                if (
                    f.filepath == filepath_b
                    and f.name == name_b
                    and (lineno_b == 0 or f.lineno == lineno_b)
                ):
                    code_b = f.source
                    if not language:
                        language = f.language
                    break

            # Use the proposed source passed from the probe response
            code_a = your_source

            recorded = False
            if code_a and code_b:
                index.record_training_pair(
                    verdict=verdict,
                    language=language or "unknown",
                    source_code_a=code_a,
                    source_code_b=code_b,
                    function_name_a=name_a,
                    function_name_b=name_b,
                    filepath_a=filepath_a,
                    filepath_b=filepath_b,
                    clone_type="type4_probe",
                    probe_type="probe",
                )
                recorded = True

            stats = index.get_training_pair_count()
            return _json_text(
                {
                    "recorded": recorded,
                    "verdict": verdict,
                    "training_pairs_collected": stats["total"],
                }
            )
        finally:
            index.close()
    except Exception as exc:
        return _json_text({"error": str(exc)})


@mcp.tool()
def recheck_file(
    filepath: str,
    repo_root: str | None = None,
) -> str:
    """
    Re-parse, re-embed, and re-check a file for duplicates after it has been modified.

    WHEN TO CALL THIS:
    - After applying a fix to a file (e.g. deleting a duplicate function, importing instead)
    - After consolidating two functions into a shared helper
    - The index is updated in place so future check_for_duplicates calls see the change

    When the VS Code extension is running, routes through the daemon to keep
    diagnostics in sync. Falls back to direct index access otherwise.

    Returns the updated findings for the file.
    """
    resolved_repo_root = _coerce_repo_root(repo_root)

    # Route through daemon when running — updates VS Code diagnostics live
    daemon_socket = _get_daemon_socket(resolved_repo_root)
    if daemon_socket:
        try:
            result = _call_daemon(
                daemon_socket,
                "check_files",
                {"files": [filepath]},
            )
            findings_for_file = (result.get("findings") or {}).get(filepath, [])
            return _json_text({
                "filepath": filepath,
                "findings": findings_for_file,
                "total": len(findings_for_file),
                "routed_through_daemon": True,
            })
        except Exception as exc:
            log.warning("Daemon routing failed for recheck_file, falling back: %s", exc)

    # Direct fallback: check the file against the current index
    try:
        from echo_guard.scanner import check_files
        from echo_guard.config import EchoGuardConfig
        from echo_guard.index import FunctionIndex

        config = EchoGuardConfig.load(resolved_repo_root)
        index = FunctionIndex(resolved_repo_root)
        try:
            resolved_ids = index.get_resolved_finding_ids()
        finally:
            index.close()
        matches = check_files(resolved_repo_root, [filepath], config=config)

        findings = []
        for match in matches:
            finding_id = FunctionIndex.make_finding_id(
                match.source_func.filepath,
                match.source_func.name,
                match.existing_func.filepath,
                match.existing_func.name,
                source_hash=match.source_func.ast_hash or "",
                existing_hash=match.existing_func.ast_hash or "",
            )
            if finding_id in resolved_ids or config.is_suppressed(
                finding_id,
                match.source_func.ast_hash or "",
                match.existing_func.ast_hash or "",
            ):
                continue
            findings.append({
                "finding_id": finding_id,
                "severity": match.severity,
                "clone_type": getattr(match, "clone_type", ""),
                "similarity": round(float(match.similarity_score), 2),
                "existing_function": match.existing_func.name,
                "existing_file": f"{match.existing_func.filepath}:{match.existing_func.lineno}",
            })

        return _json_text({
            "filepath": filepath,
            "findings": findings,
            "total": len(findings),
            "routed_through_daemon": False,
        })
    except Exception as exc:
        return _json_text({"error": str(exc)})


@mcp.tool()
def ping() -> str:
    """
    Lightweight health check.
    """
    return _json_text({"ok": True})


if __name__ == "__main__":
    mcp.run(transport="stdio")
