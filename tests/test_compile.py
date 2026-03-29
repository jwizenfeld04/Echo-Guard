"""Ensure every module in echo_guard/ compiles without syntax errors.

Catches issues like `break` outside a loop that ast.parse() misses but
Python raises at compile time. Modules with optional heavy dependencies
(fastmcp, onnxruntime) are never imported during the normal test run,
so this is the only safety net for them.
"""

import subprocess
import sys


def test_all_modules_compile():
    result = subprocess.run(
        [sys.executable, "-m", "compileall", "-q", "echo_guard/"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
