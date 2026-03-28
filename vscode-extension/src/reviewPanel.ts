/**
 * "Review All Findings" webview panel.
 *
 * Displays all current findings with:
 *   - Severity badge, clone type, similarity score
 *   - Source and existing function locations with file links
 *   - Buttons: Mark Intentional | Dismiss | Go to Code
 *
 * Messages from webview → extension:
 *   { command: "resolve", findingId, verdict }
 *   { command: "goToCode", filepath, lineno }
 */

import * as path from "path";
import * as vscode from "vscode";
import type { DaemonClient, Finding } from "./daemon";

function _severityOrder(s: string): number {
  return s === "extract" ? 3 : s === "review" ? 2 : 1;
}

function _filterBySeverity(findings: Finding[]): Finding[] {
  const minSev =
    vscode.workspace.getConfiguration("echoGuard").get<string>("minSeverity") ?? "extract";
  const minOrder = _severityOrder(minSev);
  return findings.filter((f) => _severityOrder(f.severity) >= minOrder);
}

export class EchoGuardReviewPanel {
  static readonly viewType = "echoGuard.reviewPanel";
  private static instance: EchoGuardReviewPanel | undefined;

  private readonly panel: vscode.WebviewPanel;
  private findings: Finding[] = [];
  private disposables: vscode.Disposable[] = [];

  private constructor(
    private readonly daemon: DaemonClient,
    private readonly repoRoot: string,
    private readonly onFindingResolved: (findingId: string) => void,
    extensionUri: vscode.Uri
  ) {
    this.panel = vscode.window.createWebviewPanel(
      EchoGuardReviewPanel.viewType,
      "Echo Guard — Review Findings",
      vscode.ViewColumn.One,
      {
        enableScripts: true,
        retainContextWhenHidden: true,
      }
    );

    this.panel.onDidDispose(() => this.dispose(), null, this.disposables);

    this.panel.webview.onDidReceiveMessage(
      (msg) => this._handleMessage(msg),
      null,
      this.disposables
    );
  }

  /** Open or reveal the panel, then populate with fresh findings. */
  static async show(
    daemon: DaemonClient,
    repoRoot: string,
    onFindingResolved: (findingId: string) => void,
    extensionUri: vscode.Uri
  ): Promise<void> {
    if (EchoGuardReviewPanel.instance) {
      EchoGuardReviewPanel.instance.panel.reveal(vscode.ViewColumn.One);
      await EchoGuardReviewPanel.instance._refresh();
      return;
    }

    const panel = new EchoGuardReviewPanel(daemon, repoRoot, onFindingResolved, extensionUri);
    EchoGuardReviewPanel.instance = panel;
    await panel._refresh();
  }

  dispose(): void {
    EchoGuardReviewPanel.instance = undefined;
    this.panel.dispose();
    this.disposables.forEach((d) => d.dispose());
  }

  // ── Private ────────────────────────────────────────────────────────

  private async _refresh(): Promise<void> {
    try {
      const result = await this.daemon.getFindings();
      const all = result.findings as unknown as Finding[];
      const filtered = _filterBySeverity(all);
      this.findings = filtered;
      this.panel.webview.html = this._buildHtml(filtered);
    } catch (err) {
      this.panel.webview.html = this._buildErrorHtml(`${err}`);
    }
  }

  private async _handleMessage(msg: { command: string; findingId?: string; verdict?: string; filepath?: string; lineno?: number }): Promise<void> {
    if (msg.command === "resolve" && msg.findingId && msg.verdict) {
      try {
        await this.daemon.resolvefinding(
          msg.findingId,
          msg.verdict as "resolved" | "intentional" | "dismissed"
        );
        this.onFindingResolved(msg.findingId);
        await this._refresh();
      } catch (err) {
        vscode.window.showErrorMessage(`Echo Guard: Failed to resolve finding — ${err}`);
      }
    } else if (msg.command === "goToCode" && msg.filepath && msg.lineno !== undefined) {
      const absPath = path.isAbsolute(msg.filepath)
        ? msg.filepath
        : path.join(this.repoRoot, msg.filepath);
      const uri = vscode.Uri.file(absPath);
      const line = Math.max(0, msg.lineno - 1);
      await vscode.window.showTextDocument(uri, {
        selection: new vscode.Range(line, 0, line, 0),
      });
    }
  }

