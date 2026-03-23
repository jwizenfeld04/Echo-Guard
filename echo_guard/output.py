"""Output formatting for Echo Guard results."""

from __future__ import annotations

import difflib
import json

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from echo_guard.similarity import FindingGroup, SimilarityMatch, group_matches, _common_path_prefix

console = Console()

SEVERITY_COLORS = {
    "high": "red",
    "medium": "yellow",
    "low": "cyan",
}


def _make_diff(source_code: str, existing_code: str, source_label: str, existing_label: str) -> str:
    """Generate a unified diff between two code blocks."""
    source_lines = source_code.splitlines(keepends=True)
    existing_lines = existing_code.splitlines(keepends=True)
    diff = difflib.unified_diff(
        existing_lines,
        source_lines,
        fromfile=existing_label,
        tofile=source_label,
        lineterm="",
    )
    return "\n".join(diff)


def format_match_rich(match: SimilarityMatch, index: int = 1, show_diff: bool = False) -> None:
    """Print a single match with rich formatting."""
    severity = match.severity
    color = SEVERITY_COLORS[severity]
    score_pct = f"{match.similarity_score * 100:.0f}%"

    title = f"[{color} bold]#{index} {severity.upper()} ({score_pct})[/{color} bold] — {match.match_type.replace('_', ' ')}"

    content_lines = []

    src = match.source_func
    ext = match.existing_func

    src_vis = f" [dim]({src.visibility})[/dim]" if hasattr(src, "visibility") and src.visibility != "public" else ""
    ext_vis = f" [dim]({ext.visibility})[/dim]" if hasattr(ext, "visibility") and ext.visibility != "public" else ""
    lang_tag = f"[dim]{src.language}[/dim] " if hasattr(src, "language") else ""
    ext_lang = f"[dim]{ext.language}[/dim] " if hasattr(ext, "language") else ""
    content_lines.append(f"[bold]New code:[/bold]  {lang_tag}{src.filepath}:{src.lineno}  → [cyan]{src.name}()[/cyan]{src_vis}")
    content_lines.append(f"[bold]Existing:[/bold]  {ext_lang}{ext.filepath}:{ext.lineno}  → [cyan]{ext.name}()[/cyan]{ext_vis}")

    # Reuse classification and suggestions
    if hasattr(match, "reuse_type") and match.reuse_type:
        if match.reuse_type == "reference_only":
            content_lines.append("")
            content_lines.append(f"[yellow bold]⚠ Cross-language:[/yellow bold]  {match.reuse_guidance}")
        elif match.reuse_type == "cross_service_reference":
            content_lines.append("")
            content_lines.append(
                "[yellow bold]⚠ Cross-service:[/yellow bold]  "
                "These live in separate services. Direct import is NOT possible. "
                "Consider a shared library package or accept as intentional boundary duplication."
            )
        elif match.reuse_type == "compatible_import":
            content_lines.append("")
            content_lines.append(f"[blue bold]Compatible runtime:[/blue bold]  {match.reuse_guidance}")
        elif match.reuse_type == "same_file_refactor":
            content_lines.append("")
            content_lines.append(f"[magenta bold]Same file:[/magenta bold]  {match.reuse_guidance}")
        elif match.reuse_type == "extract_utility":
            content_lines.append("")
            content_lines.append(f"[blue bold]Parameterized duplicate:[/blue bold]  {match.reuse_guidance}")

    if match.import_suggestion:
        content_lines.append("")
        if hasattr(match, "reuse_type") and match.reuse_type in ("reference_only", "same_file_refactor", "extract_utility"):
            content_lines.append(f"[dim]Suggestion:[/dim]  {match.import_suggestion}")
        else:
            content_lines.append(f"[green bold]Suggested fix:[/green bold]  {match.import_suggestion}")

    panel = Panel(
        "\n".join(content_lines),
        title=title,
        border_style=color,
        padding=(0, 1),
    )
    console.print(panel)

    # Diff view
    if show_diff:
        diff_text = _make_diff(
            src.source, ext.source,
            f"{src.filepath}:{src.name} (new)",
            f"{ext.filepath}:{ext.name} (existing)",
        )
        if diff_text.strip():
            console.print(Syntax(diff_text, "diff", theme="monokai", line_numbers=False, padding=1))
        else:
            console.print("[dim]  (identical source)[/dim]")
        console.print()


