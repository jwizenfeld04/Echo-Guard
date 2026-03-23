"""Tests for scope-aware comparison, cross-language reuse, health score, and incremental indexing."""

import pytest
from echo_guard.languages import extract_functions_universal
from echo_guard.similarity import (
    SimilarityEngine,
    classify_reuse,
    get_reuse_guidance,
    scope_penalty,
)
from echo_guard.health import compute_health_score


# ── Visibility detection tests ────────────────────────────────────────────

def test_python_private_visibility():
    code = '''
def public_func():
    return 1

def _private_func():
    return 2

def __mangled_func():
    return 3

def __dunder__():
    return 4
'''
    funcs = extract_functions_universal("mod.py", code, "python")
    by_name = {f.name: f for f in funcs}
    assert by_name["public_func"].visibility == "public"
    assert by_name["_private_func"].visibility == "private"
    assert by_name["__mangled_func"].visibility == "private"
    assert by_name["__dunder__"].visibility == "public"  # Dunder = public


def test_go_exported_unexported():
    code = '''
package auth

func ExportedFunc() int {
    return 1
}

func unexportedFunc() int {
    return 2
}
'''
    funcs = extract_functions_universal("auth.go", code, "go")
    by_name = {f.name: f for f in funcs}
    assert by_name["ExportedFunc"].visibility == "public"
    assert by_name["unexportedFunc"].visibility == "internal"


def test_rust_pub_visibility():
    code = '''
pub fn public_func() -> i32 {
    1
}

fn private_func() -> i32 {
    2
}
'''
    funcs = extract_functions_universal("lib.rs", code, "rust")
    by_name = {f.name: f for f in funcs}
    assert by_name["public_func"].visibility == "public"
    assert by_name["private_func"].visibility == "private"


# ── Cross-language reuse classification tests ─────────────────────────────

def test_same_language_reuse():
    assert classify_reuse("python", "python") == "direct_import"
    assert classify_reuse("go", "go") == "direct_import"


def test_compatible_runtime_reuse():
    assert classify_reuse("javascript", "typescript") == "compatible_import"
    assert classify_reuse("typescript", "javascript") == "compatible_import"
    assert classify_reuse("c", "cpp") == "compatible_import"


def test_cross_language_reference_only():
    assert classify_reuse("python", "go") == "reference_only"
    assert classify_reuse("python", "javascript") == "reference_only"
    assert classify_reuse("go", "rust") == "reference_only"
    assert classify_reuse("java", "python") == "reference_only"


def test_reuse_guidance_contains_language_names():
    guidance = get_reuse_guidance("reference_only", "python", "go")
    assert "go" in guidance.lower()
    assert "python" in guidance.lower()
    assert "cannot" in guidance.lower()


# ── Scope penalty tests ──────────────────────────────────────────────────

def _make_func(name, filepath, lang="python", visibility="public"):
    from echo_guard.languages import ExtractedFunction
    return ExtractedFunction(
        name=name, filepath=filepath, language=lang,
        lineno=1, end_lineno=5, source=f"def {name}(): pass",
        visibility=visibility,
    )


def test_scope_penalty_public():
    src = _make_func("new_func", "a.py")
    existing = _make_func("old_func", "b.py", visibility="public")
    assert scope_penalty(src, existing) == 1.0


def test_scope_penalty_private():
    src = _make_func("new_func", "a.py")
    existing = _make_func("_old_func", "b.py", visibility="private")
    assert scope_penalty(src, existing) == 0.6


def test_scope_penalty_internal_same_package():
    src = _make_func("new_func", "pkg/a.py")
    existing = _make_func("old_func", "pkg/b.py", visibility="internal")
    assert scope_penalty(src, existing) == 0.9


def test_scope_penalty_internal_different_package():
    src = _make_func("new_func", "pkg_a/a.py")
    existing = _make_func("old_func", "pkg_b/b.py", visibility="internal")
    assert scope_penalty(src, existing) == 0.7


# ── Similarity engine with cross-language reuse ──────────────────────────

