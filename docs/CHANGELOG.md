# Changelog

All notable changes to this project will be documented in this file.

## [0.4.0] — VS Code Extension, Daemon, Skills & Signal IPC

### Added

- **VS Code extension** (`vscode-extension/`) — real-time duplicate detection in the editor
  - Real-time diagnostics (squiggly underlines) updating 1.5s after each file save
  - Code actions: mark as intentional, dismiss, jump to duplicate, side-by-side diff, send to AI for refactoring
  - Findings tree view — sidebar showing redundancy clusters grouped by severity, top refactoring targets, hotspot files
  - Review panel — webview with severity badges, clone types, similarity scores, and inline verdicts
  - Cross-language CodeLens — grey annotations showing matches in other languages
  - Status bar — daemon state indicator with finding count
  - Branch-switch reindex — watches `.git/HEAD` for branch changes
  - Periodic reindex every 5 minutes
  - Setup wizard for first-run configuration
- **JSON-RPC daemon** (`echo-guard daemon`) — long-lived Python process for VS Code integration
  - Holds index, ONNX model, and similarity engine in memory
  - JSON-RPC 2.0 protocol over stdin/stdout
  - Methods: `initialize`, `check_files`, `scan`, `reindex`, `resolve_finding`, `get_findings`, `shutdown`
  - Auto-restart with exponential backoff (max 5 restarts)
  - Push notifications for finding resolution updates from MCP
- **New CLI commands:**
  - `echo-guard daemon` — start JSON-RPC daemon for VS Code
  - `echo-guard stats` — index statistics and dependency graph info
  - `echo-guard languages` — list supported languages and file extensions
  - `echo-guard install-hook` — install pre-commit hook configuration
  - `echo-guard prune` — remove stale finding suppressions
  - `echo-guard notify` — touch signal file to trigger daemon rescan from any external process (skills, pre-commit hooks, CI scripts)
  - `echo-guard search <query>` — search the DuckDB function index by name, source text, class name, or call names; supports `--language`, `--output json`, `--limit`
  - `echo-guard install-skills` — copy slash-command skill files to `.claude/skills/` (project) or `~/.claude/skills/` (`--global`); skills auto-upgrade with `pip install --upgrade echo-guard`
- **Signal file IPC** — daemon watches `.echo-guard/rescan.signal` via watchdog (inotify/FSEvents/kqueue); any process touching the file triggers a background rescan and pushes `findings_refreshed` to VS Code within ~1 second. Zero CPU when idle.
- **Four Claude Code slash-command skills** (`echo_guard/skills/`):
  - `/echo-guard` — auto-detects context (files in conversation vs full scan), runs `check` or `scan`, structured severity breakdown, offers refactor prompt for HIGH findings
  - `/echo-guard-refactor` — side-by-side comparison, AI-generated consolidated replacement, applies edits, calls `acknowledge` + `notify` to clear VS Code squiggles
  - `/echo-guard-review` — interactive triage of all unresolved findings, records verdicts via `acknowledge`, single `notify` at the end
  - `/echo-guard-search` — function search against the DuckDB index, offers to open found functions
- **Skills step in `echo-guard setup`** — setup wizard now offers to install skills after MCP and GitHub Action setup
- **MCP tools:**
  - `ping` — health check endpoint
  - `recheck_file` — re-scan a file after modification (syncs VS Code)
- ESLint + TypeScript linting configuration for the extension

### Improved

- **Default embedding model switched to CodeSage-small** (`codesage/codesage-small-v2`, 1024-dim) — stronger semantic understanding than UniXcoder for Type-3/4 clone detection. UniXcoder remains available as a legacy option. Existing indexes auto-rebuild on next `echo-guard index` due to dimension change (768→1024).
- MCP `resolve_finding` routes through daemon when running — VS Code diagnostics clear immediately
- `echo-guard acknowledge` auto-touches `rescan.signal` after writing the verdict — VS Code squiggles clear in ~1 second with no extra step
- VS Code extension debounces `findings_refreshed` notifications (500ms) to absorb rapid signal touches from skills or pre-commit hooks
- Code quality refactoring across Python modules
- CPU usage optimization for ONNX inference

### Benchmark results (CodeSage-small v2)

Results below use the new default model (CodeSage-small v2, 1024-dim). v0.3.0 numbers were measured with UniXcoder and overstated performance. CodeSage-base results included for comparison.

