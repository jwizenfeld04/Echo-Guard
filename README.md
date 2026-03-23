# Echo Guard

![PyPI version](https://img.shields.io/pypi/v/echo-guard)
![Python](https://img.shields.io/pypi/pyversions/echo-guard?v=2)
![License](https://img.shields.io/github/license/jwizenfeld04/Echo-Guard)
![CI](https://github.com/jwizenfeld04/Echo-Guard/actions/workflows/ci.yml/badge.svg)

> Requires **Python 3.10+**

Semantic linting CLI that detects codebase redundancy created by AI coding agents.

AI tools like Claude Code, Cursor, and Copilot generate code without full awareness of your existing codebase. This leads to duplicate logic across files, services, and even languages.

Echo Guard detects and surfaces these redundancies early — before they turn into long-term technical debt.

---

## Why Echo Guard?

Modern AI-assisted development introduces a new class of problems:

- Duplicate business logic across modules
- Slight variations of the same function
- Hidden inconsistencies across services
- Increased maintenance cost over time

Echo Guard solves this by:

- Detecting structural and semantic duplicates
- Working across multiple programming languages
- Running entirely locally (no cloud, no uploads)
- Enabling safe refactoring before duplication spreads

---

## Install

### Recommended (CLI usage)

Install with `pipx` to isolate dependencies and make the CLI globally available:

```bash
pipx install "echo-guard[languages,mcp]"
```

This is the recommended setup for most users.

### Alternative (project usage)

If you want to use Echo Guard inside a Python project:

```bash
pip install "echo-guard[languages]"
```

Without `[languages]`, only Python support is enabled.

---

## MCP Integration (Claude Code)

Echo Guard includes a built-in MCP server so AI agents can check for existing code before generating new functions.

If installed with `pipx`, register the MCP server like this:

```bash
# macOS / Linux
claude mcp add echo-guard -- ~/.local/pipx/venvs/echo-guard/bin/python -m echo_guard.mcp_server

# Windows
claude mcp add echo-guard -- %USERPROFILE%\.local\pipx\venvs\echo-guard\Scripts\python -m echo_guard.mcp_server
```

Then restart Claude Code.

### Available MCP tools

| Tool                    | Description                                         |
| ----------------------- | --------------------------------------------------- |
| `check_before_write`    | Detect existing matches before writing new code     |
| `search_functions`      | Search index by function name, keyword, or language |
| `suggest_refactor`      | Get consolidation suggestions                       |
| `get_index_stats`       | View index statistics                               |
| `get_codebase_clusters` | Understand code grouping                            |

---

## Quick Start

```bash
echo-guard setup
```

Manual workflow:

```bash
echo-guard index
echo-guard scan
echo-guard scan -v
```

---

## Example Output

```
HIGH similarity (98%)
----------------------------------------
Function A: services/auth/utils.py:validate_email
Function B: services/user/validators.py:validateEmail

These functions appear to be nearly identical.

Suggested action:
- Consolidate into a shared module
```

---

## How It Works

Echo Guard uses a multi-stage detection pipeline:

### 1. AST Fingerprinting

Tree-sitter parses functions and normalizes structure.
Captures exact structural duplicates.
**O(n)**

### 2. Signature Filtering

Filters candidates using metadata like parameter count and return types.
**O(n)**

### 3. LSH + TF-IDF

Groups similar code and computes semantic similarity.
Works across languages.
**O(n \* k)**

### 4. Intent Filtering

Removes false positives using domain-aware heuristics.
**O(n)**

All data is stored locally:

```
.echo-guard/index.duckdb
```

No external services are used.

---

## Supported Languages

- Python
- JavaScript
- TypeScript
- Go
- Rust
- Java
- Ruby
- C
- C++

Cross-language matching is supported.

---

## CLI Reference

| Command                   | Description                  |
| ------------------------- | ---------------------------- |
| `echo-guard setup`        | Interactive setup            |
| `echo-guard scan`         | Scan for redundant code      |
| `echo-guard scan -v`      | Include low-severity results |
| `echo-guard index`        | Index codebase               |
| `echo-guard check FILES`  | Check specific files         |
| `echo-guard watch`        | Watch files in real time     |
| `echo-guard health`       | Compute codebase health      |
| `echo-guard stats`        | Show statistics              |
| `echo-guard install-hook` | Install pre-commit hook      |
| `echo-guard init`         | Create config                |
| `echo-guard languages`    | List supported languages     |
| `echo-guard clear-index`  | Clear index                  |

---

## Configuration

`.echoguard.yml`

```yaml
threshold: 0.50
min_function_lines: 3
max_function_lines: 500
languages:
  - python
  - javascript
fail_on: high
enable_dep_graph: true
```

`.echoguardignore`

```
docs/
tests/
vendor/
```

---

## CI Integration

```yaml
- name: Check redundancy
  run: |
    pip install "echo-guard[languages]"
    echo-guard index
    echo-guard scan
```

---

## Privacy

- No telemetry
- No uploads
- Fully local execution

---

## Roadmap

- [ ] VSCode extension
- [ ] GitHub PR annotations
- [ ] Incremental indexing
- [ ] Large-scale monorepo optimization
- [ ] LLM-assisted refactoring

---

## Documentation

- [Changelog](CHANGELOG.md)

---

## License

MIT
