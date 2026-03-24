"""Universal language support via tree-sitter.

Provides a single interface for extracting functions from any supported language.
Each language needs a tree-sitter grammar, but the extraction pipeline, hashing,
and comparison logic is shared.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generator

import tree_sitter

# ── Language registry ──────────────────────────────────────────────────────

@dataclass
class LanguageSpec:
    """Specification for how to extract functions from a language."""
    name: str
    extensions: list[str]
    # tree-sitter node types that represent function definitions
    function_node_types: list[str]
    # tree-sitter node types for class/struct definitions (for method context)
    class_node_types: list[str]
    # How to extract the function name from a node
    name_field: str  # e.g. "name"
    # How to extract parameters
    params_field: str  # e.g. "parameters"
    # Node types that represent import/include statements
    import_node_types: list[str]
    # Node types for return statements
    return_node_types: list[str]
    # Node types for function/method calls
    call_node_types: list[str]
    # Comment/docstring node types to strip during normalization
    comment_node_types: list[str]
    # String literal node types to normalize
    string_node_types: list[str]


LANGUAGES: dict[str, LanguageSpec] = {
    "python": LanguageSpec(
        name="python",
        extensions=[".py"],
        function_node_types=["function_definition"],
        class_node_types=["class_definition"],
        name_field="name",
        params_field="parameters",
        import_node_types=["import_statement", "import_from_statement"],
        return_node_types=["return_statement"],
        call_node_types=["call"],
        comment_node_types=["comment"],
        string_node_types=["string", "concatenated_string"],
    ),
    "javascript": LanguageSpec(
        name="javascript",
        extensions=[".js", ".jsx", ".mjs", ".cjs"],
        function_node_types=["function_declaration", "arrow_function", "method_definition", "function"],
        class_node_types=["class_declaration"],
        name_field="name",
        params_field="parameters",
        import_node_types=["import_statement", "import_specifier"],
        return_node_types=["return_statement"],
        call_node_types=["call_expression"],
        comment_node_types=["comment", "jsx_comment"],
        string_node_types=["string", "template_string"],
    ),
    "typescript": LanguageSpec(
        name="typescript",
        extensions=[".ts", ".tsx"],
        function_node_types=["function_declaration", "arrow_function", "method_definition", "function"],
        class_node_types=["class_declaration", "interface_declaration"],
        name_field="name",
        params_field="parameters",
        import_node_types=["import_statement", "import_specifier"],
        return_node_types=["return_statement"],
        call_node_types=["call_expression"],
        comment_node_types=["comment"],
        string_node_types=["string", "template_string"],
    ),
    "go": LanguageSpec(
        name="go",
        extensions=[".go"],
        function_node_types=["function_declaration", "method_declaration"],
        class_node_types=[],
        name_field="name",
        params_field="parameter_list",
        import_node_types=["import_declaration"],
        return_node_types=["return_statement"],
        call_node_types=["call_expression"],
        comment_node_types=["comment"],
        string_node_types=["raw_string_literal", "interpreted_string_literal"],
    ),
    "rust": LanguageSpec(
        name="rust",
        extensions=[".rs"],
        function_node_types=["function_item"],
        class_node_types=["impl_item", "struct_item", "trait_item"],
        name_field="name",
        params_field="parameters",
        import_node_types=["use_declaration"],
        return_node_types=["return_expression"],
        call_node_types=["call_expression"],
        comment_node_types=["line_comment", "block_comment"],
        string_node_types=["string_literal", "raw_string_literal"],
    ),
    "java": LanguageSpec(
        name="java",
        extensions=[".java"],
        function_node_types=["method_declaration", "constructor_declaration"],
        class_node_types=["class_declaration", "interface_declaration"],
        name_field="name",
        params_field="formal_parameters",
        import_node_types=["import_declaration"],
        return_node_types=["return_statement"],
        call_node_types=["method_invocation"],
        comment_node_types=["line_comment", "block_comment"],
        string_node_types=["string_literal"],
    ),
    "ruby": LanguageSpec(
        name="ruby",
        extensions=[".rb"],
        function_node_types=["method", "singleton_method"],
        class_node_types=["class", "module"],
        name_field="name",
        params_field="method_parameters",
        import_node_types=["call"],  # require/include
        return_node_types=["return"],
        call_node_types=["call", "method_call"],
        comment_node_types=["comment"],
        string_node_types=["string", "string_content"],
    ),
    "c": LanguageSpec(
        name="c",
        extensions=[".c", ".h"],
        function_node_types=["function_definition"],
        class_node_types=["struct_specifier"],
        name_field="declarator",
        params_field="parameters",
        import_node_types=["preproc_include"],
        return_node_types=["return_statement"],
        call_node_types=["call_expression"],
        comment_node_types=["comment"],
        string_node_types=["string_literal"],
    ),
    "cpp": LanguageSpec(
        name="cpp",
        extensions=[".cpp", ".cc", ".cxx", ".hpp", ".hxx", ".h"],
        function_node_types=["function_definition"],
        class_node_types=["class_specifier", "struct_specifier"],
        name_field="declarator",
        params_field="parameters",
        import_node_types=["preproc_include"],
        return_node_types=["return_statement"],
        call_node_types=["call_expression"],
        comment_node_types=["comment"],
        string_node_types=["string_literal", "raw_string_literal"],
    ),
}

# Extension → language lookup
EXTENSION_MAP: dict[str, str] = {}
for lang_name, spec in LANGUAGES.items():
    for ext in spec.extensions:
        # First language to claim an extension wins (python before cpp for .h)
        if ext not in EXTENSION_MAP:
            EXTENSION_MAP[ext] = lang_name


def detect_language(filepath: str) -> str | None:
    """Detect language from file extension."""
    ext = Path(filepath).suffix.lower()
    return EXTENSION_MAP.get(ext)


def get_language_spec(lang: str) -> LanguageSpec | None:
    """Get the language spec by name."""
    return LANGUAGES.get(lang)


def supported_extensions() -> set[str]:
    """Return all supported file extensions."""
    return set(EXTENSION_MAP.keys())


def supported_languages() -> list[str]:
    """Return all supported language names."""
    return sorted(LANGUAGES.keys())


# ── Tree-sitter parser pool ───────────────────────────────────────────────

_parsers: dict[str, tree_sitter.Parser] = {}


def _get_ts_language(lang: str) -> tree_sitter.Language | None:
    """Load a tree-sitter language."""
    try:
        if lang == "python":
            import tree_sitter_python
            return tree_sitter.Language(tree_sitter_python.language())
        elif lang == "javascript":
            import tree_sitter_javascript
            return tree_sitter.Language(tree_sitter_javascript.language())
        elif lang == "typescript":
            import tree_sitter_typescript
            return tree_sitter.Language(tree_sitter_typescript.language_typescript())
        elif lang == "go":
            import tree_sitter_go
            return tree_sitter.Language(tree_sitter_go.language())
        elif lang == "rust":
            import tree_sitter_rust
            return tree_sitter.Language(tree_sitter_rust.language())
        elif lang == "java":
            import tree_sitter_java
            return tree_sitter.Language(tree_sitter_java.language())
        elif lang == "ruby":
            import tree_sitter_ruby
            return tree_sitter.Language(tree_sitter_ruby.language())
        elif lang == "c":
            import tree_sitter_c
            return tree_sitter.Language(tree_sitter_c.language())
        elif lang == "cpp":
            import tree_sitter_cpp
            return tree_sitter.Language(tree_sitter_cpp.language())
    except ImportError:
        return None
    return None


def get_parser(lang: str) -> tree_sitter.Parser | None:
    """Get or create a tree-sitter parser for a language."""
    if lang in _parsers:
        return _parsers[lang]

    ts_lang = _get_ts_language(lang)
    if ts_lang is None:
        return None

    parser = tree_sitter.Parser(ts_lang)
    _parsers[lang] = parser
    return parser


# ── Universal function extractor ──────────────────────────────────────────

@dataclass
class ExtractedFunction:
    """Language-agnostic extracted function information."""
    name: str
    filepath: str
    language: str
    lineno: int
    end_lineno: int
    source: str
    # Structural info
    ast_hash: str = ""
    param_count: int = 0
    has_return: bool = False
    return_type: str | None = None
    # Context
    class_name: str | None = None
    class_type: str | None = None  # "class", "interface", "protocol", "abstract", "trait", "impl"
    imports_used: list[str] = field(default_factory=list)
    calls_made: list[str] = field(default_factory=list)
    decorators: list[str] = field(default_factory=list)
    signature_key: str = ""
    # Scope/visibility
    visibility: str = "public"  # "public", "private", "internal", "protected"
    is_nested: bool = False  # True if defined inside another function (closure)
    # Normalized AST token sequence for tree edit distance computation
    ast_tokens: str = ""

    @property
    def qualified_name(self) -> str:
        return f"{self.filepath}:{self.name}:{self.lineno}"

    @property
    def is_reusable(self) -> bool:
        """Whether this function is intended to be imported/called externally."""
        if self.is_nested:
            return False
        return self.visibility in ("public", "internal")


def _count_params(node: tree_sitter.Node, spec: LanguageSpec) -> int:
    """Count parameters in a function node."""
    for child in node.children:
        if child.type == spec.params_field or child.type == "formal_parameters":
            # Count named children that are parameters (skip commas, parens)
            count = 0
            for p in child.children:
                if p.type in ("identifier", "typed_parameter", "typed_default_parameter",
                              "default_parameter", "formal_parameter", "parameter",
                              "required_parameter", "optional_parameter",
                              "variadic_parameter", "rest_parameter",
                              "spread_parameter"):
                    count += 1
                elif p.type == "parameter_declaration":
                    count += 1
            return count
        # C/C++: declarator -> parameter_list
        if child.type == "function_declarator":
            return _count_params(child, spec)
    return 0


def _has_return(node: tree_sitter.Node, spec: LanguageSpec) -> bool:
    """Check if a function has a non-empty return statement."""
    for child in _walk_tree(node):
        if child.type in spec.return_node_types:
            # Check it has a value (not bare return)
            return len(child.children) > 1 or child.type == "return_expression"
    return False


def _collect_calls(node: tree_sitter.Node, spec: LanguageSpec) -> list[str]:
    """Collect function/method call names."""
    calls = []
    for child in _walk_tree(node):
        if child.type in spec.call_node_types:
            # Try to get the function name
            func_node = child.child_by_field_name("function")
            if func_node is None and child.children:
                func_node = child.children[0]
            if func_node:
                if func_node.type == "identifier":
                    calls.append(_node_text(func_node))
                elif func_node.type in ("member_expression", "attribute", "field_expression"):
                    # Get the method name (last part)
                    for sub in func_node.children:
                        if sub.type in ("property_identifier", "identifier", "field_identifier"):
                            calls.append(_node_text(sub))
    return calls


def _collect_imports(root: tree_sitter.Node, spec: LanguageSpec) -> list[str]:
    """Collect import/include names from the module root."""
    imports = []
    for child in _walk_tree(root):
        if child.type in spec.import_node_types:
            text = _node_text(child)
            # Extract module name heuristically
            parts = text.replace(";", "").replace("(", "").replace(")", "").split()
            for part in parts:
                cleaned = part.strip("'\"<>")
                if cleaned and cleaned not in ("import", "from", "require", "include", "use"):
                    imports.append(cleaned.split("/")[0].split(".")[0])
                    break
    return list(set(imports))


def _node_text(node: Any) -> str:
    """Safely decode a tree-sitter node's text to UTF-8."""
    text = node.text
    if text is None:
        return ""
    return text.decode("utf-8")


