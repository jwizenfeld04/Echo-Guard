"""Two-tier similarity detection engine for code clone detection.

Architecture:
    Tier 1: AST hash matching — catches Type-1/Type-2 clones in O(1)
    Tier 2: UniXcoder embeddings — catches Type-3/Type-4 clones via
            learned code representations and cosine similarity search

Performance:
    - AST hash matching: O(n) via hash-map grouping
    - Embedding search: ~2ms at 100K functions (NumPy brute-force)
    - Batch scan: find_all_matches() processes entire codebase in one pass
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from echo_guard.embeddings import EmbeddingModel, EmbeddingStore

from echo_guard.languages import ExtractedFunction


# ── Cross-language runtime compatibility ──────────────────────────────────

_SAME_RUNTIME_GROUPS: list[set[str]] = [
    {"javascript", "typescript"},
    {"c", "cpp"},
]

_COMPATIBLE_LANGUAGES: dict[str, set[str]] = {}
for _group in _SAME_RUNTIME_GROUPS:
    for _lang in _group:
        _COMPATIBLE_LANGUAGES.setdefault(_lang, set()).update(_group - {_lang})


def classify_reuse(source_lang: str, existing_lang: str) -> str:
    if source_lang == existing_lang:
        return "direct_import"
    if existing_lang in _COMPATIBLE_LANGUAGES.get(source_lang, set()):
        return "compatible_import"
    return "reference_only"


def _extract_string_literals(source: str) -> list[str]:
    """Extract all string literals from source code."""
    # Match single-quoted, double-quoted, and template literal strings
    return re.findall(r'''(?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|`(?:[^`\\]|\\.)*`)''', source)


def _is_parameterized_variant(source_a: ExtractedFunction, source_b: ExtractedFunction) -> bool:
    """Detect if two functions are parameterized variants (same structure, different constants).

    Example: two API route handlers identical except for the endpoint path.
    Returns True when the structural skeleton (code minus string literals) is very similar
    and the actual string literals differ.
    """
    _STR_PATTERN = re.compile(r'''(?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|`(?:[^`\\]|\\.)*`)''')

    stripped_a = _STR_PATTERN.sub('""', source_a.source)
    stripped_b = _STR_PATTERN.sub('""', source_b.source)

    tokens_a = stripped_a.split()
    tokens_b = stripped_b.split()

    # Exact structural match after stripping strings
    if tokens_a == tokens_b:
        lits_a = _extract_string_literals(source_a.source)
        lits_b = _extract_string_literals(source_b.source)
        if lits_a != lits_b:
            return True
        return False

    # Near-structural match: allow minor structural differences (e.g. an extra option)
    # Use SequenceMatcher for a quick ratio — if >90% similar after stripping strings,
    # and the actual strings differ, it's a parameterized variant
    if tokens_a and tokens_b:
        from difflib import SequenceMatcher
        ratio = SequenceMatcher(None, tokens_a, tokens_b).ratio()
        if ratio >= 0.90:
            lits_a = _extract_string_literals(source_a.source)
            lits_b = _extract_string_literals(source_b.source)
            if lits_a != lits_b:
                return True

    return False


def _is_low_value_variant(source_a: ExtractedFunction, source_b: ExtractedFunction) -> bool:
    """Detect same-file "duplicates" that differ only in large opaque data (e.g. SVG paths).

    Two icon components (TelegramIcon vs DiscordIcon) share the same JSX wrapper
    structure but have completely different SVG path data. Suggesting to "consolidate"
    them is not useful — that's like saying two <img> tags are duplicates because they
    both have a src attribute.

    Returns True when:
    1. Functions are in the same file
    2. They are parameterized variants
    3. The differing literals are large (>80 chars), suggesting opaque data like SVG paths,
       base64 strings, or long config values where extraction adds indirection without
       readability gain.
    """
    if source_a.filepath != source_b.filepath:
        return False

    if not _is_parameterized_variant(source_a, source_b):
        return False

    lits_a = _extract_string_literals(source_a.source)
    lits_b = _extract_string_literals(source_b.source)

    # Check if the differing literals are large (opaque data like SVG paths)
    large_diff_count = 0
    for a, b in zip(lits_a, lits_b):
        if a != b:
            # Either literal is large — likely opaque data
            if len(a) > 80 or len(b) > 80:
                large_diff_count += 1

    return large_diff_count > 0


def _detect_service_boundaries(filepaths: list[str]) -> list[str]:
    """Auto-detect service boundaries from common monorepo directory patterns.

    Looks for top-level directories under common service root patterns like
    services/, apps/, packages/, microservices/ where each subdirectory is
    a separate deployable unit.
    """
    from pathlib import PurePosixPath

    # Common monorepo service root patterns
    service_roots = {"services", "apps", "packages", "microservices"}
    boundaries: set[str] = set()

    for fp in filepaths:
        parts = PurePosixPath(fp).parts
        for i, part in enumerate(parts):
            if part in service_roots and i + 1 < len(parts):
                boundaries.add(f"{part}/{parts[i + 1]}")

    return sorted(boundaries)


def _get_service(filepath: str, boundaries: list[str]) -> str | None:
    """Return the service boundary a filepath belongs to, or None."""
    normalized = filepath.replace("\\", "/")
    # Strip leading ./ for consistent matching
    if normalized.startswith("./"):
        normalized = normalized[2:]
    for boundary in boundaries:
        norm_boundary = boundary.replace("\\", "/").strip("/")
        if normalized.startswith(norm_boundary + "/"):
            return norm_boundary
    return None


def _crosses_service_boundary(a: ExtractedFunction, b: ExtractedFunction, boundaries: list[str]) -> bool:
    """Check if two functions are in different services (separate deployable units).

    Functions in separate services (e.g., services/worker vs services/dashboard)
    cannot import from each other — they're separate containers/processes.
    """
    if not boundaries:
        return False
    svc_a = _get_service(a.filepath, boundaries)
    svc_b = _get_service(b.filepath, boundaries)
    if svc_a is None or svc_b is None:
        return False
    return svc_a != svc_b


def _is_framework_route_handler(func: ExtractedFunction) -> bool:
    """Detect if a function is a framework route handler bound to its file path.

    Next.js App Router exports named HTTP methods (GET, POST, PUT, DELETE, PATCH)
    from route.ts/route.js files. These are bound to their file path and cannot
    be imported from one route into another.
    """
    http_methods = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}
    if func.name not in http_methods:
        return False
    # Check if filepath looks like a Next.js route file
    path = func.filepath.replace("\\", "/")
    if path.endswith(("/route.ts", "/route.js", "/route.tsx", "/route.jsx")):
        return True
    return False


def _is_framework_page_export(func: ExtractedFunction) -> bool:
    """Detect if a function is a Next.js/Remix page component or layout export.

    Page components like Home(), PromptsPage(), NewToolRedirectPage() must exist
    as separate files for routing. A one-line redirect("/path") call in a page.tsx
    is not duplication — it's a required routing convention.

    Also covers loading.tsx, error.tsx, not-found.tsx — these are file-convention
    based exports that cannot be shared.
    """
    path = func.filepath.replace("\\", "/")
    _PAGE_FILE_ENDINGS = (
        "/page.tsx", "/page.jsx", "/page.ts", "/page.js",
        "/layout.tsx", "/layout.jsx", "/layout.ts", "/layout.js",
        "/loading.tsx", "/loading.jsx", "/loading.ts", "/loading.js",
        "/error.tsx", "/error.jsx",
        "/not-found.tsx", "/not-found.jsx",
    )
    if not any(path.endswith(e) for e in _PAGE_FILE_ENDINGS):
        return False

    # Trivial page/layout/loading components (≤6 lines) are always framework boilerplate
    line_count = func.end_lineno - func.lineno + 1
    if line_count <= 6:
        return True

    # Even non-trivial default exports in page files are framework-bound
    if func.visibility == "public" and func.name in ("default", "Default"):
        return True

    return False


def _is_trivial_function(func: ExtractedFunction) -> bool:
    """Detect trivial/boilerplate functions where duplication is expected.

    A function is trivial if its body (excluding header and comments) has
    ≤1 meaningful statement, OR if it's a short guard-and-delegate pattern
    (if check: return; delegate_call) that exists purely as framework glue.
    """
    source = func.source.strip()
    all_lines = [line.strip() for line in source.splitlines()
                 if line.strip() and not line.strip().startswith(("//", "#", "/*", "*"))]
    # Filter out function declaration line itself
    body_lines = [line for line in all_lines
                  if not line.startswith(("def ", "function ", "func ", "fn ", "export ", "async ", "public ", "private "))]

    # Single-statement body — always trivial
    if len(body_lines) <= 1:
        return True

    # Pure delegate: body is a docstring/import + `return simple_call()`.
    # e.g. `def _find_repo_root(): from utils import find; return find()`
    # But NOT `return bool(re.match(...))` which is real computation.
    if len(body_lines) <= 3:
        returns = [line for line in body_lines if line.startswith("return ")]
        if len(returns) == 1:
            ret_expr = returns[0][len("return "):].strip()
            if ret_expr.endswith("()") and "(" not in ret_expr[:-2]:
                non_boilerplate = [
                    line for line in body_lines
                    if not line.startswith(("return ", "from ", "import "))
                    and not (line.startswith('"""') or line.startswith("'''"))
                ]
                if len(non_boilerplate) == 0:
                    return True

    # Guard-and-delegate: body is just `if X: return` + one call.
    if len(body_lines) <= 3:
        has_guard_return = any(
            ("return" in line and ("if " in line or line == "return"))
            for line in body_lines
        )
        non_flow = [line for line in body_lines
                    if not line.startswith(("if ", "return", "else", "elif"))]
        if has_guard_return and len(non_flow) <= 1:
            return True

    return False


