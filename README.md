# Echo Guard

Semantic linting CLI that detects codebase redundancy created by AI coding agents.

AI agents (Claude Code, Cursor, Copilot) write what's asked without knowing what already exists elsewhere in the repo. Echo Guard catches these duplicates — functionally identical code scattered across modules — before they become legacy debt.

- Works across 9 languages
- Detects cross-language redundancy
- Zero cloud dependency — everything runs locally

---

## Install

### Basic install (Python only)

```bash
pip install echo-guard
```

### Full install (recommended — all languages)

```bash
pip install "echo-guard[languages]"
```

Without `[languages]`, only Python is supported.

---

## MCP Integration (Claude Code)

Echo Guard includes a built-in MCP server so Claude Code can check for existing code before generating new functions.

```bash
claude mcp add echo-guard -- python -m echo_guard.mcp_server
```

### Available MCP tools

| Tool                    | Description                                         |
| ----------------------- | --------------------------------------------------- |
| `check_before_write`    | Detect existing matches before writing new code     |
| `search_functions`      | Search index by function name, keyword, or language |
| `suggest_refactor`      | Get consolidation suggestions for duplicate logic   |
| `get_index_stats`       | View index statistics and structure                 |
| `get_codebase_clusters` | Understand how code is grouped by domain            |

---

## Quick Start

```bash
# Interactive setup — auto-detects languages, configures, indexes, and scans
echo-guard setup
```

```bash
# Manual workflow
echo-guard index          # index your codebase (~6s for a medium repo)
echo-guard scan           # scan for redundancies (HIGH + MEDIUM shown)
echo-guard scan -v        # include LOW-severity findings
```

---

## How It Works

Echo Guard uses a 4-stage pipeline:

### 1. AST fingerprinting

Tree-sitter parses every function, normalizes the AST (strips names, comments, literals), and hashes it.  
Exact structural clones are caught instantly.  
**Complexity:** O(n)

### 2. Signature filtering

Extracts metadata (param count, return type, call count) to eliminate 90%+ of candidates before heavy computation.  
**Complexity:** O(n)

### 3. LSH + TF-IDF

Locality Sensitive Hashing groups similar code vectors into buckets.  
TF-IDF with subword tokenization runs cosine similarity only on bucket neighbors.  
Catches semantic duplicates — even across languages.  
**Complexity:** O(n \* k)

### 4. Intent filtering

Domain-noun extraction, antonym detection, UI wrapper recognition, service boundary awareness, and cross-language threshold gating.  
Removes false positives without losing signal.  
**Complexity:** O(n)

The index is stored locally in:

```text
.echo-guard/index.duckdb
```

Nothing leaves your machine.

---

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

Cross-language detection works:

- `validate_email()` in Python
- `ValidateEmail()` in Go
- `validateEmail()` in JavaScript

These will match as equivalent logic.

---

## CLI Reference

| Command                     | Description                                                  |
| --------------------------- | ------------------------------------------------------------ |
| `echo-guard setup`          | Interactive setup (detects repo, configures, indexes, scans) |
| `echo-guard scan`           | Scan for redundant code (HIGH + MEDIUM)                      |
| `echo-guard scan -v`        | Include LOW-severity findings                                |
| `echo-guard index`          | Index all functions                                          |
| `echo-guard check FILES...` | Check specific files (fast path)                             |
| `echo-guard watch`          | Watch repo and check on file save                            |
| `echo-guard health`         | Compute codebase health score                                |
| `echo-guard stats`          | Show index statistics                                        |
| `echo-guard install-hook`   | Install pre-commit hook                                      |
| `echo-guard init`           | Create default config                                        |
| `echo-guard languages`      | List supported languages                                     |
| `echo-guard clear-index`    | Clear the index                                              |

### Key Options

- `-t, --threshold FLOAT` — similarity threshold (default: 0.50)
- `-o, --output FORMAT` — `rich`, `json`, `compact`
- `-v, --verbose` — include LOW severity
- `-d, --diff` — show side-by-side diff
- `--no-graph` — disable dependency graph routing

---

## Severity Levels

| Level  | Similarity | Meaning               | Default         |
| ------ | ---------- | --------------------- | --------------- |
| HIGH   | 95–100%    | Near-exact clones     | Shown, fails CI |
| MEDIUM | 80–94%     | Strong semantic match | Shown           |
| LOW    | 50–79%     | Structural similarity | Hidden          |

---

## Configuration

Create `.echoguard.yml`:

```yaml
threshold: 0.50
min_function_lines: 3
max_function_lines: 500
languages:
  - python
  - javascript
  - typescript
fail_on: high
enable_dep_graph: true
```

### Ignore file

`.echoguardignore`:

```text
docs/
tests/
*_generated.py
vendor/
```

---

## Recommended Configs

### Small project (< 500 functions)

```yaml
threshold: 0.50
fail_on: high
enable_dep_graph: false
```

### Large monorepo (3000+ functions)

```yaml
threshold: 0.60
min_function_lines: 4
fail_on: high
enable_dep_graph: true
service_boundaries:
  - services/api
  - services/worker
```

### First-time adoption (advisory mode)

```yaml
threshold: 0.50
fail_on: none
```

---

## CI Integration

Echo Guard exits non-zero when findings exceed `fail_on`.

```yaml
- name: Check for redundant code
  run: |
    pip install "echo-guard[languages]"
    echo-guard index
    echo-guard scan
```

### Pre-commit hook

```bash
echo-guard install-hook
```

---

## Privacy

Echo Guard runs entirely locally.

- No telemetry
- No uploads
- No external APIs

The index (`.echo-guard/index.duckdb`) contains function metadata. Add it to `.gitignore`.

---

## License

MIT
