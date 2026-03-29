"""Echo Guard CLI — semantic linting for codebase redundancy."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Optional

import typer

from rich.console import Console
from echo_guard.config import EchoGuardConfig
from echo_guard.output import console, format_json, print_results

# Heavy imports (scanner → similarity → embeddings) are deferred to
# command functions so that `echo-guard --help` and `echo-guard setup`
# show output immediately.


def _version_callback(value: bool) -> None:
    if value:
        from echo_guard import __version__

        print(f"echo-guard {__version__}")
        raise typer.Exit()


app = typer.Typer(
    name="echo-guard",
    help="Semantic linting CLI that detects codebase redundancy created by AI coding agents.",
    add_completion=False,
)


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit",
    ),
) -> None:
    """Echo Guard — semantic linting for codebase redundancy."""


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


def _make_scan_progress():
    """Create a rich Progress instance for scan operations."""
    from rich.progress import (
        Progress,
        SpinnerColumn,
        BarColumn,
        MofNCompleteColumn,
        TextColumn,
        TimeElapsedColumn,
        TimeRemainingColumn,
    )

    return Progress(
        SpinnerColumn("dots", style="cyan"),
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(
            bar_width=30, style="dim", complete_style="cyan", finished_style="green"
        ),
        MofNCompleteColumn(),
        TextColumn("[dim]|[/dim]"),
        TimeElapsedColumn(),
        TextColumn("[dim]eta[/dim]"),
        TimeRemainingColumn(),
        console=console,
    )


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
    include_tests: bool = typer.Option(
        False,
        "--include-tests",
        help="Include test files in the scan (excluded by default)",
    ),
) -> None:
    """Scan the repository for redundant code."""
    from echo_guard.scanner import index_repo, scan_for_redundancy

    repo_root = Path(path) if path else _find_repo_root()
    config = EchoGuardConfig.load(repo_root)

    if no_graph:
        config.enable_dep_graph = False
    if include_tests:
        config.include_tests = True

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

    with _make_scan_progress() as progress:
        raw_matches = scan_for_redundancy(
            repo_root,
            config=config,
            verbose=verbose,
            progress=progress,
        )

    from echo_guard.index import FunctionIndex

    matches = [
        m for m in raw_matches
        if not config.is_suppressed(
            FunctionIndex.make_finding_id(
                m.source_func.filepath, m.source_func.name,
                m.existing_func.filepath, m.existing_func.name,
                source_hash=m.source_func.ast_hash or "",
                existing_hash=m.existing_func.ast_hash or "",
            ),
            m.source_func.ast_hash or "",
            m.existing_func.ast_hash or "",
        )
    ]

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
    output: str = typer.Option(
        "rich", "--output", "-o", help="Output format: rich, json, compact"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed output"),
    diff: bool = typer.Option(False, "--diff", "-d", help="Show diff for matches"),
    include_tests: bool = typer.Option(
        False,
        "--include-tests",
        help="Include test files in the scan (excluded by default)",
    ),
) -> None:
    """Check specific files against the existing index (fast path for pre-commit)."""
    from echo_guard.scanner import check_files

    repo_root = _find_repo_root()
    config = EchoGuardConfig.load(repo_root)
    if include_tests:
        config.include_tests = True

    index_path = repo_root / ".echo-guard" / "index.duckdb"
    if not index_path.exists():
        console.print("[red]No index found. Run `echo-guard index` first.[/red]")
        raise typer.Exit(code=2)

    raw_matches = check_files(
        repo_root, files, config=config, verbose=verbose
    )

    from echo_guard.index import FunctionIndex

    matches = [
        m for m in raw_matches
        if not config.is_suppressed(
            FunctionIndex.make_finding_id(
                m.source_func.filepath, m.source_func.name,
                m.existing_func.filepath, m.existing_func.name,
                source_hash=m.source_func.ast_hash or "",
                existing_hash=m.existing_func.ast_hash or "",
            ),
            m.source_func.ast_hash or "",
            m.existing_func.ast_hash or "",
        )
    ]

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
                repo_root, [filepath], config=config
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

    raw_matches = scan_for_redundancy(repo_root, config=config)

    from echo_guard.index import FunctionIndex

    # Filter suppressed findings so health counts match the sidebar/daemon
    matches = [
        m for m in raw_matches
        if not config.is_suppressed(
            FunctionIndex.make_finding_id(
                m.source_func.filepath, m.source_func.name,
                m.existing_func.filepath, m.existing_func.name,
                source_hash=m.source_func.ast_hash or "",
                existing_hash=m.existing_func.ast_hash or "",
            ),
            m.source_func.ast_hash or "",
            m.existing_func.ast_hash or "",
        )
    ]

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
        f"([red]{bd['extract']} extract[/red], [yellow]{bd['review']} review[/yellow])"
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
    """Create a default echo-guard.yml config file."""
    repo_root = _find_repo_root()
    config_path = repo_root / "echo-guard.yml"

    if config_path.exists():
        console.print(f"[yellow]Config already exists at {config_path}[/yellow]")
        return

    config_content = """\