# ── Per-service boilerplate exclusion ─────────────────────────────────

# Short functions that every service must have by convention (health checks,
# lifespan hooks, etc.).  These are intentionally duplicated across services.
_PER_SERVICE_BOILERPLATE_NAMES: set[str] = {
    "health", "healthcheck", "health_check", "readiness", "liveness",
}


def _is_per_service_boilerplate(a: ExtractedFunction, b: ExtractedFunction, boundaries: list[str]) -> bool:
    """Detect per-service boilerplate that is intentionally duplicated.

    Every microservice needs a health() endpoint. Three services each returning
    {"status": "ok"} is intentional, not duplication.
    """
    if not boundaries:
        return False
    if not _crosses_service_boundary(a, b, boundaries):
        return False
    # Both must have boilerplate names
    if a.name.lower() in _PER_SERVICE_BOILERPLATE_NAMES and b.name.lower() in _PER_SERVICE_BOILERPLATE_NAMES:
        return True
    # Short lifespan/startup functions across services
    if a.name == b.name and a.name in ("lifespan", "startup", "shutdown"):
        a_lines = a.end_lineno - a.lineno + 1
        b_lines = b.end_lineno - b.lineno + 1
        if a_lines <= 15 and b_lines <= 15:
            return True
    return False


# ── Constructor / __init__ exclusion ─────────────────────────────────

# Constructors are structurally similar across unrelated classes — two classes
# with 2-arg constructors always look the same.  Only flag if the classes
# themselves are duplicated (same class name).
_CONSTRUCTOR_NAMES: set[str] = {
    "__init__", "constructor", "__new__", "new", "init", "initialize",
}


def _is_constructor_match(a: ExtractedFunction, b: ExtractedFunction) -> bool:
    """Return True if either function is a constructor and the match is nonsensical.

    Cases suppressed:
    1. Both are constructors of different classes — any two classes with similar
       argument counts will produce this. Only allow when classes share a name.
    2. One is a constructor, the other isn't — matching _error_payload() against
       __init__() is never useful. Constructors set up state; non-constructors
       are application logic with superficially similar structure.

    The only allowed case is both being constructors of same-named classes
    (actual class-level duplication).
    """
    a_is_ctor = a.name in _CONSTRUCTOR_NAMES
    b_is_ctor = b.name in _CONSTRUCTOR_NAMES

    if not a_is_ctor and not b_is_ctor:
        return False

    # Both are constructors
    if a_is_ctor and b_is_ctor:
        a_cls = getattr(a, "class_name", None)
        b_cls = getattr(b, "class_name", None)
        if a_cls and b_cls and a_cls == b_cls:
            return False  # Same class name — could be real duplication
        return True  # Different classes — suppress

    # Only one is a constructor — always suppress
    return True


# ── Observer / Protocol pattern exclusion ────────────────────────────────

def _is_observer_pattern(a: ExtractedFunction, b: ExtractedFunction) -> bool:
    """Detect N classes implementing the same Protocol/interface method.

    When a Protocol defines on_tool_call() and 4 concrete classes implement it,
    that's the observer pattern — not 10 duplicates.  Suppress if both functions
    share the same name, belong to different classes, and at least one class is
    an abstract type (Protocol, interface, trait, ABC).
    """
    if a.name != b.name:
        return False

    a_cls = getattr(a, "class_name", None)
    b_cls = getattr(b, "class_name", None)
    if not a_cls or not b_cls:
        return False
    if a_cls == b_cls:
        return False  # Same class — not observer pattern

    # Check if either class is abstract
    a_type = getattr(a, "class_type", None)
    b_type = getattr(b, "class_type", None)
    abstract_types = {"interface", "protocol", "abstract", "trait"}

    # If one is abstract and the other is concrete → polymorphism
    if a_type in abstract_types or b_type in abstract_types:
        return True

    # If both are concrete but share the same method name across different
    # classes in the same file → likely implementing a shared protocol
    if a.filepath == b.filepath and a_cls != b_cls:
        return True

    return False


# ── Same-file CRUD pattern exclusion ─────────────────────────────────────

_CRUD_VERB_PATTERN = re.compile(
    r"^(create|update|delete|remove|get|list|fetch|insert|upsert|patch)"
    r"[_A-Z]",
)


