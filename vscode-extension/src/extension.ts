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

import * as cp from "child_process";
import * as path from "path";
import * as vscode from "vscode";
import {
  EchoGuardCodeActionProvider,
  storeFindingMetadata,
  clearFindingMetadata,
  findingMetadata,
} from "./codeActions";
import { DaemonClient } from "./daemon";
import { EchoGuardDiagnostics } from "./diagnostics";
import { EchoGuardApiMappingsProvider } from "./apiMappings";
import { EchoGuardFindingsTreeProvider, type EchoGuardTreeItem } from "./findingsTree";
import { EchoGuardReviewPanel } from "./reviewPanel";
import { EchoGuardStatusBar } from "./statusBar";
import { ensureSetup } from "./setup";
import type { Finding } from "./daemon";

let daemon: DaemonClient | undefined;
let diagnostics: EchoGuardDiagnostics | undefined;
let statusBar: EchoGuardStatusBar | undefined;
let apiMappings: EchoGuardApiMappingsProvider | undefined;
let findingsTree: EchoGuardFindingsTreeProvider | undefined;
let findingsTreeView: vscode.TreeView<EchoGuardTreeItem> | undefined;
let currentFindings: Finding[] = [];

// Disposables that are tied to the daemon lifetime, not the extension lifetime.
// Disposed and rebuilt on every daemon restart so duplicate providers/listeners
// don't accumulate in context.subscriptions.
let daemonDisposables: vscode.Disposable[] = [];

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  const workspaceFolders = vscode.workspace.workspaceFolders;
  if (!workspaceFolders || workspaceFolders.length === 0) return;

  const repoRoot = workspaceFolders[0].uri.fsPath;

  // Status bar is always shown once extension activates
  statusBar = new EchoGuardStatusBar();
  context.subscriptions.push(statusBar);

  // Ensure daemon-lifetime resources are cleaned up when the extension unloads
  context.subscriptions.push({ dispose: () => { for (const d of daemonDisposables) d.dispose(); daemonDisposables = []; } });

  // Register commands (available even before daemon starts)
  _registerCommands(context, repoRoot);

  // Run setup wizard — returns false if setup is pending (onReady fires later)
  const ready = await ensureSetup(repoRoot, () => _startDaemon(context, repoRoot));
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
  // Dispose previous daemon-lifetime resources before creating new ones,
  // so restarts don't accumulate duplicate providers/listeners/watchers.
  for (const d of daemonDisposables) d.dispose();
  daemonDisposables = [];

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

  // Handle push notifications from daemon (e.g. MCP-resolved findings)
  let _findingsRefreshedTimer: NodeJS.Timeout | undefined;
  daemon.onNotification(async (msg) => {
    if (msg.method === "finding_resolved") {
      // Instant single-finding removal from UI
      const findingId = msg.params?.finding_id as string;
      if (findingId) {
        diagnostics?.clearFindingById(findingId);
        findingsTree?.removeFinding(findingId);
        currentFindings = currentFindings.filter((f) => f.finding_id !== findingId);
        clearFindingMetadata();
        storeFindingMetadata(currentFindings);
        statusBar?.setReady(diagnostics?.totalFindings ?? 0);
      }
      // Immediately pull the updated cache from the daemon — resolve_finding
      // already removed the finding from _findings before this notification
      // fired, so this returns fresh data without waiting for the MCP rescan.
      await _refreshFromCache();
    } else if (msg.method === "findings_refreshed") {
      // Debounce rapid signal touches (e.g. skill calling notify multiple times)
      if (_findingsRefreshedTimer) clearTimeout(_findingsRefreshedTimer);
      _findingsRefreshedTimer = setTimeout(async () => {
        _findingsRefreshedTimer = undefined;
        await _refreshFromCache();
      }, 500);
    }
  });

  daemonDisposables.push(daemon);

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
  diagnostics.activate(daemonDisposables);
  daemonDisposables.push(diagnostics);

  // Register code action provider for all languages
  daemonDisposables.push(
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

  // Register API Mappings CodeLens provider for cross-language pairs
  apiMappings = new EchoGuardApiMappingsProvider(repoRoot);
  daemonDisposables.push(
    vscode.languages.registerCodeLensProvider({ scheme: "file" }, apiMappings),
    apiMappings
  );

  // Register findings sidebar tree view
  findingsTree = new EchoGuardFindingsTreeProvider(repoRoot);
  findingsTreeView = vscode.window.createTreeView("echoGuard.findings", {
    treeDataProvider: findingsTree,
    showCollapseAll: true,
  });
  daemonDisposables.push(findingsTreeView);

  // Initial full scan
  try {
    statusBar?.setIndexing();
    const result = await daemon.scan();
    currentFindings = result.findings;
    clearFindingMetadata();
    storeFindingMetadata(result.findings);
    await diagnostics.populateFromScan(result.findings);
    apiMappings?.refresh();
    findingsTree?.refresh(result.findings);
    statusBar?.setReady(diagnostics.totalFindings);
  } catch (err) {
    console.error("[Echo Guard] initial scan failed:", err);
    statusBar?.setReady(0);
  }

  // Watch .git/HEAD for branch switches → reindex
  _watchGitHead(daemonDisposables, repoRoot);

  // Periodic idle reindex every 5 minutes
  _schedulePeriodicReindex(daemonDisposables);
}