def format_group_rich(group: FindingGroup, index: int = 1) -> None:
    """Print a grouped finding — multiple related functions collapsed into one panel."""
    severity = group.severity
    color = SEVERITY_COLORS[severity]
    score_pct = f"{group.similarity_score * 100:.0f}%"

    title = (
        f"[{color} bold]#{index} {severity.upper()} ({score_pct})[/{color} bold] — "
        f"{group.pattern_description} [dim]({group.match_count} pairs collapsed)[/dim]"
    )

    content_lines = []

    content_lines.append(f"[bold]Pattern:[/bold] {group.pattern_description}")
    content_lines.append("")

    for func in group.functions:
        vis = f" [dim]({func.visibility})[/dim]" if func.visibility != "public" else ""
        lang = f"[dim]{func.language}[/dim] " if hasattr(func, "language") else ""
        cls = f"{func.class_name}." if func.class_name else ""
        content_lines.append(f"  • {lang}{func.filepath}:{func.lineno}  → [cyan]{cls}{func.name}()[/cyan]{vis}")

    content_lines.append("")

    # For groups with many functions, "import directly" is impractical — suggest extracting
    if group.reuse_type == "cross_service_reference":
        content_lines.append(
            "[yellow bold]⚠ Cross-service:[/yellow bold]  "
            "These live in separate services. Direct import is NOT possible. "
            "Consider a shared library package or accept as intentional boundary duplication."
        )
    elif group.reuse_type in ("extract_utility", "same_file_refactor"):
        content_lines.append(f"[blue bold]Suggestion:[/blue bold]  {group.reuse_guidance}")
    elif len(group.functions) >= 3:
        # With 3+ copies, extracting to a shared module is better than importing from one
        common = _common_path_prefix([f.filepath for f in group.functions])
        location_hint = f" under {common}/" if common else ""
        names = sorted(set(f.name for f in group.functions))
        name_hint = f" ({', '.join(names[:3])}{'...' if len(names) > 3 else ''})"
        content_lines.append(
            f"[green bold]Suggestion:[/green bold]  Extract{name_hint} to a shared "
            f"utility module{location_hint} — {len(group.functions)} copies is too many to maintain."
        )
    elif group.reuse_guidance:
        content_lines.append(f"[green bold]Suggestion:[/green bold]  {group.reuse_guidance}")

    panel = Panel(
        "\n".join(content_lines),
        title=title,
        border_style=color,
        padding=(0, 1),
    )
    console.print(panel)


def _get_severity(item: FindingGroup | SimilarityMatch) -> str:
    """Get severity from a grouped finding or individual match."""
    return item.severity


def print_results(
    matches: list[SimilarityMatch],
    verbose: bool = False,
    show_diff: bool = False,
    compact: bool = False,
) -> None:
    """Print all matches in human-readable format.

    Groups related pairwise matches into single findings to avoid
    combinatorial explosion (N similar functions → 1 grouped finding,
    not C(N,2) individual findings).

    By default, only HIGH and MEDIUM findings are shown. Use --verbose
    to include LOW findings as well.
    """
    if not matches:
        console.print("[green bold]✓ No redundant code detected.[/green bold]")
        return

    high = sum(1 for m in matches if m.severity == "high")
    medium = sum(1 for m in matches if m.severity == "medium")
    low = sum(1 for m in matches if m.severity == "low")

    # Group related matches to reduce noise
    grouped = group_matches(matches)

    # Filter: only show HIGH + MEDIUM by default, LOW requires --verbose
    if verbose:
        visible = grouped
    else:
        visible = [item for item in grouped if _get_severity(item) != "low"]

    hidden_low = len(grouped) - len(visible)

    console.print()
    console.print(f"[bold]Echo Guard — {len(grouped)} findings[/bold] from {len(matches)} raw pairs")
    console.print(f"  [red bold]HIGH: {high}[/red bold]  [yellow]MEDIUM: {medium}[/yellow]  [dim]LOW: {low}[/dim]")

    if hidden_low > 0 and not verbose:
        console.print(f"  [dim]({hidden_low} LOW findings hidden — use --verbose to show)[/dim]")
    console.print()

    if compact:
        _print_compact(matches)
    else:
        for i, item in enumerate(visible, 1):
            if isinstance(item, FindingGroup):
                format_group_rich(item, i)
            else:
                format_match_rich(item, i, show_diff=show_diff)

    if verbose:
        console.print()
        _print_detail_table(matches)