def _is_same_file_crud(a: ExtractedFunction, b: ExtractedFunction) -> bool:
    """Detect CRUD operations in the same file that share a structure.

    create_channel / update_channel / delete_channel in the same router file
    are inherently similar but serve different purposes.  Suppress when both
    are in the same file and both names start with different CRUD verbs
    operating on the same resource.
    """
    if a.filepath != b.filepath:
        return False

    ma = _CRUD_VERB_PATTERN.match(a.name)
    mb = _CRUD_VERB_PATTERN.match(b.name)
    if not ma or not mb:
        return False

    verb_a = ma.group(1)
    verb_b = mb.group(1)

    # Different verbs on what looks like the same resource
    if verb_a != verb_b:
        # Extract resource suffix after the verb + separator
        suffix_a = re.sub(r"^(create|update|delete|remove|get|list|fetch|insert|upsert|patch)[_]?", "", a.name)
        suffix_b = re.sub(r"^(create|update|delete|remove|get|list|fetch|insert|upsert|patch)[_]?", "", b.name)
        if suffix_a and suffix_b and suffix_a == suffix_b:
            return True
        # Even without matching suffix, different CRUD verbs in same file
        # on similar-length functions are usually intentional
        if suffix_a and suffix_b:
            # Close enough names like "create_credential" vs "update_credential"
            return True

    return False


# ── Boolean predicate pair exclusion ─────────────────────────────────────

_ANTONYM_PAIRS = {
    frozenset({"is_success", "is_failed"}),
    frozenset({"is_success", "is_failure"}),
    frozenset({"enable", "disable"}),
    frozenset({"open", "close"}),
    frozenset({"start", "stop"}),
    frozenset({"lock", "unlock"}),
    frozenset({"show", "hide"}),
    frozenset({"encrypt", "decrypt"}),
    frozenset({"encode", "decode"}),
    frozenset({"serialize", "deserialize"}),
    frozenset({"compress", "decompress"}),
    frozenset({"connect", "disconnect"}),
    frozenset({"subscribe", "unsubscribe"}),
    frozenset({"mount", "unmount"}),
    frozenset({"activate", "deactivate"}),
}


# ── Domain-noun filtering (structural-template false positives) ───────

# Common verbs/prefixes that precede a domain noun.  When two functions
# share the same verb+shape but operate on different domain nouns
# (get_automation_by_id vs get_trigger_by_id, list_model_configs vs
# list_webhook_integrations), they're intentional variants — not duplication.
_DOMAIN_VERB_PATTERN = re.compile(
    r"^(create|update|delete|remove|get|list|fetch|insert|upsert|patch|"
    r"find|search|count|validate|check|handle|process|parse|build|"
    r"register|load|save|set|refresh|resolve|format)"
    r"[_A-Z]",
)


def _extract_domain_noun(name: str) -> str | None:
    """Extract the domain noun from a function name by stripping the verb prefix.

    get_automation_by_id → automation_by_id
    listModelConfigs     → model_configs
    handle_slack_inbound → slack_inbound

    Returns None if no verb prefix is detected.
    """
    m = _DOMAIN_VERB_PATTERN.match(name)
    if m:
        verb = m.group(1)
        suffix = name[len(verb):].lstrip("_")
        if not suffix:
            return None
        # Normalize camelCase remainder: ModelConfigs → model_configs
        normalized = re.sub(r"([A-Z])", r"_\1", suffix).lower().lstrip("_")
        return normalized

    return None


def _is_structural_template_pair(a: ExtractedFunction, b: ExtractedFunction) -> bool:
    """Detect two functions that share the same verb pattern but different domain nouns.

    get_automation_by_id vs get_trigger_by_id: same verb (get), same shape
    (_by_id), but different domain (automation vs trigger).  These are
    intentional per-entity implementations, not duplication.

    Works for both same-file and cross-file pairs. Same-file examples include
    getRegisteredTools/getBackends/getModelConfigs in api.ts, or
    list_automations/list_automation_bindings in a repository file.
    """
    noun_a = _extract_domain_noun(a.name)
    noun_b = _extract_domain_noun(b.name)

    if noun_a is None or noun_b is None:
        return False

    # Same noun = potentially real duplication (fetchJson vs fetchJson)
    if noun_a == noun_b:
        return False

    # Both have a verb prefix and different nouns → structural template pair
    return True


def _normalize_to_snake(name: str) -> str:
    """Normalize camelCase/PascalCase to snake_case for matching.

    isSuccess → is_success, handleSlackInbound → handle_slack_inbound
    """
    # Insert underscores before uppercase letters
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    return s.lower()


# ── UI wrapper component exclusion ────────────────────────────────────

def _is_ui_wrapper_component(func: ExtractedFunction) -> bool:
    """Detect short UI wrapper components (design system primitives).

    Components like Panel(), Card(), Toolbar(), Badge(), Alert(), Subpanel()
    that are short (<= 8 lines) and contain className patterns are design
    system building blocks. They intentionally share the same wrapper
    structure (div + className + children) — matching them is not useful.
    """
    if func.language not in ("javascript", "typescript"):
        return False

    line_count = func.end_lineno - func.lineno + 1
    if line_count > 15:
        return False

    source = func.source
    # Must contain className, class=, or cn() utility — the hallmark of a wrapper component
    if "className" not in source and "class=" not in source and "cn(" not in source:
        return False

    # Must contain JSX return (children, div, span, etc.)
    if "<" not in source:
        return False

    return True


def _is_ui_wrapper_pair(a: ExtractedFunction, b: ExtractedFunction) -> bool:
    """Detect two UI wrapper components matching each other.

    Panel/Card/Toolbar/Badge/Alert sharing the same div+className+children
    pattern is intentional — these are design system primitives, not duplicates.

    BUT: if both functions have the same name (e.g., TelegramIcon in two files),
    that's real duplication — not a design system pattern.
    """
    if a.name == b.name:
        return False  # Same name across files = real duplication
    return _is_ui_wrapper_component(a) and _is_ui_wrapper_component(b)


_UI_COMPONENT_DIRS = {"ui/components", "ui/layout", "ui/patterns", "components/ui"}


def _is_ui_directory_pair(a: ExtractedFunction, b: ExtractedFunction) -> bool:
    """Suppress matches between components both living in UI directories.

    Components in ui/components/, ui/layout/, ui/patterns/ are design system
    primitives — matching them against each other produces noise. Same-named
    components across files are still flagged (real duplication).
    """
    if a.name == b.name:
        return False  # Same name across files = real duplication
    if a.language not in ("javascript", "typescript") or b.language not in ("javascript", "typescript"):
        return False

    def _in_ui_dir(func: ExtractedFunction) -> bool:
        path = func.filepath.replace("\\", "/")
        return any(f"/{d}/" in path or path.startswith(f"{d}/") for d in _UI_COMPONENT_DIRS)

    return _in_ui_dir(a) and _in_ui_dir(b)


