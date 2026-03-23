"""Echo Guard CLI — semantic linting for codebase redundancy."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Optional

import typer

from echo_guard.config import EchoGuardConfig
from echo_guard.output import console, format_json, print_results

# Heavy imports (scanner → similarity → sklearn/numpy) are deferred to
# command functions so that `echo-guard --help` and `echo-guard setup`
# show output immediately instead of waiting for sklearn to load.

app = typer.Typer(
    name="echo-guard",
    help="Semantic linting CLI that detects codebase redundancy created by AI coding agents.",
    add_completion=False,
)


def _find_repo_root() -> Path:
    """Find the git repository root, or fall back to cwd."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
        return Path(result.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return Path.cwd()


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
        0.50, "--threshold", "-t", help="Similarity threshold (0.0–1.0)"
    ),
    output: str = typer.Option(
        "rich", "--output", "-o", help="Output format: rich, json, compact"
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Include LOW-severity findings (hidden by default)",
    ),
    diff: bool = typer.Option(
        False, "--diff", "-d", help="Show side-by-side diff for matches"
    ),
    no_graph: bool = typer.Option(
        False, "--no-graph", help="Disable dependency graph routing"
    ),
) -> None:
    """Scan the repository for redundant code.

    By default shows only HIGH and MEDIUM findings. Use --verbose to include LOW.
    """
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
        f"([red]{bd['high']} high[/red], [yellow]{bd['medium']} med[/yellow], [cyan]{bd['low']} low[/cyan])"
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

# Minimum severity to fail CI: high, medium, low, none
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


# ── Interactive Setup Wizard ─────────────────────────────────────────────


def _detect_languages_in_repo(repo_root: Path) -> dict[str, int]:
    """Scan the repo for source files and return languages with file counts.

    Skips common heavy directories (node_modules, .git, vendor, etc.)
    to avoid slow glob on large repos.
    """
    from echo_guard.languages import LANGUAGES
    from echo_guard.config import DEFAULT_EXCLUDE_DIRS

    skip = DEFAULT_EXCLUDE_DIRS

    found: dict[str, int] = {}
    all_exts: dict[str, str] = {}  # ext → lang_name
    for lang_name, spec in LANGUAGES.items():
        for ext in spec.extensions:
            all_exts[ext] = lang_name

    # Single walk instead of N rglobs — much faster on large repos
    for f in repo_root.rglob("*"):
        if not f.is_file():
            continue
        # Skip excluded directories
        if any(part in skip for part in f.relative_to(repo_root).parts):
            continue
        ext = f.suffix.lower()
        if ext in all_exts:
            lang = all_exts[ext]
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


def _detect_exclude_candidates(repo_root: Path) -> list[str]:
    """Detect directories that are likely candidates for exclusion."""
    candidates: list[str] = []
    common_excludes = [
        "docs",
        "docs_src",
        "documentation",
        "tests",
        "test",
        "__tests__",
        "spec",
        "specs",
        "examples",
        "example",
        "samples",
        "fixtures",
        "testdata",
        "test_data",
        "migrations",
        "alembic",
        "generated",
        "gen",
        "proto",
        "scripts",
        "tools",
        "storybook",
        "stories",
        ".storybook",
    ]
    for name in common_excludes:
        if (repo_root / name).is_dir():
            candidates.append(name)
    return candidates


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


def _prompt_multi_select(
    prompt_text: str, options: list[str], preselected: list[str] | None = None
) -> list[str]:
    """Show checkboxes and let user toggle selections with clear instructions."""
    selected = set(preselected or [])

    console.print(f"\n  [bold]{prompt_text}[/bold]")

    while True:
        for i, opt in enumerate(options, 1):
            check = "[green]✓[/green]" if opt in selected else "[dim]·[/dim]"
            console.print(f"    {check} [cyan]{i}[/cyan]  {opt}")

        console.print()
        console.print(
            "  [dim]Commands: number to toggle, [bold]a[/bold]=select all, [bold]n[/bold]=select none, [bold]Enter[/bold]=confirm[/dim]"
        )
        raw = input("  > ").strip().lower()

        if not raw:
            break
        elif raw in ("a", "all"):
            selected = set(options)
        elif raw in ("n", "none"):
            selected.clear()
        else:
            for part in raw.replace(",", " ").split():
                try:
                    idx = int(part)
                    if 1 <= idx <= len(options):
                        opt = options[idx - 1]
                        if opt in selected:
                            selected.discard(opt)
                        else:
                            selected.add(opt)
                except ValueError:
                    pass

        console.print()

    return [o for o in options if o in selected]