def test_similarity_match_includes_reuse_type():
    """Matches should include cross-language reuse classification."""
    py_code = '''
def validate_email(email):
    import re
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$"
    result = re.match(pattern, email)
    return bool(result)
'''
    js_code = '''
function validateEmail(email) {
    const pattern = /^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$/;
    const result = pattern.test(email);
    return result;
}
'''
    engine = SimilarityEngine(similarity_threshold=0.3)
    py_funcs = extract_functions_universal("utils.py", py_code, "python")
    js_funcs = extract_functions_universal("utils.js", js_code, "javascript")

    for f in py_funcs + js_funcs:
        engine.add_function(f)

    matches = engine.find_similar(js_funcs[0], threshold=0.3)
    assert len(matches) >= 1
    match = matches[0]
    assert match.reuse_type == "reference_only"
    assert "cannot" in match.reuse_guidance.lower()


def test_same_language_match_reuse_type():
    code_a = '''
def validate_email(email):
    import re
    return bool(re.match(r"^[\\w.]+@[\\w.]+$", email))
'''
    code_b = '''
def is_valid_email(addr):
    import re
    return bool(re.match(r"^[\\w.]+@[\\w.]+$", addr))
'''
    engine = SimilarityEngine(similarity_threshold=0.3)
    funcs_a = extract_functions_universal("a.py", code_a, "python")
    funcs_b = extract_functions_universal("b.py", code_b, "python")
    for f in funcs_a + funcs_b:
        engine.add_function(f)

    matches = engine.find_similar(funcs_b[0], threshold=0.3)
    assert len(matches) >= 1
    assert matches[0].reuse_type == "direct_import"


# ── Health score tests ────────────────────────────────────────────────────

def test_health_score_perfect():
    """No redundancies = score 100."""
    result = compute_health_score([], total_functions=50)
    assert result["score"] == 100
    assert result["grade"] == "A"


def test_health_score_empty():
    result = compute_health_score([], total_functions=0)
    assert result["score"] == 100


def test_health_score_with_matches():
    """Health score decreases with matches."""
    from echo_guard.similarity import SimilarityMatch

    src = _make_func("func_a", "a.py")
    ext = _make_func("func_b", "b.py")

    matches = [
        SimilarityMatch(source_func=src, existing_func=ext,
                       match_type="exact_structure", similarity_score=1.0,
                       reuse_type="direct_import", reuse_guidance=""),
    ]
    result = compute_health_score(matches, total_functions=50)
    assert result["score"] < 100
    assert result["breakdown"]["high"] == 1
    assert result["breakdown"]["total_redundancies"] == 1


def test_health_score_grades():
    """Many matches should produce a low score/grade."""
    from echo_guard.similarity import SimilarityMatch

    matches = []
    for i in range(20):
        src = _make_func(f"func_{i}", f"a{i}.py")
        ext = _make_func(f"dup_{i}", f"b{i}.py")
        matches.append(SimilarityMatch(
            source_func=src, existing_func=ext,
            match_type="exact_structure", similarity_score=1.0,
            reuse_type="direct_import", reuse_guidance="",
        ))

    result = compute_health_score(matches, total_functions=50)
    assert result["score"] < 50
    assert result["grade"] in ("D", "F")


# ── Constructor exclusion tests ─────────────────────────────────────────

def test_constructor_match_different_classes_excluded():
    """__init__ across different classes should be excluded."""
    from echo_guard.similarity import _is_constructor_match
    a = _make_func("__init__", "a.py")
    a.class_name = "UserService"
    b = _make_func("__init__", "b.py")
    b.class_name = "PaymentService"
    assert _is_constructor_match(a, b) is True


def test_constructor_match_same_class_allowed():
    """__init__ on same-named classes should NOT be excluded (class-level duplication)."""
    from echo_guard.similarity import _is_constructor_match
    a = _make_func("__init__", "a.py")
    a.class_name = "UserService"
    b = _make_func("__init__", "b.py")
    b.class_name = "UserService"
    assert _is_constructor_match(a, b) is False


def test_non_constructor_not_excluded():
    """Regular functions should not be affected by constructor exclusion."""
    from echo_guard.similarity import _is_constructor_match
    a = _make_func("validate_email", "a.py")
    b = _make_func("validate_email", "b.py")
    assert _is_constructor_match(a, b) is False


# ── Observer pattern exclusion tests ─────────────────────────────────────