def _walk_tree(node: tree_sitter.Node) -> Generator[Any, None, None]:
    """Walk all descendants of a tree-sitter node."""
    cursor = node.walk()

    reached_root = False
    while not reached_root:
        yield cursor.node

        if cursor.goto_first_child():
            continue
        if cursor.goto_next_sibling():
            continue

        retracing = True
        while retracing:
            if not cursor.goto_parent():
                retracing = False
                reached_root = True
            elif cursor.goto_next_sibling():
                retracing = False


# Keywords that should never be treated as function names.
# These can leak in via the fallback identifier search on anonymous nodes.
_KEYWORD_NAMES: set[str] = {
    "async", "await", "if", "else", "for", "while", "do", "switch",
    "case", "return", "try", "catch", "finally", "throw", "new",
    "delete", "typeof", "instanceof", "void", "in", "of",
    "const", "let", "var", "function", "class", "extends", "super",
    "import", "export", "default", "from", "as", "yield",
    "true", "false", "null", "undefined", "this",
}


def _get_function_name(node: tree_sitter.Node, spec: LanguageSpec) -> str | None:
    """Extract the function name from a node."""
    # Try the standard name field
    name_node = node.child_by_field_name(spec.name_field)
    if name_node:
        if name_node.type == "identifier":
            text = _node_text(name_node)
            if text in _KEYWORD_NAMES:
                return None
            return text
        # C/C++: name might be inside a declarator
        if name_node.type in ("function_declarator", "pointer_declarator"):
            for child in _walk_tree(name_node):
                if child.type == "identifier":
                    return _node_text(child)
            return _node_text(name_node)
        return _node_text(name_node)

    # For arrow functions and anonymous functions, try to get the name from
    # the parent variable assignment: const foo = async () => { ... }
    # Tree-sitter AST: variable_declarator { name: "foo", value: arrow_function }
    if node.type in ("arrow_function", "function"):
        parent = node.parent
        if parent and parent.type == "variable_declarator":
            var_name = parent.child_by_field_name("name")
            if var_name and var_name.type == "identifier":
                text = _node_text(var_name)
                if text not in _KEYWORD_NAMES:
                    return text
        # Named export: export const foo = () => {}
        # Parent chain: arrow_function → variable_declarator → lexical_declaration → export_statement
        if parent and parent.type == "variable_declarator":
            var_name = parent.child_by_field_name("name")
            if var_name and var_name.type == "identifier":
                text = _node_text(var_name)
                if text not in _KEYWORD_NAMES:
                    return text
        # Argument to a call: someFunc(async () => { ... })
        # These are anonymous callbacks — skip them
        return None

    # Fallback: look for identifier children (but filter out keywords)
    for child in node.children:
        if child.type == "identifier":
            text = _node_text(child)
            if text not in _KEYWORD_NAMES:
                return text
        if child.type == "property_identifier":
            return _node_text(child)

    return None


