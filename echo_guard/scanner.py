"""High-level scanning orchestrator.

Ties together: universal language parsing, indexing, dependency graph,
and similarity detection into a single pipeline.
"""

from __future__ import annotations

import fnmatch
from collections import defaultdict
from pathlib import Path

from echo_guard.config import EchoGuardConfig
from echo_guard.depgraph import DependencyGraph
from echo_guard.index import FunctionIndex
from echo_guard.languages import (
    ExtractedFunction,
    detect_language,
    extract_functions_universal,
    supported_extensions,
)
from echo_guard.similarity import SimilarityEngine, SimilarityMatch


def _load_ignore_patterns(config: EchoGuardConfig) -> list[str]:
    """Return ignore patterns from the active config object."""
    return config.ignore


def _is_ignored(rel_path: str, ignore_patterns: list[str]) -> bool:
    """Check if a relative path matches any ignore pattern."""
    rel_parts = Path(rel_path).parts
    for pattern in ignore_patterns:
        clean = pattern.rstrip("/")
        # Directory name match: pattern matches any path component
        if "/" not in clean and not any(c in clean for c in "*?["):
            if clean in rel_parts:
                return True
        # Path prefix match: "docs_src/" or "tests/snapshots"
        elif rel_path.startswith(clean + "/") or rel_path.startswith(clean):
            return True
        # Glob match against full relative path
        elif fnmatch.fnmatch(rel_path, pattern):
            return True
        # Also try matching against just the filename
        elif fnmatch.fnmatch(Path(rel_path).name, pattern):
            return True
    return False


def discover_files(
    root: str | Path,
    config: EchoGuardConfig | None = None,
) -> list[Path]:
    """Find all supported source files in a directory tree."""
    if config is None:
        config = EchoGuardConfig()

    root = Path(root)
    extensions = supported_extensions()

    # If languages are restricted in config, filter extensions
    if config.languages:
        from echo_guard.languages import LANGUAGES
        allowed_exts = set()
        for lang in config.languages:
            spec = LANGUAGES.get(lang)
            if spec:
                allowed_exts.update(spec.extensions)
        extensions = extensions & allowed_exts

    ignore_patterns = _load_ignore_patterns(config)

    files = []
    for source_file in root.rglob("*"):
        if not source_file.is_file():
            continue
        if source_file.suffix.lower() not in extensions:
            continue

        rel_parts = source_file.relative_to(root).parts
        # Skip excluded directories
        if any(part in config.exclude_dirs or part.endswith(".egg-info") for part in rel_parts):
            continue
        # Skip excluded patterns
        name = source_file.name
        if any(fnmatch.fnmatch(name, pat) for pat in config.exclude_patterns):
            continue
        # Skip ignore patterns from .echoguard.yml
        rel_path = str(source_file.relative_to(root))
        if ignore_patterns and _is_ignored(rel_path, ignore_patterns):
            continue

        files.append(source_file)

    return sorted(files)


def index_repo(
    repo_root: str | Path,
    config: EchoGuardConfig | None = None,
    verbose: bool = False,
    incremental: bool = True,
) -> tuple[FunctionIndex, int, int, dict[str, int]]:
    """Index all functions in a repository.

    When incremental=True (default), only re-parses files that changed since
    the last index. Falls back to full reindex on first run.

    Returns (index, file_count, function_count, language_counts).
    """
    repo_root = Path(repo_root)
    if config is None:
        config = EchoGuardConfig.load(repo_root)

    index = FunctionIndex(repo_root)

    source_files = discover_files(repo_root, config)
    total_functions = 0
    lang_counts: dict[str, int] = defaultdict(int)
    files_parsed = 0
    files_skipped = 0

    # For incremental: track which files still exist
    current_files: set[str] = set()

    for source_file in source_files:
        rel_path = str(source_file.relative_to(repo_root))
        current_files.add(rel_path)
        lang = detect_language(rel_path)
        if lang is None:
            continue

        # Incremental: skip files that haven't changed
        if incremental and not index.file_needs_reindex(rel_path, source_file):
            files_skipped += 1
            # Count existing functions from metadata
            meta = index.get_file_metadata(rel_path)
            if meta:
                total_functions += meta["function_count"]
                lang_counts[lang] = lang_counts.get(lang, 0) + meta["function_count"]
            continue

        try:
            source = source_file.read_text(encoding="utf-8")
            functions = extract_functions_universal(rel_path, source, lang)

            functions = [
                f for f in functions
                if config.min_function_lines <= (f.end_lineno - f.lineno + 1) <= config.max_function_lines
            ]

            # Remove old functions for this file and insert new ones
            index.remove_file(rel_path)
            index.upsert_functions(functions)

            # Update file metadata for incremental tracking
            stat = source_file.stat()
            index.upsert_file_metadata(
                filepath=rel_path,
                mtime=stat.st_mtime,
                size_bytes=stat.st_size,
                git_sha=None,
                function_count=len(functions),
            )

            total_functions += len(functions)
            files_parsed += 1
            if functions:
                lang_counts[lang] = lang_counts.get(lang, 0) + len(functions)
        except Exception as e:
            if verbose:
                print(f"  Warning: could not parse {rel_path}: {e}")

    # Remove files that no longer exist
    if incremental:
        indexed_files = index.get_all_indexed_files()
        for old_file in indexed_files - current_files:
            removed = index.remove_file(old_file)
            if verbose and removed:
                print(f"  Removed {old_file} ({removed} functions) — file deleted")

    if verbose and incremental and files_skipped > 0:
        print(f"  Incremental: {files_parsed} files parsed, {files_skipped} unchanged (skipped)")

    return index, len(source_files), total_functions, dict(lang_counts)