/** Pull fresh findings from the daemon cache and update all UI surfaces.
 *  Uses get_findings (fast, returns in-memory cache) rather than scan.
 *  Safe to call concurrently — last writer wins on shared state. */
async function _refreshFromCache(): Promise<void> {
  if (!daemon?.isRunning) return;
  try {
    const result = await daemon.getFindings();
    const allFindings = result.findings as unknown as Finding[];
    currentFindings = allFindings;
    clearFindingMetadata();
    storeFindingMetadata(allFindings);
    await diagnostics?.populateFromScan(allFindings);
    findingsTree?.refresh(allFindings);
    statusBar?.setReady(diagnostics?.totalFindings ?? 0);
  } catch {
    // ignore — periodic reindex will catch any missed updates
  }
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
          currentFindings = result.findings;
          clearFindingMetadata();
          storeFindingMetadata(result.findings);
          await diagnostics?.populateFromScan(result.findings);
          apiMappings?.refresh();
          findingsTree?.refresh(result.findings);
          const count = diagnostics?.totalFindings ?? 0;
          statusBar?.setReady(count);
          vscode.window.showInformationMessage(
            `Echo Guard: Found ${count} finding${count === 1 ? "" : "s"}.`
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
          currentFindings = result.findings;
          clearFindingMetadata();
          storeFindingMetadata(result.findings);
          await diagnostics?.populateFromScan(result.findings);
          apiMappings?.refresh();
          findingsTree?.refresh(result.findings);
          statusBar?.setReady(diagnostics?.totalFindings ?? 0);
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
      async (
        findingId: string,
        verdict: "resolved" | "intentional" | "dismissed",
        fileUri: vscode.Uri,
        functionName?: string
      ) => {
        if (!daemon?.isRunning) return;
        try {
          if (functionName) {
            // Group dismiss — resolve all findings in this cluster.
            // Scope by both function name AND file path to avoid grouping
            // unrelated clusters that happen to share a common name (e.g. render()).
            const filePath = fileUri.fsPath;
            const siblings = [...findingMetadata.values()].filter(
              (f) =>
                (f.existing.name === functionName &&
                  path.resolve(f.existing.filepath) === filePath) ||
                (f.source.name === functionName &&
                  path.resolve(f.source.filepath) === filePath)
            );
            // Always include the triggering finding even if not in metadata
            const ids = new Set([findingId, ...siblings.map((f) => f.finding_id)]);
            await Promise.all(
              [...ids].map((id) => daemon!.resolvefinding(id, verdict))
            );
            for (const id of ids) {
              diagnostics?.clearFindingById(id);
              findingsTree?.removeFinding(id);
              currentFindings = currentFindings.filter((f) => f.finding_id !== id);
            }
          } else {
            await daemon!.resolvefinding(findingId, verdict);
            diagnostics?.clearFindingById(findingId);
            findingsTree?.removeFinding(findingId);
            currentFindings = currentFindings.filter((f) => f.finding_id !== findingId);
          }
          statusBar?.setReady(diagnostics?.totalFindings ?? 0);
        } catch (err) {
          vscode.window.showErrorMessage(`Echo Guard: Failed to resolve finding — ${err}`);
        }
      }
    ),

    vscode.commands.registerCommand(
      "echoGuard.tree.revealCluster",
      async (functionName: string) => {
        if (!findingsTree || !findingsTreeView) return;
        const clusterItem = findingsTree.findClusterByFunctionName(functionName);
        if (!clusterItem) return;
        await findingsTreeView.reveal(clusterItem, { select: true, focus: true, expand: true });
      }
    ),

    vscode.commands.registerCommand(
      "echoGuard.tree.dismissCluster",
      async (item: { findingId: string; functionName: string; fileUri: vscode.Uri }) => {
        await vscode.commands.executeCommand(
          "echoGuard.resolveFinding",
          item.findingId,
          "dismissed",
          item.fileUri,
          item.functionName
        );
      }
    ),

    vscode.commands.registerCommand(
      "echoGuard.tree.sendToAI",
      async (item: { findingId: string; functionName: string }) => {
        const finding = findingMetadata.get(item.findingId);
        if (finding) {
          await vscode.commands.executeCommand("echoGuard.sendToAI", finding);
        }
      }
    ),

    vscode.commands.registerCommand(
      "echoGuard.goToApiMatch",
      async (filepath: string, lineno: number, repoRootArg: string) => {
        const abs = path.isAbsolute(filepath)
          ? filepath
          : path.join(repoRootArg, filepath);
        const uri = vscode.Uri.file(abs);
        const line = Math.max(0, lineno - 1);
        await vscode.window.showTextDocument(uri, {
          selection: new vscode.Range(line, 0, line, 0),
        });
      }
    ),

    vscode.commands.registerCommand("echoGuard.showHealth", async () => {
      await _runCliCommand("health", repoRoot);
    }),

    vscode.commands.registerCommand("echoGuard.prune", async () => {
      await _runCliCommand("prune", repoRoot);
    }),

    vscode.commands.registerCommand("echoGuard.sendToAI", async (finding: Finding) => {
      if (!finding) return;
      const { prompt, findingIds } = _buildAIPrompt(finding, repoRoot);
      await vscode.env.clipboard.writeText(prompt);

      const terminals = vscode.window.terminals;
      if (terminals.length === 0) {
        vscode.window.showInformationMessage(
          "Prompt copied to clipboard — paste into your AI agent."
        );
        return;
      }

      if (terminals.length === 1) {
        const choice = await vscode.window.showInformationMessage(
          "Prompt copied to clipboard.",
          `Send to ${terminals[0].name}`
        );
        if (choice) {
          terminals[0].show();
          terminals[0].sendText(prompt, true);
          _pollForResolutions(findingIds);
        }
        return;
      }

      // Multiple terminals — let user pick
      const items = terminals.map((t) => ({ label: t.name, terminal: t }));
      const picked = await vscode.window.showQuickPick(items, {
        placeHolder: "Send refactoring prompt to...",
      });
      if (picked) {
        picked.terminal.show();
        picked.terminal.sendText(prompt, true);
        _pollForResolutions(findingIds);
      }
    })
  );
}