def _print_compact(matches: list[SimilarityMatch]) -> None:
    """Print matches in a compact one-line-per-match format."""
    for match in matches:
        severity = match.severity
        color = SEVERITY_COLORS[severity]
        score = f"{match.similarity_score * 100:.0f}%"
        src = match.source_func
        ext = match.existing_func
        reuse_tag = ""
        if hasattr(match, "reuse_type"):
            if match.reuse_type == "reference_only":
                reuse_tag = " [yellow]⚠ cross-lang[/yellow]"
            elif match.reuse_type == "cross_service_reference":
                reuse_tag = " [yellow]⚠ cross-service[/yellow]"
            elif match.reuse_type == "same_file_refactor":
                reuse_tag = " [magenta]↻ same-file[/magenta]"
            elif match.reuse_type == "extract_utility":
                reuse_tag = " [blue]⚙ extract-utility[/blue]"
        console.print(
            f"  [{color}]{severity.upper():6s}[/{color}] {score:>4s}  "
            f"{src.filepath}:{src.lineno} {src.name}() → {ext.filepath}:{ext.lineno} {ext.name}(){reuse_tag}"
        )


def _print_detail_table(matches: list[SimilarityMatch]) -> None:
    """Print a detailed table view."""
    table = Table(title="Detailed Match Table")
    table.add_column("#", style="dim", width=4)
    table.add_column("Score", justify="right", width=6)
    table.add_column("Type", width=16)
    table.add_column("Lang", width=6)
    table.add_column("New Function", style="cyan")
    table.add_column("Existing Function", style="green")

    for i, match in enumerate(matches, 1):
        score = f"{match.similarity_score * 100:.0f}%"
        lang = getattr(match.source_func, "language", "?")
        table.add_row(
            str(i),
            score,
            match.match_type,
            lang,
            f"{match.source_func.name} ({match.source_func.filepath}:{match.source_func.lineno})",
            f"{match.existing_func.name} ({match.existing_func.filepath}:{match.existing_func.lineno})",
        )

    console.print(table)


def format_json(matches: list[SimilarityMatch]) -> str:
    """Format matches as JSON for machine consumption.

    Groups related matches into consolidated findings to reduce noise.
    """
    grouped = group_matches(matches)
    findings = []
    for item in grouped:
        if isinstance(item, FindingGroup):
            findings.append({
                "type": "group",
                "severity": item.severity,
                "similarity_score": round(item.similarity_score, 3),
                "pattern_description": item.pattern_description,
                "match_count": item.match_count,
                "reuse_type": item.reuse_type,
                "reuse_guidance": item.reuse_guidance,
                "functions": [
                    {
                        "name": f.name,
                        "filepath": f.filepath,
                        "language": getattr(f, "language", "unknown"),
                        "lineno": f.lineno,
                        "visibility": getattr(f, "visibility", "public"),
                        "class_name": getattr(f, "class_name", None),
                    }
                    for f in item.functions
                ],
            })
        else:
            findings.append({
                "type": "match",
                "severity": item.severity,
                "similarity_score": round(item.similarity_score, 3),
                "match_type": item.match_type,
                "reuse_type": getattr(item, "reuse_type", ""),
                "reuse_guidance": getattr(item, "reuse_guidance", ""),
                "source": {
                    "name": item.source_func.name,
                    "filepath": item.source_func.filepath,
                    "language": getattr(item.source_func, "language", "unknown"),
                    "lineno": item.source_func.lineno,
                    "visibility": getattr(item.source_func, "visibility", "public"),
                },
                "existing": {
                    "name": item.existing_func.name,
                    "filepath": item.existing_func.filepath,
                    "language": getattr(item.existing_func, "language", "unknown"),
                    "lineno": item.existing_func.lineno,
                    "visibility": getattr(item.existing_func, "visibility", "public"),
                    "import_suggestion": item.import_suggestion,
                },
            })
    return json.dumps({
        "findings": findings,
        "finding_count": len(findings),
        "raw_match_count": len(matches),
    }, indent=2)