| Metric | v0.3.0 (UniXcoder) | CodeSage-small (default) | CodeSage-base |
|--------|-------------------|--------------------------|---------------|
| BCB Type-1 recall | 100% | **100%** | **100%** |
| BCB Type-2 recall | 100% | **99%** | **99%** |
| BCB Type-3 recall | 58.5% | **4.0%** | 1.2% |
| GCB Type-3 recall | 98.5% | **93.0%** | 92.0% |
| GCB Type-4 recall | 96.0% | 78.5% | **82.0%** |
| POJ-104 Type-4 recall | 78.6% | 1.1% | **4.3%** |
| Speed | ~15ms/func | ~58ms/func | ~189ms/func |

CodeSage-small is the recommended default — better Type-3 recall and 3x faster than base. CodeSage-base trades Type-3 recall for slightly better Type-4 (semantic) detection. The v0.3.0 recall numbers reflected a different evaluation methodology; per-type precision remains 100% for all detected types across all models.

### Removed

- **Tier 3 feature classifier** (`echo_guard/classifier.py`, `echo_guard/data/`) — removed. The classifier was trained on synthetic data using UniXcoder cosine similarity as the primary signal, which made it miscalibrated for CodeSage-small's different similarity distribution. With CodeSage's per-language embedding thresholds and the existing intent filters, the classifier provided no additional benefit. Detection is now Tiers 1+2 with intent filters only.

---

## [0.3.0] — Classifier, AST Distance & DRY Severity

### Architecture

- **Three-tier detection pipeline** — added Tier 3 feature classifier on top of AST hash (Tier 1) and embeddings (Tier 2)
- **AST edit distance** (`echo_guard/ast_distance.py`) — Zhang-Shasha tree edit distance on normalized AST token sequences, providing a continuous structural similarity signal between the binary AST hash and noisy embedding cosine
- **Feature classifier** (`echo_guard/classifier.py`) — logistic regression with 14 features replacing ~10 hand-tuned heuristic filters with one learned decision boundary. Features: AST similarity, embedding score, name/body identifier overlap, call token overlap, literal overlap, control flow similarity, parameter signature similarity, return shape similarity, same-file flag, async match, line count metrics, exact structure flag
- **DRY-based severity model** — severity now reflects actionability, not just clone confidence:
  - **HIGH**: 3+ copies of the same function (extract to shared module now)
  - **MEDIUM**: 2 exact copies (worth noting, defer per Rule of Three)
  - **LOW**: Lower-confidence semantic match (hidden by default)
- **Structural pattern rules** — deterministic rules for patterns the classifier can't learn: verb+noun suppression (list_X/get_X), same-file short-body exact-structure filter, UI wrapper same-file suppression

### Added

- `echo_guard/ast_distance.py` — tree edit distance with Zhang-Shasha algorithm, tiered performance (full tree edit for small functions, token set Jaccard for large)
- `echo_guard/classifier.py` — 14-feature extraction + logistic regression inference in pure NumPy (no sklearn at runtime)
- `echo_guard/data/classifier_weights.json` — trained model weights shipped with the package
- `scripts/train_classifier.py` — training pipeline with tqdm progress, cross-validation, confusion matrix, feature weight analysis. Supports custom JSONL training pairs and GPTCloneBench
- `ast_tokens` field on `ExtractedFunction` — normalized AST token sequence stored alongside hash for edit distance computation
- `--version` / `-V` flag on CLI
- `--include-tests` flag on `scan` and `check` commands (tests excluded by default)
- Rich progress bars with elapsed time and ETA for indexing, embedding, and detection phases
- DRY-tiered report output grouped by action type: **Extract Now** (HIGH), **Worth Noting** (MEDIUM), **Cross-Service**, **Cross-Language**
- Summary block in scan output: top refactoring targets by copy count + hotspot files
- Sequential finding numbering within each report section
- MCP `check_for_duplicates` now returns `priority` (extract_now/worth_noting/cross_service), `copies_in_codebase`, and `summary` counts
- Setup wizard improvements: detects existing config and offers to reuse it, detects existing index/scan and offers to skip, shows directory previews with file counts, all prompts handle Ctrl+C cleanly

### Improved

