"""Tests for the similarity detection engine."""

import pytest
from echo_guard.languages import extract_functions_universal
from echo_guard.similarity import SimilarityEngine


def _build_engine_from_code(snippets: list[tuple[str, str, str]]) -> tuple[SimilarityEngine, list]:
    """Build a similarity engine from code snippets. Each is (filename, code, language)."""
    engine = SimilarityEngine(similarity_threshold=0.3)
    all_funcs = []
    for filename, code, lang in snippets:
        funcs = extract_functions_universal(filename, code, lang)
        for f in funcs:
            engine.add_function(f)
            all_funcs.append(f)
    return engine, all_funcs


def test_exact_structural_match():
    """Two functions with identical structure should match at 100%."""
    code_a = '''
def hash_password(password, salt=None):
    if salt is None:
        salt = generate_salt()
    return do_hash(password, salt), salt
'''
    code_b = '''
def create_password_hash(pwd, salt_val=None):
    if salt_val is None:
        salt_val = generate_salt()
    return do_hash(pwd, salt_val), salt_val
'''
    engine, funcs = _build_engine_from_code([
        ("a.py", code_a, "python"),
        ("b.py", code_b, "python"),
    ])
    matches = engine.find_similar(funcs[1], threshold=0.3)
    assert len(matches) >= 1
    assert matches[0].match_type == "exact_structure"
    assert matches[0].similarity_score == 1.0


def test_semantic_match():
    """Semantically similar functions should match via TF-IDF."""
    code_a = '''
def validate_email(email):
    import re
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$"
    return bool(re.match(pattern, email))
'''
    code_b = '''
def is_valid_email_address(addr):
    import re
    email_regex = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$"
    return bool(re.match(email_regex, addr))
'''
    engine, funcs = _build_engine_from_code([
        ("a.py", code_a, "python"),
        ("b.py", code_b, "python"),
    ])
    matches = engine.find_similar(funcs[1], threshold=0.3)
    assert len(matches) >= 1
    assert matches[0].similarity_score > 0.5


def test_no_false_positive():
    """Unrelated functions should not match."""
    code_a = '''
def validate_email(email):
    import re
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$"
    return bool(re.match(pattern, email))
'''
    code_b = '''
def calculate_fibonacci(n):
    if n <= 1:
        return n
    a, b = 0, 1
    for _ in range(2, n + 1):
        a, b = b, a + b
    return b
'''
    engine, funcs = _build_engine_from_code([
        ("a.py", code_a, "python"),
        ("b.py", code_b, "python"),
    ])
    matches = engine.find_similar(funcs[1], threshold=0.5)
    assert len(matches) == 0


def test_cross_language_detection():
    """Similar functions in different languages should be detected."""
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
    engine, funcs = _build_engine_from_code([
        ("utils.py", py_code, "python"),
        ("utils.js", js_code, "javascript"),
    ])
    matches = engine.find_similar(funcs[1], threshold=0.3)
    # Should find similarity based on shared tokens
    # (validate, email, pattern, result)
    assert len(matches) >= 1
    assert matches[0].similarity_score > 0.3
