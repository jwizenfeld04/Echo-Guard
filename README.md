<p align="center">
  <img src="assets/logo.jpg" alt="Echo-Guard Logo" width="250px">
</p>

<p align="center">
  <kbd><b><font size="24">Echo-Guard</font></b></kbd><br>
  <br>
  <strong>Semantic linting CLI for AI-generated code redundancy</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/pypi/v/echo-guard" alt="PyPI">
  <img src="https://img.shields.io/pypi/pyversions/echo-guard" alt="Python">
  <img src="https://img.shields.io/github/license/jwizenfeld04/Echo-Guard" alt="License">
  <img src="https://github.com/jwizenfeld04/Echo-Guard/actions/workflows/ci.yml/badge.svg" alt="CI">
</p>

## What is Echo-Guard?

**Echo-Guard** is a semantic linting CLI designed to catch the subtle, functional duplication that AI coding agents often introduce.

Unlike traditional linters that focus on syntax errors or style, Echo-Guard analyzes the **logic and intent** of your code. It identifies "echoes"—blocks of code that perform the same task but might look slightly different—across your entire project, regardless of the file or service they live in.

## Why Echo-Guard?

AI-assisted development (Cursor, Claude Code, Copilot) is incredibly fast, but it has a "memory" problem. Agents often generate fresh code for a task that has already been solved elsewhere in your codebase.

Use Echo-Guard to:

