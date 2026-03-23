# Changelog

All notable changes to this project will be documented in this file.

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
