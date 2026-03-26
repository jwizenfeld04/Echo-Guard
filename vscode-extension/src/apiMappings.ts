/**
 * API Mappings — CodeLens provider for cross-language function pairs.
 *
 * When Echo Guard finds a Python↔TypeScript (or other cross-language) match,
 * instead of showing a squiggle it shows a subtle grey CodeLens above the
 * function: "↔ Python handler: get_channels() in api/channels.py:42"
 *
 * Clicking it opens the matching file at the correct line.
 */

import * as path from "path";
import * as vscode from "vscode";
import type { Finding } from "./daemon";

// Cross-language findings keyed by absolute source filepath
const apiMappingsByFile = new Map<string, Finding[]>();

/** Store cross-language findings (called from diagnostics.populateFromScan). */
export function storeApiMappings(findings: Finding[], repoRoot: string): void {
  apiMappingsByFile.clear();
  for (const f of findings) {
    const abs = path.isAbsolute(f.source.filepath)
      ? f.source.filepath
      : path.join(repoRoot, f.source.filepath);
    const group = apiMappingsByFile.get(abs) ?? [];
    group.push(f);
    apiMappingsByFile.set(abs, group);
  }
}

/** Clear all stored API mappings. */
export function clearApiMappings(): void {
  apiMappingsByFile.clear();
}

export class EchoGuardApiMappingsProvider implements vscode.CodeLensProvider {
  private readonly _onDidChange = new vscode.EventEmitter<void>();
  readonly onDidChangeCodeLenses = this._onDidChange.event;

  constructor(private readonly repoRoot: string) {}

  refresh(): void {
    this._onDidChange.fire();
  }

  provideCodeLenses(document: vscode.TextDocument): vscode.CodeLens[] {
    const findings = apiMappingsByFile.get(document.uri.fsPath);
    if (!findings || findings.length === 0) return [];

    return findings.map((f) => {
      const line = Math.max(0, f.source.lineno - 1);
      const range = new vscode.Range(line, 0, line, 0);

      const existingLang =
        f.existing.language.charAt(0).toUpperCase() + f.existing.language.slice(1);
      const shortPath = f.existing.filepath.split("/").slice(-2).join("/");
      const title = `↔ ${existingLang}: ${f.existing.name}() in ${shortPath}:${f.existing.lineno}`;

      return new vscode.CodeLens(range, {
        title,
        command: "echoGuard.goToApiMatch",
        arguments: [f.existing.filepath, f.existing.lineno, this.repoRoot],
      });
    });
  }

  dispose(): void {
    this._onDidChange.dispose();
  }
}
