# Echo Guard

**Semantic linting CLI that detects codebase redundancy created by AI coding agents.**

AI agents (Claude Code, Cursor, Copilot) write what's asked without knowing what already exists elsewhere in the repo. Echo Guard catches these duplicates — functionally identical code scattered across modules — before they become legacy debt.

Works across 9 languages. Detects cross-language redundancy. Zero cloud dependency — everything runs locally.

## Installation

```bash
pip install echo-guard
```

For all 9 language grammars (recommended):

```bash
pip install "echo-guard[languages]"
```

Without `[languages]`, only Python is available.

## Quick Start

```bash
# Interactive setup — auto-detects languages, configures, indexes, and scans
echo-guard setup

# Or do it manually:
echo-guard index          # index your codebase (~6s for a medium repo)
echo-guard scan           # scan for redundancies (HIGH + MEDIUM shown)
echo-guard scan -v        # include LOW-severity findings too
```

## How It Works

Echo Guard uses a 4-stage pipeline:

| Stage                      | What it does                                                                                                                                                                                                | Complexity |
| -------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------- |
| **1. AST fingerprinting**  | Tree-sitter parses every function, normalizes the AST (strips names, comments, literals), and hashes it. Exact structural clones are caught instantly.                                                      | O(n)       |
| **2. Signature filtering** | Extracts metadata (param count, return type, call count) to eliminate 90%+ of candidates before heavy computation.                                                                                          | O(n)       |
| **3. LSH + TF-IDF**        | Locality Sensitive Hashing groups similar code vectors into buckets. TF-IDF with subword tokenization runs cosine similarity only on bucket neighbors. Catches semantic duplicates — even across languages. | O(n\*k)    |
| **4. Intent filtering**    | Domain-noun extraction, antonym detection, UI wrapper recognition, per-service boilerplate exclusion, and cross-language threshold gating. Removes false positives without losing signal.                   | O(n)       |

The index is stored locally in DuckDB (`.echo-guard/index.duckdb`). Nothing leaves your machine.

## Supported Languages

| Language   | Extensions                    |
| ---------- | ----------------------------- |
| Python     | `.py`                         |
| JavaScript | `.js`, `.jsx`, `.mjs`, `.cjs` |
| TypeScript | `.ts`, `.tsx`                 |
| Go         | `.go`                         |
| Rust       | `.rs`                         |
| Java       | `.java`                       |
| Ruby       | `.rb`                         |
| C          | `.c`, `.h`                    |
| C++        | `.cpp`, `.cc`, `.cxx`, `.hpp` |

Cross-language detection works: a Python `validate_email()` will match a Go `ValidateEmail()` or a JS `validateEmail()`.

## CLI Reference

| Command                     | Description                                                              |
| --------------------------- | ------------------------------------------------------------------------ |
| `echo-guard setup`          | Interactive setup wizard — auto-detects repo, configures, indexes, scans |
| `echo-guard scan`           | Scan for redundant code (HIGH + MEDIUM by default)                       |
| `echo-guard scan -v`        | Include LOW-severity findings                                            |
| `echo-guard index`          | Index all functions in the repo                                          |
| `echo-guard check FILES...` | Check specific files against the index (fast path for pre-commit)        |
| `echo-guard watch`          | Watch repo and auto-check on file save                                   |
| `echo-guard health`         | Compute codebase health score (0-100)                                    |
| `echo-guard stats`          | Show index statistics                                                    |
| `echo-guard install-hook`   | Install git pre-commit hook                                              |
| `echo-guard init`           | Create default `.echoguard.yml`                                          |
| `echo-guard languages`      | List supported languages                                                 |
| `echo-guard clear-index`    | Clear the index                                                          |

### Key options

- `-t, --threshold FLOAT` — Similarity threshold 0.0-1.0 (default: 0.50)
- `-o, --output FORMAT` — Output format: `rich` (default), `json`, `compact`
- `-v, --verbose` — Include LOW-severity findings (hidden by default)
- `-d, --diff` — Show side-by-side diff for each match
- `--no-graph` — Disable dependency graph routing

## Severity Levels

| Level      | Similarity | What it means                                                                       | Default behavior  |
| ---------- | ---------- | ----------------------------------------------------------------------------------- | ----------------- |
| **HIGH**   | 95-100%    | Near-exact clones. Copy-pasted code with minimal changes.                           | Shown, fails CI   |
| **MEDIUM** | 80-94%     | Strong semantic match. Same logic, different variable names or minor restructuring. | Shown             |
| **LOW**    | 50-79%     | Structural similarity. May be intentional patterns or real duplication.             | Hidden (use `-v`) |

The default report shows only HIGH + MEDIUM.

## Configuration

Create `.echoguard.yml` in your repo root (or run `echo-guard setup`):

```yaml
threshold: 0.50
min_function_lines: 3
max_function_lines: 500
languages:
  - python
  - javascript
  - typescript
fail_on: high # high, medium, low, none
enable_dep_graph: true

# Monorepo service boundaries (auto-detected if not set)
# service_boundaries:
#   - services/worker
#   - services/dashboard
```

### `.echoguardignore`

Gitignore-style file to exclude paths from scanning:

```gitignore
docs/
tests/
*_generated.py
vendor/
```

### Recommended configs

**Small project** (< 500 functions):

```yaml
threshold: 0.50
fail_on: high
enable_dep_graph: false
```

**Large monorepo** (3K+ functions):

```yaml
threshold: 0.60
min_function_lines: 4
fail_on: high
enable_dep_graph: true
service_boundaries:
  - services/api
  - services/worker
```

**First-time setup** (advisory mode):

```yaml
threshold: 0.50
fail_on: none
```

## CI Integration

Echo Guard exits non-zero when findings exceed the configured `fail_on` severity.

```yaml
# GitHub Actions
- name: Check for redundant code
  run: |
    pip install "echo-guard[languages]"
    echo-guard index
    echo-guard scan
```

```bash
# Pre-commit hook (installed automatically)
echo-guard install-hook
```

## MCP Server (Claude Code Integration)

Echo Guard includes an MCP server so Claude Code can check for existing code before writing new functions:

```bash
# Add to Claude Code
claude mcp add echo-guard -- python -m echo_guard.mcp_server
```

| Tool                    | Description                                                           |
| ----------------------- | --------------------------------------------------------------------- |
| `check_before_write`    | Pass proposed code, get back existing matches with import suggestions |
| `search_functions`      | Search the index by function name, keyword, or language               |
| `suggest_refactor`      | Get full context for consolidating two redundant functions            |
| `get_index_stats`       | Index statistics and dependency graph info                            |
| `get_codebase_clusters` | View how the codebase is organized by domain                          |

## Privacy

Echo Guard runs entirely on your machine. No code, metrics, or telemetry are sent anywhere. The index (`.echo-guard/index.duckdb`) contains function metadata from your repo — add it to your `.gitignore`.

## License

[MIT](LICENSE)
