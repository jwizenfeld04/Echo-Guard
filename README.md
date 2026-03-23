# Echo Guard

![PyPI version](https://img.shields.io/pypi/v/echo-guard?v=2)
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
claude mcp add echo-guard -- "$(pipx environment --value PIPX_LOCAL_VENVS)/echo-guard/bin/python" -m echo_guard.mcp_server

# Windows (PowerShell)
claude mcp add echo-guard -- "$(pipx environment --value PIPX_LOCAL_VENVS)\echo-guard\Scripts\python" -m echo_guard.mcp_server
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

### GitHub Action (recommended)

Add this to `.github/workflows/echo-guard.yml` in your repo:

```yaml
name: Echo Guard
on: [pull_request]
permissions:
  contents: read
  pull-requests: write
jobs:
  echo-guard:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - uses: jwizenfeld04/Echo-Guard@main
        with:
          threshold: "0.50"    # similarity threshold (0.0-1.0)
          fail-on: "high"      # high, medium, low, or none
          comment: "true"      # post PR summary comment
```

This posts inline annotations on duplicate functions at the configured severity level, adds a PR summary comment with a findings table, and fails the check when matches at or above `fail-on` severity are found. Lower-severity findings are collapsed in the comment but not annotated.

### Manual CI

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

## Benchmark Results

Echo Guard is evaluated against established academic clone detection benchmarks using the real `echo-guard scan` pipeline. Full analysis with per-type breakdowns: **[BENCHMARKS.md](BENCHMARKS.md)**

| Dataset | Precision | Recall | F1 | Pairs |
|---------|-----------|--------|----|-------|
| BigCloneBench (Java) | 100.0% | 40.8% | 58.0% | 1,200 |
| GPTCloneBench (Java/Python) | 64.3% | 88.8% | 74.6% | 600 |
| POJ-104 (C) | 86.1% | 10.9% | 19.4% | 382 |

**How this compares:**
- **Zero false positives on BigCloneBench** — when Echo Guard flags something on human-written code, it's real
- Perfect on Type-1/2, consistent with all tools (NiCad, SourcererCC, etc.)
- Type-3 recall (2%) is low compared to traditional tools (63-94%) — they use line-level normalization that handles statement changes better than TF-IDF
- Type-4 is 0% on human-written clones, consistent with all traditional tools (0-2%)
- Code embeddings (Phase 2) will target the Type-3/4 gap — ML models score 82-95% on semantic clones

```bash
python -m benchmarks.runner                            # run all benchmarks
python -m benchmarks.runner --dataset bigclonebench -v # per-pair details
python -m benchmarks.runner --sweep                    # threshold sweep
```

---

## Roadmap

- [x] **Benchmarking** — Validate against BigCloneBench, GPTCloneBench, POJ-104
- [x] **GitHub Action** — PR annotations, summary comments, severity-based gating
- [ ] **Semantic detection** — Optional code embeddings for Type-4 clone detection
- [ ] **VS Code extension** — Real-time inline diagnostics via MCP
- [ ] **LLM-assisted refactoring** — Automated consolidation patches
- [ ] **Monorepo scale** — Sharded indexing and parallel scanning

See [ROADMAP.md](ROADMAP.md) for the full plan with details and rationale.

---

## Documentation

- [Changelog](CHANGELOG.md)
- [Benchmarks](BENCHMARKS.md)
- [Roadmap](ROADMAP.md)
- [Contributing](CONTRIBUTING.md)

---

## License

MIT