# Echo Guard configuration
# See: https://github.com/echo-guard/echo-guard

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

# Minimum severity to fail CI: extract, review, none
fail_on: extract

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

    Creates .github/workflows/echo-guard-ci.yml in your repo.
    """
    repo_root = Path(path) if path else _find_repo_root()
    _setup_github_action(repo_root, console)


# ── Interactive Setup Wizard ─────────────────────────────────────────────


def _detect_languages_in_repo(
    repo_root: Path, exclude_dirs: set[str] | None = None
) -> dict[str, int]:
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
        if name.startswith(".") or name in DEFAULT_EXCLUDE_DIRS:
            continue
        dirs.append(name)
    return dirs


def _get_dir_summary(repo_root: Path, dir_name: str) -> str:
    """Get a short summary of a directory (subdirs, file count)."""
    dir_path = repo_root / dir_name
    subdirs = []
    file_count = 0

    for child in sorted(dir_path.iterdir()):
        if child.name.startswith("."):
            continue
        if child.is_dir():
            subdirs.append(child.name)
        elif child.is_file():
            file_count += 1

    # Count files recursively (cap at 10K to avoid slow repos)
    total_files = 0
    for item in dir_path.rglob("*"):
        if not item.is_file():
            continue
        total_files += 1
        if total_files > 10000:
            break

    parts = []
    if subdirs:
        preview = ", ".join(subdirs[:4])
        if len(subdirs) > 4:
            preview += f", +{len(subdirs) - 4} more"
        parts.append(preview)
    parts.append(f"{total_files} files")
    return " · ".join(parts)


def _prompt_choice(prompt_text: str, options: list[str], default_idx: int = 0) -> int:
    """Show a numbered menu and return the selected index (0-based).

    Press Enter to accept the default. Enter a number to pick.
    """
    console.print(f"\n  [bold]{prompt_text}[/bold]")
    for i, opt in enumerate(options, 1):
        marker = " [green]← default[/green]" if i - 1 == default_idx else ""
        console.print(f"    [cyan]{i}[/cyan]  {opt}{marker}")

    while True:
        try:
            raw = input(f"\n  Pick [1-{len(options)}] or Enter for default: ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print()
            raise KeyboardInterrupt from None
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
    """Ask a yes/no question with clear formatting. Raises KeyboardInterrupt on Ctrl+C."""
    default_hint = (
        "[bold green]Y[/bold green]/n" if default else "y/[bold green]N[/bold green]"
    )
    console.print(f"\n  {prompt_text} ({default_hint}) ", end="")
    try:
        raw = input("").strip().lower()
    except (KeyboardInterrupt, EOFError):
        console.print()
        raise KeyboardInterrupt
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

    style = Style(
        [
            ("qmark", "fg:ansicyan bold"),
            ("question", "bold"),
            ("pointer", "fg:ansicyan bold"),
            ("highlighted", "noinherit bold"),
            ("selected", "fg:ansigreen noinherit"),
            ("checkbox", "fg:ansigreen"),
            ("instruction", "fg:ansigray italic"),
            ("answer", "fg:ansigreen bold"),
        ]
    )

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

    if result is None:
        raise KeyboardInterrupt  # Ctrl+C during questionary prompt
    return result


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
                capture_output=True,
                text=True,
                timeout=5,
            )
            pipx_venvs = result.stdout.strip()
            pipx_python = Path(pipx_venvs) / "echo-guard" / "bin" / "python"
            if pipx_python.exists():
                python_path = str(pipx_python)
        except Exception:
            pass

    return python_path


def _register_mcp(
    tool_name: str, cli_cmd: str, python_path: str, console: Console
) -> None:
    """Register the MCP server with a specific AI tool."""
    try:
        result = subprocess.run(
            [
                cli_cmd,
                "mcp",
                "add",
                "echo-guard",
                "--",
                python_path,
                "-m",
                "echo_guard.mcp_server",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            console.print(f"  [green]✓[/green] MCP server registered for {tool_name}")
            console.print(f"    [dim]Restart {tool_name} to activate.[/dim]")
        else:
            err = result.stderr.strip() or result.stdout.strip()
            console.print(f"  [yellow]{tool_name} returned: {err}[/yellow]")
            console.print(
                f"  [dim]Manual: {cli_cmd} mcp add echo-guard -- {python_path} -m echo_guard.mcp_server[/dim]"
            )
    except Exception as exc:
        console.print(f"  [yellow]Could not register with {tool_name}: {exc}[/yellow]")
        console.print(
            f"  [dim]Manual: {cli_cmd} mcp add echo-guard -- {python_path} -m echo_guard.mcp_server[/dim]"
        )


def _is_mcp_registered(cli_cmd: str) -> bool:
    """Check if echo-guard MCP server is already registered with an AI tool."""
    try:
        result = subprocess.run(
            [cli_cmd, "mcp", "list"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return "echo-guard" in result.stdout.lower()
    except Exception:
        return False


def _setup_mcp_integration(console: Console) -> None:
    """Detect AI tools and offer to register the MCP server."""
    import shutil

    tools: list[tuple[str, str]] = []  # (display_name, cli_command)
    if shutil.which("claude"):
        tools.append(("Claude Code", "claude"))
    if shutil.which("codex"):
        tools.append(("Codex", "codex"))

    if not tools:
        console.print(
            "  [dim]No AI tools detected (Claude Code, Codex). Skipping MCP setup.[/dim]"
        )
        return

    # Check which tools already have echo-guard registered
    needs_setup: list[tuple[str, str]] = []
    already_registered: list[str] = []

    for name, cli_cmd in tools:
        if _is_mcp_registered(cli_cmd):
            already_registered.append(name)
        else:
            needs_setup.append((name, cli_cmd))

    for name in already_registered:
        console.print(f"  [green]✓[/green] MCP server already registered for {name}")

    if not needs_setup:
        return

    tool_names = [name for name, _ in needs_setup]
    selected = _checkbox("Register MCP server for", tool_names, preselected=tool_names)

    if not selected:
        return

    python_path = _get_echo_guard_python()
    for name, cli_cmd in needs_setup:
        if name in selected:
            _register_mcp(name, cli_cmd, python_path, console)


def _setup_github_action(repo_root: Path, console: Console) -> None:
    """Offer to generate the GitHub Action workflow file."""
    git_dir = repo_root / ".git"
    if not git_dir.exists():
        console.print("  [dim]Not a git repository. Skipping GitHub Action.[/dim]")
        return

    workflow_dir = repo_root / ".github" / "workflows"
    workflow_path = workflow_dir / "echo-guard-ci.yml"

    if workflow_path.exists():
        console.print("  [green]✓[/green] GitHub Action already configured")
        return

    if not _prompt_yes_no("Add GitHub Action for PR duplicate checking?"):
        return

    # Ask fail-on behavior only if they want the action
    fail_options = [
        "Advisory only — never fail (good for first-time setup)",
        "Fail on EXTRACT — 3+ copies (recommended)",
        "Fail on REVIEW+ — includes all duplicate pairs",
    ]
    fail_values = ["none", "extract", "review"]
    fidx = _prompt_choice("When should the PR check fail?", fail_options, default_idx=1)
    fail_on = fail_values[fidx]

    workflow_dir.mkdir(parents=True, exist_ok=True)

    from echo_guard import __version__

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
      - uses: jwizenfeld04/Echo-Guard@v{__version__}
        with:
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
    try:
        _setup_interactive(path)
    except KeyboardInterrupt:
        console.print("\n\n  [dim]Setup cancelled.[/dim]")
        raise typer.Exit(code=0)


def _setup_interactive(path: Optional[str] = None) -> None:
    """Inner setup logic — separated so KeyboardInterrupt is caught cleanly."""
    from rich.status import Status

    _show_banner()

    repo_root = Path(path) if path else _find_repo_root()

    console.print(f"  Repository: [bold]{repo_root}[/bold]")
    console.print()

    # ── Check existing state ─────────────────────────────────────────
    config_path = repo_root / "echo-guard.yml"
    index_path = repo_root / ".echo-guard" / "index.duckdb"
    scan_results_path = repo_root / ".echo-guard" / "scan-results.txt"
    has_config = config_path.exists()
    has_index = index_path.exists()
    has_scan = scan_results_path.exists()

    # ── Returning user: config already exists ─────────────────────────
    if has_config:
        config = EchoGuardConfig.load(repo_root)

        console.print(
            f"  Existing config found at [bold]{config_path.relative_to(repo_root)}[/bold]"
        )
        console.print()
        console.print(f"    Languages:  {', '.join(config.languages)}")
        if config.ignore:
            console.print(f"    Excluding:  {', '.join(config.ignore)}")
        if config.service_boundaries:
            console.print(f"    Services:   {', '.join(config.service_boundaries)}")

        use_existing = _prompt_yes_no("Use this configuration?", default=True)

        if use_existing:
            # Skip config, go straight to integrations + scan
            console.print()
            _setup_integrations(repo_root, console)
            _setup_index_and_scan(repo_root, config, has_index, has_scan, console)
            return
        else:
            # Fall through to full setup (will overwrite)
            console.print()
            pass

    # ── First-time setup: full configuration ──────────────────────────
    with Status("[bold]Detecting project structure...[/bold]", console=console):
        all_dirs = _detect_directories(repo_root)
        service_dirs = _detect_service_dirs(repo_root)

    console.print()
    console.print("[bold]━━━ Configuration ━━━[/bold]")
    console.print()

    # Show directory preview
    ignore_patterns: list[str] = []
    if all_dirs:
        console.print("  [bold]Project structure:[/bold]")
        console.print()
        for d in all_dirs:
            summary = _get_dir_summary(repo_root, d)
            console.print(f"    [cyan]{d}/[/cyan]  [dim]{summary}[/dim]")
        console.print()

        selected_to_scan = _checkbox(
            "Select directories to scan (deselect to exclude)",
            all_dirs,
            preselected=all_dirs,
        )
        excluded = [d for d in all_dirs if d not in selected_to_scan]
        if excluded:
            ignore_patterns = [f"{d}/" for d in excluded]

        console.print()
        if excluded:
            console.print(f"  [dim]Excluding: {', '.join(excluded)}[/dim]")

    # Detect languages
    excluded_set = {p.rstrip("/") for p in ignore_patterns}
    with Status("[bold]Scanning source files...[/bold]", console=console):
        detected = _detect_languages_in_repo(repo_root, exclude_dirs=excluded_set)

    if detected:
        console.print()
        console.print("  [green]Languages detected:[/green]")
        for lang, count in sorted(detected.items(), key=lambda x: -x[1]):
            console.print(f"    [cyan]{lang:12s}[/cyan]  {count} files")
        selected_langs = sorted(detected.keys())
    else:
        console.print()
        console.print(
            "  [yellow]No source files detected — using all languages.[/yellow]"
        )
        selected_langs = [
            "python",
            "javascript",
            "typescript",
            "go",
            "rust",
            "java",
            "ruby",
            "c",
            "cpp",
        ]

    service_boundaries: list[str] = []
    if service_dirs:
        console.print()
        console.print("  [green]Monorepo services detected:[/green]")
        for sd in service_dirs:
            console.print(f"    [cyan]•[/cyan] {sd}")
        service_boundaries = service_dirs

    # Write config
    fail_on = "extract"
    lang_block = "\n".join(f"  - {lang}" for lang in selected_langs)
    ignore_block = (
        ("\n" + "\n".join(f"  - {p}" for p in ignore_patterns))
        if ignore_patterns
        else " []"
    )
    svc_block = (
        (
            "\nservice_boundaries:\n"
            + "\n".join(f"  - {b}" for b in service_boundaries)
            + "\n"
        )
        if service_boundaries
        else "\n# service_boundaries: auto-detected at scan time\n"
    )

    config_content = f"""\
