# Echo Guard — VS Code Extension

Real-time AI-generated code redundancy detection, right in your editor.

## What it does

Echo Guard detects when you've written code that already exists elsewhere in your codebase — even when it's been slightly modified, renamed, or restructured. It flags these "echoes" as diagnostics (squiggles) directly in the editor and suggests whether to import the existing version or keep both copies intentionally.

## Features

- **Real-time linting on save** — checks for duplicates 1.5 seconds after you save, with no perceptible slowdown
- **Two-tier detection** — AST hash matching (exact/renamed clones) → CodeSage-small embeddings (semantic duplicates). Catches exact copies, structural clones, and semantic duplicates
- **DRY-based severity** — EXTRACT (3+ copies, red squiggles), REVIEW (2 copies, yellow squiggles)
- **Code actions (Ctrl+.)** — mark as intentional, dismiss false positives, jump to duplicate, or show a side-by-side diff
- **Review panel** — "Echo Guard: Review All Findings" opens a panel listing all findings with inline actions
- **Status bar** — shows current finding count at a glance; click to review

## Requirements

Echo Guard requires the `echo-guard` Python package:

```bash
pip install "echo-guard[languages]"
```

Then run the setup wizard in your project:

```bash
echo-guard setup
```

The extension will prompt you to do both on first activation.

## Quick Start

1. Install `echo-guard` via pip (see above)
2. Open a workspace containing an `echo-guard.yml` config file
3. The extension activates automatically, builds the index on first run, and starts scanning
4. Findings appear as yellow (REVIEW) or red (EXTRACT) squiggles
5. Click the squiggle → Ctrl+. to see actions

## Commands

| Command | Description |
|---------|-------------|
| `Echo Guard: Activate` | Start the daemon (if it stopped) |
| `Echo Guard: Scan Workspace` | Run a full scan and refresh all diagnostics |
| `Echo Guard: Reindex` | Rebuild the function index incrementally |
| `Echo Guard: Review All Findings` | Open the findings review panel |
| `Echo Guard: Show Health Score` | Run `echo-guard health` in the terminal |

## Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `echoGuard.pythonPath` | `""` | Path to Python with echo-guard installed. Leave empty to use system Python. |
| `echoGuard.threshold` | `0.5` | Similarity threshold (0.0–1.0) |
| `echoGuard.debounceMs` | `1500` | Milliseconds to wait after save before checking |
| `echoGuard.minSeverity` | `extract` | Minimum severity to surface in the Problems panel (`extract` or `review`) |
| `echoGuard.feedbackConsent` | `"private"` | Data sharing consent for model improvement |

## How it works

The extension runs a long-lived Python daemon (`echo-guard daemon`) that holds the function index, ONNX embedding model, and similarity engine in memory. This avoids the ~2-3s cold start on every file save.

On each save, the extension sends a `check_files` RPC to the daemon. The daemon re-parses the file, re-embeds changed functions, and returns findings in ~200-500ms. Diagnostics update without any blocking.

## MCP Integration

Echo Guard also ships an MCP server for AI agent integration. When the VS Code extension is running, the MCP server automatically routes `resolve_finding` calls through the daemon — so when an AI agent marks a finding as resolved, VS Code diagnostics clear immediately.

## License

MIT
