"""Tests for dependency graph routing."""

import pytest
from echo_guard.depgraph import DependencyGraph, _detect_cluster, _extract_keywords
from echo_guard.languages import ExtractedFunction


def _make_func(name: str, filepath: str, language: str = "python", calls: list[str] | None = None) -> ExtractedFunction:
    return ExtractedFunction(
        name=name,
        filepath=filepath,
        language=language,
        lineno=1,
        end_lineno=5,
        source=f"def {name}(): pass",
        calls_made=calls or [],
    )


def test_cluster_detection():
    keywords = _extract_keywords("src/auth/login.py", {"hash_password", "verify_token"})
    cluster = _detect_cluster(keywords)
    assert cluster == "auth"


def test_cluster_detection_api():
    keywords = _extract_keywords("routes/api_handler.py", {"handle_request", "format_response"})
    cluster = _detect_cluster(keywords)
    assert cluster == "api"


def test_cluster_detection_util():
    keywords = _extract_keywords("lib/utils/helpers.py", {"format_date", "parse_config"})
    cluster = _detect_cluster(keywords)
    assert cluster == "util"


def test_graph_build():
    graph = DependencyGraph()
    funcs_a = [_make_func("hash_password", "auth/password.py", calls=["pbkdf2"])]
    funcs_b = [_make_func("login_user", "auth/login.py", calls=["hash_password"])]
    funcs_c = [_make_func("format_response", "api/response.py")]

    graph.add_module("auth/password.py", funcs_a)
    graph.add_module("auth/login.py", funcs_b)
    graph.add_module("api/response.py", funcs_c)
    graph.build()

    assert graph.get_cluster("auth/password.py") == "auth"
    assert graph.get_cluster("api/response.py") == "api"

    related = graph.get_related_files("auth/login.py")
    assert "auth/password.py" in related


def test_graph_comparison_candidates():
    graph = DependencyGraph()
    func_a = _make_func("hash_password", "auth/password.py")
    func_b = _make_func("verify_password", "auth/verify.py", calls=["hash_password"])
    func_c = _make_func("render_page", "ui/page.py")

    graph.add_module("auth/password.py", [func_a])
    graph.add_module("auth/verify.py", [func_b])
    graph.add_module("ui/page.py", [func_c])
    graph.build()

    all_funcs = {
        "auth/password.py": [func_a],
        "auth/verify.py": [func_b],
        "ui/page.py": [func_c],
    }

    # Auth module should prioritize auth candidates
    candidates = graph.get_comparison_candidates("auth/verify.py", all_funcs)
    names = [c.name for c in candidates]
    # hash_password should come before render_page (same cluster)
    assert names.index("hash_password") < names.index("render_page")
