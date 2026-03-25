/**
 * Diagnostics — maps Echo Guard findings to VS Code diagnostic squiggles.
 *
 * Trigger: onDidSaveTextDocument (debounced 1500ms).
 * Each save sends a check_file RPC to the daemon and updates the diagnostic
 * collection for that file only. Findings for other files are not disturbed.
 */

import * as path from "path";
import * as vscode from "vscode";
import type { DaemonClient, Finding } from "./daemon";

// Severity→DiagnosticSeverity mapping
const SEVERITY_MAP: Record<string, vscode.DiagnosticSeverity> = {
  high: vscode.DiagnosticSeverity.Error,
  medium: vscode.DiagnosticSeverity.Warning,
  low: vscode.DiagnosticSeverity.Information,
};

export class EchoGuardDiagnostics {
  private collection: vscode.DiagnosticCollection;
  private daemon: DaemonClient;
  private repoRoot: string;
  private debounceMs: number;
  private debounceTimers = new Map<string, NodeJS.Timeout>();
  private disposables: vscode.Disposable[] = [];

  constructor(daemon: DaemonClient, repoRoot: string) {
    this.daemon = daemon;
    this.repoRoot = repoRoot;
    this.collection = vscode.languages.createDiagnosticCollection("echo-guard");
    this.debounceMs = vscode.workspace
      .getConfiguration("echoGuard")
      .get<number>("debounceMs") ?? 1500;
  }

  /** Start listening for file saves. */
  activate(context: vscode.ExtensionContext): void {
    const saveListener = vscode.workspace.onDidSaveTextDocument((doc) => {
      this._onFileSaved(doc);
    });
    this.disposables.push(saveListener);
    context.subscriptions.push(this.collection, saveListener);
  }

  /** Populate diagnostics for all currently open files (called after initial scan). */
  async populateFromScan(findings: Finding[]): Promise<void> {
    // Group findings by source file
    const byFile = new Map<string, Finding[]>();
    for (const f of findings) {
      const abs = this._absPath(f.source.filepath);
      const group = byFile.get(abs) ?? [];
      group.push(f);
      byFile.set(abs, group);
    }

    // Clear all existing diagnostics and set fresh ones
    this.collection.clear();
    for (const [absPath, filefindings] of byFile) {
      const uri = vscode.Uri.file(absPath);
      this.collection.set(uri, filefindings.map((f) => this._toDiagnostic(f)));
    }
  }

  /** Update diagnostics for a specific file. */
  setFileFindings(absPath: string, findings: Finding[]): void {
    const uri = vscode.Uri.file(absPath);
    if (findings.length === 0) {
      this.collection.delete(uri);
    } else {
      this.collection.set(uri, findings.map((f) => this._toDiagnostic(f)));
    }
  }

  /** Clear diagnostics for a file (e.g. when it's deleted). */
  clearFile(absPath: string): void {
    this.collection.delete(vscode.Uri.file(absPath));
  }

  /** Remove a single finding (by ID) from diagnostics across all files. */
  clearFindingById(findingId: string): void {
    const updates: Array<[vscode.Uri, vscode.Diagnostic[]]> = [];
    this.collection.forEach((uri, diags) => {
      const filtered = diags.filter((d) => d.code !== findingId);
      if (filtered.length !== diags.length) {
        updates.push([uri, filtered]);
      }
    });
    for (const [uri, diags] of updates) {
      if (diags.length === 0) {
        this.collection.delete(uri);
      } else {
        this.collection.set(uri, diags);
      }
    }
  }

  /** Get total finding count across all files. */
  get totalFindings(): number {
    let count = 0;
    this.collection.forEach((_, diags) => (count += diags.length));
    return count;
  }

  dispose(): void {
    this.collection.dispose();
    this.disposables.forEach((d) => d.dispose());
    for (const t of this.debounceTimers.values()) clearTimeout(t);
  }

  // ── Private ─────────────────────────────────────────────────────────

  private _onFileSaved(doc: vscode.TextDocument): void {
    // Only check files inside the workspace/repo
    if (!doc.uri.fsPath.startsWith(this.repoRoot)) return;
    // Skip unsupported URIs (e.g. git:// scheme)
    if (doc.uri.scheme !== "file") return;

    const absPath = doc.uri.fsPath;

    // Debounce: reset timer on each save
    const existing = this.debounceTimers.get(absPath);
    if (existing) clearTimeout(existing);

    const timer = setTimeout(() => {
      this.debounceTimers.delete(absPath);
      this._checkFile(absPath).catch((err) => {
        console.error("[Echo Guard] check_file error:", err);
      });
    }, this.debounceMs);

    this.debounceTimers.set(absPath, timer);
  }

  private async _checkFile(absPath: string): Promise<void> {
    if (!this.daemon.isRunning) return;

    // Convert absolute path to relative for the daemon
    const relPath = path.relative(this.repoRoot, absPath);
    const result = await this.daemon.checkFiles([relPath]);

    // Update diagnostics for this file
    const findings = result.findings[relPath] ?? [];
    this.setFileFindings(absPath, findings);
  }

  private _toDiagnostic(finding: Finding): vscode.Diagnostic {
    const showLow = vscode.workspace
      .getConfiguration("echoGuard")
      .get<boolean>("showLowSeverity") ?? false;

    if (finding.severity === "low" && !showLow) {
      // Return an information diagnostic but it won't surface without the setting
    }

    const severity =
      SEVERITY_MAP[finding.severity] ?? vscode.DiagnosticSeverity.Warning;

    // Point the squiggle at the function start line (lineno is 1-indexed)
    const line = Math.max(0, finding.source.lineno - 1);
    const range = new vscode.Range(line, 0, line, 999);

    const crossSvcTag = finding.cross_service ? " [cross-service]" : "";
    const score = `${Math.round(finding.similarity * 100)}%`;
    const existingRef = `${finding.existing.filepath}:${finding.existing.lineno}`;

    const message =
      `${finding.clone_type_label}${crossSvcTag} — ` +
      `${finding.existing.name}() in ${existingRef} (${score} similar)`;

    const diagnostic = new vscode.Diagnostic(range, message, severity);
    diagnostic.source = "echo-guard";
    diagnostic.code = finding.finding_id;

    // Related information points to the duplicate
    const existingUri = vscode.Uri.file(
      this._absPath(finding.existing.filepath)
    );
    const existingLine = Math.max(0, finding.existing.lineno - 1);
    diagnostic.relatedInformation = [
      new vscode.DiagnosticRelatedInformation(
        new vscode.Location(existingUri, new vscode.Range(existingLine, 0, existingLine, 0)),
        `Duplicate: ${finding.existing.name}()`
      ),
    ];

    return diagnostic;
  }

  private _absPath(relOrAbs: string): string {
    if (path.isAbsolute(relOrAbs)) return relOrAbs;
    return path.join(this.repoRoot, relOrAbs);
  }
}