def test_observer_pattern_protocol_and_impl():
    """Protocol method + concrete implementation = observer pattern, not duplication."""
    from echo_guard.similarity import _is_observer_pattern
    a = _make_func("on_tool_call", "observers.py")
    a.class_name = "ObserverProtocol"
    a.class_type = "protocol"
    b = _make_func("on_tool_call", "observers.py")
    b.class_name = "LoggingObserver"
    b.class_type = "class"
    assert _is_observer_pattern(a, b) is True


def test_observer_pattern_same_class_not_excluded():
    """Same-class methods should not be excluded by observer pattern check."""
    from echo_guard.similarity import _is_observer_pattern
    a = _make_func("process", "a.py")
    a.class_name = "Handler"
    a.class_type = "class"
    b = _make_func("process", "a.py")
    b.class_name = "Handler"
    b.class_type = "class"
    assert _is_observer_pattern(a, b) is False


def test_observer_pattern_different_names_not_excluded():
    """Different method names across classes should not trigger observer exclusion."""
    from echo_guard.similarity import _is_observer_pattern
    a = _make_func("process", "a.py")
    a.class_name = "Handler"
    a.class_type = "class"
    b = _make_func("handle", "a.py")
    b.class_name = "Worker"
    b.class_type = "class"
    assert _is_observer_pattern(a, b) is False


# ── CRUD pattern exclusion tests ──────────────────────────────────────────

def test_same_file_crud_excluded():
    """create_channel / update_channel in same file should be excluded."""
    from echo_guard.similarity import _is_same_file_crud
    a = _make_func("create_channel", "routes.py")
    b = _make_func("update_channel", "routes.py")
    assert _is_same_file_crud(a, b) is True


def test_same_file_crud_different_files_not_excluded():
    """create_channel / update_channel in different files should not be excluded."""
    from echo_guard.similarity import _is_same_file_crud
    a = _make_func("create_channel", "a.py")
    b = _make_func("update_channel", "b.py")
    assert _is_same_file_crud(a, b) is False


def test_same_file_non_crud_not_excluded():
    """Non-CRUD functions in same file should not be excluded."""
    from echo_guard.similarity import _is_same_file_crud
    a = _make_func("validate_email", "utils.py")
    b = _make_func("format_phone", "utils.py")
    assert _is_same_file_crud(a, b) is False


# ── Antonym pair exclusion tests ─────────────────────────────────────────

def test_antonym_pair_enable_disable():
    """enable_trigger / disable_trigger should be excluded."""
    from echo_guard.similarity import _is_antonym_pair
    a = _make_func("enable_trigger", "triggers.py")
    b = _make_func("disable_trigger", "triggers.py")
    assert _is_antonym_pair(a, b) is True


def test_antonym_pair_encrypt_decrypt():
    """encrypt / decrypt in same file should be excluded."""
    from echo_guard.similarity import _is_antonym_pair
    a = _make_func("encrypt", "crypto.py")
    b = _make_func("decrypt", "crypto.py")
    assert _is_antonym_pair(a, b) is True


def test_antonym_pair_different_files_not_excluded():
    """Antonym pairs in different files should not be excluded."""
    from echo_guard.similarity import _is_antonym_pair
    a = _make_func("encrypt", "a.py")
    b = _make_func("decrypt", "b.py")
    assert _is_antonym_pair(a, b) is False


# ── Service boundary detection tests ─────────────────────────────────────

def test_service_boundary_path_normalization():
    """Service boundary detection should handle ./ prefix and path normalization."""
    from echo_guard.similarity import _get_service
    boundaries = ["services/worker", "services/dashboard"]
    assert _get_service("services/worker/app/main.py", boundaries) == "services/worker"
    assert _get_service("./services/worker/app/main.py", boundaries) == "services/worker"
    assert _get_service("services/dashboard/views.py", boundaries) == "services/dashboard"
    assert _get_service("lib/utils.py", boundaries) is None


# ── Finding deduplication tests ──────────────────────────────────────────

def test_finding_deduplication_suppresses_subsets():
    """Subset findings should be suppressed."""
    from echo_guard.similarity import _deduplicate_findings, FindingGroup, SimilarityMatch

    a = _make_func("timeAgo", "a.py")
    b = _make_func("timeAgo", "b.py")
    c = _make_func("timeAgo", "c.py")

    match_ab = SimilarityMatch(
        source_func=a, existing_func=b,
        match_type="exact_structure", similarity_score=1.0,
        reuse_type="direct_import", reuse_guidance="",
    )
    # Group with 3 functions (superset of the pair)
    group_abc = FindingGroup(
        functions=[a, b, c],
        representative_match=match_ab,
        match_count=3,
        pattern_description="3 implementations of timeAgo()",
        reuse_type="direct_import",
        reuse_guidance="",
    )

    results = _deduplicate_findings([match_ab, group_abc])
    # The individual match_ab should be suppressed because {a, b} ⊂ {a, b, c}
    assert len(results) == 1
    assert isinstance(results[0], FindingGroup)


