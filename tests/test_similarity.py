"""Tests for the two-tier similarity detection engine.

Tier 1 (AST hash) tests run without embeddings.
Tier 2 (embedding) tests require the model to be downloaded and are
marked with @pytest.mark.slow for separate test runs.
"""

import pytest
from echo_guard.languages import extract_functions_universal
from echo_guard.similarity import SimilarityEngine


def _build_engine_from_code(snippets: list[tuple[str, str, str]]) -> tuple[SimilarityEngine, list]:
    """Build a similarity engine from code snippets (Tier 1 only — no embeddings)."""
    engine = SimilarityEngine()
    all_funcs = []
    for filename, code, lang in snippets:
        funcs = extract_functions_universal(filename, code, lang)
        for f in funcs:
            engine.add_function(f)
            all_funcs.append(f)
    return engine, all_funcs


def test_exact_structural_match():
    """Type-1/Type-2: Two functions with identical structure should match via AST hash."""
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
    assert matches[0].clone_type == "type1_type2"
    # Pairwise matches (2 copies) are REVIEW in DRY severity model
    assert matches[0].severity == "review"
    assert matches[0].similarity_score == 1.0


def test_renamed_identifiers_match():
    """Type-2: Functions with renamed identifiers match via normalized AST hash."""
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
    assert matches[0].match_type == "exact_structure"
    assert matches[0].clone_type == "type1_type2"


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


def test_clone_type_classification():
    """Verify clone type and severity are derived correctly from match_type and score."""
    from echo_guard.similarity import SimilarityMatch
    from echo_guard.languages import ExtractedFunction

    f = ExtractedFunction(
        name="test", filepath="a.py", language="python",
        lineno=1, end_lineno=5, source="def test(): pass",
    )

    # Tier 1: exact_structure → type1_type2, review (pairwise = 2 copies)
    # EXTRACT is reserved for FindingGroups with 3+ copies
    m1 = SimilarityMatch(source_func=f, existing_func=f, match_type="exact_structure",
                         similarity_score=0.60, raw_score=1.0, ast_similarity=1.0)
    assert m1.clone_type == "type1_type2"
    assert m1.severity == "review"
    assert m1.clone_type_label == "Exact/Renamed Clone"

    # Tier 2: embedding + high AST similarity (≥0.80) → type3, review (pairwise)
    m2 = SimilarityMatch(source_func=f, existing_func=f, match_type="embedding_semantic",
                         similarity_score=0.95, raw_score=0.97, ast_similarity=0.90)
    assert m2.clone_type == "type3"
    assert m2.severity == "review"

    # Tier 2: embedding + low AST similarity (<0.80) → type4, review (pairwise)
    m3 = SimilarityMatch(source_func=f, existing_func=f, match_type="embedding_semantic",
                         similarity_score=0.93, raw_score=0.94, ast_similarity=0.50)
    assert m3.clone_type == "type4"
    assert m3.severity == "review"


def test_batch_scan_tier1():
    """find_all_matches should find AST hash matches without embeddings."""
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
    matches = engine.find_all_matches(threshold=0.3)
    assert len(matches) >= 1
    assert matches[0].match_type == "exact_structure"
    assert matches[0].clone_type == "type1_type2"