def _build_dep_graph(
    index: FunctionIndex,
) -> DependencyGraph:
    """Build a dependency graph from the indexed functions."""
    graph = DependencyGraph()
    all_functions = index.get_all_functions()

    # Group by file
    by_file: dict[str, list[ExtractedFunction]] = defaultdict(list)
    for func in all_functions:
        by_file[func.filepath].append(func)

    for filepath, funcs in by_file.items():
        graph.add_module(filepath, funcs)

    graph.build()
    return graph


def _setup_embeddings(
    index: FunctionIndex,
    all_functions: list[ExtractedFunction],
    index_dir: Path,
    verbose: bool = False,
) -> tuple["EmbeddingStore | None", "EmbeddingModel | None", dict[str, int]]:
    """Set up embedding infrastructure for Tier 2 detection.

    Computes embeddings for any functions that don't have them yet
    (incremental — only new/changed functions are embedded).

    Returns (store, model, embedding_row_map). Returns (None, None, {})
    if embedding setup fails (e.g., model download fails on first use).
    The scan will proceed with Tier 1 only.
    """
    try:
        from echo_guard.embeddings import EmbeddingModel, EmbeddingStore

        store = EmbeddingStore(index_dir)
        model = EmbeddingModel()

        # Check which functions need embeddings
        model_version = model.model_id
        needs_embedding = index.get_functions_needing_embeddings(model_version)

        if needs_embedding:
            if verbose:
                import logging
                logging.getLogger("echo_guard.embeddings").setLevel(logging.INFO)
                logging.basicConfig(level=logging.INFO, format="%(message)s")

            model.ensure_ready()

            embeddings = model.embed_functions(
                needs_embedding,
                show_progress=verbose,
            )

            rows = store.add_embeddings(embeddings)
            if len(rows) != len(needs_embedding):
                raise RuntimeError(
                    f"Embedding count mismatch: {len(needs_embedding)} functions but {len(rows)} rows"
                )
            updates = [
                (func.qualified_name, row, model_version)
                for func, row in zip(needs_embedding, rows, strict=True)
            ]
            index.set_embedding_rows(updates)

        row_map = index.get_embedding_row_map()
        return store, model, row_map

    except Exception as exc:
        import logging
        logging.getLogger("echo_guard.embeddings").warning(
            "Embedding setup failed (%s). Tier 2 detection disabled for this scan.", exc,
        )
        return None, None, {}


def scan_for_redundancy(
    repo_root: str | Path,
    target_files: list[str] | None = None,
    threshold: float | None = None,
    config: EchoGuardConfig | None = None,
    verbose: bool = False,
) -> list[SimilarityMatch]:
    """Scan the repo for redundant functions using the two-tier pipeline.

    Architecture:
        Tier 1: AST hash matching for Type-1/Type-2 clones (O(1) lookup)
        Tier 2: UniXcoder embeddings for Type-3/Type-4 clones (cosine search)
    """
    repo_root = Path(repo_root)
    if config is None:
        config = EchoGuardConfig.load(repo_root)
    if threshold is None:
        threshold = config.threshold

    index = FunctionIndex(repo_root)
    all_functions = index.get_all_functions()
    if not all_functions:
        return []

    # Detect service boundaries — from config or auto-detect from file paths
    from echo_guard.similarity import _detect_service_boundaries
    svc_boundaries = config.service_boundaries
    if not svc_boundaries:
        svc_boundaries = _detect_service_boundaries([f.filepath for f in all_functions])

    # Set up embedding infrastructure (if available)
    index_dir = repo_root / ".echo-guard"
    embedding_store, embedding_model, row_map = _setup_embeddings(
        index, all_functions, index_dir, verbose=verbose,
    )

    # Build similarity engine with Tier 2 if embeddings available
    engine = SimilarityEngine(

        similarity_threshold=threshold,
        service_boundaries=svc_boundaries,
        embedding_store=embedding_store,
        embedding_model=embedding_model,
    )
    for func in all_functions:
        emb_row = row_map.get(func.qualified_name)
        engine.add_function(func, embedding_row=emb_row)

    if target_files:
        # Per-file scan: only check specific files against the full index
        all_matches: list[SimilarityMatch] = []
        seen_pairs: set[tuple[str, str]] = set()
        for filepath in target_files:
            rel_path = str(Path(filepath).relative_to(repo_root)) if Path(filepath).is_absolute() else filepath
            file_funcs = index.get_functions_by_file(rel_path)
            for func in file_funcs:
                matches = engine.find_similar(func, threshold=threshold)
                for match in matches:
                    # Only report matches to functions outside target files
                    if match.existing_func.filepath not in target_files:
                        names = sorted([
                            match.source_func.qualified_name,
                            match.existing_func.qualified_name,
                        ])
                        pair = (names[0], names[1])
                        if pair not in seen_pairs:
                            seen_pairs.add(pair)
                            all_matches.append(match)
        all_matches.sort(key=lambda m: m.similarity_score, reverse=True)
        return all_matches
    else:
        # Full repo batch scan — the fast path
        return engine.find_all_matches(threshold=threshold)