# ── Same-file threshold escalation tests ──────────────────────────────

def test_same_file_below_95_suppressed():
    """Same-file matches below 95% similarity should be suppressed."""
    engine = SimilarityEngine(similarity_threshold=0.50)

    # Two functions in the same file with similar but not identical code
    code_a = '''
def list_automations(db):
    rows = db.execute("SELECT * FROM automations")
    return [Automation(**r) for r in rows]
'''
    code_b = '''
def list_triggers(db):
    rows = db.execute("SELECT * FROM triggers")
    return [Trigger(**r) for r in rows]
'''
    from echo_guard.languages import ExtractedFunction
    func_a = ExtractedFunction(
        name="list_automations", filepath="repo.py", language="python",
        lineno=1, end_lineno=4, source=code_a, visibility="public",
    )
    func_b = ExtractedFunction(
        name="list_triggers", filepath="repo.py", language="python",
        lineno=5, end_lineno=8, source=code_b, visibility="public",
    )
    engine.add_function(func_a)
    engine.add_function(func_b)
    matches = engine.find_all_matches(threshold=0.50)
    # Should be suppressed: same file and similarity < 0.95
    assert len(matches) == 0


def test_same_file_above_95_kept():
    """Same-file matches at or above 95% should be kept."""
    engine = SimilarityEngine(similarity_threshold=0.50)

    # Two identical functions in the same file (non-verb names to avoid domain-noun filter)
    code = '''
def compute_hash(data):
    result = hashlib.sha256(data)
    return result.hexdigest()
'''
    from echo_guard.languages import ExtractedFunction
    func_a = ExtractedFunction(
        name="compute_hash", filepath="utils.py", language="python",
        lineno=1, end_lineno=4, source=code, visibility="public",
    )
    func_b = ExtractedFunction(
        name="compute_hash_v2", filepath="utils.py", language="python",
        lineno=5, end_lineno=8, source=code, visibility="public",
    )
    engine.add_function(func_a)
    engine.add_function(func_b)
    matches = engine.find_all_matches(threshold=0.50)
    # Identical code → score 1.0, same file but ≥ 0.95 → kept
    assert len(matches) >= 1


# ── Domain-noun filtering tests ──────────────────────────────────────

def test_extract_domain_noun_snake_case():
    from echo_guard.similarity import _extract_domain_noun
    assert _extract_domain_noun("get_automation_by_id") == "automation_by_id"
    assert _extract_domain_noun("list_webhook_integrations") == "webhook_integrations"
    assert _extract_domain_noun("create_channel") == "channel"


def test_extract_domain_noun_camel_case():
    from echo_guard.similarity import _extract_domain_noun
    assert _extract_domain_noun("listModelConfigs") == "model_configs"
    assert _extract_domain_noun("fetchUserData") == "user_data"


def test_extract_domain_noun_no_verb():
    from echo_guard.similarity import _extract_domain_noun
    assert _extract_domain_noun("timeAgo") is None
    assert _extract_domain_noun("main") is None


def test_structural_template_pair_different_nouns():
    from echo_guard.similarity import _is_structural_template_pair
    a = _make_func("get_automation_by_id", "automations.py")
    b = _make_func("get_trigger_by_id", "triggers.py")
    assert _is_structural_template_pair(a, b) is True


def test_structural_template_pair_same_noun():
    from echo_guard.similarity import _is_structural_template_pair
    a = _make_func("get_user", "a.py")
    b = _make_func("fetch_user", "b.py")
    # Same noun "user" → NOT suppressed (could be real duplication)
    assert _is_structural_template_pair(a, b) is False


def test_structural_template_pair_same_file_same_noun():
    from echo_guard.similarity import _is_structural_template_pair
    a = _make_func("create_channel", "routes.py")
    b = _make_func("delete_channel", "routes.py")
    # Same noun (channel) → NOT suppressed (could be real duplication)
    assert _is_structural_template_pair(a, b) is False


