"""Tests for the universal language parser."""

import pytest
from echo_guard.languages import (
    detect_language,
    extract_functions_universal,
    supported_languages,
)


def test_detect_language():
    assert detect_language("foo.py") == "python"
    assert detect_language("bar.js") == "javascript"
    assert detect_language("baz.ts") == "typescript"
    assert detect_language("qux.go") == "go"
    assert detect_language("lib.rs") == "rust"
    assert detect_language("Main.java") == "java"
    assert detect_language("app.rb") == "ruby"
    assert detect_language("main.c") == "c"
    assert detect_language("main.cpp") == "cpp"
    assert detect_language("data.csv") is None


def test_supported_languages():
    langs = supported_languages()
    assert "python" in langs
    assert "javascript" in langs
    assert "go" in langs
    assert len(langs) == 9


PYTHON_CODE = '''
def add(a, b):
    return a + b

def multiply(x, y):
    result = x * y
    return result
'''

JS_CODE = '''
function add(a, b) {
    return a + b;
}

function multiply(x, y) {
    const result = x * y;
    return result;
}
'''

TS_CODE = '''
export function add(a: number, b: number): number {
    return a + b;
}

export function multiply(x: number, y: number): number {
    const result = x * y;
    return result;
}
'''

GO_CODE = '''
package math

func Add(a int, b int) int {
    return a + b
}

func Multiply(x int, y int) int {
    result := x * y
    return result
}
'''

RUST_CODE = '''
fn add(a: i32, b: i32) -> i32 {
    a + b
}

fn multiply(x: i32, y: i32) -> i32 {
    let result = x * y;
    result
}
'''

JAVA_CODE = '''
public class Math {
    public static int add(int a, int b) {
        return a + b;
    }

    public static int multiply(int x, int y) {
        int result = x * y;
        return result;
    }
}
'''


@pytest.mark.parametrize("code,lang,expected_names", [
    (PYTHON_CODE, "python", ["add", "multiply"]),
    (JS_CODE, "javascript", ["add", "multiply"]),
    (TS_CODE, "typescript", ["add", "multiply"]),
    (GO_CODE, "go", ["Add", "Multiply"]),
    (RUST_CODE, "rust", ["add", "multiply"]),
    (JAVA_CODE, "java", ["add", "multiply"]),
])
def test_extract_functions(code, lang, expected_names):
    funcs = extract_functions_universal(f"test.{lang}", code, lang)
    names = [f.name for f in funcs]
    assert names == expected_names, f"Expected {expected_names}, got {names}"
    for func in funcs:
        assert func.language == lang
        assert func.param_count == 2
        assert func.ast_hash  # should have a hash


def test_structural_hash_normalization():
    """Functions with same structure but different names should have same hash."""
    code_a = '''
def add(a, b):
    return a + b
'''
    code_b = '''
def sum_values(x, y):
    return x + y
'''
    funcs_a = extract_functions_universal("a.py", code_a, "python")
    funcs_b = extract_functions_universal("b.py", code_b, "python")
    assert len(funcs_a) == 1 and len(funcs_b) == 1
    assert funcs_a[0].ast_hash == funcs_b[0].ast_hash


def test_different_structure_different_hash():
    """Functions with different structure should have different hashes."""
    code_a = '''
def add(a, b):
    return a + b
'''
    code_b = '''
def add_with_log(a, b):
    print(a, b)
    result = a + b
    return result
'''
    funcs_a = extract_functions_universal("a.py", code_a, "python")
    funcs_b = extract_functions_universal("b.py", code_b, "python")
    assert funcs_a[0].ast_hash != funcs_b[0].ast_hash