- **Kill Hidden Redundancy:** Catch duplicate business logic that "grep" or simple string matching would miss.
- **Prevent "AI Rot":** Stop your codebase from bloating with slightly different versions of the same utility functions.
- **Keep Your Data Local:** Built for privacy-conscious teams. Echo-Guard runs entirely on your machine—no code is ever uploaded to the cloud for analysis.
- **Scale Across Languages:** Maintain a DRY (Don't Repeat Yourself) architecture even in polyglot repositories.

## Install

### Prerequisites

If you don't have `pipx` installed:

```bash
# macOS
brew install pipx && pipx ensurepath

# Linux / WSL
python3 -m pip install --user pipx && pipx ensurepath

# Windows (PowerShell)
pip install pipx
pipx ensurepath
```

### Recommended (CLI usage)

Install with `pipx` to isolate dependencies and make the CLI globally available:

```bash
pipx install "echo-guard[languages,mcp]"
```

This installs Echo Guard with all language support and the MCP server. The UniXcoder embedding model (~500MB) is downloaded automatically on first scan and cached locally.

### Alternative (project usage)

If you want to use Echo Guard inside a Python project:

```bash
pip install "echo-guard[languages]"
```

Without `[languages]`, only Python support is enabled.

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

| Tool                       | Description                                         |
| -------------------------- | --------------------------------------------------- |
| `check_for_duplicates`       | Check code for duplicates (before/after writing)    |
| `resolve_finding`          | Record verdict: fixed, acknowledged, or false_positive |
| `get_finding_resolutions`  | View resolution history and stats                   |
| `search_functions`         | Search index by function name, keyword, or language |
| `suggest_refactor`         | Get consolidation suggestions                       |
| `get_index_stats`          | View index statistics                               |
| `get_codebase_clusters`    | Understand code grouping                            |

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

## Example Output

```
#1 HIGH — T1/T2 Exact (98%)
  New code:  python services/auth/utils.py:12 → validate_email()
  Existing:  python services/user/validators.py:8 → validate_email()

  Suggested fix: from services.user.validators import validate_email

#2 MEDIUM — T4 Semantic (84%)
  New code:  python utils/auth.py:45 → hash_token()
  Existing:  python crypto/tokens.py:20 → generate_token_hash()

  Action: SAME INTENT, different implementation. Evaluate whether to reuse.
```

## How It Works

Echo Guard uses a two-tier detection pipeline that catches all four clone types:

### Tier 1 — AST Hash Matching (Type-1/Type-2)

Tree-sitter parses functions, normalizes identifiers, and computes structural hashes.
Two functions with the same hash are exact or renamed clones.
**O(n) — 100% recall, zero false positives.**

### Tier 2 — Code Embeddings (Type-3/Type-4)

[UniXcoder](https://github.com/microsoft/CodeBERT/tree/master/UniXcoder) encodes each function into a 768-dim embedding vector.
Cosine similarity search finds modified clones (same structure, different statements) and semantic clones (same intent, completely different implementation).
**~15ms per function, ~2ms search at 100K functions.**

### Intent Filtering

Domain-aware heuristics remove false positives: CRUD patterns, constructor matches, observer/protocol implementations, framework-required exports, and more.

### Clone Type Classification

| Finding | Clone Type | Severity | Meaning |
|---------|-----------|----------|---------|
| AST hash match | T1/T2 Exact | **HIGH** | Exact or renamed duplicate — import instead |
| Embedding ≥ threshold | T3 Modified | **HIGH** | Very similar structure — refactor into shared function |
| Embedding ≥ threshold | T4 Semantic | **MEDIUM** | Same intent, different code — evaluate reuse |

All data is stored locally:

```
.echo-guard/
├── index.duckdb      # Function metadata
├── embeddings.npy    # Code embedding vectors
└── model_cache/      # Cached UniXcoder model
```

No external services are used.

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

## CLI Reference

| Command                   | Description                  |
| ------------------------- | ---------------------------- |
| `echo-guard setup`        | Interactive setup            |
| `echo-guard scan`         | Scan for redundant code      |
| `echo-guard scan -v`      | Show detailed match table    |
| `echo-guard index`        | Index codebase               |
| `echo-guard check FILES`  | Check specific files         |
| `echo-guard watch`        | Watch files in real time     |
| `echo-guard health`       | Compute codebase health      |
| `echo-guard stats`        | Show statistics              |
| `echo-guard acknowledge`  | Acknowledge a finding for CI |
| `echo-guard install-hook` | Install pre-commit hook      |
| `echo-guard init`         | Create config                |
| `echo-guard languages`    | List supported languages     |
| `echo-guard clear-index`  | Clear index                  |

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
          threshold: "0.50" # similarity threshold (0.0-1.0)
          fail-on: "high" # high, medium, or none
          comment: "true" # post PR summary comment
```

This posts inline annotations on duplicate functions at the configured severity level, adds a PR summary comment with a findings table, and fails the check when matches at or above `fail-on` severity are found. Lower-severity findings are collapsed in the comment but not annotated.

### Acknowledging Findings

When Echo Guard flags intentional duplication that blocks your PR:

```bash
# Get finding IDs
echo-guard scan --output json | jq '.findings[].finding_id'

# Acknowledge a finding (adds to .echoguardignore-findings)
echo-guard acknowledge "path/a.py:func_a||path/b.py:func_b" --note "Intentional — framework requirement"

# Commit the ignore file so CI skips this finding
git add .echoguardignore-findings
```

The `.echoguardignore-findings` file lists finding IDs that have been reviewed. The CI automatically skips these. Remove a line to re-enable checking.

AI agents using the MCP server can also call `resolve_finding` with verdict `acknowledged` — this writes to both the local index and the ignore file.

### Manual CI

```yaml
- name: Check redundancy
  run: |
    pip install "echo-guard[languages]"
    echo-guard index
    echo-guard scan
```

## Privacy

- No telemetry
- No uploads
- Fully local execution

## Benchmark Results

Echo Guard is evaluated against established academic clone detection benchmarks using the real `echo-guard scan` pipeline. Full analysis with per-type breakdowns: **[BENCHMARKS.md](BENCHMARKS.md)**

| Dataset                     | Precision | Recall | F1    | Pairs |
| --------------------------- | --------- | ------ | ----- | ----- |
| BigCloneBench (Java)        | 100.0%    | 40.8%  | 58.0% | 1,200 |
| GPTCloneBench (Java/Python) | 64.3%     | 88.8%  | 74.6% | 600   |
| POJ-104 (C)                 | 86.1%     | 10.9%  | 19.4% | 382   |

**How this compares:**

- **Zero false positives on BigCloneBench** — when Echo Guard flags something on human-written code, it's real
- Perfect on Type-1/2, consistent with all tools (NiCad, SourcererCC, etc.)
- Type-3/4 detection powered by UniXcoder embeddings — ML models score 82-95% on semantic clones
- Results above are from the legacy TF-IDF pipeline; re-benchmarking with the new embedding architecture is in progress

```bash
python -m benchmarks.runner                            # run all benchmarks
python -m benchmarks.runner --dataset bigclonebench -v # per-pair details
python -m benchmarks.runner --sweep                    # threshold sweep
```

## Roadmap

- [x] **Benchmarking** — Validate against BigCloneBench, GPTCloneBench, POJ-104
- [x] **GitHub Action** — PR annotations, summary comments, severity-based gating
- [x] **Semantic detection** — UniXcoder embeddings for Type-3/Type-4 clone detection
- [ ] **VS Code extension** — Real-time inline diagnostics via MCP
- [ ] **LLM-assisted refactoring** — Automated consolidation patches
- [ ] **Monorepo scale** — Sharded indexing and parallel scanning

See [ROADMAP.md](ROADMAP.md) for the full plan with details and rationale.

## Documentation

- [Architecture](docs/ARCHITECTURE.md) — Two-tier detection pipeline, clone types, storage, scaling
- [Benchmarks](BENCHMARKS.md) — Results on BigCloneBench, GPTCloneBench, POJ-104
- [Roadmap](ROADMAP.md) — Development phases and planned features
- [Changelog](CHANGELOG.md)

## License

MIT