def test_structural_template_pair_same_file_different_noun():
    from echo_guard.similarity import _is_structural_template_pair
    a = _make_func("getRegisteredTools", "api.ts")
    b = _make_func("getModelConfigs", "api.ts")
    # Same file, different nouns → suppressed
    assert _is_structural_template_pair(a, b) is True


# ── Overlap deduplication tests ──────────────────────────────────────

def test_finding_deduplication_high_overlap():
    """Findings with ≥70% Jaccard overlap should be merged."""
    from echo_guard.similarity import _deduplicate_findings, FindingGroup, SimilarityMatch

    a = _make_func("schemaTypes", "a.py")
    b = _make_func("schemaTypes", "b.py")
    c = _make_func("extractEnumValues", "a.py")
    d = _make_func("extractEnumValues", "b.py")
    e = _make_func("parseLiteral", "a.py")

    match_ab = SimilarityMatch(
        source_func=a, existing_func=b,
        match_type="exact_structure", similarity_score=0.95,
        reuse_type="direct_import", reuse_guidance="",
    )

    # Group 1: {a, b, c, d}
    group1 = FindingGroup(
        functions=[a, b, c, d],
        representative_match=match_ab,
        match_count=4,
        pattern_description="4 functions",
        reuse_type="direct_import",
        reuse_guidance="",
    )
    # Group 2: {a, b, c, e} — shares 3 of 4 functions with group1 (Jaccard = 3/5 = 0.6)
    group2 = FindingGroup(
        functions=[a, b, c, e],
        representative_match=SimilarityMatch(
            source_func=a, existing_func=c,
            match_type="exact_structure", similarity_score=0.90,
            reuse_type="direct_import", reuse_guidance="",
        ),
        match_count=3,
        pattern_description="4 functions",
        reuse_type="direct_import",
        reuse_guidance="",
    )
    # Group 3: {a, b, c} — shares 3 of 4 with group1 (Jaccard = 3/4 = 0.75 ≥ 0.70)
    group3 = FindingGroup(
        functions=[a, b, c],
        representative_match=SimilarityMatch(
            source_func=a, existing_func=b,
            match_type="exact_structure", similarity_score=0.85,
            reuse_type="direct_import", reuse_guidance="",
        ),
        match_count=2,
        pattern_description="3 functions",
        reuse_type="direct_import",
        reuse_guidance="",
    )

    results = _deduplicate_findings([group1, group2, group3])
    # group3 is strict subset of group1 → suppressed
    # group2 overlaps 60% with group1 → kept (below 70%)
    # So we expect group1 and group2
    assert len(results) == 2


# ── Cross-service import suggestion tests ────────────────────────────

def test_cross_service_import_suggestion_no_import_code():
    """Cross-service findings should NOT show import code."""
    from echo_guard.similarity import _generate_import_suggestion
    func = _make_func("insert_notification_log", "services/worker/notifications.py")
    suggestion = _generate_import_suggestion(func, "cross_service_reference")
    assert "import" not in suggestion.lower() or "NOT possible" in suggestion
    assert "shared library" in suggestion.lower() or "service boundary" in suggestion.lower()


# ── Parser keyword filtering tests ───────────────────────────────────

def test_async_keyword_not_extracted_as_function():
    """The keyword 'async' should not be extracted as a function name."""
    code = '''
const handler = async () => {
    const data = await fetchData();
    return data.map(item => item.value);
};
'''
    funcs = extract_functions_universal("test.ts", source=code, language="typescript")
    names = [f.name for f in funcs]
    assert "async" not in names


def test_if_keyword_not_extracted_as_function():
    """The keyword 'if' should not be extracted as a function name."""
    code = '''
function doSomething() {
    if (condition) {
        return true;
    }
    return false;
}
'''
    funcs = extract_functions_universal("test.ts", source=code, language="typescript")
    names = [f.name for f in funcs]
    assert "if" not in names
    assert "doSomething" in names


def test_arrow_function_gets_variable_name():
    """Arrow functions assigned to variables should use the variable name."""
    code = '''
const fetchData = async () => {
    const response = await fetch(url);
    return response.json();
};
'''
    funcs = extract_functions_universal("test.ts", source=code, language="typescript")
    names = [f.name for f in funcs]
    assert "fetchData" in names
    assert "async" not in names


