"""Output formatting for Echo Guard results."""

from __future__ import annotations

import json
from collections import Counter, defaultdict

from rich.console import Console
from rich.table import Table

from echo_guard.similarity import (
    FindingGroup,
    SimilarityMatch,
    group_matches,
    _common_path_prefix,
)

console = Console()

SEVERITY_COLORS = {
    "high": "red",
    "medium": "yellow",
    "low": "blue",
}

SEVERITY_ICONS = {
    "high": "[red bold]●[/red bold]",
    "medium": "[yellow]●[/yellow]",
    "low": "[blue dim]●[/blue dim]",
}

CLONE_TYPE_LABELS = {
    "type1_type2": "T1/T2 Exact",
    "type3": "T3 Modified",
    "type4": "T4 Semantic",
}


# ── Helpers ────────────────────────────────────────────────────────────


def _short_path(filepath: str, min_segments: int = 4) -> str:
    """Shorten a filepath for display, keeping at least min_segments components.

    Never shows path segments above the repo root (no /Users/... leaking).
    """
    parts = filepath.replace("\\", "/").split("/")
    if len(parts) <= min_segments:
        return filepath
    return "/".join(parts[-min_segments:])


def _func_count(item: FindingGroup | SimilarityMatch) -> int:
    """Get the number of unique functions in a finding."""
    if isinstance(item, FindingGroup):
        return len(item.functions)
    return 2


def _categorize_findings(
    findings: list[FindingGroup | SimilarityMatch],
) -> dict[str, list[tuple[int, FindingGroup | SimilarityMatch]]]:
    """Categorize findings into action-based sections."""
    sections: dict[str, list[tuple[int, FindingGroup | SimilarityMatch]]] = {
        "high": [],
        "medium": [],
        "cross_service": [],
        "cross_language": [],
        "low": [],
    }

    for i, item in enumerate(findings, 1):
        reuse = ""
        if isinstance(item, FindingGroup):
            reuse = item.reuse_type
        else:
            reuse = getattr(item, "reuse_type", "")

        if reuse == "cross_service_reference":
            # Check if it's cross-language
            if isinstance(item, SimilarityMatch):
                if item.source_func.language != item.existing_func.language:
                    sections["cross_language"].append((i, item))
                    continue
            sections["cross_service"].append((i, item))
        elif reuse == "reference_only":
            sections["cross_language"].append((i, item))
        elif item.severity == "high":
            sections["high"].append((i, item))
        elif item.severity == "medium":
            sections["medium"].append((i, item))
        else:
            sections["low"].append((i, item))

    return sections


# ── Summary block ──────────────────────────────────────────────────────


def _print_summary(grouped: list[FindingGroup | SimilarityMatch]) -> None:
    """Print the top refactoring targets and hotspot files."""
    # Count copies per function name
    name_copies: Counter = Counter()
    name_files: dict[str, set[str]] = defaultdict(set)
    file_dups: dict[str, list[str]] = defaultdict(list)

    for item in grouped:
        if isinstance(item, FindingGroup):
            for func in item.functions:
                name_copies[func.name] += 1
                name_files[func.name].add(_short_path(func.filepath))
        else:
            for func in [item.source_func, item.existing_func]:
                name_copies[func.name] += 1
                name_files[func.name].add(_short_path(func.filepath))

    # Top refactoring targets (by copy count, min 3)
    top_targets = [
        (name, count) for name, count in name_copies.most_common(8) if count >= 3
    ]

    if top_targets:
        console.print("  [bold]Top refactoring targets:[/bold]")
        for name, count in top_targets:
            console.print(f"    [cyan]{name}()[/cyan]  [dim]—[/dim]  {count} copies")
        console.print()

    # Hotspot files (files that source the most duplicated functions)
    source_counts: Counter = Counter()
    for item in grouped:
        if isinstance(item, FindingGroup):
            for func in item.functions:
                source_counts[_short_path(func.filepath)] += 1
        else:
            source_counts[_short_path(item.source_func.filepath)] += 1
            source_counts[_short_path(item.existing_func.filepath)] += 1

    top_files = source_counts.most_common(5)
    if top_files and top_files[0][1] >= 3:
        console.print("  [bold]Hotspot files:[/bold]")
        for filepath, count in top_files:
            if count >= 2:
                console.print(
                    f"    [cyan]{filepath}[/cyan]  [dim]—[/dim]  {count} duplicated functions"
                )
        console.print()