function _buildAIPrompt(
  finding: Finding,
  repoRoot: string
): { prompt: string; findingIds: string[] } {
  // Gather the full cluster — all findings sharing the same representative (existing.name)
  const clusterName = finding.existing.name;
  const siblings = [...findingMetadata.values()].filter(
    (f) => f.existing.name === clusterName && f.severity === finding.severity
  );
  const cluster = siblings.length > 0 ? siblings : [finding];

  // Collect unique locations (source + existing sides, deduplicated)
  const locSet = new Set<string>();
  const locations: Array<{ name: string; filepath: string; lineno: number }> = [];
  for (const f of cluster) {
    for (const side of [f.source, f.existing]) {
      const key = `${side.filepath}:${side.lineno}`;
      if (!locSet.has(key)) {
        locSet.add(key);
        locations.push({ name: side.name, filepath: side.filepath, lineno: side.lineno });
      }
    }
  }

  const findingIds = cluster.map((f) => f.finding_id);
  const sim = Math.round(finding.similarity * 100);

  const lines = [
    `Echo Guard detected a potential duplicate code cluster (${finding.severity}, ${finding.clone_type_label}, ~${sim}% similar):`,
    ``,
    ...locations.map((loc) => `- \`${loc.name}()\` in ${loc.filepath}:${loc.lineno}`),
    ``,
    `Analyze each function independently — some may be real duplicates worth consolidating,`,
    `while others may be false positives (similar names but different implementations).`,
    `For each, determine:`,
    `1. If this is real duplication → refactor into a single shared implementation and update all callers`,
    `2. If the duplication is intentional (different contexts, error handling, etc.) → mark as intentional`,
    `3. If it's a false positive (different implementation despite similar name) → dismiss it`,
    ``,
    `If you have echo-guard MCP tools, use suggest_refactor with`,
    `source_filepath="${finding.source.filepath}" source_function="${finding.source.name}"`,
    `existing_filepath="${finding.existing.filepath}" existing_function="${finding.existing.name}"`,
    `repo_root="${repoRoot}"`,
    `for full context including callers. After deciding, use resolve_finding for each finding ID`,
    `with repo_root="${repoRoot}" and the appropriate verdict ("resolved", "intentional", or "dismissed"):`,
    ...findingIds.map((id) => `- "${id}"`),
  ];

  return { prompt: lines.join("\n"), findingIds };
}