# Echo Guard configuration — generated by `echo-guard setup`

min_function_lines: 3
max_function_lines: 500

# Embedding model: codesage-small (default), codesage-base (slower, higher recall), unixcoder (legacy)
model: codesage-small

languages:
{lang_block}

fail_on: {fail_on}
{svc_block}
# Directories to exclude from scanning
ignore:{ignore_block}

# Suppressed findings — add via `echo-guard review` or `echo-guard acknowledge`
# verdict: intentional (re-surfaces if code changes) or dismissed (permanent)
acknowledged: []
"""
    config_path.write_text(config_content)
    console.print()
    console.print(f"  [green]✓[/green] Wrote {config_path}")

    config = EchoGuardConfig.load(repo_root)

    # Integrations + scan
    console.print()
    _setup_integrations(repo_root, console)
    _setup_index_and_scan(repo_root, config, False, False, console)


def _setup_skills(repo_root: Path, console: Console) -> None:
    """Offer to install Echo Guard slash-command skills for Claude Code."""
    import shutil

    skills_src = Path(__file__).parent / "skills"
    if not skills_src.exists():
        return  # Skills not bundled — skip silently

    if not _prompt_yes_no("Install Echo Guard slash-command skills for Claude Code?", default=True):
        return

    options = [
        "This project only  (.claude/skills/ in repo)",
        "Globally  (~/.claude/skills/ — all sessions)",
    ]
    idx = _prompt_choice("Where should skills be installed?", options, default_idx=0)
    dest = Path.home() / ".claude" / "skills" if idx == 1 else repo_root / ".claude" / "skills"
    dest.mkdir(parents=True, exist_ok=True)

    for skill_file in sorted(skills_src.glob("*.md")):
        shutil.copy2(skill_file, dest / skill_file.name)
        console.print(f"  [green]✓[/green] /{skill_file.stem}")

    console.print(f"  [dim]Installed to {dest}[/dim]")
    console.print("  [dim]Restart Claude Code to pick up new skills.[/dim]")


def _setup_integrations(repo_root: Path, console: Console) -> None:
    """Set up MCP + GitHub Action + skills integrations."""
    console.print()
    console.print("[bold]━━━ Integrations ━━━[/bold]")
    console.print()

    _setup_mcp_integration(console)
    _setup_github_action(repo_root, console)
    console.print()
    _setup_skills(repo_root, console)


def _setup_index_and_scan(
    repo_root: Path,
    config: EchoGuardConfig,
    has_index: bool,
    has_scan: bool,
    console: Console,
) -> None:
    """Index and scan — handles returning users who already have results."""
    from echo_guard.scanner import index_repo, scan_for_redundancy

    # ── Index ────────────────────────────────────────────────────────
    if has_index:
        from echo_guard.index import FunctionIndex

        idx = FunctionIndex(repo_root)
        stats = idx.get_stats()
        idx.close()

        console.print()
        console.print(
            f"  Existing index found — [bold]{stats.get('total_functions', '?')}[/bold] functions across [bold]{stats.get('total_files', '?')}[/bold] files"
        )

        if has_scan:
            scan_path = repo_root / ".echo-guard" / "scan-results.txt"
            scan_age = ""
            try:
                import datetime

                mtime = datetime.datetime.fromtimestamp(scan_path.stat().st_mtime)
                delta = datetime.datetime.now() - mtime
                if delta.days > 0:
                    scan_age = f" ({delta.days}d ago)"
                elif delta.seconds > 3600:
                    scan_age = f" ({delta.seconds // 3600}h ago)"
                else:
                    scan_age = f" ({delta.seconds // 60}m ago)"
            except Exception:
                pass
            console.print(f"  Previous scan results available{scan_age}")

        if not _prompt_yes_no("Re-index and scan?", default=False):
            console.print()
            console.print("[bold green]✓ Everything is up to date.[/bold green]")
            console.print("  Run [cyan]echo-guard scan[/cyan] to re-scan anytime.")
            console.print(
                "  Run [cyan]echo-guard scan --verbose[/cyan] for detailed output."
            )
            return

        # Re-index and scan requested
        pass

    # ── Run indexing (first-time or re-index) ─────────────────────────
    label = "Re-indexing" if has_index else "Indexing"
    console.print()
    console.print()
    console.print(f"[bold]━━━ {label} ━━━[/bold]")
    console.print()

    with _make_scan_progress() as idx_progress:
        idx, file_count, func_count, lang_counts = index_repo(
            repo_root,
            config=config,
            verbose=False,
            progress=idx_progress,
            incremental=not has_index,  # Full re-index if existing, incremental if first time
        )
        idx.close()

    console.print(
        f"  [green]✓[/green] Indexed [bold]{func_count}[/bold] functions across [bold]{file_count}[/bold] files"
    )
    for lang, count in sorted(lang_counts.items()):
        console.print(f"    {lang}: {count}")

    if func_count == 0:
        console.print()
        console.print(
            "  [yellow]No functions found. Check your language settings and exclude patterns.[/yellow]"
        )
        return

    # ── Scan ─────────────────────────────────────────────────────────
    if not _prompt_yes_no("Run scan?"):
        console.print()
        console.print()
        console.print("[bold green]✓ Setup complete![/bold green]")
        console.print("  Run [cyan]echo-guard scan[/cyan] when ready.")
        return

    console.print()
    console.print()
    console.print("[bold]━━━ Scanning ━━━[/bold]")
    console.print()

    with _make_scan_progress() as progress:
        matches = scan_for_redundancy(
            repo_root,
            config=config,
            progress=progress,
        )

    _print_setup_results(matches, repo_root, config, console)


def _print_setup_results(
    matches: list,
    repo_root: Path,
    config: EchoGuardConfig,
    console: Console,
) -> None:
    """Print scan results and write report file."""
    if not matches:
        console.print("  [green bold]✓ No redundant code detected![/green bold]")
    else:
        from echo_guard.similarity import FindingGroup, group_matches as _group
        from echo_guard.index import FunctionIndex

        grouped = _group(matches)
        extract = sum(1 for item in grouped if item.severity == "extract")
        review = sum(1 for item in grouped if item.severity == "review")

        console.print(
            f"  Found [bold]{len(grouped)}[/bold] findings ({len(matches)} raw pairs)"
        )
        console.print(
            f"    [red bold]EXTRACT: {extract}[/red bold]  [yellow]REVIEW: {review}[/yellow]"
        )

        # Get stats for the report header
        idx = FunctionIndex(repo_root)
        stats = idx.get_stats()
        idx.close()

        report_path = repo_root / ".echo-guard" / "scan-results.txt"
        report_lines: list[str] = []
        report_lines.append("=" * 72)
        report_lines.append("ECHO GUARD — SCAN REPORT")
        report_lines.append("=" * 72)
        report_lines.append(f"Repository:  {repo_root}")
        report_lines.append(
            f"Functions:   {stats.get('total_functions', '?')}  |  Files: {stats.get('total_files', '?')}"
        )
        report_lines.append(
            f"Findings:    {len(grouped)}  (EXTRACT={extract}  REVIEW={review})"
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

    console.print()
    console.print("[bold green]✓ Setup complete![/bold green]")
    console.print()
    console.print("  [bold]What's next:[/bold]")
    console.print("    [cyan]echo-guard scan[/cyan]       Run a scan anytime")
    console.print(
        "    [cyan]echo-guard review[/cyan]     Review and acknowledge findings"
    )
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
    echo-guard.yml so they won't block CI.

    Run this after `echo-guard scan` or when a PR check fails.
    """
    from echo_guard.scanner import scan_for_redundancy
    from echo_guard.index import FunctionIndex

    repo_root = Path(path) if path else _find_repo_root()
    config = EchoGuardConfig.load(repo_root)

    suppressed_ids: set[str] = config.get_suppressed_ids()

    # Run scan
    console.print("[bold]Scanning for findings...[/bold]")
    matches = scan_for_redundancy(repo_root, config=config)

    if not matches:
        console.print("[green bold]✓ No findings to review.[/green bold]")
        return

    # Build finding IDs and filter out already-suppressed
    unresolved = []
    for match in matches:
        fid = FunctionIndex.make_finding_id(
            match.source_func.filepath,
            match.source_func.name,
            match.existing_func.filepath,
            match.existing_func.name,
            source_hash=match.source_func.ast_hash or "",
            existing_hash=match.existing_func.ast_hash or "",
        )
        if not config.is_suppressed(fid, match.source_func.ast_hash or "",
                                     match.existing_func.ast_hash or ""):
            unresolved.append((fid, match))

    if not unresolved:
        console.print(
            f"[green bold]✓ All {len(matches)} findings already suppressed.[/green bold]"
        )
        return

    console.print(
        f"\n[bold]{len(unresolved)} unresolved findings[/bold] ({len(suppressed_ids)} already suppressed)\n"
    )

    new_resolved = 0
    skipped = 0
    training_idx = FunctionIndex(repo_root)

    for i, (fid, match) in enumerate(unresolved, 1):
        src = match.source_func
        ext = match.existing_func
        clone_label = match.clone_type_label
        score_pct = f"{match.similarity_score * 100:.0f}%"

        console.print(
            f"[bold]── Finding {i}/{len(unresolved)} ──[/bold]  {clone_label} ({score_pct})"
        )
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
        console.print(
            "\n  [bold]i[/bold]=intentional (keep both)  [bold]d[/bold]=dismiss (not a dup)  [bold]s[/bold]=skip  [bold]q[/bold]=quit"
        )
        while True:
            choice = input("  → ").strip().lower()
            if choice in ("i", "intentional", "d", "dismiss", "dismissed"):
                verdict = (
                    "dismissed"
                    if choice in ("d", "dismiss", "dismissed")
                    else "intentional"
                )
                label = (
                    "Dismissed" if verdict == "dismissed" else "Intentional"
                )

                # Save to echo-guard.yml
                config.add_suppressed(fid, verdict,
                                       src.ast_hash or "", ext.ast_hash or "")
                new_resolved += 1

                # Record training data (code pair + verdict)
                try:
                    train_verdict = (
                        "not_clone" if verdict == "dismissed" else "clone"
                    )
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
                console.print(
                    f"\n[bold]Review paused.[/bold] {new_resolved} resolved, {skipped} skipped."
                )
                if new_resolved > 0:
                    console.print(
                        "  [green]✓[/green] echo-guard.yml updated — commit to suppress in CI."
                    )
                return
            else:
                console.print("  [dim]Press i, d, s, or q[/dim]")

        console.print()

    training_idx.close()

    console.print(
        "[bold]Review complete.[/bold] {0} resolved, {1} skipped.".format(
            new_resolved, skipped
        )
    )
    if new_resolved > 0:
        console.print(
            "  [green]✓[/green] echo-guard.yml updated — commit to suppress in CI."
        )