# ── Finding formatters ─────────────────────────────────────────────────


def _format_finding_compact(
    index: int,
    item: FindingGroup | SimilarityMatch,
) -> None:
    """Print a single finding in compact format."""
    severity = item.severity
    color = SEVERITY_COLORS.get(severity, "yellow")
    icon = SEVERITY_ICONS.get(severity, "●")
    clone_label = CLONE_TYPE_LABELS.get(item.clone_type, item.clone_type)

    if isinstance(item, FindingGroup):
        names = sorted(set(f.name for f in item.functions))
        count = len(item.functions)
        name_display = (
            names[0] if len(names) == 1 else f"{names[0]} + {len(names) - 1} related"
        )

        console.print(
            f"  {icon} [bold]#{index}[/bold]  [{color}]{clone_label}[/{color}] — [cyan]{name_display}()[/cyan] x{count}"
        )
        console.print()

        for func in item.functions:
            vis = (
                f" [dim]({func.visibility})[/dim]"
                if func.visibility != "public"
                else ""
            )
            console.print(
                f"       {_short_path(func.filepath)}:{func.lineno}  [cyan]{func.name}()[/cyan]{vis}"
            )

        # Suggestion
        if item.reuse_type == "cross_service_reference":
            console.print()
            console.print(
                f"       [yellow]⚠ Cross-service — direct import NOT possible[/yellow]"
            )
        elif count >= 3:
            common = _common_path_prefix([f.filepath for f in item.functions])
            hint = f" under {common}/" if common else ""
            console.print()
            console.print(f"       [green]→ Extract to shared module{hint}[/green]")

    else:
        src = item.source_func
        ext = item.existing_func
        score_pct = f"{item.similarity_score * 100:.0f}%"
        reuse = getattr(item, "reuse_type", "")

        if src.name == ext.name:
            console.print(
                f"  {icon} [bold]#{index}[/bold]  [{color}]{clone_label}[/{color}] — [cyan]{src.name}()[/cyan]  ({score_pct})"
            )
        else:
            console.print(
                f"  {icon} [bold]#{index}[/bold]  [{color}]{clone_label}[/{color}] — [cyan]{src.name}()[/cyan] ↔ [cyan]{ext.name}()[/cyan]  ({score_pct})"
            )

        console.print()

        if reuse == "cross_service_reference":
            console.print(
                f"       {_short_path(src.filepath)}:{src.lineno}  [yellow]↔[/yellow]  {_short_path(ext.filepath)}:{ext.lineno}"
            )
            console.print(
                f"       [yellow]⚠ Cross-service — direct import NOT possible[/yellow]"
            )
        elif src.filepath == ext.filepath:
            console.print(
                f"       {_short_path(src.filepath)}  [dim](same file, lines {src.lineno} and {ext.lineno})[/dim]"
            )
        else:
            console.print(f"       {_short_path(src.filepath)}:{src.lineno}")
            console.print(f"       {_short_path(ext.filepath)}:{ext.lineno}")
            common = _common_path_prefix([src.filepath, ext.filepath])
            hint = f" under {common}/" if common else ""
            console.print(f"       [green]→ Extract to shared utility{hint}[/green]")

    console.print()


# ── Section printer ───────────────────────────────────────────────────