def check_files(
    repo_root: str | Path,
    files: list[str],
    threshold: float | None = None,
    config: EchoGuardConfig | None = None,
    verbose: bool = False,
) -> list[SimilarityMatch]:
    """Check specific files against the existing index (fast path for hooks).

    Only parses the changed files and compares them against the full index.
    Uses two-tier detection: AST hash (T1/T2) + embeddings (T3/T4).
    """
    repo_root = Path(repo_root)
    if config is None:
        config = EchoGuardConfig.load(repo_root)
    if threshold is None:
        threshold = config.threshold

    index = FunctionIndex(repo_root)
    all_functions = index.get_all_functions()

    # Set up embedding infrastructure (if available)
    index_dir = repo_root / ".echo-guard"
    embedding_store, embedding_model, row_map = _setup_embeddings(
        index, all_functions, index_dir, verbose=verbose,
    )

    # Detect service boundaries
    from echo_guard.similarity import _detect_service_boundaries
    svc_boundaries = config.service_boundaries
    if not svc_boundaries:
        svc_boundaries = _detect_service_boundaries([f.filepath for f in all_functions])

    engine = SimilarityEngine(
        similarity_threshold=threshold,
        service_boundaries=svc_boundaries,
        embedding_store=embedding_store,
        embedding_model=embedding_model,
    )
    for func in all_functions:
        emb_row = row_map.get(func.qualified_name)
        engine.add_function(func, embedding_row=emb_row)

    # Build dep graph
    dep_graph = None
    by_file: dict[str, list[ExtractedFunction]] = defaultdict(list)
    for func in all_functions:
        by_file[func.filepath].append(func)

    if config.enable_dep_graph:
        dep_graph = _build_dep_graph(index)

    # Parse target files and check
    all_matches: list[SimilarityMatch] = []
    seen_pairs: set[tuple[str, str]] = set()

    for filepath in files:
        abs_path = Path(filepath)
        if not abs_path.is_absolute():
            abs_path = repo_root / filepath

        if not abs_path.exists():
            continue

        rel_path = str(abs_path.relative_to(repo_root))
        lang = detect_language(rel_path)
        if lang is None:
            continue

        try:
            source = abs_path.read_text(encoding="utf-8")
        except Exception:
            continue

        new_functions = extract_functions_universal(rel_path, source, lang)
        new_functions = [
            f for f in new_functions
            if config.min_function_lines <= (f.end_lineno - f.lineno + 1) <= config.max_function_lines
        ]

        # Compute embeddings for new functions if Tier 2 is active
        new_emb_rows: dict[str, int] = {}
        if embedding_store is not None and embedding_model is not None and new_functions:
            embeddings = embedding_model.embed_functions(new_functions)
            rows = embedding_store.add_embeddings(embeddings)
            if len(rows) != len(new_functions):
                raise RuntimeError(
                    f"Embedding count mismatch: {len(new_functions)} functions but {len(rows)} rows"
                )
            model_version = embedding_model.model_id
            for func, emb_row in zip(new_functions, rows, strict=True):
                new_emb_rows[func.qualified_name] = emb_row

        for func in new_functions:
            emb_row = new_emb_rows.get(func.qualified_name)
            engine.add_function(func, embedding_row=emb_row)

            candidates = None
            if dep_graph is not None:
                candidates = dep_graph.get_comparison_candidates(func.filepath, by_file)

            matches = engine.find_similar(func, threshold=threshold, candidates=candidates)
            for match in matches:
                if match.existing_func.filepath not in files:
                    names = sorted([
                        match.source_func.qualified_name,
                        match.existing_func.qualified_name,
                    ])
                    pair = (names[0], names[1])
                    if pair not in seen_pairs:
                        seen_pairs.add(pair)
                        all_matches.append(match)

        # Update index
        index.remove_file(rel_path)
        index.upsert_functions(new_functions)

        # Persist embedding rows so they aren't re-computed next time
        if new_emb_rows:
            emb_updates = [
                (qname, row, model_version)
                for qname, row in new_emb_rows.items()
            ]
            index.set_embedding_rows(emb_updates)

    all_matches.sort(key=lambda m: m.similarity_score, reverse=True)
    return all_matches
