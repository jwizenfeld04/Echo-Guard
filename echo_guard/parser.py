"""AST parsing and function extraction for Python source files."""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FunctionInfo:
    """Extracted metadata about a single function or method."""

    name: str
    filepath: str
    lineno: int
    end_lineno: int | None
    source: str
    # Stage 1: structural fingerprint
    ast_hash: str = ""
    # Stage 2: signature metadata
    param_count: int = 0
    has_return: bool = False
    return_type: str | None = None
    imports_used: list[str] = field(default_factory=list)
    decorators: list[str] = field(default_factory=list)
    calls_made: list[str] = field(default_factory=list)
    # Computed later
    signature_key: str = ""
    is_nested: bool = False  # True if defined inside another function (closure)

    @property
    def qualified_name(self) -> str:
        return f"{self.filepath}:{self.name}:{self.lineno}"


class _ASTNormalizer(ast.NodeTransformer):
    """Normalize an AST by stripping names, docstrings, and comments.

    This makes structurally identical code produce the same hash
    regardless of variable naming.
    """

    _name_counter: int

    def __init__(self) -> None:
        self._name_counter = 0
        self._name_map: dict[str, str] = {}

    def _normalize_name(self, name: str) -> str:
        if name not in self._name_map:
            self._name_map[name] = f"_v{self._name_counter}"
            self._name_counter += 1
        return self._name_map[name]

    def visit_Name(self, node: ast.Name) -> ast.AST:
        node.id = self._normalize_name(node.id)
        return self.generic_visit(node)

    def visit_arg(self, node: ast.arg) -> ast.AST:
        node.arg = self._normalize_name(node.arg)
        node.annotation = None
        return self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        node.name = self._normalize_name(node.name)
        node.decorator_list = []
        node.returns = None
        # Strip docstring
        if (
            node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
        ):
            node.body = node.body[1:] if len(node.body) > 1 else [ast.Pass()]
        return self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_Constant(self, node: ast.Constant) -> ast.Constant:
        # Normalize string constants but keep numeric/bool structure
        if isinstance(node.value, str):
            node.value = "_s"
        return node


class _ImportCollector(ast.NodeVisitor):
    """Collect top-level imports from a module."""

    def __init__(self) -> None:
        self.imports: set[str] = set()

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.add(alias.name.split(".")[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self.imports.add(node.module.split(".")[0])


class _CallCollector(ast.NodeVisitor):
    """Collect function/method call names within a function body."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name):
            self.calls.append(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            self.calls.append(node.func.attr)
        self.generic_visit(node)


def compute_ast_hash(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Stage 1: Compute a normalized structural hash of a function AST."""
    import copy

    node_copy = copy.deepcopy(func_node)
    normalizer = _ASTNormalizer()
    normalized = normalizer.visit(node_copy)
    ast.fix_missing_locations(normalized)
    dump = ast.dump(normalized, annotate_fields=False)
    return hashlib.sha256(dump.encode()).hexdigest()[:16]


def compute_signature_key(info: FunctionInfo) -> str:
    """Stage 2: Build a lightweight signature key for fast candidate filtering."""
    parts = [
        str(info.param_count),
        "R" if info.has_return else "N",
        str(len(info.calls_made)),
        ",".join(sorted(info.imports_used)[:5]),
    ]
    return "|".join(parts)


def extract_functions(filepath: str, source: str | None = None) -> list[FunctionInfo]:
    """Parse a Python file and extract all function/method definitions."""
    path = Path(filepath)
    if source is None:
        source = path.read_text(encoding="utf-8")

    try:
        tree = ast.parse(source, filename=filepath)
    except SyntaxError:
        return []

    # Collect module-level imports
    import_collector = _ImportCollector()
    import_collector.visit(tree)
    module_imports = sorted(import_collector.imports)

    source_lines = source.splitlines()
    functions: list[FunctionInfo] = []

    # Build parent map so we can detect nested functions
    for parent_node in ast.walk(tree):
        for child in ast.iter_child_nodes(parent_node):
            child._parent = parent_node  # type: ignore[attr-defined]

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        # Extract source lines for this function
        start = node.lineno - 1
        end = node.end_lineno if node.end_lineno else start + 1
        func_source = "\n".join(source_lines[start:end])

        # Check for return statements
        has_return = any(
            isinstance(n, ast.Return) and n.value is not None for n in ast.walk(node)
        )

        # Return type annotation
        return_type = None
        if node.returns:
            try:
                return_type = ast.unparse(node.returns)
            except Exception:
                pass

        # Decorators
        decorators = []
        for dec in node.decorator_list:
            try:
                decorators.append(ast.unparse(dec))
            except Exception:
                pass

        # Calls made inside the function
        call_collector = _CallCollector()
        call_collector.visit(node)

        # Figure out which module imports this function actually uses
        func_names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        used_imports = [imp for imp in module_imports if imp in func_names]

        # Detect if this function is nested inside another function
        is_nested = False
        ancestor = getattr(node, "_parent", None)
        while ancestor is not None:
            if isinstance(ancestor, (ast.FunctionDef, ast.AsyncFunctionDef)):
                is_nested = True
                break
            ancestor = getattr(ancestor, "_parent", None)

        info = FunctionInfo(
            name=node.name,
            filepath=str(filepath),
            lineno=node.lineno,
            end_lineno=node.end_lineno,
            source=func_source,
            param_count=len(node.args.args),
            has_return=has_return,
            return_type=return_type,
            imports_used=used_imports,
            decorators=decorators,
            calls_made=call_collector.calls,
            is_nested=is_nested,
        )
        info.ast_hash = compute_ast_hash(node)
        info.signature_key = compute_signature_key(info)
        functions.append(info)

    return functions


def extract_functions_from_file(filepath: str) -> list[FunctionInfo]:
    """Convenience wrapper to extract functions from a file path."""
    return extract_functions(filepath)
