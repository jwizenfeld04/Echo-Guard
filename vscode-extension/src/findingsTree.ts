/**
 * Findings sidebar TreeView for Echo Guard.
 *
 * Tree structure:
 *   Overview (expanded)
 *     [red]   HIGH     21 groups
 *     [yellow] MEDIUM  33 pairs
 *     ─ Top targets
 *       [red fn] fetchJson()        13 copies
 *       ...
 *     ─ Hotspot files
 *       [file] route.ts             18 findings
 *       ...
 *   [red]    HIGH — 21 groups  (expanded)
 *     [red fn] fetchJson()  13 copies
 *       route.ts  :7
 *       ...
 *   [yellow] MEDIUM — 33 pairs  (collapsed)
 *     [yellow fn] parsePayload()  2 copies
 *       ...
 */

import * as path from "path";
import * as vscode from "vscode";
import type { Finding } from "./daemon";

// ── Colors ───────────────────────────────────────────────────────────────

const RED = new vscode.ThemeColor("errorForeground");
const YELLOW = new vscode.ThemeColor("terminal.ansiYellow");
const MUTED = new vscode.ThemeColor("descriptionForeground");

// ── Internal data model ──────────────────────────────────────────────────

interface Cluster {
  functionName: string;
  severity: "extract" | "review";
  locations: Array<{ filepath: string; lineno: number }>;
  /** Finding ID of one representative finding — used for group dismiss. */
  findingId: string;
  fileUri: vscode.Uri;
}

function _buildClusters(
  findings: Finding[],
  repoRoot: string
): { extract: Cluster[]; review: Cluster[] } {
  // Sidebar always shows EXTRACT + REVIEW regardless of minSeverity diagnostic filter
  const visible = findings.filter((f) => f.reuse_type !== "reference_only");

  // EXTRACT: group by existing.name (the representative function), union locations
  const extractMap = new Map<
    string,
    { locs: Map<string, { filepath: string; lineno: number }>; findingId: string; fileUri: vscode.Uri }
  >();

  const reviewClusters: Cluster[] = [];

  for (const f of visible) {
    const absSource = path.isAbsolute(f.source.filepath)
      ? f.source.filepath
      : path.join(repoRoot, f.source.filepath);

    if (f.severity === "extract") {
      const key = f.existing.name;
      if (!extractMap.has(key)) {
        const absExisting = path.isAbsolute(f.existing.filepath)
          ? f.existing.filepath
          : path.join(repoRoot, f.existing.filepath);
        extractMap.set(key, {
          locs: new Map(),
          findingId: f.finding_id,
          fileUri: vscode.Uri.file(absExisting),
        });
      }
      const entry = extractMap.get(key)!;
      for (const side of [f.source, f.existing]) {
        const k = `${side.filepath}:${side.lineno}`;
        if (!entry.locs.has(k)) {
          entry.locs.set(k, { filepath: side.filepath, lineno: side.lineno });
        }
      }
    } else if (f.severity === "review") {
      const displayName =
        f.source.name === f.existing.name
          ? f.source.name
          : `${f.source.name} → ${f.existing.name}`;
      reviewClusters.push({
        functionName: displayName,
        severity: "review",
        locations: [
          { filepath: f.source.filepath, lineno: f.source.lineno },
          { filepath: f.existing.filepath, lineno: f.existing.lineno },
        ],
        findingId: f.finding_id,
        fileUri: vscode.Uri.file(absSource),
      });
    }
  }

  const extract: Cluster[] = [...extractMap.entries()]
    .map(([name, entry]) => ({
      functionName: name,
      severity: "extract" as const,
      locations: [...entry.locs.values()],
      findingId: entry.findingId,
      fileUri: entry.fileUri,
    }))
    .sort((a, b) => b.locations.length - a.locations.length);

  return { extract, review: reviewClusters };
}

// ── Tree item classes ────────────────────────────────────────────────────

export type EchoGuardTreeItem =
  | OverviewGroupItem
  | OverviewStatItem
  | SeverityGroupItem
  | ClusterItem
  | LocationItem;

/** Root "Overview" node — shows counts + top targets + hotspot files. */
export class OverviewGroupItem extends vscode.TreeItem {
  readonly kind = "overview" as const;
  private _statItems: OverviewStatItem[];