def _get_class_context(node: tree_sitter.Node, spec: LanguageSpec) -> str | None:
    """Get the enclosing class/struct/impl name if any."""
    parent = node.parent
    while parent:
        if parent.type in spec.class_node_types:
            name_node = parent.child_by_field_name("name")
            if name_node:
                return _node_text(name_node)
        parent = parent.parent
    return None


def _is_nested_function(node: tree_sitter.Node, spec: LanguageSpec) -> bool:
    """Check if a function node is nested inside another function (closure)."""
    parent = node.parent
    while parent:
        if parent.type in spec.function_node_types:
            return True
        parent = parent.parent
    return False


def _get_class_type(node: tree_sitter.Node, language: str) -> str | None:
    """Determine whether the enclosing class is concrete, abstract, interface, protocol, or trait.

    This prevents false positives where a Protocol/interface method and its
    concrete implementation are flagged as duplicates — they're polymorphism,
    not redundancy.
    """
    parent = node.parent
    while parent:
        if parent.type == "class_definition" and language == "python":
            # Check if class inherits from Protocol or ABC
            superclasses = parent.child_by_field_name("superclasses")
            if superclasses:
                text = _node_text(superclasses)
                if "Protocol" in text:
                    return "protocol"
                if "ABC" in text or "ABCMeta" in text:
                    return "abstract"
            # Check for @abstractmethod on the function
            for child in node.children:
                if child.type == "decorator":
                    dec_text = _node_text(child)
                    if "abstractmethod" in dec_text:
                        return "abstract"
            return "class"

        if parent.type == "interface_declaration":
            # TypeScript, Java
            return "interface"

        if parent.type == "class_declaration":
            # Check for abstract keyword (Java/TS)
            for child in parent.children:
                if child.type == "abstract":
                    return "abstract"
                if child.type in ("modifiers", "modifier"):
                    mod_text = _node_text(child)
                    if "abstract" in mod_text:
                        return "abstract"
            return "class"

        if parent.type == "trait_item":
            return "trait"

        if parent.type == "impl_item":
            return "impl"

        parent = parent.parent

    return None


