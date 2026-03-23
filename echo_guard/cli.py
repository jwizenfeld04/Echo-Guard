"""Echo Guard CLI — semantic linting for codebase redundancy."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Optional

import typer

from echo_guard.config import EchoGuardConfig
from echo_guard.output import console, format_json, print_results

# Heavy imports (scanner → similarity → embeddings) are deferred to
# command functions so that `echo-guard --help` and `echo-guard setup`
# show output immediately.

app = typer.Typer(
    name="echo-guard",
    help="Semantic linting CLI that detects codebase redundancy created by AI coding agents.",
    add_completion=False,
)


# ── CLI Banner ────────────────────────────────────────────────────────────

# Logo-inspired color palette using ANSI codes
_CYAN = "\033[38;5;51m"
_SLATE = "\033[38;5;244m"
_ORANGE = "\033[38;5;214m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


def _show_banner() -> None:
    """Display the Echo Guard ASCII banner."""
    import sys
    import os
    if sys.platform == "win32":
        os.system("color")

    banner = rf"""
{_CYAN}{_BOLD}    ___      _             ___                    _
   | __| ___| |_  ___ ___ / __|_  _ __ _ _ _ __| |
   | _| /  _| ' \/ _ \___| (_ | || / _` | '_/ _` |
   |___|\___|_||_\___/    \___|\_,_\__,_|_| \__,_|{_RESET}
"""
    print(banner)


def _find_repo_root() -> Path:
    """Find the git repository root, or fall back to cwd."""
    from echo_guard.utils import find_repo_root
    return find_repo_root()


@app.command()
def index(
    path: Optional[str] = typer.Argument(
        None, help="Path to repository root (default: auto-detect)"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed output"),
    full: bool = typer.Option(
        False, "--full", help="Force full reindex (skip incremental)"
    ),
) -> None:
    """Index all functions in the repository (all supported languages).

    By default uses incremental indexing — only re-parses files that changed.
    Use --full to force a complete reindex.
    """
    from echo_guard.scanner import index_repo

    repo_root = Path(path) if path else _find_repo_root()
    config = EchoGuardConfig.load(repo_root)

    mode = "full" if full else "incremental"
    console.print(f"[bold]Indexing[/bold] {repo_root} ({mode}) ...")
    console.print(f"  Languages: {', '.join(config.languages)}")

    idx, file_count, func_count, lang_counts = index_repo(
        repo_root,
        config=config,
        verbose=verbose,
        incremental=not full,
    )
    idx.close()

    console.print(
        f"[green bold]✓[/green bold] Indexed [cyan]{func_count}[/cyan] functions across [cyan]{file_count}[/cyan] files"
    )
    for lang, count in sorted(lang_counts.items()):
        console.print(f"  {lang}: {count} functions")
    console.print(f"  Index: {repo_root / '.echo-guard' / 'index.duckdb'}")


@app.command()
def scan(
    path: Optional[str] = typer.Argument(None, help="Path to repository root"),
    threshold: float = typer.Option(
        0.50, "--threshold", "-t", help="Similarity threshold (0.0-1.0)"
    ),
    output: str = typer.Option(
        "rich", "--output", "-o", help="Output format: rich, json, compact"
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show detailed match table with per-match breakdown",
    ),
    diff: bool = typer.Option(
        False, "--diff", "-d", help="Show side-by-side diff for matches"
    ),
    no_graph: bool = typer.Option(
        False, "--no-graph", help="Disable dependency graph routing"
    ),
) -> None:
    """Scan the repository for redundant code."""
    from echo_guard.scanner import index_repo, scan_for_redundancy

    repo_root = Path(path) if path else _find_repo_root()
    config = EchoGuardConfig.load(repo_root)

    if no_graph:
        config.enable_dep_graph = False

    # Auto-index if needed
    index_path = repo_root / ".echo-guard" / "index.duckdb"
    if not index_path.exists():
        console.print("[yellow]No index found. Running auto-index...[/yellow]")
        idx, file_count, func_count, _ = index_repo(
            repo_root, config=config, verbose=verbose
        )
        idx.close()
        console.print(
            f"[green]✓[/green] Indexed {func_count} functions across {file_count} files\n"
        )

    matches = scan_for_redundancy(
        repo_root, threshold=threshold, config=config, verbose=verbose
    )

    if output == "json":
        print(format_json(matches))
    else:
        print_results(
            matches, verbose=verbose, show_diff=diff, compact=(output == "compact")
        )

    # Exit with non-zero based on config
    for m in matches:
        if config.should_fail(m.severity):
            raise typer.Exit(code=1)


@app.command()
def check(
    files: list[str] = typer.Argument(..., help="Files to check against the index"),
    threshold: float = typer.Option(
        0.50, "--threshold", "-t", help="Similarity threshold"
    ),
    output: str = typer.Option(
        "rich", "--output", "-o", help="Output format: rich, json, compact"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed output"),
    diff: bool = typer.Option(False, "--diff", "-d", help="Show diff for matches"),
) -> None:
    """Check specific files against the existing index (fast path for pre-commit)."""
    from echo_guard.scanner import check_files

    repo_root = _find_repo_root()
    config = EchoGuardConfig.load(repo_root)

    index_path = repo_root / ".echo-guard" / "index.duckdb"
    if not index_path.exists():
        console.print("[red]No index found. Run `echo-guard index` first.[/red]")
        raise typer.Exit(code=2)

    matches = check_files(
        repo_root, files, threshold=threshold, config=config, verbose=verbose
    )

    if output == "json":
        print(format_json(matches))
    else:
        print_results(
            matches, verbose=verbose, show_diff=diff, compact=(output == "compact")
        )

    for m in matches:
        if config.should_fail(m.severity):
            raise typer.Exit(code=1)


@app.command()
def watch(
    path: Optional[str] = typer.Argument(None, help="Path to repository root"),
    threshold: float = typer.Option(
        0.50, "--threshold", "-t", help="Similarity threshold"
    ),
) -> None:
    """Watch the repository and check files on save."""
    from echo_guard.scanner import check_files, index_repo

    repo_root = Path(path) if path else _find_repo_root()
    config = EchoGuardConfig.load(repo_root)

    # Ensure index exists
    index_path = repo_root / ".echo-guard" / "index.duckdb"
    if not index_path.exists():
        console.print("[yellow]No index found. Indexing first...[/yellow]")
        idx, _, func_count, _ = index_repo(repo_root, config=config)
        idx.close()
        console.print(f"[green]✓[/green] Indexed {func_count} functions\n")

    from echo_guard.watcher import watch_repo

    def on_change(filepath: str) -> None:
        console.print(f"\n[dim]File changed: {filepath}[/dim]")
        try:
            matches = check_files(
                repo_root, [filepath], threshold=threshold, config=config
            )
            if matches:
                print_results(matches, compact=True)
            else:
                console.print("[green]✓ No redundancy.[/green]")
        except Exception as e:
            console.print(f"[red]Error checking {filepath}: {e}[/red]")

    console.print(f"[bold]Watching[/bold] {repo_root} for file changes...")
    console.print("[dim]Press Ctrl+C to stop.[/dim]")

    observer = watch_repo(repo_root, on_change, config)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
    console.print("\n[dim]Stopped watching.[/dim]")


@app.command()
def health(
    path: Optional[str] = typer.Argument(None, help="Path to repository root"),
    threshold: float = typer.Option(
        0.50, "--threshold", "-t", help="Similarity threshold"
    ),
    output: str = typer.Option(
        "rich", "--output", "-o", help="Output format: rich, json"
    ),
    history: bool = typer.Option(False, "--history", help="Show health score trend"),
) -> None:
    """Compute and display the codebase health score (0-100)."""
    from echo_guard.health import compute_health_score, get_trend, record_health
    from echo_guard.scanner import index_repo, scan_for_redundancy

    repo_root = Path(path) if path else _find_repo_root()
    config = EchoGuardConfig.load(repo_root)

    # Auto-index if needed
    index_path = repo_root / ".echo-guard" / "index.duckdb"
    if not index_path.exists():
        console.print("[yellow]No index found. Indexing first...[/yellow]")
        idx, _, _, _ = index_repo(repo_root, config=config)
        idx.close()

    if history:
        from echo_guard.index import FunctionIndex

        idx = FunctionIndex(repo_root)
        trend = get_trend(idx)
        idx.close()
        if not trend:
            console.print(
                "[dim]No health score history yet. Run `echo-guard health` to record the first score.[/dim]"
            )
            return
        if output == "json":
            import json

            print(json.dumps(trend, indent=2))
        else:
            console.print("[bold]Health Score Trend[/bold]")
            for entry in reversed(trend):
                score = entry["score"]
                color = "green" if score >= 75 else "yellow" if score >= 50 else "red"
                console.print(
                    f"  {entry['recorded_at'][:19]}  [{color}]{score:>3d}[/{color}]  "
                    f"({entry['total_redundancies']} redundancies / {entry['total_functions']} functions)"
                )
        return

    matches = scan_for_redundancy(repo_root, threshold=threshold, config=config)

    from echo_guard.index import FunctionIndex

    idx = FunctionIndex(repo_root)
    total_funcs = idx.get_stats()["total_functions"]
    score_data = compute_health_score(matches, total_funcs)
    record_health(idx, score_data)
    idx.close()

    if output == "json":
        import json

        print(json.dumps(score_data, indent=2))
        return

    score = score_data["score"]
    grade = score_data["grade"]
    color = "green" if score >= 75 else "yellow" if score >= 50 else "red"

    console.print()
    console.print(
        f"[bold]Codebase Health Score:[/bold]  [{color} bold]{score}/100 ({grade})[/{color} bold]"
    )
    console.print()

    bd = score_data["breakdown"]
    console.print(f"  Functions:     {bd['total_functions']}")
    console.print(
        f"  Redundancies:  {bd['total_redundancies']}  "
        f"([red]{bd['high']} high[/red], [yellow]{bd['medium']} medium[/yellow])"
    )
    console.print(f"  Redundancy rate: {bd['redundancy_rate_pct']}%")

    if bd.get("cross_language_matches", 0) > 0:
        console.print(
            f"  Cross-language: {bd['cross_language_matches']} (cannot import directly)"
        )
    if bd.get("private_scope_matches", 0) > 0:
        console.print(
            f"  Private scope:  {bd['private_scope_matches']} (existing func is private)"
        )

    if score_data["recommendations"]:
        console.print()
        console.print("[bold]Recommendations:[/bold]")
        for rec in score_data["recommendations"]:
            console.print(f"  • {rec}")
    console.print()


@app.command()
def stats() -> None:
    """Show index statistics and dependency graph info."""
    repo_root = _find_repo_root()

    from echo_guard.index import FunctionIndex

    try:
        idx = FunctionIndex(repo_root)
        s = idx.get_stats()
    except Exception:
        console.print("[red]No index found. Run `echo-guard index` first.[/red]")
        raise typer.Exit(code=2)

    console.print("[bold]Echo Guard Index Stats[/bold]")
    console.print(f"  Functions indexed: [cyan]{s['total_functions']}[/cyan]")
    console.print(f"  Files indexed:     [cyan]{s['total_files']}[/cyan]")

    if s.get("by_language"):
        console.print("  By language:")
        for lang, count in sorted(s["by_language"].items()):
            console.print(f"    {lang}: {count}")

    if s.get("by_visibility"):
        console.print("  By visibility:")
        for vis, count in sorted(s["by_visibility"].items()):
            console.print(f"    {vis}: {count}")

    # Dependency graph stats
    from echo_guard.scanner import _build_dep_graph

    try:
        graph = _build_dep_graph(idx)
        gs = graph.get_stats()
        console.print("  Module clusters:")
        for cluster, count in sorted(gs["clusters"].items()):
            console.print(f"    {cluster}: {count} modules")
        console.print(f"  Dependency edges: {gs['total_edges']}")
    except Exception:
        pass

    idx.close()
    console.print(f"  Index location:    {repo_root / '.echo-guard' / 'index.duckdb'}")


@app.command()
def languages() -> None:
    """List supported languages."""
    from echo_guard.languages import LANGUAGES

    console.print("[bold]Supported languages:[/bold]")
    for name, spec in sorted(LANGUAGES.items()):
        exts = ", ".join(spec.extensions)
        console.print(f"  [cyan]{name:12s}[/cyan]  {exts}")


@app.command(name="install-hook")
def install_hook() -> None:
    """Install Echo Guard as a git pre-commit hook."""
    repo_root = _find_repo_root()
    hooks_dir = repo_root / ".git" / "hooks"

    if not hooks_dir.exists():
        console.print("[red]Not a git repository.[/red]")
        raise typer.Exit(code=2)

    hook_path = hooks_dir / "pre-commit"

    # Updated hook to handle all supported file types
    hook_script = """#!/bin/sh
# Echo Guard pre-commit hook
# Checks staged source files for redundant code

STAGED_FILES=$(git diff --cached --name-only --diff-filter=ACM -- \
    '*.py' '*.js' '*.jsx' '*.ts' '*.tsx' '*.go' '*.rs' \
    '*.java' '*.rb' '*.c' '*.cpp' '*.cc' '*.h' '*.hpp')

if [ -z "$STAGED_FILES" ]; then
    exit 0
fi

echo "Echo Guard: checking staged files for redundancy..."
echo "$STAGED_FILES" | xargs echo-guard check

EXIT_CODE=$?
if [ $EXIT_CODE -ne 0 ]; then
    echo ""
    echo "Echo Guard detected potential redundancies."
    echo "Review the matches above. To skip: git commit --no-verify"
    exit 1
fi
"""

    if hook_path.exists():
        existing = hook_path.read_text()
        if "echo-guard" in existing.lower():
            console.print("[yellow]Echo Guard hook already installed.[/yellow]")
            return
        with open(hook_path, "a") as f:
            f.write("\n" + hook_script)
        console.print(
            "[green]✓[/green] Appended Echo Guard to existing pre-commit hook"
        )
    else:
        hook_path.write_text(hook_script)
        hook_path.chmod(0o755)
        console.print(
            "[green]✓[/green] Installed pre-commit hook at .git/hooks/pre-commit"
        )

    console.print(
        "  Staged source files will be checked for redundancy before each commit."
    )


@app.command(name="init")
def init_config() -> None:
    """Create a default .echoguard.yml config file."""
    repo_root = _find_repo_root()
    config_path = repo_root / ".echoguard.yml"

    if config_path.exists():
        console.print(f"[yellow]Config already exists at {config_path}[/yellow]")
        return

    config_content = """\
# Echo Guard configuration
# See: https://github.com/echo-guard/echo-guard

# Similarity threshold (0.0 to 1.0)
threshold: 0.50

# Minimum function size to consider (in lines)
min_function_lines: 3

# Maximum function size to consider
max_function_lines: 500

# Languages to analyze (comment out to disable)
languages:
  - python
  - javascript
  - typescript
  - go
  - rust
  - java
  - ruby
  - c
  - cpp

# Additional directories to exclude (on top of defaults)
# exclude_dirs:
#   - generated
#   - vendor

# Additional file patterns to exclude
# exclude_patterns:
#   - "*.generated.ts"

# Output format: rich, json, compact
output_format: rich

# Minimum severity to fail CI: high, medium, none
fail_on: high

# Enable dependency graph for smarter comparison routing
enable_dep_graph: true

# Service boundaries — directories that represent separate deployable services.
# Functions across service boundaries get shared-library suggestions instead of
# impossible direct-import suggestions.
# Auto-detected from services/, apps/, packages/, microservices/ patterns if not set.
# service_boundaries:
#   - services/worker
#   - services/dashboard
#   - services/tool-gateway
"""
    config_path.write_text(config_content)
    console.print(f"[green]✓[/green] Created {config_path}")


@app.command(name="add-mcp")
def add_mcp() -> None:
    """Register the Echo Guard MCP server with Claude Code.

    Detects your Python environment and runs `claude mcp add` automatically.
    """
    _setup_mcp_integration(console)


@app.command(name="add-action")
def add_action(
    path: Optional[str] = typer.Argument(None, help="Path to repository root"),
) -> None:
    """Generate a GitHub Action workflow for PR duplicate checking.

    Creates .github/workflows/echo-guard.yml in your repo.
    """
    repo_root = Path(path) if path else _find_repo_root()
    _setup_github_action(repo_root, console)


# ── Interactive Setup Wizard ─────────────────────────────────────────────


def _detect_languages_in_repo(repo_root: Path, exclude_dirs: set[str] | None = None) -> dict[str, int]:
    """Scan the repo for source files and return languages with file counts."""
    from echo_guard.languages import detect_language, supported_extensions
    from echo_guard.config import DEFAULT_EXCLUDE_DIRS

    skip = DEFAULT_EXCLUDE_DIRS | (exclude_dirs or set())
    exts = supported_extensions()
    found: dict[str, int] = {}

    for f in repo_root.rglob("*"):
        if not f.is_file():
            continue
        parts = f.relative_to(repo_root).parts
        if any(p in skip or p.startswith(".") for p in parts):
            continue
        if f.suffix.lower() not in exts:
            continue
        lang = detect_language(str(f))
        if lang:
            found[lang] = found.get(lang, 0) + 1
    return found


def _detect_service_dirs(repo_root: Path) -> list[str]:
    """Look for common monorepo service directory patterns."""
    service_roots = ["services", "apps", "packages", "microservices"]
    boundaries: list[str] = []

    for root_name in service_roots:
        root_dir = repo_root / root_name
        if root_dir.is_dir():
            for child in sorted(root_dir.iterdir()):
                if child.is_dir() and not child.name.startswith("."):
                    boundaries.append(f"{root_name}/{child.name}")

    return boundaries


def _detect_directories(repo_root: Path) -> list[str]:
    """List all top-level directories in the repo, excluding hidden and known infra dirs."""
    from echo_guard.config import DEFAULT_EXCLUDE_DIRS

    dirs = []
    for child in sorted(repo_root.iterdir()):
        if not child.is_dir():
            continue
        name = child.name
        # Skip hidden dirs and dirs already auto-excluded by default
        if name.startswith(".") or name in DEFAULT_EXCLUDE_DIRS:
            continue
        dirs.append(name)
    return dirs


def _prompt_choice(prompt_text: str, options: list[str], default_idx: int = 0) -> int:
    """Show a numbered menu and return the selected index (0-based).

    Press Enter to accept the default. Enter a number to pick.
    """
    console.print(f"\n  [bold]{prompt_text}[/bold]")
    for i, opt in enumerate(options, 1):
        marker = " [green]← default[/green]" if i - 1 == default_idx else ""
        console.print(f"    [cyan]{i}[/cyan]  {opt}{marker}")

    while True:
        raw = input(f"\n  Pick [1-{len(options)}] or Enter for default: ").strip()
        if not raw:
            return default_idx
        try:
            idx = int(raw)
            if 1 <= idx <= len(options):
                return idx - 1
        except ValueError:
            pass
        console.print(f"  [red]Enter a number between 1 and {len(options)}[/red]")


def _prompt_yes_no(prompt_text: str, default: bool = True) -> bool:
    """Ask a yes/no question with clear formatting."""
    default_hint = (
        "[bold green]Y[/bold green]/n" if default else "y/[bold green]N[/bold green]"
    )
    console.print(f"\n  {prompt_text} ({default_hint}) ", end="")
    raw = input("").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


def _checkbox(
    prompt_text: str, options: list[str], preselected: list[str] | None = None
) -> list[str]:
    """Interactive multi-select: ↑↓ move, space toggle, enter confirm.

    Uses ◉/◯ bullets (green/grey) without highlight bar background.
    """
    import questionary
    from prompt_toolkit.styles import Style

    style = Style([
        ("qmark", "fg:ansicyan bold"),
        ("question", "bold"),
        ("pointer", "fg:ansicyan bold"),
        ("highlighted", "noinherit bold"),
        ("selected", "fg:ansigreen noinherit"),
        ("checkbox", "fg:ansigreen"),
        ("instruction", "fg:ansigray italic"),
        ("answer", "fg:ansigreen bold"),
    ])

    choices = [
        questionary.Choice(opt, checked=(opt in (preselected or options)))
        for opt in options
    ]

    result = questionary.checkbox(
        prompt_text,
        choices=choices,
        instruction="(↑↓ move, space toggle, enter confirm)",
        pointer="›",
        style=style,
    ).ask()

    return result if result is not None else []


def _get_echo_guard_python() -> str:
    """Find the best python path for running the MCP server."""
    import shutil
    import sys

    python_path = sys.executable

    # If installed via pipx, use the pipx venv python
    if shutil.which("pipx"):
        try:
            result = subprocess.run(
                ["pipx", "environment", "--value", "PIPX_LOCAL_VENVS"],
                capture_output=True, text=True, timeout=5,
            )
            pipx_venvs = result.stdout.strip()
            pipx_python = Path(pipx_venvs) / "echo-guard" / "bin" / "python"
            if pipx_python.exists():
                python_path = str(pipx_python)
        except Exception:
            pass

    return python_path


def _register_mcp(tool_name: str, cli_cmd: str, python_path: str, console: "Console") -> None:
    """Register the MCP server with a specific AI tool."""
    try:
        result = subprocess.run(
            [cli_cmd, "mcp", "add", "echo-guard", "--",
             python_path, "-m", "echo_guard.mcp_server"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            console.print(f"  [green]✓[/green] MCP server registered for {tool_name}")
            console.print(f"    [dim]Restart {tool_name} to activate.[/dim]")
        else:
            err = result.stderr.strip() or result.stdout.strip()
            console.print(f"  [yellow]{tool_name} returned: {err}[/yellow]")
            console.print(f"  [dim]Manual: {cli_cmd} mcp add echo-guard -- {python_path} -m echo_guard.mcp_server[/dim]")
    except Exception as exc:
        console.print(f"  [yellow]Could not register with {tool_name}: {exc}[/yellow]")
        console.print(f"  [dim]Manual: {cli_cmd} mcp add echo-guard -- {python_path} -m echo_guard.mcp_server[/dim]")


def _setup_mcp_integration(console: "Console") -> None:
    """Detect AI tools and offer to register the MCP server."""
    import shutil

    tools: list[tuple[str, str]] = []  # (display_name, cli_command)
    if shutil.which("claude"):
        tools.append(("Claude Code", "claude"))
    if shutil.which("codex"):
        tools.append(("Codex", "codex"))

    if not tools:
        console.print("\n  [dim]No AI tools detected (Claude Code, Codex). Skipping MCP setup.[/dim]")
        return

    tool_names = [name for name, _ in tools]
    selected = _checkbox("Register MCP server for", tool_names, preselected=tool_names)

    if not selected:
        return

    python_path = _get_echo_guard_python()
    for name, cli_cmd in tools:
        if name in selected:
            _register_mcp(name, cli_cmd, python_path, console)


def _setup_github_action(repo_root: Path, console: "Console") -> None:
    """Offer to generate the GitHub Action workflow file."""
    git_dir = repo_root / ".git"
    if not git_dir.exists():
        return

    if not _prompt_yes_no("Add GitHub Action for PR duplicate checking?"):
        return

    # Ask fail-on behavior only if they want the action
    fail_options = [
        "Advisory only — never fail (good for first-time setup)",
        "Fail on HIGH — exact/near-exact clones (recommended)",
        "Fail on MEDIUM+ — includes modified and semantic clones",
    ]
    fail_values = ["none", "high", "medium"]
    fidx = _prompt_choice("When should the PR check fail?", fail_options, default_idx=1)
    fail_on = fail_values[fidx]

    workflow_dir = repo_root / ".github" / "workflows"
    workflow_path = workflow_dir / "echo-guard.yml"

    if workflow_path.exists():
        if not _prompt_yes_no("Workflow already exists. Overwrite?", default=False):
            console.print("  [dim]Keeping existing workflow.[/dim]")
            return

    workflow_dir.mkdir(parents=True, exist_ok=True)

    workflow_content = f"""\
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
          threshold: "0.50"
          fail-on: "{fail_on}"
          comment: "true"
"""
    workflow_path.write_text(workflow_content)
    console.print(f"  [green]✓[/green] Wrote {workflow_path.relative_to(repo_root)}")
    console.print("    [dim]Commit this file to enable PR checks.[/dim]")


@app.command()
def setup(
    path: Optional[str] = typer.Argument(
        None, help="Path to repository root (default: auto-detect)"
    ),
) -> None:
    """Interactive project setup — detects your repo, configures Echo Guard, and runs the first scan."""
    from rich.status import Status

    _show_banner()

    repo_root = Path(path) if path else _find_repo_root()

    console.print(f"  Repository: [bold]{repo_root}[/bold]\n")

    # ── Detect project structure ─────────────────────────────────────
    with Status("[bold]Detecting project structure...[/bold]", console=console):
        all_dirs = _detect_directories(repo_root)
        service_dirs = _detect_service_dirs(repo_root)

    # ── Quick configuration ──────────────────────────────────────────
    console.print("\n[bold]━━━ Configuration ━━━[/bold]")

    # Directory selection — choose which to SCAN (all on by default, toggle off to exclude)
    ignore_patterns: list[str] = []
    if all_dirs:
        selected_to_scan = _checkbox(
            "Select directories to scan (deselect to exclude)",
            all_dirs,
            preselected=all_dirs,  # All on by default
        )
        excluded = [d for d in all_dirs if d not in selected_to_scan]
        if excluded:
            ignore_patterns = [f"{d}/" for d in excluded]

        console.print(f"\n  [dim]Scanning: {', '.join(selected_to_scan)}[/dim]")
        if excluded:
            console.print(f"  [dim]Excluding: {', '.join(excluded)}[/dim]")

    # Detect languages (respecting the exclude selections)
    excluded_set = {p.rstrip("/") for p in ignore_patterns}
    with Status("[bold]Scanning source files...[/bold]", console=console):
        detected = _detect_languages_in_repo(repo_root, exclude_dirs=excluded_set)

    if detected:
        console.print("\n  [green]Languages detected:[/green]")
        for lang, count in sorted(detected.items(), key=lambda x: -x[1]):
            console.print(f"    [cyan]{lang:12s}[/cyan]  {count} files")
        selected_langs = sorted(detected.keys())
    else:
        console.print(
            "\n  [yellow]No source files detected — using all languages.[/yellow]"
        )
        selected_langs = [
            "python", "javascript", "typescript", "go",
            "rust", "java", "ruby", "c", "cpp",
        ]

    service_boundaries: list[str] = []
    if service_dirs:
        console.print("\n  [green]Monorepo services detected:[/green]")
        for sd in service_dirs:
            console.print(f"    [cyan]•[/cyan] {sd}")
        service_boundaries = service_dirs

    # ── Write config ─────────────────────────────────────────────────
    fail_on = "high"  # Default — only changed if GitHub Action is set up
    config_path = repo_root / ".echoguard.yml"
    write_config = True

    if config_path.exists():
        if not _prompt_yes_no("Config already exists. Overwrite?", default=False):
            write_config = False
            console.print("  [dim]Keeping existing config.[/dim]")

    if write_config:
        lang_block = "\n".join(f"  - {l}" for l in selected_langs)
        if ignore_patterns:
            ignore_block = "\n" + "\n".join(f"  - {p}" for p in ignore_patterns)
        else:
            ignore_block = " []"
        svc_block = ""
        if service_boundaries:
            svc_lines = "\n".join(f"  - {b}" for b in service_boundaries)
            svc_block = f"\nservice_boundaries:\n{svc_lines}\n"
        else:
            svc_block = "\n# service_boundaries: auto-detected at scan time\n"

        threshold = 0.50  # General threshold (embedding thresholds are per-language and automatic)

        config_content = f"""\
# Echo Guard configuration — generated by `echo-guard setup`

threshold: {threshold}
min_function_lines: 3
max_function_lines: 500

languages:
{lang_block}

fail_on: {fail_on}
{svc_block}
# Directories to exclude from scanning
ignore:{ignore_block}

# Acknowledged findings — suppressed in CI
# Run `echo-guard review` to add entries interactively
acknowledged: []
"""
        config_path.write_text(config_content)
        console.print(f"\n  [green]✓[/green] Wrote {config_path}")

    # ── Integrations ──────────────────────────────────────────────────
    console.print("\n[bold]━━━ Integrations ━━━[/bold]")

    # MCP server registration
    _setup_mcp_integration(console)

    # GitHub Action
    _setup_github_action(repo_root, console)

    # ── Index ────────────────────────────────────────────────────────
    # Defer heavy imports until after config
    from echo_guard.scanner import index_repo, scan_for_redundancy

    console.print("\n[bold]━━━ Indexing ━━━[/bold]")

    config = EchoGuardConfig.load(repo_root)
    with Status("[bold]Parsing source files...[/bold]", console=console):
        idx, file_count, func_count, lang_counts = index_repo(
            repo_root, config=config, verbose=False
        )
        idx.close()

    console.print(
        f"  [green]✓[/green] Indexed [bold]{func_count}[/bold] functions across [bold]{file_count}[/bold] files"
    )
    for lang, count in sorted(lang_counts.items()):
        console.print(f"    {lang}: {count}")

    if func_count == 0:
        console.print(
            "\n  [yellow]No functions found. Check your language settings and exclude patterns.[/yellow]"
        )
        return

    # ── Scan ─────────────────────────────────────────────────────────
    if not _prompt_yes_no("Run initial scan?"):
        console.print()
        console.print("[bold green]✓ Setup complete![/bold green]")
        console.print("  Run [cyan]echo-guard scan[/cyan] when ready.")
        return

    console.print("\n[bold]━━━ Scanning ━━━[/bold]")

    with Status("[bold]Detecting redundancies...[/bold]", console=console):
        matches = scan_for_redundancy(repo_root, threshold=threshold, config=config)

    if not matches:
        console.print("  [green bold]✓ No redundant code detected![/green bold]")
    else:
        from echo_guard.similarity import FindingGroup, group_matches as _group

        grouped = _group(matches)
        high = sum(1 for m in matches if m.severity == "high")
        medium = sum(1 for m in matches if m.severity == "medium")

        console.print(
            f"  Found [bold]{len(grouped)}[/bold] findings ({len(matches)} raw pairs)"
        )
        console.print(
            f"    [red bold]HIGH: {high}[/red bold]  [yellow]MEDIUM: {medium}[/yellow]"
        )

        # Write report — only HIGH + MEDIUM
        report_path = repo_root / ".echo-guard" / "scan-results.txt"
        report_lines: list[str] = []
        report_lines.append("=" * 72)
        report_lines.append("ECHO GUARD — SCAN REPORT")
        report_lines.append("=" * 72)
        report_lines.append(f"Repository:  {repo_root}")
        report_lines.append(f"Threshold:   {threshold}")
        report_lines.append(f"Functions:   {func_count}  |  Files: {file_count}")
        report_lines.append(f"Languages:   {', '.join(sorted(lang_counts.keys()))}")
        report_lines.append(
            f"Findings:    {len(grouped)}  (HIGH={high}  MEDIUM={medium})"
        )
        report_lines.append("")

        for i, item in enumerate(grouped, 1):
            report_lines.append("-" * 72)
            if isinstance(item, FindingGroup):
                score_pct = f"{item.similarity_score * 100:.0f}%"
                report_lines.append(
                    f"#{i}  {item.severity.upper()} ({score_pct}) — "
                    f"{item.pattern_description}  ({item.match_count} pairs collapsed)"
                )
                report_lines.append("")
                for func in item.functions:
                    vis = f" ({func.visibility})" if func.visibility != "public" else ""
                    cls = f"{func.class_name}." if func.class_name else ""
                    report_lines.append(
                        f"  • {func.language}  {func.filepath}:{func.lineno}  {cls}{func.name}(){vis}"
                    )
                report_lines.append("")
                if item.reuse_type == "cross_service_reference":
                    report_lines.append(
                        "  ⚠ Cross-service — direct import NOT possible."
                    )
                elif item.reuse_guidance:
                    report_lines.append(f"  Suggestion: {item.reuse_guidance}")
            else:
                score_pct = f"{item.similarity_score * 100:.0f}%"
                report_lines.append(
                    f"#{i}  {item.severity.upper()} ({score_pct}) — {item.match_type.replace('_', ' ')}"
                )
                src = item.source_func
                ext = item.existing_func
                report_lines.append(
                    f"  New:      {src.language}  {src.filepath}:{src.lineno}  {src.name}()"
                )
                report_lines.append(
                    f"  Existing: {ext.language}  {ext.filepath}:{ext.lineno}  {ext.name}()"
                )
                if item.reuse_type == "cross_service_reference":
                    report_lines.append(
                        "  ⚠ Cross-service — direct import NOT possible."
                    )
                elif item.reuse_type == "reference_only":
                    report_lines.append(f"  ⚠ Cross-language: {item.reuse_guidance}")
                elif item.import_suggestion:
                    report_lines.append(f"  Import: {item.import_suggestion}")
            report_lines.append("")

        report_lines.append("=" * 72)
        report_path.write_text("\n".join(report_lines) + "\n")
        console.print(
            f"\n  [green]✓[/green] Report saved to [bold]{report_path.name}[/bold]"
        )

    # ── Done ─────────────────────────────────────────────────────────
    console.print()
    console.print("[bold green]✓ Setup complete![/bold green]")
    console.print()
    console.print("  [bold]What's next:[/bold]")
    console.print("    [cyan]echo-guard scan[/cyan]       Run a scan anytime")
    console.print("    [cyan]echo-guard review[/cyan]     Review and acknowledge findings")
    console.print("    [cyan]echo-guard watch[/cyan]      Auto-check on file save")


@app.command(name="clear-index")
def clear_index() -> None:
    """Clear the Echo Guard index."""
    repo_root = _find_repo_root()
    from echo_guard.index import FunctionIndex

    idx = FunctionIndex(repo_root)
    idx.clear()
    idx.close()
    console.print("[green]✓[/green] Index cleared.")


@app.command(name="feedback-stats")
def feedback_stats() -> None:
    """Show feedback collection statistics."""
    repo_root = _find_repo_root()
    from echo_guard.index import FunctionIndex

    idx = FunctionIndex(repo_root)
    stats = idx.get_feedback_stats()
    idx.close()

    if stats["total"] == 0:
        console.print("[dim]No feedback collected yet.[/dim]")
        return

    console.print(f"[bold]Feedback Stats[/bold]  ({stats['total']} records)")
    if stats["by_verdict"]:
        console.print("  By verdict:")
        for verdict, count in sorted(stats["by_verdict"].items()):
            console.print(f"    {verdict}: {count}")
    if stats["by_severity"]:
        console.print("  By severity:")
        for severity, count in sorted(stats["by_severity"].items()):
            console.print(f"    {severity}: {count}")


@app.command(name="export-feedback")
def export_feedback(
    output: str = typer.Option(
        "-", "--output", "-o", help="Output file path (default: stdout)"
    ),
) -> None:
    """Export anonymized feedback as JSONL for model training.

    No source code, file paths, or function names are included —
    only structural features and user verdicts.
    """
    import json

    repo_root = _find_repo_root()
    from echo_guard.index import FunctionIndex

    idx = FunctionIndex(repo_root)
    records = idx.export_feedback_jsonl()
    idx.close()

    if not records:
        console.print("[dim]No feedback to export.[/dim]")
        return

    lines = [json.dumps(r, default=str) for r in records]
    text = "\n".join(lines) + "\n"

    if output == "-":
        print(text, end="")
    else:
        from pathlib import Path
        Path(output).write_text(text)
        console.print(f"[green]✓[/green] Exported {len(records)} records to {output}")


@app.command()
def review(
    path: Optional[str] = typer.Argument(None, help="Path to repository root"),
) -> None:
    """Interactively review all findings — acknowledge, fix, or skip each one.

    Walks through each unresolved finding, shows the code side-by-side,
    and lets you decide what to do. Acknowledged findings are saved to
    .echoguard.yml so they won't block CI.

    Run this after `echo-guard scan` or when a PR check fails.
    """
    from echo_guard.scanner import scan_for_redundancy
    from echo_guard.index import FunctionIndex

    repo_root = Path(path) if path else _find_repo_root()
    config = EchoGuardConfig.load(repo_root)

    acknowledged: set[str] = set(config.acknowledged)

    # Run scan
    console.print("[bold]Scanning for findings...[/bold]")
    matches = scan_for_redundancy(repo_root, config=config)

    if not matches:
        console.print("[green bold]✓ No findings to review.[/green bold]")
        return

    # Build finding IDs and filter out already-acknowledged
    unresolved = []
    for match in matches:
        fid = FunctionIndex.make_finding_id(
            match.source_func.filepath, match.source_func.name,
            match.existing_func.filepath, match.existing_func.name,
        )
        if fid not in acknowledged:
            unresolved.append((fid, match))

    if not unresolved:
        console.print(f"[green bold]✓ All {len(matches)} findings already acknowledged.[/green bold]")
        return

    console.print(f"\n[bold]{len(unresolved)} unresolved findings[/bold] ({len(acknowledged)} already acknowledged)\n")

    new_acknowledged = 0
    skipped = 0
    training_idx = FunctionIndex(repo_root)

    for i, (fid, match) in enumerate(unresolved, 1):
        src = match.source_func
        ext = match.existing_func
        clone_label = match.clone_type_label
        score_pct = f"{match.similarity_score * 100:.0f}%"

        console.print(f"[bold]── Finding {i}/{len(unresolved)} ──[/bold]  {clone_label} ({score_pct})")
        console.print(f"  [cyan]{src.filepath}:{src.lineno}[/cyan]  {src.name}()")
        console.print(f"  [green]{ext.filepath}:{ext.lineno}[/green]  {ext.name}()")

        # Show source preview (first 6 lines of each)
        src_preview = "\n".join(src.source.splitlines()[:6])
        ext_preview = "\n".join(ext.source.splitlines()[:6])
        console.print("\n  [dim]Your code:[/dim]")
        for line in src_preview.splitlines():
            console.print(f"    [cyan]{line}[/cyan]")
        console.print("  [dim]Existing:[/dim]")
        for line in ext_preview.splitlines():
            console.print(f"    [green]{line}[/green]")

        # Prompt
        console.print("\n  [bold]a[/bold]=acknowledge (intentional)  [bold]f[/bold]=false positive  [bold]s[/bold]=skip  [bold]q[/bold]=quit")
        while True:
            choice = input("  → ").strip().lower()
            if choice in ("a", "acknowledge", "f", "false_positive", "fp"):
                verdict = "false_positive" if choice in ("f", "false_positive", "fp") else "acknowledged"
                label = "False positive" if verdict == "false_positive" else "Acknowledged"

                # Save to .echoguard.yml acknowledged list
                config.add_acknowledged(fid)
                acknowledged.add(fid)
                new_acknowledged += 1

                # Record training data (code pair + verdict)
                try:
                    train_verdict = "not_clone" if verdict == "false_positive" else "clone"
                    training_idx.record_training_pair(
                        verdict=train_verdict,
                        language=src.language,
                        source_code_a=src.source,
                        source_code_b=ext.source,
                        function_name_a=src.name,
                        function_name_b=ext.name,
                        filepath_a=src.filepath,
                        filepath_b=ext.filepath,
                        embedding_score=match.similarity_score,
                        clone_type=match.clone_type,
                        probe_type="review",
                    )
                except Exception:
                    pass

                console.print(f"  [green]✓ {label}[/green]")
                break
            elif choice in ("s", "skip", ""):
                skipped += 1
                console.print("  [dim]Skipped[/dim]")
                break
            elif choice in ("q", "quit"):
                console.print(f"\n[bold]Review paused.[/bold] {new_acknowledged} acknowledged, {skipped} skipped.")
                if new_acknowledged > 0:
                    console.print("  [green]✓[/green] .echoguard.yml updated — commit to suppress in CI.")
                return
            else:
                console.print("  [dim]Press a, f, s, or q[/dim]")

        console.print()

    training_idx.close()

    console.print("[bold]Review complete.[/bold] {0} acknowledged, {1} skipped.".format(new_acknowledged, skipped))
    if new_acknowledged > 0:
        console.print("  [green]✓[/green] .echoguard.yml updated — commit to suppress in CI.")


@app.command(name="acknowledge")
def acknowledge_finding(
    finding_id: str = typer.Argument(..., help="Finding ID from scan --output json"),
    note: str = typer.Option("", "--note", "-n", help="Why this is intentional"),
) -> None:
    """Acknowledge a finding so it won't block CI.

    Adds the finding ID to the `acknowledged` list in .echoguard.yml.

    Get finding IDs from: echo-guard scan --output json
    """
    repo_root = _find_repo_root()
    config = EchoGuardConfig.load(repo_root)

    if finding_id in config.acknowledged:
        console.print(f"[dim]Already acknowledged: {finding_id}[/dim]")
        return

    config.add_acknowledged(finding_id)

    # Also record in local DuckDB index
    from echo_guard.index import FunctionIndex
    try:
        idx = FunctionIndex(repo_root)
        parts = finding_id.split("||")
        if len(parts) == 2:
            a_parts = parts[0].rsplit(":", 1)
            b_parts = parts[1].rsplit(":", 1)
            idx.resolve_finding(
                finding_id=finding_id,
                verdict="acknowledged",
                source_filepath=a_parts[0] if len(a_parts) == 2 else "",
                source_function=a_parts[1] if len(a_parts) == 2 else "",
                source_lineno=None,
                existing_filepath=b_parts[0] if len(b_parts) == 2 else "",
                existing_function=b_parts[1] if len(b_parts) == 2 else "",
                existing_lineno=None,
                note=note,
            )
        idx.close()
    except Exception:
        pass

    console.print(f"[green]✓[/green] Acknowledged: {finding_id}")
    console.print("  Saved to .echoguard.yml — commit to suppress in CI.")


@app.command(name="training-data")
def training_data(
    export: str = typer.Option("", "--export", "-e", help="Export to JSONL file"),
) -> None:
    """View or export collected training data for model fine-tuning.

    Training data is collected from:
    - resolve_finding verdicts (fixed → clone, false_positive → not_clone)
    - respond_to_probe verdicts (low-confidence exploration)
    """
    repo_root = _find_repo_root()
    from echo_guard.index import FunctionIndex

    idx = FunctionIndex(repo_root)
    stats = idx.get_training_pair_count()

    if stats["total"] == 0:
        console.print("[dim]No training data collected yet.[/dim]")
        console.print("  Training data is collected automatically when you use:")
        console.print("    • resolve_finding (MCP) — from duplicate resolutions")
        console.print("    • respond_to_probe (MCP) — from low-confidence explorations")
        idx.close()
        return

    console.print(f"[bold]Training Data[/bold]  ({stats['total']} pairs)")
    if stats["by_verdict"]:
        console.print("  By verdict:")
        for verdict, count in sorted(stats["by_verdict"].items()):
            console.print(f"    {verdict}: {count}")
    if stats["by_probe_type"]:
        console.print("  By source:")
        for ptype, count in sorted(stats["by_probe_type"].items()):
            console.print(f"    {ptype}: {count}")

    if export:
        import json
        pairs = idx.export_training_pairs()
        with open(export, "w") as f:
            for pair in pairs:
                f.write(json.dumps(pair, default=str) + "\n")
        console.print(f"\n[green]✓[/green] Exported {len(pairs)} pairs to {export}")

    idx.close()


if __name__ == "__main__":
    app()