  constructor(extract: Cluster[], review: Cluster[]) {
    super("Overview", vscode.TreeItemCollapsibleState.Expanded);
    this.iconPath = new vscode.ThemeIcon("pulse");
    this.contextValue = "echoGuardOverview";

    const items: OverviewStatItem[] = [];

    if (extract.length === 0 && review.length === 0) {
      items.push(new OverviewStatItem("No findings", undefined, "pass", MUTED));
    } else {
      // Top refactoring targets — EXTRACT clusters by copy count
      if (extract.length > 0) {
        items.push(new OverviewStatItem("Top targets", undefined, "list-ordered", MUTED, true));
        for (const c of extract.slice(0, 5)) {
          items.push(
            new OverviewStatItem(
              `${c.functionName}()`,
              `${c.locations.length} copies`,
              "symbol-function",
              RED,
              false,
              c.functionName
            )
          );
        }
      }

    }

    this._statItems = items;
  }

  getStatItems(): OverviewStatItem[] {
    return this._statItems;
  }
}

export class OverviewStatItem extends vscode.TreeItem {
  readonly kind = "stat" as const;
  readonly clusterFunctionName: string | undefined;

  constructor(
    label: string,
    description?: string,
    iconName?: string,
    iconColor?: vscode.ThemeColor,
    isHeader = false,
    clusterFunctionName?: string
  ) {
    super(label, vscode.TreeItemCollapsibleState.None);
    this.description = description;
    this.contextValue = "echoGuardStat";
    this.clusterFunctionName = clusterFunctionName;
    if (iconName) {
      this.iconPath = new vscode.ThemeIcon(iconName, iconColor);
    }
    if (isHeader) {
      this.tooltip = label;
    }
    if (clusterFunctionName) {
      this.command = {
        title: "Reveal in findings",
        command: "echoGuard.tree.revealCluster",
        arguments: [clusterFunctionName],
      };
      this.tooltip = `Click to reveal ${clusterFunctionName}() in the findings tree`;
    }
  }
}

export class SeverityGroupItem extends vscode.TreeItem {
  readonly kind = "severityGroup" as const;
  constructor(
    public readonly severity: "extract" | "review",
    count: number
  ) {
    const label =
      severity === "extract"
        ? `Extract — ${count} group${count === 1 ? "" : "s"}`
        : `Review — ${count} pair${count === 1 ? "" : "s"}`;
    super(
      label,
      severity === "extract"
        ? vscode.TreeItemCollapsibleState.Expanded
        : vscode.TreeItemCollapsibleState.Collapsed
    );
    this.iconPath =
      severity === "extract"
        ? new vscode.ThemeIcon("circle-filled", RED)
        : new vscode.ThemeIcon("circle-filled", YELLOW);
    this.contextValue = "echoGuardSeverityGroup";
  }
}

export class ClusterItem extends vscode.TreeItem {
  readonly kind = "cluster" as const;
  readonly findingId: string;
  readonly functionName: string;
  readonly fileUri: vscode.Uri;

  constructor(cluster: Cluster) {
    super(
      `${cluster.functionName}()`,
      vscode.TreeItemCollapsibleState.Collapsed
    );
    this.description = `${cluster.locations.length} copies`;
    // Color the function icon by severity
    this.iconPath = new vscode.ThemeIcon(
      "symbol-function",
      cluster.severity === "extract" ? RED : YELLOW
    );
    this.contextValue = "echoGuardCluster";
    this.findingId = cluster.findingId;
    this.functionName = cluster.functionName;
    this.fileUri = cluster.fileUri;
    this.tooltip = `${cluster.functionName}() — ${cluster.locations.length} copies across the codebase`;
  }
}

export class LocationItem extends vscode.TreeItem {
  readonly kind = "location" as const;

  constructor(
    loc: { filepath: string; lineno: number },
    repoRoot: string
  ) {
    const basename = path.basename(loc.filepath);
    super(basename, vscode.TreeItemCollapsibleState.None);
    // Show parent dir as description for context
    const rel = path.isAbsolute(loc.filepath)
      ? path.relative(repoRoot, loc.filepath)
      : loc.filepath;
    const dir = path.dirname(rel);
    this.description = `${dir !== "." ? dir + "  " : ""}:${loc.lineno}`;
    this.iconPath = new vscode.ThemeIcon("go-to-file");
    this.contextValue = "echoGuardLocation";
    this.tooltip = `${loc.filepath}:${loc.lineno}`;

    const absPath = path.isAbsolute(loc.filepath)
      ? loc.filepath
      : path.join(repoRoot, loc.filepath);
    const line = Math.max(0, loc.lineno - 1);
    this.command = {
      title: "Go to location",
      command: "vscode.open",
      arguments: [
        vscode.Uri.file(absPath),
        { selection: new vscode.Range(line, 0, line, 0) },
      ],
    };
  }
}

