# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased] — Semantic Detection & Scale

### Architecture

- **Two-tier detection pipeline** replacing LSH + TF-IDF:
  - **Tier 1**: AST hash matching for Type-1/Type-2 clones (O(1) lookup, 100% recall)
  - **Tier 2**: UniXcoder embeddings for Type-3/Type-4 clones (ONNX INT8, ~15ms/function)
- **Removed** all LSH, TF-IDF, MinHash, datasketch, and scikit-learn code and dependencies
- Embeddings are now a **core dependency**, not optional — all clone types detected out of the box
- Per-language embedding thresholds calibrated empirically (Python: 0.94, Java: 0.81, JS: 0.85, Go: 0.81, C/C++: 0.83)

### Added

- `echo_guard/embeddings.py` — EmbeddingModel (ONNX UniXcoder) and EmbeddingStore (NumPy memmap)
- `echo_guard/utils.py` — shared utilities (find_repo_root)
- **Clone type classification** on every finding: `type1_type2`, `type3`, `type4` with human-readable labels
- **Severity derived from clone type**: HIGH (T1/T2 exact, T3 modified) and MEDIUM (T4 semantic). Removed "low" severity.
- `echo-guard review` — interactive CLI to walk through findings and acknowledge/mark false positives
- `echo-guard acknowledge` — acknowledge a single finding by ID
- `echo-guard training-data` — view/export collected training data for model fine-tuning
- **MCP tools**:
  - `check_for_duplicates` (renamed from `check_before_write`) — smarter tool description guiding when to call it
  - `resolve_finding` — record verdict (fixed/acknowledged/false_positive), collects training data
  - `respond_to_probe` — evaluate low-confidence matches for training data collection
  - `get_finding_resolutions` — observability for resolution history
- **Finding IDs** — stable, deterministic IDs for every finding (`filepath:func||filepath:func`)
- **Finding resolution system** — `finding_resolutions` table in DuckDB, resolutions suppress findings in MCP and CI
- **Training data collection** — `training_pairs` table stores code pairs + verdicts from resolutions and probes
- **Low-confidence probes** — 20% of `check_for_duplicates` calls include a below-threshold candidate for the agent to evaluate
- **USearch ANN** optional scale tier (`pip install "echo-guard[scale]"`) for >500K function codebases
- **CLI banner** — ASCII art displayed during `echo-guard setup`
- `.echoguard-acknowledged` — committed file listing reviewed findings, suppressed in CI
- `.echoguard-ignore` — committed file with scan exclusion patterns (was `.echoguardignore`)
- Both files auto-generated during `echo-guard setup`
- DuckDB schema additions: `embedding_row`, `embedding_version` columns, `finding_resolutions` table, `training_pairs` table

### Improved

- **Benchmark results** (two-tier pipeline vs old TF-IDF):
  - BCB Type-3 recall: 2% → **58.5%** (+29x)
  - GCB Type-4 recall: 82% → **96.0%** (+14pp)
  - POJ-104 Type-4 recall: 10.9% → **78.6%** (+7x)
  - BCB F1: 58.0% → **76.1%** (+18pp)
- GitHub Action shows **clone type** in annotations and PR comment table
- GitHub Action reads `.echoguard-acknowledged` to skip reviewed findings
- MCP `check_for_duplicates` response includes `finding_id`, `clone_type`, `action` (concise guidance), and `fix` (import statement)
- MCP `resolve_finding` writes to both DuckDB and `.echoguard-acknowledged`
- CLI output shows clone type labels (T1/T2 Exact, T3 Modified, T4 Semantic) alongside severity
- Trivial function filter improved — catches guard-and-delegate patterns and pure delegate wrappers
- Intent filter: `_is_structural_template_pair` catches more verb+noun patterns
- `check_files` now detects service boundaries (was missing)
- `_setup_embeddings` handles model download failures gracefully (falls back to Tier 1)
- Inline comment parsing fixed in `.echoguard-acknowledged` readers

### Removed

- `datasketch` dependency (MinHash, MinHashLSH)
- `scikit-learn` dependency (TfidfVectorizer, cosine_similarity)
- `[embeddings]` optional dependency group — embeddings are now core
- "low" severity — all findings are either HIGH or MEDIUM
- LSH threshold, TF-IDF matrix, MinHash signatures, tokenization code
- `_tokenize_code()`, `_make_minhash()`, `_build_tfidf()`, `_find_tfidf_matches()`, `_signature_compatible()`
- `embeddings_available()` function and all conditional guards
- `use_embeddings` property
- Dead `compute_signature_key()` in parser.py
- Dead `_get_severity()` in output.py
- `cli-title.py` standalone file (integrated into CLI setup)

### Documentation

- `docs/ARCHITECTURE.md` — full two-tier pipeline documentation
- `docs/TYPE4-ANALYSIS.md` — analysis of why Type-4 detection varies by dataset, with code samples
- `docs/FINE-TUNING.md` — roadmap for contrastive fine-tuning, available datasets, data collection strategy, privacy model
- Updated `BENCHMARKS.md` — generated from new pipeline with before/after comparison
- Updated `ROADMAP.md` — Phase 3 and Phase 6 marked complete, consent model documented
- Updated `README.md` — new architecture, benchmark results, MCP tools, CLI commands, privacy notice

---

## [0.1.2] - 2026-03-23

### Added

- `CONTRIBUTING.md` with development setup and contribution guidelines
- `py.typed` marker for PEP 561 type hint support
- Multi-version CI testing (Python 3.10, 3.11, 3.12, 3.13)
- Automated release pipeline — tag push creates GitHub Release and publishes to PyPI
- `bump-my-version` config for single-command version bumps
- PyPI keywords for discoverability
- Changelog URL in project metadata
- Windows path example for MCP server registration in README

### Improved

- MCP server: normalize file paths so `./src/foo.py` and `src/foo.py` match
- MCP server: use `try/finally` to close index on all exception paths
- MCP server: resolve file paths against repo root for file previews
- MCP server: reject blank queries in `search_functions`
- Moved `fastmcp` from core dependencies to `[mcp]` optional extra

### Fixed

- MCP server: `_find_callers` used substring matching causing false positives
- EN DASH character in CLI help text causing terminal display issues
- Removed dead code: unused imports, variables, and constants across all modules
- Fixed `max()` key argument type errors in `health.py` and `depgraph.py`
- Fixed `fetchone()` null safety in `index.py`
- Fixed tuple type mismatches in `scanner.py` and `similarity.py`
- Removed unused `_CRUD_PREFIXES`, `_LEXER_MAP`, and `source_bytes` parameters
- Added `_node_text()` helper to safely handle tree-sitter `None` text values
- Removed GPTCloneBench references from benchmarks

### Removed

- `.pypirc` from repo root (use `~/.pypirc` instead)
- `fastmcp` from core dependencies (now in `[mcp]` extra)

---

## [0.1.1] - 2026-03-22

### Added

- MCP server integration for Claude Code
- Multi-language support via tree-sitter grammars
- CLI commands: setup, scan, index, watch, health
- DuckDB-backed local indexing

### Improved

- README structure and installation flow
- Packaging configuration (PyPI-ready)

### Fixed

- CI pipeline issues
- Dependency resolution for optional extras

---

## [0.1.0] - Initial

### Added

- Core semantic redundancy detection engine
- AST fingerprinting + LSH + TF-IDF pipeline
- Cross-language detection
- CLI interface