  private _buildHtml(findings: Finding[]): string {
    const sevOrder: Record<string, number> = { extract: 0, review: 1 };
    const sorted = [...findings].sort(
      (a, b) => (sevOrder[a.severity] ?? 3) - (sevOrder[b.severity] ?? 3)
    );
    const rows = sorted.length === 0
      ? `<div class="empty">No findings — your codebase is clean!</div>`
      : sorted.map((f) => this._buildRow(f)).join("");

    return /* html */ `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Echo Guard — Review Findings</title>
  <style>
    body {
      font-family: var(--vscode-font-family);
      font-size: var(--vscode-font-size);
      color: var(--vscode-foreground);
      background: var(--vscode-editor-background);
      padding: 16px;
      margin: 0;
    }
    h1 { font-size: 1.2em; margin-bottom: 16px; }
    .count { color: var(--vscode-descriptionForeground); font-weight: normal; }
    .finding {
      border: 1px solid var(--vscode-panel-border);
      border-radius: 4px;
      padding: 12px 14px;
      margin-bottom: 12px;
    }
    .header { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
    .badge {
      font-size: 0.75em;
      font-weight: bold;
      padding: 2px 6px;
      border-radius: 3px;
      text-transform: uppercase;
    }
    .badge-extract { background: var(--vscode-inputValidation-errorBackground); color: var(--vscode-inputValidation-errorForeground); }
    .badge-review  { background: var(--vscode-inputValidation-warningBackground); color: var(--vscode-inputValidation-warningForeground); }
    .clone-type { font-size: 0.85em; color: var(--vscode-descriptionForeground); }
    .score { margin-left: auto; font-size: 0.85em; color: var(--vscode-descriptionForeground); }
    .locations { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 10px; }
    .loc-label { font-size: 0.75em; text-transform: uppercase; color: var(--vscode-descriptionForeground); margin-bottom: 2px; }
    .loc-link {
      font-family: var(--vscode-editor-font-family, monospace);
      font-size: 0.9em;
      color: var(--vscode-textLink-foreground);
      cursor: pointer;
      text-decoration: underline;
      word-break: break-all;
    }
    .loc-link:hover { color: var(--vscode-textLink-activeForeground); }
    .actions { display: flex; gap: 8px; }
    button {
      font-family: var(--vscode-font-family);
      font-size: 0.85em;
      padding: 4px 10px;
      border: 1px solid var(--vscode-button-border, transparent);
      border-radius: 3px;
      cursor: pointer;
    }
    .btn-intentional {
      background: var(--vscode-button-secondaryBackground);
      color: var(--vscode-button-secondaryForeground);
    }
    .btn-intentional:hover { background: var(--vscode-button-secondaryHoverBackground); }
    .btn-dismiss {
      background: var(--vscode-button-secondaryBackground);
      color: var(--vscode-button-secondaryForeground);
    }
    .btn-dismiss:hover { background: var(--vscode-button-secondaryHoverBackground); }
    .btn-goTo {
      background: var(--vscode-button-background);
      color: var(--vscode-button-foreground);
    }
    .btn-goTo:hover { background: var(--vscode-button-hoverBackground); }
    .cross-service { font-size: 0.75em; color: var(--vscode-charts-orange); margin-left: 4px; }
    .empty { color: var(--vscode-descriptionForeground); font-style: italic; margin-top: 32px; text-align: center; }
  </style>
</head>
<body>
  <h1>Echo Guard <span class="count">(${findings.length} finding${findings.length === 1 ? "" : "s"})</span></h1>
  ${rows}
  <script>
    const vscode = acquireVsCodeApi();
    function resolve(findingId, verdict) {
      vscode.postMessage({ command: 'resolve', findingId, verdict });
    }
    function goToCode(filepath, lineno) {
      vscode.postMessage({ command: 'goToCode', filepath, lineno });
    }
  </script>
</body>
</html>`;
  }

  private _buildRow(f: Finding): string {
    const score = `${Math.round(f.similarity * 100)}%`;
    const crossTag = f.cross_service ? `<span class="cross-service">[cross-service]</span>` : "";
    const srcLoc = `${f.source.filepath}:${f.source.lineno}`;
    const extLoc = `${f.existing.filepath}:${f.existing.lineno}`;
    const id = this._escapeJs(f.finding_id);
    const srcPath = this._escapeJs(f.source.filepath);
    const extPath = this._escapeJs(f.existing.filepath);

    return /* html */ `
<div class="finding">
  <div class="header">
    <span class="badge badge-${f.severity}">${f.severity}</span>
    <span class="clone-type">${this._escape(f.clone_type_label)}</span>
    ${crossTag}
    <span class="score">${score} similar</span>
  </div>
  <div class="locations">
    <div>
      <div class="loc-label">Source</div>
      <span class="loc-link" onclick="goToCode('${srcPath}', ${f.source.lineno})">
        ${this._escape(f.source.name)}() — ${this._escape(srcLoc)}
      </span>
    </div>
    <div>
      <div class="loc-label">Duplicate of</div>
      <span class="loc-link" onclick="goToCode('${extPath}', ${f.existing.lineno})">
        ${this._escape(f.existing.name)}() — ${this._escape(extLoc)}
      </span>
    </div>
  </div>
  <div class="actions">
    <button class="btn-intentional" onclick="resolve('${id}', 'intentional')">Intentional — keep both</button>
    <button class="btn-dismiss" onclick="resolve('${id}', 'dismissed')">Not a duplicate</button>
    <button class="btn-goTo" onclick="goToCode('${srcPath}', ${f.source.lineno})">Go to source</button>
  </div>
</div>`;
  }

  private _buildErrorHtml(message: string): string {
    return `<!DOCTYPE html><html><body style="padding:16px;font-family:sans-serif;">
      <p style="color:red;">Echo Guard: Failed to load findings — ${this._escape(message)}</p>
    </body></html>`;
  }

  private _escape(str: string): string {
    return str
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  private _escapeJs(str: string): string {
    return str
      .replace(/\\/g, "\\\\")
      .replace(/'/g, "\\'")
      .replace(/\n/g, "\\n")
      .replace(/\r/g, "\\r");
  }
}
