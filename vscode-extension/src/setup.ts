/**
 * First-run setup wizard for the Echo Guard extension.
 *
 * Checks:
 * 1. Is `echo-guard` installed?  No → prompt to install.
 * 2. Does `echo-guard.yml` exist? No → copy setup command to clipboard,
 *    watch for the file, and call onReady once setup completes.
 *
 * Returns true if setup is already complete (daemon can start immediately).
 * Returns false if setup is pending — onReady will be called later.
 */

import * as cp from "child_process";
import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";

export async function ensureSetup(
  repoRoot: string,
  onReady: () => Promise<void>,
  disposables: vscode.Disposable[] = []
): Promise<boolean> {
  const pythonPath = _getPythonPath();

  // ── 1. Check echo-guard is installed ──────────────────────────────
  const installed = await _checkInstalled(pythonPath);
  if (!installed) {
    const choice = await vscode.window.showErrorMessage(
      "Echo Guard is not installed. Install it with pip to use this extension.",
      "Copy Install Command",
      "Dismiss"
    );
    if (choice === "Copy Install Command") {
      const cmd = `"${pythonPath}" -m pip install "echo-guard[languages]"`;
      await vscode.env.clipboard.writeText(cmd);
      vscode.window.showInformationMessage(
        'Install command copied to clipboard. Paste it in your terminal, then run "Echo Guard: Activate" when done.'
      );
    }
    return false;
  }

  // ── 2. Check config file exists ───────────────────────────────────
  if (_configExists(repoRoot)) {
    return true;
  }

  // No config — copy the setup command and watch for the file to appear
  const setupCmd = `"${pythonPath}" -m echo_guard.cli setup`;
  await vscode.env.clipboard.writeText(setupCmd);

  vscode.window.showInformationMessage(
    `Echo Guard: setup command copied to clipboard — paste it in your terminal to configure this workspace.`,
    "Dismiss"
  );

  _watchForConfig(repoRoot, onReady, disposables);
  return false;
}

// ── Helpers ────────────────────────────────────────────────────────────

function _configExists(repoRoot: string): boolean {
  return (
    fs.existsSync(path.join(repoRoot, "echo-guard.yml")) ||
    fs.existsSync(path.join(repoRoot, "echo-guard.yaml"))
  );
}

function _getPythonPath(): string {
  return (
    vscode.workspace
      .getConfiguration("echoGuard")
      .get<string>("pythonPath") || "python3"
  );
}

function _checkInstalled(pythonPath: string): Promise<boolean> {
  return new Promise((resolve) => {
    cp.execFile(
      pythonPath,
      ["-m", "echo_guard.cli", "--version"],
      { timeout: 5000 },
      (err) => resolve(!err)
    );
  });
}

/**
 * Watch the repo root for echo-guard.yml to appear.
 * Once the config exists, wait for embeddings.npy to appear (signals indexing
 * actually ran), then poll until it stabilizes before prompting the user.
 * If the user skipped indexing, the prompt never fires — they use
 * "Echo Guard: Activate" manually and the daemon indexes on first start.
 */
function _watchForConfig(
  repoRoot: string,
  onReady: () => Promise<void>,
  disposables: vscode.Disposable[]
): void {
  // Re-check in case config appeared between ensureSetup() and here
  if (_configExists(repoRoot)) {
    _waitForEmbeddings(repoRoot, onReady, disposables);
    return;
  }

  const configWatcher = fs.watch(repoRoot, (_event, filename) => {
    if (!filename) return;
    if (filename !== "echo-guard.yml" && filename !== "echo-guard.yaml") return;
    if (!_configExists(repoRoot)) return;
    configWatcher.close();
    _waitForEmbeddings(repoRoot, onReady, disposables);
  });
  disposables.push({ dispose: () => configWatcher.close() });
}

/**
 * Stage 1: Poll every second until embeddings.npy appears.
 * Stage 2: Once it appears, poll until it hasn't been modified for 3s.
 * Then show the "Activate now?" prompt.
 */
function _waitForEmbeddings(
  repoRoot: string,
  onReady: () => Promise<void>,
  disposables: vscode.Disposable[]
): void {
  const embeddingsPath = path.join(repoRoot, ".echo-guard", "embeddings.npy");

  // Stage 1 — wait for embeddings to appear
  const appearPoll = setInterval(() => {
    if (!fs.existsSync(embeddingsPath)) return;
    clearInterval(appearPoll);

    // Stage 2 — poll until stable (indexing done)
    const stablePoll = setInterval(async () => {
      const ageMs = Date.now() - fs.statSync(embeddingsPath).mtimeMs;
      if (ageMs < 3000) return;
      clearInterval(stablePoll);

      const choice = await vscode.window.showInformationMessage(
        "Echo Guard setup complete! Activate now?",
        "Activate",
        "Later"
      );
      if (choice === "Activate") {
        await onReady();
      }
    }, 1000);
    disposables.push({ dispose: () => clearInterval(stablePoll) });
  }, 1000);
  disposables.push({ dispose: () => clearInterval(appearPoll) });
}