def _print_section(
    title: str,
    subtitle: str,
    color: str,
    findings: list[tuple[int, FindingGroup | SimilarityMatch]],
    show_diff: bool = False,
) -> None:
    """Print a titled section of findings."""
    if not findings:
        return

    console.print()
    console.print(f"  [{color} bold]━━━ {title} ({len(findings)}) ━━━[/{color} bold]")
    console.print(f"  [dim]{subtitle}[/dim]")
    console.print()

    # Sort by copy count descending, then by score
    findings.sort(
        key=lambda x: (_func_count(x[1]), x[1].similarity_score), reverse=True
    )

    # Renumber sequentially within section
    for section_num, (_orig_index, item) in enumerate(findings, 1):
        _format_finding_compact(section_num, item)


# ── Main entry points ─────────────────────────────────────────────────


def print_results(
    matches: list[SimilarityMatch],
    verbose: bool = False,
    show_diff: bool = False,
    compact: bool = False,
) -> None:
    """Print all matches grouped by action type.

    Sections:
    - HIGH: 3+ copies — extract to shared module (red)
    - MEDIUM: 2 exact copies — worth noting (yellow)
    - CROSS-SERVICE: Architectural decision needed (cyan)
    - LOW: Hidden by default, shown with --verbose (blue)
    """
    if not matches:
        console.print("[green bold]✓ No redundant code detected.[/green bold]")
        return

    grouped = group_matches(matches)

    # Count severities
    high = sum(1 for item in grouped if item.severity == "high")
    medium = sum(1 for item in grouped if item.severity == "medium")
    low = sum(1 for item in grouped if item.severity == "low")

    # Separate cross-service from severity counts for display
    cross_svc = sum(
        1
        for item in grouped
        if (
            isinstance(item, FindingGroup)
            and item.reuse_type == "cross_service_reference"
        )
        or (
            isinstance(item, SimilarityMatch)
            and getattr(item, "reuse_type", "") == "cross_service_reference"
        )
    )

    # Categorize into sections
    sections = _categorize_findings(grouped)

    # Hide LOW by default
    if not verbose:
        low_hidden = len(sections["low"])
        sections["low"] = []
    else:
        low_hidden = 0

    visible_count = sum(len(v) for v in sections.values())

    # ── Header ──
    console.print()
    console.print("[bold]Echo Guard — Scan Results[/bold]")
    console.print()
    parts = []
    if high:
        parts.append(f"[red bold]{high} HIGH[/red bold]")
    if medium:
        parts.append(f"[yellow]{medium} MEDIUM[/yellow]")
    if low:
        parts.append(f"[blue dim]{low} LOW[/blue dim]")
    console.print(f"  {' · '.join(parts)}  [dim]({len(matches)} raw pairs)[/dim]")
    if low_hidden:
        console.print(
            f"  [dim]{low_hidden} LOW findings hidden — use --verbose to show[/dim]"
        )
    console.print()

    if compact:
        _print_compact(grouped)
        return

    # ── Summary ──
    _print_summary(grouped)

    # ── Sections ──
    _print_section(
        "EXTRACT NOW",
        "3+ copies — real DRY violations",
        "red",
        sections["high"],
        show_diff,
    )

    _print_section(
        "WORTH NOTING",
        "2 exact copies — fix if complex, defer per Rule of Three",
        "yellow",
        sections["medium"],
        show_diff,
    )

    _print_section(
        "CROSS-SERVICE",
        "Same language, different services — consider shared library",
        "cyan",
        sections["cross_service"],
        show_diff,
    )

    _print_section(
        "CROSS-LANGUAGE",
        "Same logic in different languages — must change together",
        "magenta",
        sections["cross_language"],
        show_diff,
    )

    if sections["low"]:
        _print_section(
            "LOW CONFIDENCE",
            "Semantic matches — review for relevance",
            "blue",
            sections["low"],
            show_diff,
        )

    # ── Detail table (verbose only) ──
    if verbose:
        console.print()
        _print_detail_table(matches)