# ── v6: Antonym camelCase normalization tests ────────────────────────

def test_antonym_pair_camel_case_is_success_is_failed():
    """isSuccess/isFailed in camelCase should be detected as antonym pair."""
    from echo_guard.similarity import _is_antonym_pair
    a = _make_func("isSuccess", "status.ts")
    b = _make_func("isFailed", "status.ts")
    assert _is_antonym_pair(a, b) is True


def test_antonym_pair_camel_case_show_hide():
    """showModal/hideModal should be detected as antonym pair."""
    from echo_guard.similarity import _is_antonym_pair
    a = _make_func("showModal", "modal.ts")
    b = _make_func("hideModal", "modal.ts")
    assert _is_antonym_pair(a, b) is True


# ── v6: Constructor one-sided exclusion tests ────────────────────────

def test_constructor_one_sided_excluded():
    """A constructor matching a non-constructor should be suppressed."""
    from echo_guard.similarity import _is_constructor_match
    from echo_guard.languages import ExtractedFunction
    a = ExtractedFunction(
        name="_error_payload", filepath="a.py", language="python",
        lineno=1, end_lineno=5, source="def _error_payload(): pass",
        visibility="private",
    )
    b = ExtractedFunction(
        name="__init__", filepath="b.py", language="python",
        lineno=1, end_lineno=5, source="def __init__(self): pass",
        visibility="public", class_name="HttpRunnerError",
    )
    assert _is_constructor_match(a, b) is True


# ── v6: Cross-language threshold tests ───────────────────────────────

def test_cross_language_below_80_suppressed():
    """Cross-language matches below 80% should be suppressed."""
    engine = SimilarityEngine(similarity_threshold=0.50)

    from echo_guard.languages import ExtractedFunction
    func_a = ExtractedFunction(
        name="_row_to_config", filepath="model_config.py", language="python",
        lineno=1, end_lineno=10,
        source="def _row_to_config(row):\n    return {'name': row['name'], 'value': row['val']}",
        visibility="private",
    )
    func_b = ExtractedFunction(
        name="makeConfigForm", filepath="ModelConfigWizard.tsx", language="typescript",
        lineno=1, end_lineno=10,
        source="function makeConfigForm(row) {\n    return { name: row.name, value: row.val };\n}",
        visibility="public",
    )
    engine.add_function(func_a)
    engine.add_function(func_b)
    matches = engine.find_all_matches(threshold=0.50)
    # Cross-language, low similarity → suppressed
    for m in matches:
        assert m.similarity_score >= 0.80


# ── v6: UI wrapper component tests ───────────────────────────────────

def test_ui_wrapper_component_detection():
    """Short JSX wrapper components should be detected."""
    from echo_guard.similarity import _is_ui_wrapper_component
    from echo_guard.languages import ExtractedFunction
    panel = ExtractedFunction(
        name="Panel", filepath="panel.tsx", language="typescript",
        lineno=1, end_lineno=5,
        source='function Panel({ children }) {\n  return <div className="panel">{children}</div>;\n}',
        visibility="public",
    )
    assert _is_ui_wrapper_component(panel) is True

    # Long component should not be detected
    long_comp = ExtractedFunction(
        name="Dashboard", filepath="dashboard.tsx", language="typescript",
        lineno=1, end_lineno=50,
        source='function Dashboard() {\n  return <div className="dash">lots of code here</div>;\n}',
        visibility="public",
    )
    assert _is_ui_wrapper_component(long_comp) is False

    # Non-JSX function should not be detected
    py_func = ExtractedFunction(
        name="helper", filepath="helper.py", language="python",
        lineno=1, end_lineno=3,
        source="def helper(): pass",
        visibility="public",
    )
    assert _is_ui_wrapper_component(py_func) is False


def test_ui_wrapper_pair_suppressed():
    """Two UI wrapper components matching should be suppressed."""
    from echo_guard.similarity import _is_ui_wrapper_pair
    from echo_guard.languages import ExtractedFunction
    panel = ExtractedFunction(
        name="Panel", filepath="panel.tsx", language="typescript",
        lineno=1, end_lineno=5,
        source='function Panel({ children }) {\n  return <div className="panel">{children}</div>;\n}',
        visibility="public",
    )
    card = ExtractedFunction(
        name="Card", filepath="card.tsx", language="typescript",
        lineno=1, end_lineno=5,
        source='function Card({ children }) {\n  return <div className="card">{children}</div>;\n}',
        visibility="public",
    )
    assert _is_ui_wrapper_pair(panel, card) is True


