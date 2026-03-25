/**
 * Code actions for Echo Guard diagnostics.
 *
 * Actions available on each finding (via lightbulb / Ctrl+.):
 *   1. Mark as intentional   → resolve_finding(id, "intentional")
 *   2. Dismiss               → resolve_finding(id, "dismissed")
 *   3. Go to duplicate       → open existing function location
 *   4. Show side-by-side diff → vscode.diff
 */

import * as path from "path";
import * as vscode from "vscode";
import type { DaemonClient, Finding } from "./daemon";

// Stored alongside diagnostics so code actions can read finding metadata
const findingMetadata = new Map<string, Finding>();

/** Register a finding's metadata so code actions can look it up by ID. */
export function storeFindingMetadata(findings: Finding[]): void {
  for (const f of findings) {
    findingMetadata.set(f.finding_id, f);
  }
}

/** Clear all stored finding metadata (e.g. after a full rescan). */
export function clearFindingMetadata(): void {
  findingMetadata.clear();
}

export class EchoGuardCodeActionProvider implements vscode.CodeActionProvider {
  static readonly providedCodeActionKinds = [vscode.CodeActionKind.QuickFix];

  constructor(
    private readonly daemon: DaemonClient,
    private readonly repoRoot: string,
    private readonly onFindingResolved: (findingId: string) => void
  ) {}

  provideCodeActions(
    document: vscode.TextDocument,
    _range: vscode.Range | vscode.Selection,
    context: vscode.CodeActionContext
  ): vscode.CodeAction[] {
    const actions: vscode.CodeAction[] = [];

    for (const diagnostic of context.diagnostics) {
      if (diagnostic.source !== "echo-guard") continue;

      const findingId = diagnostic.code as string;
      if (!findingId) continue;

      const finding = findingMetadata.get(findingId);

      actions.push(this._makeIntentionalAction(document, diagnostic, findingId));
      actions.push(this._makeDismissAction(document, diagnostic, findingId));

      if (finding) {
        actions.push(this._makeGoToAction(finding));
        actions.push(this._makeDiffAction(document, finding));
      }
    }

    return actions;
  }

  // ── Private ────────────────────────────────────────────────────────

  private _makeIntentionalAction(
    document: vscode.TextDocument,
    diagnostic: vscode.Diagnostic,
    findingId: string
  ): vscode.CodeAction {
    const action = new vscode.CodeAction(
      "Echo Guard: Mark as intentional (keep both copies)",
      vscode.CodeActionKind.QuickFix
    );
    action.diagnostics = [diagnostic];
    action.isPreferred = false;
    action.command = {
      title: "Mark as intentional",
      command: "echoGuard.resolveFinding",
      arguments: [findingId, "intentional", document.uri],
    };
    return action;
  }

  private _makeDismissAction(
    document: vscode.TextDocument,
    diagnostic: vscode.Diagnostic,
    findingId: string
  ): vscode.CodeAction {
    const action = new vscode.CodeAction(
      "Echo Guard: Dismiss (not a real duplicate)",
      vscode.CodeActionKind.QuickFix
    );
    action.diagnostics = [diagnostic];
    action.isPreferred = false;
    action.command = {
      title: "Dismiss finding",
      command: "echoGuard.resolveFinding",
      arguments: [findingId, "dismissed", document.uri],
    };
    return action;
  }

  private _makeGoToAction(finding: Finding): vscode.CodeAction {
    const existingUri = vscode.Uri.file(this._absPath(finding.existing.filepath));
    const line = Math.max(0, finding.existing.lineno - 1);

    const action = new vscode.CodeAction(
      `Echo Guard: Go to duplicate → ${finding.existing.name}() in ${finding.existing.filepath}`,
      vscode.CodeActionKind.Empty
    );
    action.command = {
      title: "Go to duplicate",
      command: "vscode.open",
      arguments: [
        existingUri,
        { selection: new vscode.Range(line, 0, line, 0) },
      ],
    };
    return action;
  }

  private _makeDiffAction(
    document: vscode.TextDocument,
    finding: Finding
  ): vscode.CodeAction {
    const existingUri = vscode.Uri.file(this._absPath(finding.existing.filepath));
    const title = `${finding.source.name}() ↔ ${finding.existing.name}()`;

    const action = new vscode.CodeAction(
      "Echo Guard: Show side-by-side diff",
      vscode.CodeActionKind.Empty
    );
    action.command = {
      title: "Show diff",
      command: "vscode.diff",
      arguments: [document.uri, existingUri, title],
    };
    return action;
  }

  private _absPath(relOrAbs: string): string {
    if (path.isAbsolute(relOrAbs)) return relOrAbs;
    return path.join(this.repoRoot, relOrAbs);
  }
}