function _pollForResolutions(findingIds: string[]): void {
  const POLL_MS = 3000;
  const MAX_POLLS = 60; // 3 min timeout
  let polls = 0;
  let lastCount = findingIds.length;

  const timer = setInterval(async () => {
    polls++;
    if (polls > MAX_POLLS || !daemon?.isRunning) {
      clearInterval(timer);
      return;
    }
    try {
      const result = await daemon!.getFindings();
      const allFindings = result.findings as unknown as Finding[];
      const remaining = allFindings.filter((f) =>
        findingIds.includes(f.finding_id)
      );
      if (remaining.length < lastCount) {
        lastCount = remaining.length;
        clearFindingMetadata();
        storeFindingMetadata(allFindings);
        currentFindings = allFindings;
        await diagnostics?.populateFromScan(allFindings);
        findingsTree?.refresh(allFindings);
        statusBar?.setReady(diagnostics?.totalFindings ?? 0);
      }
      if (remaining.length === 0) clearInterval(timer);
    } catch {
      // ignore polling errors
    }
  }, POLL_MS);
}

function _watchGitHead(
  disposables: vscode.Disposable[],
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
          currentFindings = result.findings;
          clearFindingMetadata();
          storeFindingMetadata(result.findings);
          await diagnostics?.populateFromScan(result.findings);
          apiMappings?.refresh();
          findingsTree?.refresh(result.findings);
          statusBar?.setReady(diagnostics?.totalFindings ?? 0);
        }
      }
    } catch {
      // ignore read errors
    }
  });

  disposables.push({ dispose: () => watcher.close() });
}

function _schedulePeriodicReindex(disposables: vscode.Disposable[]): void {
  const FIVE_MINUTES = 5 * 60 * 1000;
  const timer = setInterval(async () => {
    if (!daemon?.isRunning) return;
    try {
      await daemon.reindex();
      const result = await daemon.getFindings();
      const allFindings = result.findings as unknown as Finding[];
      clearFindingMetadata();
      storeFindingMetadata(allFindings);
      currentFindings = allFindings;
      await diagnostics?.populateFromScan(allFindings);
      findingsTree?.refresh(allFindings);
      statusBar?.setReady(diagnostics?.totalFindings ?? 0);
    } catch {
      // ignore background errors
    }
  }, FIVE_MINUTES);

  disposables.push({ dispose: () => clearInterval(timer) });
}

/** Run an echo-guard CLI subcommand as a child process, streaming output to
 *  a shared Output Channel. Uses the configured pythonPath so it works
 *  regardless of terminal venv state. */
let _outputChannel: vscode.OutputChannel | undefined;

async function _runCliCommand(
  subcommand: string,
  repoRoot: string,
  extraArgs: string[] = []
): Promise<void> {
  if (!_outputChannel) {
    _outputChannel = vscode.window.createOutputChannel("Echo Guard");
  }
  _outputChannel.show(true);
  _outputChannel.appendLine(`\n── echo-guard ${subcommand} ──\n`);

  const pythonPath = vscode.workspace
    .getConfiguration("echoGuard")
    .get<string>("pythonPath") || "python3";

  const args = ["-m", "echo_guard.cli", subcommand, repoRoot, ...extraArgs];

  return new Promise((resolve) => {
    const proc = cp.spawn(pythonPath, args, { cwd: repoRoot });

    proc.stdout.on("data", (data: Buffer) => {
      _outputChannel!.append(data.toString());
    });
    proc.stderr.on("data", (data: Buffer) => {
      _outputChannel!.append(data.toString());
    });
    proc.on("close", (code) => {
      if (code !== 0) {
        _outputChannel!.appendLine(`\nExited with code ${code}`);
      }
      resolve();
    });
    proc.on("error", (err) => {
      _outputChannel!.appendLine(`\nFailed to run: ${err.message}`);
      resolve();
    });
  });
}