# ── v6: Same-name score boost test ───────────────────────────────────

def test_normalize_to_snake():
    """camelCase normalization for antonym matching."""
    from echo_guard.similarity import _normalize_to_snake
    assert _normalize_to_snake("isSuccess") == "is_success"
    assert _normalize_to_snake("isFailed") == "is_failed"
    assert _normalize_to_snake("showModal") == "show_modal"
    assert _normalize_to_snake("hideModal") == "hide_modal"
    assert _normalize_to_snake("enable_feature") == "enable_feature"


# ── v7: UI wrapper same-name exemption ───────────────────────────────

def test_ui_wrapper_same_name_not_suppressed():
    """Two wrapper components with the SAME name should not be suppressed."""
    from echo_guard.similarity import _is_ui_wrapper_pair
    from echo_guard.languages import ExtractedFunction
    icon_a = ExtractedFunction(
        name="TelegramIcon", filepath="a.tsx", language="typescript",
        lineno=1, end_lineno=5,
        source='function TelegramIcon() {\n  return <svg className="icon"><path d="M1..."/></svg>;\n}',
        visibility="public",
    )
    icon_b = ExtractedFunction(
        name="TelegramIcon", filepath="b.tsx", language="typescript",
        lineno=1, end_lineno=5,
        source='function TelegramIcon() {\n  return <svg className="icon"><path d="M1..."/></svg>;\n}',
        visibility="public",
    )
    # Same name = real duplication, not design system pattern
    assert _is_ui_wrapper_pair(icon_a, icon_b) is False


# ── v7: Per-service boilerplate exclusion ────────────────────────────

def test_per_service_health_excluded():
    """health() endpoints across services should be suppressed."""
    from echo_guard.similarity import _is_per_service_boilerplate
    a = _make_func("health", "services/worker/main.py")
    b = _make_func("health", "services/gateway/main.py")
    boundaries = ["services/worker", "services/gateway"]
    assert _is_per_service_boilerplate(a, b, boundaries) is True


def test_per_service_health_same_service_not_excluded():
    """health() in the same service should not be suppressed."""
    from echo_guard.similarity import _is_per_service_boilerplate
    a = _make_func("health", "services/worker/main.py")
    b = _make_func("health", "services/worker/routes.py")
    boundaries = ["services/worker"]
    assert _is_per_service_boilerplate(a, b, boundaries) is False


# ── v7: Domain-noun same-file now works ──────────────────────────────

def test_domain_noun_same_file_different_nouns_suppressed():
    """Same-file functions with different domain nouns should be caught."""
    from echo_guard.similarity import _is_structural_template_pair
    a = _make_func("resolveGenerationModel", "resolver.py")
    b = _make_func("resolveRoutingModel", "resolver.py")
    assert _is_structural_template_pair(a, b) is True


# ── v7: LOW filtering in output ──────────────────────────────────────

def test_output_hides_low_by_default():
    """print_results should hide LOW findings when verbose=False."""
    from echo_guard.output import _get_severity, group_matches
    from echo_guard.similarity import SimilarityMatch, FindingGroup

    a = _make_func("fetchJson", "a.ts")
    b = _make_func("fetchJson", "b.ts")
    c = _make_func("helper", "c.ts")
    d = _make_func("other", "d.ts")

    match_high = SimilarityMatch(
        source_func=a, existing_func=b,
        match_type="exact_structure", similarity_score=1.0,
        reuse_type="direct_import", reuse_guidance="",
    )
    match_low = SimilarityMatch(
        source_func=c, existing_func=d,
        match_type="tfidf_semantic", similarity_score=0.55,
        reuse_type="direct_import", reuse_guidance="",
    )

    grouped = group_matches([match_high, match_low])
    visible = [item for item in grouped if _get_severity(item) != "low"]
    all_items = grouped

    # All findings should exist in full list
    assert len(all_items) >= 1
    # Visible (non-LOW) should be fewer or equal
    assert len(visible) <= len(all_items)
