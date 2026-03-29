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
import { extractFindingId } from "./diagnostics";

// Stored alongside diagnostics so code actions can read finding metadata
export const findingMetadata = new Map<string, Finding>();

/** Register a finding's metadata so code actions can look it up by ID.
 *  Cross-language (reference_only) findings are excluded — they appear as CodeLens instead.
 */
export function storeFindingMetadata(findings: Finding[]): void {
  for (const f of findings) {
    if (f.reuse_type === "reference_only") continue;
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

      const findingId = extractFindingId(diagnostic.code);
      if (!findingId) continue;

      const finding = findingMetadata.get(findingId);

      actions.push(this._makeIntentionalAction(document, diagnostic, findingId));
      actions.push(this._makeDismissAction(document, diagnostic, findingId, finding));

      if (finding) {
        actions.push(this._makeGoToAction(finding));
        actions.push(this._makeDiffAction(document, finding));
        actions.push(this._makeSendToAIAction(finding));
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
      "Echo Guard: Keep both — duplication is intentional",
      vscode.CodeActionKind.QuickFix
    );
    action.diagnostics = [diagnostic];
    action.isPreferred = false;
    action.command = {
      title: "Keep both — duplication is intentional",
      command: "echoGuard.resolveFinding",
      arguments: [findingId, "intentional", document.uri],
    };
    return action;
  }

  private _makeDismissAction(
    document: vscode.TextDocument,
    diagnostic: vscode.Diagnostic,
    findingId: string,
    finding?: Finding
  ): vscode.CodeAction {
    const isSameNameGroup =
      finding && finding.source.name === finding.existing.name;
    const label = isSameNameGroup
      ? `Echo Guard: False positive — stop flagging ${finding.source.name}()`
      : "Echo Guard: False positive — not a real duplicate";

    const action = new vscode.CodeAction(label, vscode.CodeActionKind.QuickFix);
    action.diagnostics = [diagnostic];
    action.isPreferred = false;
    action.command = {
      title: "False positive — not a real duplicate",
      command: "echoGuard.resolveFinding",
      arguments: isSameNameGroup
        ? [findingId, "dismissed", document.uri, finding.source.name]
        : [findingId, "dismissed", document.uri],
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

  private _makeSendToAIAction(finding: Finding): vscode.CodeAction {
    const action = new vscode.CodeAction(
      "Echo Guard: Ask AI to refactor this duplicate",
      vscode.CodeActionKind.QuickFix
    );
    action.command = {
      title: "Ask AI to refactor this duplicate",
      command: "echoGuard.sendToAI",
      arguments: [finding],
    };
    return action;
  }

  private _absPath(relOrAbs: string): string {
    if (path.isAbsolute(relOrAbs)) return relOrAbs;
    return path.join(this.repoRoot, relOrAbs);
  }
}