def _is_antonym_pair(a: ExtractedFunction, b: ExtractedFunction) -> bool:
    """Detect semantically inverse function pairs (encrypt/decrypt, enable/disable).

    These are structurally similar but do opposite things — not real duplicates.
    Handles both snake_case (is_success/is_failed) and camelCase (isSuccess/isFailed).
    """
    if a.filepath != b.filepath:
        return False

    # Normalize camelCase to snake_case before checking
    a_norm = _normalize_to_snake(a.name)
    b_norm = _normalize_to_snake(b.name)

    pair = frozenset({a_norm, b_norm})
    if pair in _ANTONYM_PAIRS:
        return True

    # Check prefix-based antonyms (enable_X / disable_X, isSuccess / isFailed)
    for antonyms in _ANTONYM_PAIRS:
        ant_list = sorted(antonyms)
        if len(ant_list) == 2:
            p1, p2 = ant_list
            if a_norm.startswith(p1) and b_norm.startswith(p2):
                suf_a = a_norm[len(p1):].lstrip("_")
                suf_b = b_norm[len(p2):].lstrip("_")
                if suf_a and suf_a == suf_b:
                    return True
            elif a_norm.startswith(p2) and b_norm.startswith(p1):
                suf_a = a_norm[len(p2):].lstrip("_")
                suf_b = b_norm[len(p1):].lstrip("_")
                if suf_a and suf_a == suf_b:
                    return True

    return False


# ── Intentional Duplication Patterns ─────────────────────────────────────

