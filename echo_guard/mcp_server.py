"""MCP server for Echo Guard — allows AI coding agents to check for redundancy.

Agents can call this BEFORE generating new utilities to prevent redundancy
at creation time rather than catching it after the fact.

Run with: python -m echo_guard.mcp_server
Configure in MCP settings:
{
    "mcpServers": {
        "echo-guard": {
            "command": "python",
            "args": ["-m", "echo_guard.mcp_server"]
        }
    }
}
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _find_repo_root() -> Path:
    import subprocess
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
        )
        return Path(result.stdout.strip())
    except Exception:
        return Path.cwd()


def handle_check_before_write(arguments: dict) -> dict:
    """Check if proposed code already exists in the codebase."""
    from echo_guard.index import FunctionIndex
    from echo_guard.languages import ExtractedFunction, extract_functions_universal, detect_language
    from echo_guard.similarity import SimilarityEngine

    repo_root = Path(arguments.get("repo_root", str(_find_repo_root())))
    code_snippet = arguments.get("code", "")
    language = arguments.get("language")
    threshold = float(arguments.get("threshold", 0.50))
    filename = arguments.get("filename", "<proposed>")

    if not code_snippet.strip():
        return {"matches": [], "message": "No code provided to check."}

    # Auto-detect language from filename or content
    if language is None and filename != "<proposed>":
        language = detect_language(filename)
    if language is None:
        language = _guess_language(code_snippet)

    # Parse the proposed code
    new_functions = extract_functions_universal(filename, code_snippet, language)
    if not new_functions:
        return {"matches": [], "message": "Could not parse any functions from the provided code."}

    # Load index
    try:
        index = FunctionIndex(repo_root)
        all_functions = index.get_all_functions()
        index.close()
    except Exception:
        return {"matches": [], "message": "No Echo Guard index found. Run `echo-guard index` first."}

    if not all_functions:
        return {"matches": [], "message": "Index is empty. Run `echo-guard index` first."}

    # Build engine and check — use a low LSH threshold so cross-language
    # candidates (Jaccard ≈ 0.2) aren't dropped before TF-IDF scoring
    engine = SimilarityEngine(lsh_threshold=0.15, similarity_threshold=threshold)
    for func in all_functions:
        engine.add_function(func)

    # Use a lower threshold to surface candidates for Claude to judge semantically.
    # The fast pipeline is a recall-optimized filter; Claude is the precision layer.
    candidate_threshold = min(threshold, 0.4)

    all_results = []
    for func in new_functions:
        engine.add_function(func)
        matches = engine.find_similar(func, threshold=candidate_threshold)
        for match in matches:
            result = {
                "proposed_function": func.name,
                "proposed_source": func.source,
                "existing_function": match.existing_func.name,
                "existing_filepath": match.existing_func.filepath,
                "existing_lineno": match.existing_func.lineno,
                "existing_language": match.existing_func.language,
                "existing_source": match.existing_func.source,
                "existing_visibility": match.existing_func.visibility,
                "similarity_score": round(match.similarity_score, 3),
                "match_type": match.match_type,
                "import_suggestion": match.import_suggestion,
                "severity": match.severity,
                "reuse_type": match.reuse_type,
                "reuse_guidance": match.reuse_guidance,
            }
            all_results.append(result)

    # Only surface HIGH and MEDIUM matches to the agent. LOW findings are
    # mostly structural noise (84% signal at HIGH+MED vs 14% at LOW) and
    # would cause the agent to second-guess valid code.
    results = [r for r in all_results if r["severity"] in ("high", "medium")]

    if results:
        high = [r for r in results if r["severity"] == "high"]
        medium = [r for r in results if r["severity"] == "medium"]
        parts = []
        if high:
            parts.append(
                f"{len(high)} high-confidence match(es) — these almost certainly duplicate existing code. "
                "Import the existing function instead of writing new code."
            )
        if medium:
            parts.append(
                f"{len(medium)} medium-confidence match(es) — strong similarity detected. "
                "Compare the proposed and existing source to decide if they are truly redundant. "
                "If they solve the same problem, import the existing one."
            )
        message = " ".join(parts)
    else:
        message = "No similar functions found. Safe to proceed with writing this code."

    return {"matches": results, "message": message}


def handle_search_functions(arguments: dict) -> dict:
    """Search the index for functions matching a name or keyword."""
    from echo_guard.index import FunctionIndex

    repo_root = Path(arguments.get("repo_root", str(_find_repo_root())))
    query = arguments.get("query", "")
    language = arguments.get("language")

    try:
        index = FunctionIndex(repo_root)
        all_functions = index.get_all_functions()
        index.close()
    except Exception:
        return {"results": [], "message": "No index found."}

    query_lower = query.lower()
    results = []
    for func in all_functions:
        if language and func.language != language:
            continue
        score = 0
        if query_lower in func.name.lower():
            score += 3
        if query_lower in func.source.lower():
            score += 1
        if any(query_lower in call.lower() for call in func.calls_made):
            score += 1
        if func.class_name and query_lower in func.class_name.lower():
            score += 2
        if score > 0:
            results.append({
                "name": func.name,
                "filepath": func.filepath,
                "language": func.language,
                "lineno": func.lineno,
                "class_name": func.class_name,
                "score": score,
                "source_preview": func.source[:300],
            })

    results.sort(key=lambda r: r["score"], reverse=True)
    return {"results": results[:20], "count": len(results)}


def handle_get_stats(arguments: dict) -> dict:
    """Get index statistics and dependency graph info."""
    from echo_guard.index import FunctionIndex

    repo_root = Path(arguments.get("repo_root", str(_find_repo_root())))
    try:
        index = FunctionIndex(repo_root)
        stats = index.get_stats()

        # Add dep graph stats
        from echo_guard.scanner import _build_dep_graph
        try:
            graph = _build_dep_graph(index)
            stats["dependency_graph"] = graph.get_stats()
        except Exception:
            pass

        index.close()
        return stats
    except Exception:
        return {"error": "No index found."}


def handle_get_clusters(arguments: dict) -> dict:
    """Get dependency graph clusters to understand codebase organization."""
    from echo_guard.index import FunctionIndex
    from echo_guard.scanner import _build_dep_graph

    repo_root = Path(arguments.get("repo_root", str(_find_repo_root())))
    try:
        index = FunctionIndex(repo_root)
        graph = _build_dep_graph(index)
        index.close()

        clusters = {}
        for filepath, node in graph.nodes.items():
            cluster = node.cluster
            if cluster not in clusters:
                clusters[cluster] = []
            clusters[cluster].append({
                "filepath": filepath,
                "functions": sorted(node.function_names),
                "keywords": sorted(node.keywords)[:10],
            })

        return {"clusters": clusters}
    except Exception:
        return {"error": "No index found."}


def handle_suggest_refactor(arguments: dict) -> dict:
    """Provide full context for Claude to generate a refactoring that eliminates redundancy.

    Returns both functions' full source, their file contexts, and the dependency graph
    cluster they belong to — everything Claude needs to produce a concrete refactoring plan.
    """
    from echo_guard.index import FunctionIndex
    from echo_guard.scanner import _build_dep_graph

    repo_root = Path(arguments.get("repo_root", str(_find_repo_root())))
    filepath_a = arguments.get("filepath_a", "")
    function_a = arguments.get("function_a", "")
    filepath_b = arguments.get("filepath_b", "")
    function_b = arguments.get("function_b", "")

    try:
        index = FunctionIndex(repo_root)
        all_functions = index.get_all_functions()
    except Exception:
        return {"error": "No index found. Run `echo-guard index` first."}

    # Find the two functions
    func_a = None
    func_b = None
    for func in all_functions:
        if func.filepath == filepath_a and func.name == function_a:
            func_a = func
        if func.filepath == filepath_b and func.name == function_b:
            func_b = func

    if func_a is None or func_b is None:
        return {"error": f"Could not find one or both functions in the index."}

    # Get dependency graph context
    graph_context = {}
    try:
        graph = _build_dep_graph(index)
        cluster_a = graph.get_cluster(filepath_a)
        cluster_b = graph.get_cluster(filepath_b)
        graph_context = {
            "cluster_a": cluster_a,
            "cluster_b": cluster_b,
            "same_cluster": cluster_a == cluster_b,
            "related_files_a": sorted(graph.get_related_files(filepath_a))[:10],
            "related_files_b": sorted(graph.get_related_files(filepath_b))[:10],
        }
    except Exception:
        pass

    # Find all callers of each function (who imports/calls them)
    callers_a = []
    callers_b = []
    for func in all_functions:
        if function_a in func.calls_made and func.filepath != filepath_a:
            callers_a.append(f"{func.filepath}:{func.name}")
        if function_b in func.calls_made and func.filepath != filepath_b:
            callers_b.append(f"{func.filepath}:{func.name}")

    index.close()

    return {
        "function_a": {
            "name": func_a.name,
            "filepath": func_a.filepath,
            "language": func_a.language,
            "lineno": func_a.lineno,
            "source": func_a.source,
            "param_count": func_a.param_count,
            "calls_made": func_a.calls_made,
            "imports_used": func_a.imports_used,
            "callers": callers_a,
        },
        "function_b": {
            "name": func_b.name,
            "filepath": func_b.filepath,
            "language": func_b.language,
            "lineno": func_b.lineno,
            "source": func_b.source,
            "param_count": func_b.param_count,
            "calls_made": func_b.calls_made,
            "imports_used": func_b.imports_used,
            "callers": callers_b,
        },
        "graph_context": graph_context,
        "refactor_instructions": (
            "You have the full source of both functions, their callers, and their dependency graph context. "
            "To refactor: (1) Decide which function is the canonical version (prefer the one with more callers, "
            "or the one in the more appropriate domain cluster). (2) If they differ in behavior, unify them into "
            "a single function that handles both cases, or extract the shared logic. (3) Update all callers of "
            "the removed function to use the canonical one. (4) Generate the concrete file edits needed."
        ),
    }


def _guess_language(code: str) -> str:
    """Rough heuristic to guess language from code content."""
    if "def " in code and ":" in code:
        return "python"
    if "func " in code and "{" in code:
        if "package " in code or ":=" in code:
            return "go"
        return "rust" if "fn " in code else "go"
    if "fn " in code:
        return "rust"
    if "function " in code or "=>" in code or "const " in code:
        if ": " in code and ("interface " in code or "type " in code):
            return "typescript"
        return "javascript"
    if "public " in code or "private " in code:
        return "java"
    if "#include" in code:
        return "cpp"
    return "python"


# ── MCP JSON-RPC Protocol ────────────────────────────────────────────────

TOOLS = [
    {
        "name": "check_before_write",
        "description": (
            "Check if code you're about to write already exists in the codebase. "
            "Pass the proposed code and Echo Guard will find similar existing functions, "
            "suggest imports, and prevent redundant code generation. "
            "Works with Python, JavaScript, TypeScript, Go, Rust, Java, Ruby, C, and C++. "
            "CALL THIS BEFORE writing any new utility function."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "The function code you plan to write",
                },
                "language": {
                    "type": "string",
                    "description": "Language: python, javascript, typescript, go, rust, java, ruby, c, cpp",
                },
                "filename": {
                    "type": "string",
                    "description": "Proposed filename (used for language detection if language not specified)",
                },
                "threshold": {
                    "type": "number",
                    "description": "Similarity threshold 0.0-1.0 (default 0.50)",
                    "default": 0.50,
                },
                "repo_root": {
                    "type": "string",
                    "description": "Path to repo root (auto-detected if not provided)",
                },
            },
            "required": ["code"],
        },
    },
    {
        "name": "search_functions",
        "description": "Search the codebase index for functions matching a name or keyword.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Function name or keyword to search for",
                },
                "language": {
                    "type": "string",
                    "description": "Filter by language (optional)",
                },
                "repo_root": {
                    "type": "string",
                    "description": "Path to repo root (auto-detected if not provided)",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_index_stats",
        "description": "Get statistics about the Echo Guard function index and dependency graph.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_root": {
                    "type": "string",
                    "description": "Path to repo root (auto-detected if not provided)",
                },
            },
        },
    },
    {
        "name": "get_codebase_clusters",
        "description": "Get the dependency graph clusters showing how the codebase is organized by domain.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_root": {
                    "type": "string",
                    "description": "Path to repo root (auto-detected if not provided)",
                },
            },
        },
    },
    {
        "name": "suggest_refactor",
        "description": (
            "Get full context for refactoring two redundant functions into one. "
            "Returns both functions' complete source code, their callers, and dependency graph context. "
            "Use this AFTER check_before_write identifies a match, when you want to eliminate the redundancy "
            "rather than just importing. You provide the semantic judgment and generate the concrete edits."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "filepath_a": {
                    "type": "string",
                    "description": "File path of the first function",
                },
                "function_a": {
                    "type": "string",
                    "description": "Name of the first function",
                },
                "filepath_b": {
                    "type": "string",
                    "description": "File path of the second function",
                },
                "function_b": {
                    "type": "string",
                    "description": "Name of the second function",
                },
                "repo_root": {
                    "type": "string",
                    "description": "Path to repo root (auto-detected if not provided)",
                },
            },
            "required": ["filepath_a", "function_a", "filepath_b", "function_b"],
        },
    },
]

HANDLERS = {
    "check_before_write": handle_check_before_write,
    "search_functions": handle_search_functions,
    "get_index_stats": handle_get_stats,
    "get_codebase_clusters": handle_get_clusters,
    "suggest_refactor": handle_suggest_refactor,
}


def _send(msg: dict) -> None:
    data = json.dumps(msg)
    sys.stdout.write(f"Content-Length: {len(data)}\r\n\r\n{data}")
    sys.stdout.flush()


def _read_message() -> dict | None:
    headers = {}
    while True:
        line = sys.stdin.readline()
        if not line or line == "\r\n" or line == "\n":
            break
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()

    content_length = int(headers.get("content-length", 0))
    if content_length == 0:
        return None

    body = sys.stdin.read(content_length)
    return json.loads(body)


def run_server() -> None:
    """Run the MCP server on stdin/stdout."""
    while True:
        try:
            msg = _read_message()
        except (EOFError, KeyboardInterrupt):
            break

        if msg is None:
            break

        method = msg.get("method", "")
        msg_id = msg.get("id")
        params = msg.get("params", {})

        if method == "initialize":
            _send({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "echo-guard", "version": "0.1.0"},
                },
            })
        elif method == "notifications/initialized":
            pass
        elif method == "tools/list":
            _send({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"tools": TOOLS},
            })
        elif method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            handler = HANDLERS.get(tool_name)
            if handler:
                try:
                    result = handler(arguments)
                    _send({
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": {
                            "content": [{"type": "text", "text": json.dumps(result, indent=2)}]
                        },
                    })
                except Exception as e:
                    _send({
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": {
                            "content": [{"type": "text", "text": json.dumps({"error": str(e)})}],
                            "isError": True,
                        },
                    })
            else:
                _send({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
                })
        elif method == "ping":
            _send({"jsonrpc": "2.0", "id": msg_id, "result": {}})
        else:
            if msg_id is not None:
                _send({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                })


if __name__ == "__main__":
    run_server()