def _detect_visibility(
    name: str,
    node: tree_sitter.Node,
    language: str,
    class_name: str | None,
) -> str:
    """Detect function visibility/scope based on language conventions.

    Returns: "public", "private", "internal", or "protected"

    Language rules:
    - Python: _prefix = private, __prefix = private, no prefix = public
    - JS/TS: #prefix = private, no export = internal to module
    - Go: lowercase first letter = internal (unexported), uppercase = public
    - Rust: pub = public, no pub = private
    - Java/C++: explicit public/private/protected keywords
    - Ruby: after private/protected keyword = private/protected
    - C: static = internal to file, otherwise public
    """
    if language == "python":
        if name.startswith("__") and name.endswith("__"):
            return "public"  # Dunder methods (__init__, __str__) are public
        if name.startswith("__"):
            return "private"  # Name-mangled
        if name.startswith("_"):
            return "private"
        return "public"

    elif language in ("javascript", "typescript"):
        # Check for # private fields (class methods)
        if name.startswith("#"):
            return "private"
        # Check for 'export' keyword in parent or on the function itself
        parent = node.parent
        if parent and parent.type == "export_statement":
            return "public"
        # If inside a class, check for private/protected keywords (TS)
        if class_name:
            for child in node.children:
                if child.type == "accessibility_modifier":
                    mod = _node_text(child)
                    if mod == "private":
                        return "private"
                    if mod == "protected":
                        return "protected"
            return "public"  # Default class method
        # Module-level function without export
        if parent and parent.type in ("program", "statement_block"):
            return "internal"  # Not exported
        return "public"

    elif language == "go":
        if name[0].islower():
            return "internal"  # Unexported
        return "public"  # Exported

    elif language == "rust":
        for child in node.children:
            if child.type == "visibility_modifier":
                text = _node_text(child)
                if "pub" in text:
                    if "crate" in text:
                        return "internal"
                    return "public"
        return "private"

    elif language == "java":
        for child in node.children:
            if child.type == "modifiers":
                mod_text = _node_text(child)
                if "private" in mod_text:
                    return "private"
                if "protected" in mod_text:
                    return "protected"
                if "public" in mod_text:
                    return "public"
        return "internal"  # Package-private (default in Java)

    elif language == "ruby":
        # Ruby uses method-level or block-level private/protected declarations
        # Check siblings before this node for private/protected calls
        if node.parent:
            found_modifier = None
            for sibling in node.parent.children:
                if sibling == node:
                    break
                if sibling.type in ("call", "identifier"):
                    text = _node_text(sibling).strip()
                    if text in ("private", "protected"):
                        found_modifier = text
            if found_modifier:
                return found_modifier
        return "public"

    elif language in ("c", "cpp"):
        # C: static = file-internal
        for child in node.children:
            if child.type == "storage_class_specifier":
                if _node_text(child) == "static":
                    return "internal"
        # C++ class context: check access specifier
        if class_name and node.parent:
            parent = node.parent
            while parent:
                if parent.type == "access_specifier":
                    text = _node_text(parent).strip().rstrip(":")
                    if text in ("private", "protected", "public"):
                        return text
                parent = parent.prev_sibling if hasattr(parent, "prev_sibling") else None
        return "public"

    return "public"