@app.command()
def setup(
    path: Optional[str] = typer.Argument(
        None, help="Path to repository root (default: auto-detect)"
    ),
) -> None:
    """Interactive project setup — detects your repo, configures Echo Guard, and runs the first scan."""
    from rich.status import Status

    repo_root = Path(path) if path else _find_repo_root()

    console.print()
    console.print(
        "[bold cyan]┌─ Echo Guard Setup ─────────────────────────────────┐[/bold cyan]"
    )
    console.print(f"[bold cyan]│[/bold cyan]  Repository: [bold]{repo_root}[/bold]")
    console.print(
        "[bold cyan]└────────────────────────────────────────────────────┘[/bold cyan]"
    )

    # ── Auto-detect everything ───────────────────────────────────────
    with Status("[bold]Scanning repository...[/bold]", console=console):
        detected = _detect_languages_in_repo(repo_root)
        service_dirs = _detect_service_dirs(repo_root)
        exclude_candidates = _detect_exclude_candidates(repo_root)

    # Show detection results immediately
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
        console.print("\n  [green]Monorepo services detected:[/green]")
        for sd in service_dirs:
            console.print(f"    [cyan]•[/cyan] {sd}")
        service_boundaries = service_dirs

    if exclude_candidates:
        console.print(
            f"\n  [green]Directories you may want to exclude:[/green] [dim]{', '.join(exclude_candidates)}[/dim]"
        )

    # ── Quick configuration ──────────────────────────────────────────
    console.print("\n[bold]━━━ Configuration ━━━[/bold]")

    # Threshold
    threshold_options = [
        "Loose  (0.40) — more matches, more noise, good for audits",
        "Normal (0.50) — balanced for most projects",
        "Strict (0.60) — fewer matches, less noise, good for large repos",
        "Very strict (0.70) — only near-identical code",
    ]
    threshold_values = [0.40, 0.50, 0.60, 0.70]
    tidx = _prompt_choice("Similarity threshold?", threshold_options, default_idx=1)
    threshold = threshold_values[tidx]

    # Exclude directories
    echoguardignore_lines: list[str] = []
    if exclude_candidates:
        selected_excludes = _prompt_multi_select(
            "Exclude directories from scanning?",
            exclude_candidates,
            preselected=[],
        )
        if selected_excludes:
            echoguardignore_lines = [f"{d}/" for d in selected_excludes]

    # CI behavior
    fail_options = [
        "Advisory only — never fail (good for first-time setup)",
        "Fail on HIGH — near-exact clones (recommended)",
        "Fail on MEDIUM+ — strong matches too",
        "Fail on LOW+ — strictest, any match above threshold",
    ]
    fail_values = ["none", "high", "medium", "low"]
    fidx = _prompt_choice("CI behavior?", fail_options, default_idx=1)
    fail_on = fail_values[fidx]

    # ── Write config ─────────────────────────────────────────────────
    config_path = repo_root / ".echoguard.yml"
    write_config = True

    if config_path.exists():
        if not _prompt_yes_no("Config already exists. Overwrite?", default=False):
            write_config = False
            console.print("  [dim]Keeping existing config.[/dim]")

    if write_config:
        lang_block = "\n".join(f"  - {l}" for l in selected_langs)
        svc_block = ""
        if service_boundaries:
            svc_lines = "\n".join(f"  - {b}" for b in service_boundaries)
            svc_block = f"\nservice_boundaries:\n{svc_lines}\n"
        else:
            svc_block = "\n# service_boundaries: auto-detected at scan time\n"

        config_content = f"""\
# Echo Guard configuration — generated by `echo-guard setup`

threshold: {threshold}
min_function_lines: 3
max_function_lines: 500

languages:
{lang_block}

output_format: rich
fail_on: {fail_on}
enable_dep_graph: true
{svc_block}"""
        config_path.write_text(config_content)
        console.print(f"\n  [green]✓[/green] Wrote {config_path}")

    # Write .echoguardignore
    ignore_path = repo_root / ".echoguardignore"
    if echoguardignore_lines:
        if ignore_path.exists():
            existing = ignore_path.read_text()
            new_patterns = "\n".join(
                line for line in echoguardignore_lines if line not in existing
            )
            if new_patterns:
                with open(ignore_path, "a") as f:
                    f.write(f"\n# Added by echo-guard setup\n{new_patterns}\n")
                console.print(f"  [green]✓[/green] Updated {ignore_path}")
        else:
            content = "# Echo Guard ignore patterns\n\n"
            content += "\n".join(echoguardignore_lines) + "\n"
            ignore_path.write_text(content)
            console.print(f"  [green]✓[/green] Wrote {ignore_path}")

    # ── Index ────────────────────────────────────────────────────────
    # Defer heavy imports until after config — this is where sklearn/numpy load
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
        low = sum(1 for m in matches if m.severity == "low")

        # Filter: only HIGH + MEDIUM in the report
        def _sev(item: object) -> str:
            return item.severity  # type: ignore[union-attr]

        visible = [item for item in grouped if _sev(item) != "low"]
        hidden_count = len(grouped) - len(visible)

        console.print(
            f"  Found [bold]{len(visible)}[/bold] actionable findings ({len(matches)} raw pairs)"
        )
        console.print(
            f"    [red bold]HIGH: {high}[/red bold]  [yellow]MEDIUM: {medium}[/yellow]  [dim]LOW: {low} (hidden)[/dim]"
        )

        # Write report — only HIGH + MEDIUM
        report_path = repo_root / "echo-guard-report.txt"
        report_lines: list[str] = []
        report_lines.append("=" * 72)
        report_lines.append("ECHO GUARD — SCAN REPORT")
        report_lines.append("=" * 72)
        report_lines.append(f"Repository:  {repo_root}")
        report_lines.append(f"Threshold:   {threshold}")
        report_lines.append(f"Functions:   {func_count}  |  Files: {file_count}")
        report_lines.append(f"Languages:   {', '.join(sorted(lang_counts.keys()))}")
        report_lines.append(
            f"Findings:    {len(visible)} shown  (HIGH={high}  MEDIUM={medium})"
        )
        if hidden_count > 0:
            report_lines.append(
                f"Hidden:      {hidden_count} LOW findings — rescan with `echo-guard scan -v` to include"
            )
        report_lines.append("")

        for i, item in enumerate(visible, 1):
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
        if hidden_count > 0:
            report_lines.append(f"{hidden_count} LOW-severity findings hidden.")
            report_lines.append(
                "Run `echo-guard scan -v` to see all findings including LOW."
            )
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
    console.print("    [cyan]echo-guard scan[/cyan]          Run a scan anytime")
    console.print(
        "    [cyan]echo-guard scan -v[/cyan]       Include LOW-severity findings"
    )
    console.print("    [cyan]echo-guard install-hook[/cyan]   Add pre-commit hook")
    console.print("    [cyan]echo-guard watch[/cyan]          Auto-check on file save")


@app.command(name="clear-index")
def clear_index() -> None:
    """Clear the Echo Guard index."""
    repo_root = _find_repo_root()
    from echo_guard.index import FunctionIndex

    idx = FunctionIndex(repo_root)
    idx.clear()
    idx.close()
    console.print("[green]✓[/green] Index cleared.")


if __name__ == "__main__":
    app()