@app.command(name="acknowledge")
def acknowledge_finding(
    finding_id: str = typer.Argument(..., help="Finding ID from scan --output json"),
    verdict: str = typer.Option(
        "intentional",
        "--verdict", "-v",
        help="intentional (keep both) or dismissed (not a duplicate)",
    ),
    note: str = typer.Option("", "--note", "-n", help="Why this is intentional"),
) -> None:
    """Suppress a finding so it won't block CI.

    Adds the finding to the suppressed list in echo-guard.yml.
    intentional findings re-surface if the function changes; dismissed are permanent.

    Get finding IDs from: echo-guard scan --output json
    """
    repo_root = _find_repo_root()
    config = EchoGuardConfig.load(repo_root)

    if verdict not in ("intentional", "dismissed"):
        console.print(f"[red]Invalid verdict: {verdict}. Use: intentional, dismissed[/red]")
        raise typer.Exit(1)

    if finding_id in config.get_suppressed_ids():
        console.print(f"[dim]Already suppressed: {finding_id}[/dim]")
        return

    src_hash = existing_hash = ""
    parts = finding_id.split("||")
    if len(parts) == 2:
        a = parts[0].rsplit(":", 1)
        b = parts[1].rsplit(":", 1)
        src_hash = a[1] if len(a) == 2 else ""
        existing_hash = b[1] if len(b) == 2 else ""
    config.add_suppressed(finding_id, verdict, src_hash, existing_hash)

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
                verdict=verdict,
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

    label = "Intentional" if verdict == "intentional" else "Dismissed"
    console.print(f"[green]✓[/green] {label}: {finding_id}")
    console.print("  Saved to echo-guard.yml — commit to suppress in CI.")

    # Touch signal file so any running daemon triggers a rescan → VS Code updates
    signal_path = repo_root / ".echo-guard" / "rescan.signal"
    try:
        signal_path.parent.mkdir(parents=True, exist_ok=True)
        signal_path.touch()
    except Exception:
        pass