def _print_compact(items: list[FindingGroup | SimilarityMatch]) -> None:
    """Print findings in a compact one-line-per-finding format."""
    for item in items:
        severity = item.severity
        color = SEVERITY_COLORS.get(severity, "yellow")
        score = f"{item.similarity_score * 100:.0f}%"
        reuse = getattr(item, "reuse_type", "")
        reuse_tag = ""
        if reuse == "cross_service_reference":
            reuse_tag = " [cyan]⚠ cross-service[/cyan]"
        elif reuse == "same_file_refactor":
            reuse_tag = " [dim]↻ same-file[/dim]"

        if isinstance(item, FindingGroup):
            names = ", ".join(f.name + "()" for f in item.functions[:3])
            if len(item.functions) > 3:
                names += f", +{len(item.functions) - 3}"
            console.print(
                f"  [{color}]{severity.upper():6s}[/{color}] {'group':12s} {score:>4s}  "
                f"{len(item.functions)} funcs: {names}{reuse_tag}"
            )
        else:
            clone_label = CLONE_TYPE_LABELS.get(item.clone_type, "?")
            src = item.source_func
            ext = item.existing_func
            console.print(
                f"  [{color}]{severity.upper():6s}[/{color}] {clone_label:12s} {score:>4s}  "
                f"{src.filepath}:{src.lineno} {src.name}() → {ext.filepath}:{ext.lineno} {ext.name}(){reuse_tag}"
            )


def _print_detail_table(matches: list[SimilarityMatch]) -> None:
    """Print a detailed table view."""
    table = Table(title="Detailed Match Table")
    table.add_column("#", style="dim", width=4)
    table.add_column("Sev", width=6)
    table.add_column("Clone Type", width=14)
    table.add_column("Score", justify="right", width=6)
    table.add_column("Lang", width=6)
    table.add_column("New Function", style="cyan")
    table.add_column("Existing Function", style="green")

    for i, match in enumerate(matches, 1):
        score = f"{match.similarity_score * 100:.0f}%"
        lang = getattr(match.source_func, "language", "?")
        sev_color = SEVERITY_COLORS.get(match.severity, "yellow")
        clone_label = CLONE_TYPE_LABELS.get(match.clone_type, match.clone_type)
        table.add_row(
            str(i),
            f"[{sev_color}]{match.severity.upper()}[/{sev_color}]",
            clone_label,
            score,
            lang,
            f"{match.source_func.name} ({match.source_func.filepath}:{match.source_func.lineno})",
            f"{match.existing_func.name} ({match.existing_func.filepath}:{match.existing_func.lineno})",
        )

    console.print(table)


# ── JSON output ───────────────────────────────────────────────────────


def format_json(matches: list[SimilarityMatch]) -> str:
    """Format matches as JSON for machine consumption."""
    grouped = group_matches(matches)
    findings = []
    for item in grouped:
        if isinstance(item, FindingGroup):
            findings.append(
                {
                    "type": "group",
                    "clone_type": item.clone_type,
                    "clone_type_label": item.clone_type_label,
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
                }
            )
        else:
            from echo_guard.index import FunctionIndex

            finding_id = FunctionIndex.make_finding_id(
                item.source_func.filepath,
                item.source_func.name,
                item.existing_func.filepath,
                item.existing_func.name,
                source_lineno=item.source_func.lineno,
                existing_lineno=item.existing_func.lineno,
            )
            findings.append(
                {
                    "type": "match",
                    "finding_id": finding_id,
                    "clone_type": item.clone_type,
                    "clone_type_label": item.clone_type_label,
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
                        "visibility": getattr(
                            item.existing_func, "visibility", "public"
                        ),
                        "import_suggestion": item.import_suggestion,
                    },
                }
            )
    return json.dumps(
        {
            "findings": findings,
            "finding_count": len(findings),
            "raw_match_count": len(matches),
        },
        indent=2,
    )
