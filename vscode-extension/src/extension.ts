/**
 * Echo Guard VS Code Extension — main entry point.
 *
 * Activation flow:
 *   1. Run setup wizard (check install, config, index)
 *   2. Spawn the JSON-RPC daemon
 *   3. Run initial workspace scan → populate diagnostics
 *   4. Register file-save listener (debounced check_file)
 *   5. Register commands + code actions
 *
 * Deactivation: shut down daemon cleanly.
 */

import * as vscode from "vscode";
import {
  EchoGuardCodeActionProvider,
  storeFindingMetadata,
  clearFindingMetadata,
} from "./codeActions";
import { DaemonClient } from "./daemon";
import { EchoGuardDiagnostics } from "./diagnostics";
import { EchoGuardReviewPanel } from "./reviewPanel";
import { EchoGuardStatusBar } from "./statusBar";
import { ensureSetup } from "./setup";

let daemon: DaemonClient | undefined;
let diagnostics: EchoGuardDiagnostics | undefined;
let statusBar: EchoGuardStatusBar | undefined;

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  const workspaceFolders = vscode.workspace.workspaceFolders;
  if (!workspaceFolders || workspaceFolders.length === 0) return;

  const repoRoot = workspaceFolders[0].uri.fsPath;

  // Status bar is always shown once extension activates
  statusBar = new EchoGuardStatusBar();
  context.subscriptions.push(statusBar);

  // Register commands (available even before daemon starts)
  _registerCommands(context, repoRoot);

  // Run setup wizard — returns false if user needs to complete a step first
  const ready = await ensureSetup(repoRoot);
  if (!ready) {
    statusBar.setStopped();
    return;
  }

  await _startDaemon(context, repoRoot);
}

export async function deactivate(): Promise<void> {
  await daemon?.stop();
}

// ── Internal ───────────────────────────────────────────────────────────

async function _startDaemon(
  context: vscode.ExtensionContext,
  repoRoot: string
): Promise<void> {
  daemon = new DaemonClient(repoRoot);
  diagnostics = new EchoGuardDiagnostics(daemon, repoRoot);

  // Wire daemon status → status bar
  daemon.onStatusChange((status) => {
    switch (status) {
      case "starting":
        statusBar?.setStarting();
        break;
      case "indexing":
        statusBar?.setIndexing();
        break;
      case "ready":
        statusBar?.setReady(diagnostics?.totalFindings ?? 0);
        break;
      case "restarting":
        statusBar?.setRestarting();
        break;
      case "stopped":
        statusBar?.setStopped();
        break;
    }
  });

  context.subscriptions.push(daemon);

  try {
    await daemon.start();
  } catch (err) {
    vscode.window.showErrorMessage(
      `Echo Guard: Failed to start daemon — ${err}. Check that echo-guard is installed.`
    );
    statusBar?.setStopped();
    return;
  }

  // Activate diagnostics (registers file-save listener)
  diagnostics.activate(context);
  context.subscriptions.push(diagnostics);

  // Register code action provider for all languages
  context.subscriptions.push(
    vscode.languages.registerCodeActionsProvider(
      { scheme: "file" },
      new EchoGuardCodeActionProvider(
        daemon,
        repoRoot,
        (findingId) => diagnostics?.clearFindingById(findingId)
      ),
      { providedCodeActionKinds: EchoGuardCodeActionProvider.providedCodeActionKinds }
    )
  );

  // Initial full scan
  try {
    statusBar?.setIndexing();
    const result = await daemon.scan();
    clearFindingMetadata();
    storeFindingMetadata(result.findings);
    await diagnostics.populateFromScan(result.findings);
    statusBar?.setReady(result.total);
  } catch (err) {
    console.error("[Echo Guard] initial scan failed:", err);
    statusBar?.setReady(0);
  }

  // Watch .git/HEAD for branch switches → reindex
  _watchGitHead(context, repoRoot);

  // Periodic idle reindex every 5 minutes
  _schedulePeriodicReindex(context);
}