@app.command(name="prune")
def prune_acknowledged(
    path: Optional[str] = typer.Argument(None, help="Path to repository root"),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n", help="Show what would be removed without changing anything"
    ),
) -> None:
    """Remove stale entries from the acknowledged list in echo-guard.yml.

    Runs a scan and drops any acknowledged entry whose finding ID no longer
    matches a current result.  This keeps the config file lean over time as
    code evolves and old suppressions become irrelevant.
    """
    from echo_guard.scanner import index_repo, scan_for_redundancy
    from echo_guard.similarity import group_matches, FindingGroup
    from echo_guard.index import FunctionIndex

    repo_root = Path(path) if path else _find_repo_root()
    config = EchoGuardConfig.load(repo_root)

    if not config.acknowledged:
        console.print("[dim]Nothing to prune — no acknowledged entries.[/dim]")
        return

    # Auto-index if needed
    index_path = repo_root / ".echo-guard" / "index.duckdb"
    if not index_path.exists():
        console.print("[yellow]No index found. Running auto-index...[/yellow]")
        idx, file_count, func_count, _ = index_repo(repo_root, config=config)
        idx.close()

    # Run a full scan (without suppression filtering) to collect every
    # finding ID that the codebase currently produces.
    scan_config = EchoGuardConfig.load(repo_root)
    scan_config.acknowledged = []  # disable suppression so we see everything

    with _make_scan_progress() as progress:
        raw_matches = scan_for_redundancy(repo_root, config=scan_config, progress=progress)

    grouped = group_matches(raw_matches)
    live_ids: set[str] = set()

    for item in grouped:
        if isinstance(item, FindingGroup):
            rep = item.representative_match
            for func in item.functions:
                if (func.filepath == rep.source_func.filepath
                        and func.name == rep.source_func.name):
                    continue
                live_ids.add(FunctionIndex.make_finding_id(
                    func.filepath, func.name,
                    rep.source_func.filepath, rep.source_func.name,
                    source_hash=func.ast_hash or "",
                    existing_hash=rep.source_func.ast_hash or "",
                ))
        else:
            match = item
            live_ids.add(FunctionIndex.make_finding_id(
                match.source_func.filepath, match.source_func.name,
                match.existing_func.filepath, match.existing_func.name,
                source_hash=match.source_func.ast_hash or "",
                existing_hash=match.existing_func.ast_hash or "",
            ))

    # Build stable keys for live findings so dismissed entries survive
    # cluster representative changes (where the exact pair ID shifts but
    # the underlying functions remain the same).
    live_stable_keys = {EchoGuardConfig.make_stable_key(fid) for fid in live_ids}

    # Partition acknowledged entries into kept vs stale
    kept = []
    stale = []
    for entry in config.acknowledged:
        eid = entry.get("id", "")
        if eid in live_ids:
            kept.append(entry)
        elif entry.get("verdict") == "dismissed":
            # Dismissed entries match by stable key (filepath:name without hash)
            sk = entry.get("stable_key") or EchoGuardConfig.make_stable_key(eid)
            if sk in live_stable_keys:
                kept.append(entry)
            else:
                stale.append(entry)
        else:
            stale.append(entry)

    if not stale:
        console.print(
            f"[green]✓[/green] All {len(kept)} acknowledged "
            f"{'entry is' if len(kept) == 1 else 'entries are'} still active — nothing to prune."
        )
        return

    for entry in stale:
        verdict = entry.get("verdict", "?")
        console.print(f"  [red]−[/red] {entry.get('id', '?')}  [dim]({verdict})[/dim]")

    if dry_run:
        console.print(
            f"\n[yellow]Dry run:[/yellow] would remove {len(stale)} stale "
            f"{'entry' if len(stale) == 1 else 'entries'}, "
            f"keeping {len(kept)}."
        )
        return

    config.acknowledged = kept
    config._save_acknowledged()
    console.print(
        f"\n[green]✓[/green] Pruned {len(stale)} stale "
        f"{'entry' if len(stale) == 1 else 'entries'} from echo-guard.yml "
        f"({len(kept)} remaining)."
    )


