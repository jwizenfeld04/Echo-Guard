/**
 * First-run setup wizard for the Echo Guard extension.
 *
 * Checks:
 * 1. Is `echo-guard` installed (via `python3 -m echo_guard.cli --version`)?
 *    No → prompt to install with pip.
 * 2. Does `echo-guard.yml` exist in the workspace root?
 *    No → offer to run `echo-guard setup` in the integrated terminal.
 * 3. Is the index empty or missing?
 *    Yes → run `echo-guard index` with a progress notification.
 *
 * Returns true if setup is complete and the daemon can start.
 */

import * as cp from "child_process";
import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";

export async function ensureSetup(repoRoot: string): Promise<boolean> {
  const pythonPath = _getPythonPath();

  // ── 1. Check echo-guard is installed ──────────────────────────────
  const installed = await _checkInstalled(pythonPath);
  if (!installed) {
    const choice = await vscode.window.showErrorMessage(
      "Echo Guard is not installed. Install it with pip to use this extension.",
      "Install echo-guard",
      "Dismiss"
    );
    if (choice === "Install echo-guard") {
      const terminal = vscode.window.createTerminal("Echo Guard Setup");
      terminal.show();
      terminal.sendText(
        `${pythonPath} -m pip install "echo-guard[languages]"`,
        true
      );
      vscode.window.showInformationMessage(
        'After installation completes, run "Echo Guard: Activate" from the command palette.'
      );
    }
    return false;
  }

  // ── 2. Check config file exists ───────────────────────────────────
  const configExists =
    fs.existsSync(path.join(repoRoot, "echo-guard.yml")) ||
    fs.existsSync(path.join(repoRoot, "echo-guard.yaml"));

  if (!configExists) {
    const choice = await vscode.window.showInformationMessage(
      "Echo Guard is not configured for this workspace. Run the setup wizard?",
      "Run Setup",
      "Skip"
    );
    if (choice === "Run Setup") {
      const terminal = vscode.window.createTerminal("Echo Guard Setup");
      terminal.show();
      terminal.sendText(`echo-guard setup`, true);
      vscode.window.showInformationMessage(
        'After setup completes, run "Echo Guard: Activate" from the command palette.'
      );
      return false;
    }
    // User skipped — continue without config (defaults will be used)
  }

  // ── 3. Build index if needed ──────────────────────────────────────
  const indexExists = fs.existsSync(
    path.join(repoRoot, ".echo-guard", "index.duckdb")
  );
  if (!indexExists) {
    await vscode.window.withProgress(
      {
        location: vscode.ProgressLocation.Notification,
        title: "Echo Guard: Building index...",
        cancellable: false,
      },
      async (progress) => {
        progress.report({ message: "Indexing codebase (first run)" });
        await _runIndex(pythonPath, repoRoot);
        progress.report({ message: "Done" });
      }
    );
  }

  return true;
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
    cp.exec(
      `${pythonPath} -m echo_guard.cli --version`,
      { timeout: 5000 },
      (err) => {
        resolve(!err);
      }
    );
  });
}

function _runIndex(pythonPath: string, repoRoot: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const proc = cp.spawn(
      pythonPath,
      ["-m", "echo_guard.cli", "index", repoRoot],
      { cwd: repoRoot, stdio: "inherit" }
    );
    proc.on("exit", (code) => {
      if (code === 0) resolve();
      else reject(new Error(`echo-guard index exited with code ${code}`));
    });
    proc.on("error", reject);
  });
}