def _compute_structural_hash(node: tree_sitter.Node, spec: LanguageSpec) -> tuple[str, str]:
    """Compute a normalized structural hash and token sequence of a function.

    Strips:
    - All identifiers (replaced with positional placeholders)
    - Comments and docstrings
    - String literal contents
    - Type annotations

    Preserves:
    - Control flow structure
    - Operator usage
    - Nesting depth
    - Number of statements

    Returns:
        (hash, tokens) — the 16-char hex hash and the space-separated
        normalized AST token sequence (used for tree edit distance).
    """
    id_map: dict[str, str] = {}
    id_counter = 0

    def _normalize_id(name: str) -> str:
        nonlocal id_counter
        if name not in id_map:
            id_map[name] = f"v{id_counter}"
            id_counter += 1
        return id_map[name]

    parts: list[str] = []

    def _visit(n: tree_sitter.Node) -> None:
        # Skip comments and docstrings
        if n.type in spec.comment_node_types:
            return
        if n.type in ("expression_statement",) and n.children:
            first = n.children[0]
            if first.type in spec.string_node_types:
                return  # Skip docstrings

        # Normalize identifiers
        if n.type == "identifier":
            parts.append(_normalize_id(_node_text(n)))
            return

        # Normalize strings
        if n.type in spec.string_node_types:
            parts.append('"_s"')
            return

        # Keep the node type as structural information
        if n.child_count == 0:
            # Leaf node — include its type
            if n.type in ("integer", "float", "number", "integer_literal", "float_literal"):
                parts.append("_num")
            elif n.type in ("true", "false", "boolean"):
                parts.append("_bool")
            elif n.type == "none":
                parts.append("_none")
            else:
                parts.append(n.type)
        else:
            parts.append(f"({n.type}")
            for child in n.children:
                _visit(child)
            parts.append(")")

    _visit(node)
    dump = " ".join(parts)
    hash_val = hashlib.sha256(dump.encode("utf-8")).hexdigest()[:16]
    return hash_val, dump