@app.command(name="training-data")
def training_data(
    export: str = typer.Option("", "--export", "-e", help="Export to JSONL file"),
) -> None:
    """View or export collected training data for model fine-tuning.

    Training data is collected from:
    - resolve_finding verdicts (resolved/intentional → clone, dismissed → not_clone)
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


@app.command(name="notify")
def notify(
    path: Optional[str] = typer.Argument(None, help="Path to repository root"),
) -> None:
    """Touch the signal file to trigger a daemon rescan.

    Any running daemon detects the mtime change and pushes a findings_refreshed
    notification to VS Code, updating diagnostics in real-time.
    """
    repo_root = Path(path) if path else _find_repo_root()
    signal_path = repo_root / ".echo-guard" / "rescan.signal"
    signal_path.parent.mkdir(parents=True, exist_ok=True)
    signal_path.touch()

    lock_path = repo_root / ".echo-guard" / "daemon.lock"
    if lock_path.exists():
        console.print("[green]✓[/green] Daemon rescan triggered.")
    else:
        console.print("[dim]No daemon running (signal file written for when one starts).[/dim]")


@app.command(name="search")
def search_functions(
    query: str = typer.Argument(..., help="Function name, description, or call name to search"),
    language: Optional[str] = typer.Option(None, "--language", "-l", help="Filter by language"),
    output: str = typer.Option("rich", "--output", "-o", help="Output format: rich, json"),
    limit: int = typer.Option(20, "--limit", "-n", help="Maximum results to return"),
) -> None:
    """Search the function index by name, source text, or call names.

    Searches the DuckDB index for functions matching the query.
    Useful for finding where specific functionality is implemented.
    """
    import json as _json

    repo_root = _find_repo_root()
    index_path = repo_root / ".echo-guard" / "index.duckdb"
    if not index_path.exists():
        console.print("[red]No index found. Run `echo-guard index` first.[/red]")
        raise typer.Exit(code=2)

    from echo_guard.index import FunctionIndex

    idx = FunctionIndex(repo_root)
    try:
        results = idx.search_functions(query, language=language, limit=limit)
    finally:
        idx.close()

    if output == "json":
        print(_json.dumps(results, indent=2))
        return

    if not results:
        console.print(f"[dim]No functions found matching '{query}'.[/dim]")
        return

    console.print(f"\n[bold]Search results for '[cyan]{query}[/cyan]'[/bold]  ({len(results)} found)\n")
    for r in results:
        lang_color = "cyan"
        cls_prefix = f"{r['class_name']}." if r.get("class_name") else ""
        console.print(
            f"  [bold]{cls_prefix}{r['name']}[/bold]  "
            f"[{lang_color}]{r['language']}[/{lang_color}]  "
            f"[dim]{r['filepath']}:{r['lineno']}[/dim]"
        )
        if r.get("preview"):
            for line in r["preview"].splitlines()[:3]:
                console.print(f"    [dim]{line}[/dim]")
        console.print()


@app.command(name="install-skills")
def install_skills(
    global_install: bool = typer.Option(
        False, "--global", "-g", help="Install to ~/.claude/skills/ (all sessions)"
    ),
) -> None:
    """Install Echo Guard slash-command skills for Claude Code.

    Copies skill files to .claude/skills/ in the current project (default)
    or to ~/.claude/skills/ for use in all sessions (--global).

    Skills installed:
      /echo-guard          Scan & detect duplicates
      /echo-guard-refactor AI-assisted refactoring
      /echo-guard-review   Interactive triage
      /echo-guard-search   Function search
    """
    import shutil

    skills_src = Path(__file__).parent / "skills"
    if not skills_src.exists():
        console.print("[red]Skills directory not found in package.[/red]")
        raise typer.Exit(code=2)

    if global_install:
        dest = Path.home() / ".claude" / "skills"
    else:
        repo_root = _find_repo_root()
        dest = repo_root / ".claude" / "skills"

    dest.mkdir(parents=True, exist_ok=True)

    installed = []
    for skill_file in sorted(skills_src.glob("*.md")):
        target = dest / skill_file.name
        shutil.copy2(skill_file, target)
        installed.append(skill_file.stem)
        console.print(f"  [green]✓[/green] {skill_file.name}")

    if installed:
        scope = "globally (~/.claude/skills/)" if global_install else f"in {dest.relative_to(Path.home()) if global_install else dest}"
        console.print(f"\n[green bold]✓[/green bold] Installed {len(installed)} skills to {dest}")
        console.print("  Restart Claude Code to pick up new skills.")
    else:
        console.print("[yellow]No skill files found to install.[/yellow]")


@app.command(name="daemon")
def daemon_command(
    path: str = typer.Option("", "--path", "-p", help="Repository root (default: auto-detect)"),
) -> None:
    """Start the JSON-RPC daemon for VS Code extension integration.

    The daemon holds the index, ONNX model, and similarity engine in memory
    to avoid cold-start costs (~2-3s) on every file save. Communicates via
    JSON-RPC 2.0 over stdin/stdout.

    The VS Code extension spawns this process automatically. You do not need
    to run it manually unless debugging.
    """
    from echo_guard.daemon import run_daemon

    repo_root = Path(path) if path else _find_repo_root()
    run_daemon(repo_root)


if __name__ == "__main__":
    app()