- **Signal rate**: 93% (up from 84% in v0.2.0) on real-world monorepo with ~1400 functions
- **Noise**: 4 findings (down from ~15 in v0.2.0)
- **Group precision**: findings like fetchJson 13x, schemaTypes 4x, bindingToText 4x are now clean groups without unrelated functions mixed in
- Eliminated persistent false positives: on_error/on_tool_recovery, canAdvance(), typeChip/statusDot, UI primitive pollution in groups
- CPU usage reduced — ONNX inference threads capped at half CPU count to prevent thermal throttling
- Same-file filtering: verb+noun pattern detection using language-aware name tokenizer (`_split_name_tokens`) that handles snake_case, camelCase, PascalCase
- Cross-service findings emitted as individual pairs, never grouped into mega-clusters
- Per-function deduplication prevents same function from appearing in multiple findings
- UI wrapper suppression: same-file JSX components with className/cn() pattern always suppressed regardless of line count
- MCP action guidance rewritten for DRY model — differentiates "import existing" from "extract to shared module" from "cross-service architectural decision"
- Config file renamed from `.echoguard.yml` to `echo-guard.yml` (visible, consistent with `.echo-guard/` data directory)
- GitHub Action workflow renamed from `echo-guard.yml` to `echo-guard-ci.yml` (no ambiguity with config file)
- GitHub Action version pinned dynamically to installed echo-guard version

### Removed

- 8 hand-tuned heuristic filters replaced by the feature classifier:
  - `_is_ui_wrapper_component()`, `_is_ui_wrapper_pair()`, `_is_ui_directory_pair()`
  - `_is_low_value_variant()`
  - `_is_structural_template_pair()`, `_extract_domain_noun()`, `_DOMAIN_VERB_PATTERN`
  - `_is_antonym_pair()`, `_ANTONYM_PAIRS`, `_normalize_to_snake()`
  - `_is_same_resource_different_op()` (logic moved to structural pattern rules)
- Hardcoded same-file 0.98 embedding threshold
- Hardcoded cross-language 0.80 threshold
- MEDIUM group demotion hack (raw_score override)
- Backward compatibility for `.echoguard.yml` / `.echoguard.yaml` config filenames
- Dotfile directories now excluded from scanning by default (`.claude/`, `.codex/`, `.github/`, etc.)
- Test files excluded from scanning by default (`tests/`, `test_*.py`, `*.spec.ts`, etc.)

---

## [0.2.0] — Semantic Detection & Scale

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
- `echo-guard add-mcp` — standalone command to register MCP server (Claude Code + Codex)
- `echo-guard add-action` — standalone command to generate GitHub Action workflow
- **Setup wizard** (`echo-guard setup`) overhauled:
  - Interactive directory selector with arrow keys (via `questionary`)
  - Auto-detects Claude Code and Codex, registers MCP server in one step
  - Optionally generates GitHub Action with fail-on behavior prompt
  - Generates single `echo-guard.yml` with all settings (ignore, acknowledged, config)
  - Excludes respected during language detection (no more scanning venv/benchmarks)
- **Consolidated config** — `ignore` patterns and `acknowledged` findings moved into `echo-guard.yml` (removed `.echoguard-ignore` and `.echoguard-acknowledged` files)
- DuckDB schema additions: `embedding_row`, `embedding_version` columns, `finding_resolutions` table, `training_pairs` table
- `questionary` added as dependency for interactive CLI prompts

### Improved

- **Benchmark results** (two-tier pipeline vs old TF-IDF, later corrected in v0.4.0):
  - BCB Type-3 recall: 2% → **15.3%** (corrected from initially reported 58.5%)
  - GCB Type-4 recall: 82% → **69.5%** (corrected from initially reported 96.0%)
  - POJ-104 Type-4 recall: 10.9% → **17.1%** (corrected from initially reported 78.6%)
  - BCB Type-1/Type-2 recall: 100%
- GitHub Action shows **clone type** in annotations and PR comment table
- GitHub Action reads `acknowledged` list from `echo-guard.yml` to skip reviewed findings
- MCP `check_for_duplicates` response includes `finding_id`, `clone_type`, `action` (concise guidance), and `fix` (import statement)
- MCP `resolve_finding` writes to both DuckDB and `echo-guard.yml` acknowledged list
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
- `.echoguard-ignore` and `.echoguard-acknowledged` separate files (consolidated into `echo-guard.yml`)

### Documentation

- `docs/ARCHITECTURE.md` — full two-tier pipeline documentation
- `docs/TYPE4-ANALYSIS.md` — analysis of why Type-4 detection varies by dataset, with code samples
- `docs/FINE-TUNING.md` — roadmap for contrastive fine-tuning, available datasets, data collection strategy, privacy model
- Updated `BENCHMARKS.md` — generated from new pipeline with before/after comparison
- Updated `ROADMAP.md` — Phase 3 and Phase 6 marked complete, consent model documented
- Updated `README.md` — setup-first flow, full config reference, MCP integration, CLI commands
- Moved `BENCHMARKS.md`, `CHANGELOG.md`, `ROADMAP.md` into `docs/` directory

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