# Framework conventions where duplication is required by the framework itself.
# These patterns match filepath + export name combos that MUST exist per-file.
_FRAMEWORK_REQUIRED_EXPORTS: dict[str, set[str]] = {
    # Next.js App Router
    "route.ts": {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"},
    "route.js": {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"},
    "route.tsx": {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"},
    "page.tsx": {"default", "generateMetadata", "generateStaticParams"},
    "page.jsx": {"default", "generateMetadata", "generateStaticParams"},
    "page.ts": {"default", "generateMetadata", "generateStaticParams"},
    "layout.tsx": {"default", "generateMetadata"},
    "layout.jsx": {"default", "generateMetadata"},
    "loading.tsx": {"default"},
    "error.tsx": {"default"},
    "not-found.tsx": {"default"},
    # Flask/FastAPI
    # (decorators like @app.route are handled differently)
}


def _is_framework_required_export(func: ExtractedFunction) -> bool:
    """Check if a function is a framework-required per-file export.

    These are functions that MUST exist in each file due to framework conventions.
    Flagging them as duplicates of each other is always a false positive.
    """
    from pathlib import PurePosixPath
    filename = PurePosixPath(func.filepath.replace("\\", "/")).name
    required = _FRAMEWORK_REQUIRED_EXPORTS.get(filename, set())
    return func.name in required


def classify_suggestion(
    source_func: ExtractedFunction,
    existing_func: ExtractedFunction,
    base_reuse_type: str,
    service_boundaries: list[str] | None = None,
) -> str:
    """Classify what kind of suggestion to give for a similarity match.

    Returns a suggestion type that determines the guidance shown to the user:
    - "same_file_refactor": both functions in same file → suggest local refactoring
    - "extract_utility": parameterized variants → suggest extracting a shared helper
    - "cross_service_reference": functions in separate services → can't import
    - "direct_import" / "compatible_import" / "reference_only": pass through from base
    """
    # Same file → never suggest import
    if source_func.filepath == existing_func.filepath:
        return "same_file_refactor"

    # Cross-service boundary — separate deployable units cannot import from each other
    if service_boundaries and _crosses_service_boundary(source_func, existing_func, service_boundaries):
        return "cross_service_reference"

    # Framework route handlers cannot be imported across routes
    if _is_framework_route_handler(source_func) or _is_framework_route_handler(existing_func):
        if _is_parameterized_variant(source_func, existing_func):
            return "extract_utility"
        # Even if not parameterized variants, don't suggest direct import
        return "extract_utility"

    # Check for parameterized variants (same structure, different string constants)
    if base_reuse_type in ("direct_import", "compatible_import"):
        if _is_parameterized_variant(source_func, existing_func):
            return "extract_utility"

    return base_reuse_type


def get_reuse_guidance(reuse_type: str, source_lang: str, existing_lang: str) -> str:
    if reuse_type == "direct_import":
        return "Same language — import the existing function directly."
    elif reuse_type == "compatible_import":
        return (
            f"{source_lang} and {existing_lang} share a runtime. "
            f"You can import this directly (TS↔JS compile to the same target)."
        )
    elif reuse_type == "same_file_refactor":
        return (
            "Both functions are in the same file — importing is not applicable. "
            "If the logic is truly duplicated, consider consolidating into a single function. "
            "If they serve different models/classes, the duplication may be intentional."
        )
    elif reuse_type == "extract_utility":
        return (
            "These functions are structurally identical but differ in constants (e.g. endpoint paths, "
            "config values). Consider extracting a shared helper that accepts the varying parts as parameters."
        )
    elif reuse_type == "cross_service_reference":
        return (
            "These functions live in separate services (different deployable units). "
            "Direct import is NOT possible. Options: (1) Extract to a shared library package "
            "that both services depend on. (2) Accept the duplication as an intentional service boundary. "
            "(3) Expose shared logic via an internal API."
        )
    else:
        return (
            f"This logic already exists in {existing_lang} but CANNOT be imported into {source_lang}. "
            f"Options: (1) Rewrite it in {source_lang}, referencing the existing implementation to ensure "
            f"identical behavior. (2) Expose the {existing_lang} version as a shared API/service. "
            f"(3) If this is a polyglot monorepo, consider consolidating to one language for shared utilities."
        )


# ── Scope-aware scoring ──────────────────────────────────────────────────

def _is_interface_impl_pair(a: ExtractedFunction, b: ExtractedFunction) -> bool:
    """Detect if two functions are an interface/protocol definition and its implementation.

    A Protocol method and its concrete implementation sharing the same name is
    standard polymorphism, not duplication.
    """
    a_type = getattr(a, "class_type", None)
    b_type = getattr(b, "class_type", None)

    if a_type is None or b_type is None:
        return False

    # One is abstract/interface/protocol/trait, the other is concrete
    abstract_types = {"interface", "protocol", "abstract", "trait"}
    concrete_types = {"class", "impl"}

    if a_type in abstract_types and b_type in concrete_types:
        return a.name == b.name
    if b_type in abstract_types and a_type in concrete_types:
        return a.name == b.name

    return False


def scope_penalty(source_func: ExtractedFunction, existing_func: ExtractedFunction) -> float:
    # Interface/protocol method + implementation = polymorphism, not duplication
    if _is_interface_impl_pair(source_func, existing_func):
        return 0.0
    # Nested functions (closures) cannot be imported — skip them as reuse targets
    if getattr(existing_func, "is_nested", False):
        return 0.0
    if existing_func.visibility == "private":
        return 0.6
    if existing_func.visibility == "internal":
        from pathlib import Path
        if Path(source_func.filepath).parent == Path(existing_func.filepath).parent:
            return 0.9
        return 0.7
    if existing_func.visibility == "protected":
        return 0.75
    return 1.0


# ── SimilarityMatch ──────────────────────────────────────────────────────

@dataclass
class SimilarityMatch:
    source_func: ExtractedFunction
    existing_func: ExtractedFunction
    match_type: str
    similarity_score: float  # Score after scope penalty (used for ranking/display)
    import_suggestion: str = ""
    reuse_type: str = ""
    reuse_guidance: str = ""
    raw_score: float = 0.0  # Score before scope penalty (used for clone type classification)

    @property
    def clone_type(self) -> str:
        """Classify the type of clone detected.

        Uses raw_score (before scope penalty) so that a private exact clone
        is still classified as Type-1/Type-2, not downgraded to Type-4 just
        because the scope penalty reduced the display score.

        Clone types follow the standard academic taxonomy:
        - type1_type2: Exact structural clone or renamed identifiers (Tier 1, AST hash)
        - type3: Modified statements — same structure with additions/removals (Tier 2)
        - type4: Semantic clone — same intent, completely different implementation (Tier 2)
        """
        if self.match_type == "exact_structure":
            return "type1_type2"
        # Use raw_score (before scope penalty) for T3/T4 classification.
        # ≥0.96 = strong structural overlap (Type-3: modified statements)
        # <0.96 = semantic similarity only (Type-4: different implementation)
        score = self.raw_score if self.raw_score > 0 else self.similarity_score
        return "type3" if score >= 0.96 else "type4"

    @property
    def clone_type_label(self) -> str:
        """Human-readable label for the clone type."""
        labels = {
            "type1_type2": "Exact/Renamed Clone",
            "type3": "Modified Clone",
            "type4": "Semantic Clone",
        }
        return labels.get(self.clone_type, self.clone_type)

    @property
    def severity(self) -> str:
        """Severity derived from clone type and confidence score.

        - high: Type-1/Type-2 (always actionable — exact duplicates) or
                Type-3 (modified clones with strong structural overlap)
        - medium: Type-4 semantic clones with high confidence (score >= 0.94)
        - low: Type-4 semantic clones with lower confidence (score < 0.94)
              Hidden by default, shown with --verbose.
        """
        ct = self.clone_type
        if ct in ("type1_type2", "type3"):
            return "high"
        # Type-4: split into MEDIUM (high confidence) and LOW (lower confidence)
        score = self.raw_score if self.raw_score > 0 else self.similarity_score
        return "medium" if score >= 0.94 else "low"


@dataclass
class FindingGroup:
    """A cluster of related matches that represent a single observation.

    Instead of reporting C(n,2) pairwise findings for n similar functions,
    we group them into one finding with all involved functions listed.
    """
    functions: list[ExtractedFunction]
    representative_match: SimilarityMatch  # Highest-scoring match in the group
    match_count: int  # How many pairwise matches were collapsed
    pattern_description: str  # e.g. "API proxy route handlers"
    reuse_type: str
    reuse_guidance: str

    @property
    def severity(self) -> str:
        return self.representative_match.severity

    @property
    def clone_type(self) -> str:
        return self.representative_match.clone_type

    @property
    def clone_type_label(self) -> str:
        return self.representative_match.clone_type_label

    @property
    def similarity_score(self) -> float:
        return self.representative_match.similarity_score


def group_matches(matches: list[SimilarityMatch]) -> list[FindingGroup | SimilarityMatch]:
    """Cluster related pairwise matches into grouped findings.

    Uses union-find to merge matches that share functions into connected
    components, but only merges functions that are genuinely interchangeable
    (same name or very high similarity). This prevents over-grouping where
    unrelated functions like isJsonObject() and classifyType() get lumped
    together just because they're both similar to a third function.

    Returns a mix of FindingGroups (for clusters of 3+ functions) and
    individual SimilarityMatch objects (for isolated pairs).
    """
    if not matches:
        return []

    MAX_GROUP_SIZE = 15

    # Build union-find over function qualified names
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # Group matches by reuse_type so we don't merge unrelated categories
    type_buckets: dict[str, list[SimilarityMatch]] = defaultdict(list)
    for m in matches:
        type_buckets[m.reuse_type].append(m)

    results: list[FindingGroup | SimilarityMatch] = []

    for reuse_type, bucket_matches in type_buckets.items():
        # Cross-service findings are always emitted as individual pairs —
        # grouping them creates meaningless mega-clusters (#86: 129 functions).
        if reuse_type == "cross_service_reference":
            results.extend(bucket_matches)
            continue

        parent.clear()

        for m in bucket_matches:
            key_a = m.source_func.qualified_name
            key_b = m.existing_func.qualified_name
            parent.setdefault(key_a, key_a)
            parent.setdefault(key_b, key_b)

            # Only union if the functions share a name (true duplicates) or
            # are exact structural clones. This prevents transitive grouping
            # of merely similar functions (e.g., isJsonObject + classifyType).
            # For same-name embedding matches, require >= 0.90 to prevent
            # canAdvance()-type false positives (same name, different body).
            should_merge = (
                m.match_type == "exact_structure"
                or (m.source_func.name == m.existing_func.name
                    and m.similarity_score >= 0.90)
            )
            if should_merge:
                union(key_a, key_b)

        # Collect connected components
        components: dict[str, list[SimilarityMatch]] = defaultdict(list)
        for m in bucket_matches:
            root = find(m.source_func.qualified_name)
            components[root].append(m)

        for component_matches in components.values():
            # Collect all unique functions in this component
            func_map: dict[str, ExtractedFunction] = {}
            for m in component_matches:
                func_map[m.source_func.qualified_name] = m.source_func
                func_map[m.existing_func.qualified_name] = m.existing_func

            # Cap oversized groups by keeping highest-scoring functions
            if len(func_map) > MAX_GROUP_SIZE:
                func_scores: dict[str, float] = defaultdict(float)
                for m in component_matches:
                    func_scores[m.source_func.qualified_name] = max(
                        func_scores[m.source_func.qualified_name], m.similarity_score
                    )
                    func_scores[m.existing_func.qualified_name] = max(
                        func_scores[m.existing_func.qualified_name], m.similarity_score
                    )
                top_keys = sorted(func_scores, key=func_scores.get, reverse=True)[:MAX_GROUP_SIZE]
                top_set = set(top_keys)
                func_map = {k: v for k, v in func_map.items() if k in top_set}
                component_matches = [
                    m for m in component_matches
                    if m.source_func.qualified_name in top_set
                    and m.existing_func.qualified_name in top_set
                ]

            if len(func_map) <= 2:
                # Isolated pair — keep as individual match
                results.extend(component_matches)
            else:
                # 3+ functions — collapse into a group
                best = max(component_matches, key=lambda m: m.similarity_score)
                funcs = sorted(func_map.values(), key=lambda f: (f.filepath, f.lineno))
                desc = _describe_pattern(funcs)
                results.append(FindingGroup(
                    functions=funcs,
                    representative_match=best,
                    match_count=len(component_matches),
                    pattern_description=desc,
                    reuse_type=reuse_type,
                    reuse_guidance=best.reuse_guidance,
                ))

    # ── Finding deduplication: suppress subsets ──────────────────────
    # If finding A's function set is a subset of finding B's, suppress A.
    # This prevents redundant findings like #12 and #54 covering overlapping
    # functions, or #50 and #80 being the same timeAgo list with 1 extra entry.
    results = _deduplicate_findings(results)
    results = _deduplicate_per_function(results)

    # Sort: groups and matches together by score descending
    results.sort(
        key=lambda r: r.similarity_score if isinstance(r, FindingGroup) else r.similarity_score,
        reverse=True,
    )
    return results


def _deduplicate_findings(
    results: list[FindingGroup | SimilarityMatch],
) -> list[FindingGroup | SimilarityMatch]:
    """Remove findings whose function set overlaps heavily with another finding.

    Two deduplication passes:
    1. Strict subset: finding A ⊂ finding B → suppress A.
    2. High overlap: findings sharing ≥70% of their functions (Jaccard) are
       merged into the one with the higher similarity score. This prevents
       near-duplicate grouped findings (#24 and #29 covering the same functions
       with 1-2 different members).
    """
    def _get_func_keys(item: FindingGroup | SimilarityMatch) -> set[str]:
        if isinstance(item, FindingGroup):
            return {f.qualified_name for f in item.functions}
        return {item.source_func.qualified_name, item.existing_func.qualified_name}

    # Build sets for each finding
    item_sets = [(item, _get_func_keys(item)) for item in results]

    # Pass 1: strict subset suppression
    suppressed: set[int] = set()
    for i, (item_i, set_i) in enumerate(item_sets):
        if i in suppressed:
            continue
        for j, (item_j, set_j) in enumerate(item_sets):
            if i == j or j in suppressed:
                continue
            if set_j < set_i:
                suppressed.add(j)

    # Pass 2: high-overlap merging (Jaccard ≥ threshold)
    # Use lower threshold (0.60) for group-vs-group to catch near-identical
    # findings like #91/#92 that differ by one member. Pairs keep 0.70.
    # When merging two groups, union their function sets into the survivor.
    remaining = [i for i in range(len(item_sets)) if i not in suppressed]
    for idx_a, i in enumerate(remaining):
        if i in suppressed:
            continue
        item_i, set_i = item_sets[i]
        both_groups = isinstance(item_i, FindingGroup)
        for idx_b in range(idx_a + 1, len(remaining)):
            j = remaining[idx_b]
            if j in suppressed:
                continue
            item_j, set_j = item_sets[j]
            # Jaccard similarity
            intersection = len(set_i & set_j)
            union_size = len(set_i | set_j)
            if union_size == 0:
                continue
            jaccard = intersection / union_size
            merge_threshold = 0.60 if (both_groups and isinstance(item_j, FindingGroup)) else 0.70
            if jaccard >= merge_threshold:
                # Keep the one with higher similarity score (or more functions)
                score_i = item_i.similarity_score
                score_j = item_j.similarity_score
                if score_j > score_i or (score_j == score_i and len(set_j) > len(set_i)):
                    suppressed.add(i)
                    break  # i is suppressed, no need to check further
                else:
                    suppressed.add(j)

    return [item for idx, (item, _) in enumerate(item_sets) if idx not in suppressed]


def _deduplicate_per_function(
    results: list[FindingGroup | SimilarityMatch],
) -> list[FindingGroup | SimilarityMatch]:
    """Ensure each function appears in at most one finding.

    For each function that appears in multiple findings, keep it only in the
    highest-scoring finding and remove it from others. If removing a function
    causes a FindingGroup to have fewer than 3 functions, convert it to
    individual pairs or suppress it.
    """
    # Build map: function qualified_name -> list of (finding_index, finding_score)
    func_to_findings: dict[str, list[tuple[int, float]]] = defaultdict(list)

    for idx, item in enumerate(results):
        score = item.similarity_score
        if isinstance(item, FindingGroup):
            for f in item.functions:
                func_to_findings[f.qualified_name].append((idx, score))
        else:
            func_to_findings[item.source_func.qualified_name].append((idx, score))
            func_to_findings[item.existing_func.qualified_name].append((idx, score))

    # For each function in multiple findings, assign to the highest-scoring one
    func_assignment: dict[str, int] = {}
    for func_name, finding_entries in func_to_findings.items():
        if len(finding_entries) <= 1:
            func_assignment[func_name] = finding_entries[0][0]
            continue
        best_idx = max(finding_entries, key=lambda x: x[1])[0]
        func_assignment[func_name] = best_idx

    # Rebuild findings, removing unassigned functions from groups
    new_results: list[FindingGroup | SimilarityMatch] = []
    for idx, item in enumerate(results):
        if isinstance(item, FindingGroup):
            kept_funcs = [f for f in item.functions if func_assignment.get(f.qualified_name) == idx]
            if len(kept_funcs) >= 3:
                new_results.append(FindingGroup(
                    functions=kept_funcs,
                    representative_match=item.representative_match,
                    match_count=item.match_count,
                    pattern_description=_describe_pattern(kept_funcs),
                    reuse_type=item.reuse_type,
                    reuse_guidance=item.reuse_guidance,
                ))
            elif len(kept_funcs) == 2:
                # Downgrade to pair — use the representative match
                new_results.append(item.representative_match)
            # 0-1 functions remaining → suppress entirely
        else:
            src_assigned = func_assignment.get(item.source_func.qualified_name) == idx
            ext_assigned = func_assignment.get(item.existing_func.qualified_name) == idx
            if src_assigned and ext_assigned:
                new_results.append(item)

    return new_results


def _describe_pattern(funcs: list[ExtractedFunction]) -> str:
    """Generate a human-readable description of what a group of functions share."""
    # Check if they share a common directory pattern
    filepaths = [f.filepath for f in funcs]
    common_dir = _common_path_prefix(filepaths)

    # Check if they share common names
    names = [f.name for f in funcs]
    if len(set(names)) == 1:
        return f"{len(funcs)} implementations of {names[0]}()"

    # Check for common naming pattern (e.g., all are GET/POST handlers)
    http_methods = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
    if all(n.upper() in http_methods for n in names):
        return f"{len(funcs)} HTTP method handlers"

    if common_dir:
        return f"{len(funcs)} structurally similar functions under {common_dir}/"

    return f"{len(funcs)} structurally similar functions"


def _common_path_prefix(paths: list[str]) -> str:
    """Find the longest common directory prefix across file paths."""
    if not paths:
        return ""
    parts = [p.split("/") for p in paths]
    common = []
    for level in zip(*parts):
        if len(set(level)) == 1:
            common.append(level[0])
        else:
            break
    return "/".join(common)


# ── Import suggestion ─────────────────────────────────────────────────────

_IMPORT_TEMPLATES: dict[str, str] = {
    "python": "from {module} import {name}",
    "javascript": "import {{ {name} }} from '{module}';",
    "typescript": "import {{ {name} }} from '{module}';",
    "go": "// Use {name} from {module}",
    "rust": "use {module}::{name};",
    "java": "import {module}.{name};",
    "ruby": "require '{module}'  # use {name}",
    "c": '#include "{module}" // use {name}',
    "cpp": '#include "{module}" // use {name}',
}


def _generate_import_suggestion(
    func: ExtractedFunction,
    reuse_type: str,
    source_func: ExtractedFunction | None = None,
) -> str:
    if reuse_type == "cross_service_reference":
        return (
            f"Cross-service: {func.name}() in {func.filepath} — direct import NOT possible. "
            f"Extract to a shared library or accept as intentional service boundary duplication."
        )
    if reuse_type == "reference_only":
        return f"// Reference: {func.name}() in {func.filepath} ({func.language}) — cannot import directly"
    if getattr(func, "is_nested", False):
        return (
            f"// Reference: {func.name}() is a nested function in {func.filepath}:{func.lineno} "
            f"— cannot be imported (closure)"
        )
    if reuse_type == "same_file_refactor":
        ctx_parts = []
        if source_func and getattr(source_func, "class_name", None):
            ctx_parts.append(f"{source_func.class_name}.{source_func.name}()")
        else:
            ctx_parts.append(f"{func.name}() at line {func.lineno}")
        if getattr(func, "class_name", None):
            ctx_parts.append(f"{func.class_name}.{func.name}()")
        return f"Refactor within {func.filepath} — consolidate or acknowledge intentional duplication"
    if reuse_type == "extract_utility":
        # Identify what differs between the two functions
        diff_parts = []
        if source_func:
            lits_a = _extract_string_literals(source_func.source)
            lits_b = _extract_string_literals(func.source)
            # Find literals that differ
            for a, b in zip(lits_a, lits_b):
                if a != b:
                    diff_parts.append(f"{a} vs {b}")
        diff_hint = f" (differs in: {', '.join(diff_parts[:3])})" if diff_parts else ""
        return (
            f"Extract a shared helper that accepts the varying parts as parameters{diff_hint}"
        )
    module_path = func.filepath
    if module_path.endswith(".py"):
        module_path = module_path[:-3].replace("/", ".").replace("\\", ".").lstrip(".")
    template = _IMPORT_TEMPLATES.get(func.language, "// Use {name} from {module}")
    return template.format(module=module_path, name=func.name)


# ── Main engine ───────────────────────────────────────────────────────────

class SimilarityEngine:
    """Two-tier similarity detection engine for code clone detection.

    Architecture:
        Tier 1: AST hash matching — catches Type-1/Type-2 clones in O(1).
            Exact structural clones detected via hash-map grouping.
            100% recall on Type-1 and Type-2 with zero false positives.

        Tier 2: UniXcoder embedding similarity — catches Type-3/Type-4 clones.
            Pre-computed 768-dim vectors stored on disk, cosine similarity
            search via NumPy brute-force (~2ms at 100K functions).

    The two tiers run in parallel and produce non-overlapping results:
    - Tier 1 catches Type-1/Type-2 (exact/renamed clones)
    - Tier 2 catches Type-3/Type-4 (modified/semantic clones)
    No deduplication needed because they target different clone types.

    Usage:
        engine = SimilarityEngine(
            embedding_store=store,
            embedding_model=model,
        )
    """

    def __init__(
        self,
        similarity_threshold: float = 0.50,
        service_boundaries: list[str] | None = None,
        embedding_store: EmbeddingStore | None = None,
        embedding_model: EmbeddingModel | None = None,
    ):
        self.similarity_threshold = similarity_threshold
        self.service_boundaries: list[str] = service_boundaries or []
        self._functions: dict[str, ExtractedFunction] = {}
        # AST hash index for O(1) Type-1/Type-2 lookups
        self._ast_hash_groups: dict[str, list[str]] = defaultdict(list)
        # Tier 2: Embedding-based Type-3/Type-4 detection
        self._embedding_store = embedding_store
        self._embedding_model = embedding_model
        self._embedding_rows: dict[str, int] = {}

    def add_function(
        self, func: ExtractedFunction, embedding_row: int | None = None,
    ) -> None:
        key = func.qualified_name
        self._functions[key] = func

        # Tier 1: AST hash index for Type-1/Type-2 detection
        if func.ast_hash:
            self._ast_hash_groups[func.ast_hash].append(key)

        # Tier 2: Track embedding row for Type-3/Type-4 detection
        if embedding_row is not None:
            self._embedding_rows[key] = embedding_row

    def find_all_matches(self, threshold: float | None = None) -> list[SimilarityMatch]:
        """Batch scan: find ALL redundancies in the entire index.

        Two-tier architecture:
            Tier 1: AST hash grouping (O(n)) — catches Type-1/Type-2 clones
            Tier 2: Embedding similarity — catches Type-3/Type-4 clones

        Tiers run in parallel and target non-overlapping clone types,
        so no deduplication is needed in the merge step.
        """
        if threshold is None:
            threshold = self.similarity_threshold

        matches: list[SimilarityMatch] = []
        seen_pairs: set[tuple[str, str]] = set()

        def _add_match(key_a: str, key_b: str, match_type: str, score: float) -> None:
            names = sorted([key_a, key_b])
            pair = (names[0], names[1])
            if pair in seen_pairs:
                return
            seen_pairs.add(pair)

            func_a = self._functions[key_a]
            func_b = self._functions[key_b]

            match = self._apply_filters(
                func_a, func_b, match_type, score, threshold, batch_mode=True,
            )
            if match is not None:
                matches.append(match)

        # ── Tier 1: AST hash groups (O(n)) — Type-1/Type-2 ──────────
        for keys in self._ast_hash_groups.values():
            if len(keys) < 2:
                continue
            for i in range(len(keys)):
                for j in range(i + 1, len(keys)):
                    if keys[i] != keys[j]:
                        _add_match(keys[i], keys[j], "exact_structure", 1.0)

        # ── Tier 2: Embedding similarity — Type-3/Type-4 ────────────
        if self._embedding_store is not None:
            from echo_guard.embeddings import DEFAULT_EMBEDDING_THRESHOLD, get_embedding_threshold

            # Use the lowest per-language threshold to get all candidates,
            # then filter per-pair based on the languages involved.
            min_threshold = min(
                DEFAULT_EMBEDDING_THRESHOLD,
                *[get_embedding_threshold(f.language) for f in self._functions.values()],
            ) if self._functions else DEFAULT_EMBEDDING_THRESHOLD

            emb_pairs = self._embedding_store.batch_search(
                threshold=min_threshold,
            )

            row_to_key: dict[int, str] = {
                row: key for key, row in self._embedding_rows.items()
            }

            for row_a, row_b, score in emb_pairs:
                key_a = row_to_key.get(row_a)
                key_b = row_to_key.get(row_b)
                if key_a is None or key_b is None:
                    continue
                if key_a not in self._functions or key_b not in self._functions:
                    continue

                # Apply per-language threshold
                func_a = self._functions[key_a]
                func_b = self._functions[key_b]
                lang_threshold = get_embedding_threshold(func_a.language, func_b.language)
                if score < lang_threshold:
                    continue

                _add_match(key_a, key_b, "embedding_semantic", score)

        matches.sort(key=lambda m: m.similarity_score, reverse=True)
        return matches

    def _apply_filters(
        self,
        func_a: ExtractedFunction,
        func_b: ExtractedFunction,
        match_type: str,
        score: float,
        threshold: float,
        batch_mode: bool = False,
    ) -> SimilarityMatch | None:
        """Apply scope penalties and intent filters to a candidate match.

        Returns a SimilarityMatch if the pair passes all filters, None otherwise.
        These filters are shared across all tiers (Tier 1, Tier 2, and fallback).

        Args:
            batch_mode: If True, apply stricter thresholds for same-file and
                cross-language matches (used by find_all_matches). Single-function
                queries (find_similar) use the caller's threshold directly.
        """
        # Apply scope penalty
        penalty = scope_penalty(func_a, func_b)
        adjusted = score * penalty
        if adjusted < threshold:
            return None

        # Batch-mode-only filters: stricter thresholds for scan context
        if batch_mode:
            # Same-file matches need ≥95% similarity
            if func_a.filepath == func_b.filepath and adjusted < 0.95:
                return None

            # Cross-language matches need ≥80% similarity
            if func_a.language != func_b.language and adjusted < 0.80:
                return None

        # Skip same-file variants that differ only in large opaque data (SVG paths, etc.)
        if _is_low_value_variant(func_a, func_b):
            return None

        # Skip framework-required exports that must exist per-file
        if _is_framework_required_export(func_a) and _is_framework_required_export(func_b):
            return None

        # Skip framework page exports that must exist as separate files
        if _is_framework_page_export(func_a) and _is_framework_page_export(func_b):
            return None

        # Skip trivial functions (one-liners) that are both trivial
        if _is_trivial_function(func_a) and _is_trivial_function(func_b):
            return None

        # Skip per-service boilerplate (health endpoints, lifespan hooks)
        if _is_per_service_boilerplate(func_a, func_b, self.service_boundaries):
            return None

        # Skip constructor matches across unrelated classes
        if _is_constructor_match(func_a, func_b):
            return None

        # Skip observer/Protocol pattern (N classes implementing same interface method)
        if _is_observer_pattern(func_a, func_b):
            return None

        # Skip same-file CRUD operations (create_X / update_X / delete_X)
        if _is_same_file_crud(func_a, func_b):
            return None

        # Skip semantically inverse pairs (encrypt/decrypt, enable/disable)
        if _is_antonym_pair(func_a, func_b):
            return None

        # Skip cross-file structural templates with different domain nouns
        if _is_structural_template_pair(func_a, func_b):
            return None

        # Skip UI wrapper component matches (Panel/Card/Toolbar/Badge/Alert)
        if _is_ui_wrapper_pair(func_a, func_b):
            return None

        # Skip matches between different-named components in UI directories
        if _is_ui_directory_pair(func_a, func_b):
            return None

        base_reuse = classify_reuse(func_a.language, func_b.language)
        reuse = classify_suggestion(func_a, func_b, base_reuse, self.service_boundaries)
        return SimilarityMatch(
            source_func=func_a,
            existing_func=func_b,
            match_type=match_type,
            similarity_score=adjusted,
            import_suggestion=_generate_import_suggestion(func_b, reuse, source_func=func_a),
            reuse_type=reuse,
            reuse_guidance=get_reuse_guidance(reuse, func_a.language, func_b.language),
            raw_score=score,
        )

    def find_similar(
        self,
        func: ExtractedFunction,
        threshold: float | None = None,
        candidates: list[ExtractedFunction] | None = None,
    ) -> list[SimilarityMatch]:
        """Find functions similar to a single function.

        Used by:
        - `echo-guard check` (single-file pre-commit check)
        - MCP server `check_for_duplicates` (must complete in <500ms)

        Two-tier architecture:
            Tier 1: AST hash lookup (O(1)) — Type-1/Type-2
            Tier 2: Embedding search (~17ms) — Type-3/Type-4
        """
        if threshold is None:
            threshold = self.similarity_threshold

        func_key = func.qualified_name
        matches: list[SimilarityMatch] = []
        seen_keys: set[str] = set()

        search_space = {f.qualified_name: f for f in candidates} if candidates else self._functions

        # ── Tier 1: AST hash lookup (O(1)) — Type-1/Type-2 ──────────
        if func.ast_hash and func.ast_hash in self._ast_hash_groups:
            for key in self._ast_hash_groups[func.ast_hash]:
                if key == func_key or key not in search_space:
                    continue
                existing = self._functions[key]
                match = self._apply_filters(
                    func, existing, "exact_structure", 1.0, threshold,
                )
                if match is not None:
                    matches.append(match)
                seen_keys.add(key)

        # ── Tier 2: Embedding search — Type-3/Type-4 ────────────────
        if self._embedding_store is not None and self._embedding_model is not None:
            from echo_guard.embeddings import DEFAULT_EMBEDDING_THRESHOLD, get_embedding_threshold

            query_embedding = self._embedding_model.embed_function(func)

            exclude_rows: set[int] = set()
            if func_key in self._embedding_rows:
                exclude_rows.add(self._embedding_rows[func_key])

            # When candidates are specified (e.g., dep graph routing), restrict
            # the search to only rows in the candidate set so out-of-scope
            # rows don't crowd out valid matches in the top-k.
            if candidates is not None:
                allowed_keys = set(search_space.keys())
                for key, row in self._embedding_rows.items():
                    if key not in allowed_keys:
                        exclude_rows.add(row)

            search_threshold = min(
                DEFAULT_EMBEDDING_THRESHOLD,
                get_embedding_threshold(func.language),
            )

            results = self._embedding_store.search(
                query=query_embedding,
                k=50,
                threshold=search_threshold,
                exclude_rows=exclude_rows,
            )

            row_to_key: dict[int, str] = {
                row: key for key, row in self._embedding_rows.items()
            }

            for row_idx, score in results:
                neighbor_key = row_to_key.get(row_idx)
                if neighbor_key is None or neighbor_key in seen_keys:
                    continue

                neighbor = self._functions[neighbor_key]

                # Apply per-language threshold
                lang_threshold = get_embedding_threshold(func.language, neighbor.language)
                if score < lang_threshold:
                    continue

                match = self._apply_filters(
                    func, neighbor, "embedding_semantic", score, threshold,
                )
                if match is not None:
                    matches.append(match)
                seen_keys.add(neighbor_key)

        matches.sort(key=lambda m: m.similarity_score, reverse=True)
        return matches

    @property
    def indexed_count(self) -> int:
        return len(self._functions)