// ── Tree provider ────────────────────────────────────────────────────────

export class EchoGuardFindingsTreeProvider
  implements vscode.TreeDataProvider<EchoGuardTreeItem>
{
  private _onDidChangeTreeData = new vscode.EventEmitter<
    EchoGuardTreeItem | undefined | void
  >();
  readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

  private _findings: Finding[] = [];
  private _extractClusters: Cluster[] = [];
  private _reviewClusters: Cluster[] = [];

  // Cached tree items so reveal() gets stable references
  private _overviewItem: OverviewGroupItem | undefined;
  private _extractGroupItem: SeverityGroupItem | undefined;
  private _reviewGroupItem: SeverityGroupItem | undefined;
  private _extractClusterItems: ClusterItem[] = [];
  private _reviewClusterItems: ClusterItem[] = [];
  private _locationItems = new Map<string, LocationItem[]>();

  constructor(private readonly repoRoot: string) {}

  refresh(findings: Finding[]): void {
    this._findings = findings;
    const { extract, review } = _buildClusters(findings, this.repoRoot);
    this._extractClusters = extract;
    this._reviewClusters = review;

    this._overviewItem = new OverviewGroupItem(extract, review);
    this._extractGroupItem = extract.length > 0 ? new SeverityGroupItem("extract", extract.length) : undefined;
    this._reviewGroupItem = review.length > 0 ? new SeverityGroupItem("review", review.length) : undefined;
    this._extractClusterItems = extract.map((c) => new ClusterItem(c));
    this._reviewClusterItems = review.map((c) => new ClusterItem(c));
    this._locationItems.clear();

    this._onDidChangeTreeData.fire();
  }

  /** Remove a resolved finding and refresh. */
  removeFinding(findingId: string): void {
    this._findings = this._findings.filter(
      (f) => f.finding_id !== findingId
    );
    this.refresh(this._findings);
  }

  /** Find the ClusterItem for a given function name (used by revealCluster command). */
  findClusterByFunctionName(functionName: string): ClusterItem | undefined {
    return (
      this._extractClusterItems.find((c) => c.functionName === functionName) ??
      this._reviewClusterItems.find((c) => c.functionName === functionName)
    );
  }

  getTreeItem(element: EchoGuardTreeItem): vscode.TreeItem {
    return element;
  }

  getParent(element: EchoGuardTreeItem): vscode.ProviderResult<EchoGuardTreeItem> {
    if (element instanceof OverviewGroupItem || element instanceof SeverityGroupItem) {
      return undefined;
    }
    if (element instanceof OverviewStatItem) {
      return this._overviewItem;
    }
    if (element instanceof ClusterItem) {
      if (this._extractClusterItems.includes(element)) return this._extractGroupItem;
      if (this._reviewClusterItems.includes(element)) return this._reviewGroupItem;
      return undefined;
    }
    if (element instanceof LocationItem) {
      for (const [findingId, locs] of this._locationItems) {
        if (locs.includes(element)) {
          return (
            this._extractClusterItems.find((c) => c.findingId === findingId) ??
            this._reviewClusterItems.find((c) => c.findingId === findingId)
          );
        }
      }
      return undefined;
    }
    return undefined;
  }

  getChildren(
    element?: EchoGuardTreeItem
  ): vscode.ProviderResult<EchoGuardTreeItem[]> {
    if (!element) {
      if (!this._overviewItem) return [];
      const items: EchoGuardTreeItem[] = [this._overviewItem];
      if (this._extractGroupItem) items.push(this._extractGroupItem);
      if (this._reviewGroupItem) items.push(this._reviewGroupItem);
      return items;
    }

    if (element instanceof OverviewGroupItem) {
      return element.getStatItems();
    }

    if (element instanceof SeverityGroupItem) {
      return element.severity === "extract"
        ? this._extractClusterItems
        : this._reviewClusterItems;
    }

    if (element instanceof ClusterItem) {
      if (!this._locationItems.has(element.findingId)) {
        const cluster =
          this._extractClusters.find((c) => c.findingId === element.findingId) ??
          this._reviewClusters.find((c) => c.findingId === element.findingId);
        const locs = cluster?.locations.map((loc) => new LocationItem(loc, this.repoRoot)) ?? [];
        this._locationItems.set(element.findingId, locs);
      }
      return this._locationItems.get(element.findingId) ?? [];
    }

    return [];
  }
}