def _compute_signature_key(func: ExtractedFunction) -> str:
    """Build a lightweight signature key for fast candidate filtering."""
    parts = [
        func.language,
        str(func.param_count),
        "R" if func.has_return else "N",
        str(min(len(func.calls_made), 20)),
    ]
    return "|".join(parts)


def extract_functions_universal(
    filepath: str,
    source: str | None = None,
    language: str | None = None,
) -> list[ExtractedFunction]:
    """Extract all functions from a source file using tree-sitter.

    Works for any supported language.
    """
    if language is None:
        language = detect_language(filepath)
    if language is None:
        return []

    spec = get_language_spec(language)
    if spec is None:
        return []

    parser = get_parser(language)
    if parser is None:
        return []

    path = Path(filepath)
    if source is None:
        source = path.read_text(encoding="utf-8")

    source_bytes = source.encode("utf-8")
    tree = parser.parse(source_bytes)
    root = tree.root_node

    # Collect module-level imports
    module_imports = _collect_imports(root, spec)

    source_lines = source.splitlines()
    functions: list[ExtractedFunction] = []

    for node in _walk_tree(root):
        if node.type not in spec.function_node_types:
            continue

        name = _get_function_name(node, spec)
        if name is None:
            continue

        # Final safety net: reject any name that's a language keyword.
        # This catches edge cases from different tree-sitter parse paths.
        if name in _KEYWORD_NAMES:
            continue

        # Skip very short anonymous/lambda nodes
        line_count = node.end_point[0] - node.start_point[0] + 1
        if line_count < 2 and name.startswith("_"):
            continue

        lineno = node.start_point[0] + 1  # 1-indexed
        end_lineno = node.end_point[0] + 1
        func_source = "\n".join(source_lines[node.start_point[0]:node.end_point[0] + 1])

        class_name = _get_class_context(node, spec)
        class_type = _get_class_type(node, language)
        nested = _is_nested_function(node, spec)
        visibility = _detect_visibility(name, node, language, class_name)

        func = ExtractedFunction(
            name=name,
            filepath=str(filepath),
            language=language,
            lineno=lineno,
            end_lineno=end_lineno,
            source=func_source,
            param_count=_count_params(node, spec),
            has_return=_has_return(node, spec),
            class_name=class_name,
            class_type=class_type,
            imports_used=module_imports,
            calls_made=_collect_calls(node, spec),
            visibility=visibility,
            is_nested=nested,
        )
        func.ast_hash, func.ast_tokens = _compute_structural_hash(node, spec)
        func.signature_key = _compute_signature_key(func)
        functions.append(func)

    return functions