function _registerCommands(
  context: vscode.ExtensionContext,
  repoRoot: string
): void {
  context.subscriptions.push(
    vscode.commands.registerCommand("echoGuard.activate", async () => {
      if (daemon?.isRunning) {
        vscode.window.showInformationMessage("Echo Guard is already running.");
        return;
      }
      await _startDaemon(context, repoRoot);
    }),

    vscode.commands.registerCommand("echoGuard.scan", async () => {
      if (!daemon?.isRunning) {
        vscode.window.showWarningMessage("Echo Guard daemon is not running.");
        return;
      }
      await vscode.window.withProgress(
        {
          location: vscode.ProgressLocation.Notification,
          title: "Echo Guard: Scanning workspace...",
          cancellable: false,
        },
        async () => {
          const result = await daemon!.scan();
          clearFindingMetadata();
          storeFindingMetadata(result.findings);
          await diagnostics?.populateFromScan(result.findings);
          statusBar?.setReady(result.total);
          vscode.window.showInformationMessage(
            `Echo Guard: Found ${result.total} finding${result.total === 1 ? "" : "s"}.`
          );
        }
      );
    }),

    vscode.commands.registerCommand("echoGuard.reindex", async () => {
      if (!daemon?.isRunning) {
        vscode.window.showWarningMessage("Echo Guard daemon is not running.");
        return;
      }
      await vscode.window.withProgress(
        {
          location: vscode.ProgressLocation.Notification,
          title: "Echo Guard: Reindexing...",
          cancellable: false,
        },
        async () => {
          await daemon!.reindex();
          const result = await daemon!.scan();
          clearFindingMetadata();
          storeFindingMetadata(result.findings);
          await diagnostics?.populateFromScan(result.findings);
          statusBar?.setReady(result.total);
        }
      );
    }),

    vscode.commands.registerCommand("echoGuard.reviewFindings", async () => {
      if (!daemon?.isRunning) {
        vscode.window.showWarningMessage("Echo Guard daemon is not running.");
        return;
      }
      await EchoGuardReviewPanel.show(
        daemon!,
        repoRoot,
        (findingId) => {
          diagnostics?.clearFindingById(findingId);
          statusBar?.setReady(diagnostics?.totalFindings ?? 0);
        },
        context.extensionUri
      );
    }),

    vscode.commands.registerCommand(
      "echoGuard.resolveFinding",
      async (findingId: string, verdict: "resolved" | "intentional" | "dismissed", fileUri: vscode.Uri) => {
        if (!daemon?.isRunning) return;
        try {
          await daemon!.resolvefinding(findingId, verdict);
          diagnostics?.clearFindingById(findingId);
          statusBar?.setReady(diagnostics?.totalFindings ?? 0);
        } catch (err) {
          vscode.window.showErrorMessage(`Echo Guard: Failed to resolve finding — ${err}`);
        }
      }
    ),

    vscode.commands.registerCommand("echoGuard.showHealth", async () => {
      const terminal = vscode.window.createTerminal("Echo Guard Health");
      terminal.show();
      terminal.sendText(`echo-guard health "${repoRoot}"`, true);
    })
  );
}

function _watchGitHead(
  context: vscode.ExtensionContext,
  repoRoot: string
): void {
  const fs = require("fs") as typeof import("fs");
  const path = require("path") as typeof import("path");
  const headPath = path.join(repoRoot, ".git", "HEAD");

  if (!fs.existsSync(headPath)) return;

  let lastHead = fs.readFileSync(headPath, "utf8").trim();

  const watcher = fs.watch(headPath, async () => {
    try {
      const current = fs.readFileSync(headPath, "utf8").trim();
      if (current !== lastHead) {
        lastHead = current;
        // Branch switched — trigger incremental reindex
        vscode.window.showInformationMessage(
          "Echo Guard: Branch changed — reindexing..."
        );
        if (daemon?.isRunning) {
          await daemon.reindex();
          const result = await daemon.scan();
          clearFindingMetadata();
          storeFindingMetadata(result.findings);
          await diagnostics?.populateFromScan(result.findings);
          statusBar?.setReady(result.total);
        }
      }
    } catch {
      // ignore read errors
    }
  });

  context.subscriptions.push({ dispose: () => watcher.close() });
}

function _schedulePeriodicReindex(context: vscode.ExtensionContext): void {
  const FIVE_MINUTES = 5 * 60 * 1000;
  const timer = setInterval(async () => {
    if (!daemon?.isRunning) return;
    try {
      await daemon.reindex();
      const result = await daemon.getFindings();
      statusBar?.setReady(result.total);
    } catch {
      // ignore background errors
    }
  }, FIVE_MINUTES);

  context.subscriptions.push({ dispose: () => clearInterval(timer) });
}
