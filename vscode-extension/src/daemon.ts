/**
 * Daemon client — spawns `echo-guard daemon` and communicates via JSON-RPC 2.0
 * over stdin/stdout. Holds the Python process alive across file saves to avoid
 * the ~2-3s cold start cost of reloading ONNX model + DuckDB on every check.
 */

import * as cp from "child_process";
import * as path from "path";
import * as vscode from "vscode";

export interface Finding {
  finding_id: string;
  severity: "high" | "medium" | "low";
  clone_type: string;
  clone_type_label: string;
  similarity: number;
  cross_service: boolean;
  source: { name: string; filepath: string; lineno: number; language: string };
  existing: { name: string; filepath: string; lineno: number; language: string };
  import_suggestion: string;
  reuse_guidance: string;
}

export interface CheckResult {
  findings: Record<string, Finding[]>;
  total: number;
}

export interface ScanResult {
  findings: Finding[];
  total: number;
}

type RpcCallback = (err: Error | null, result: unknown) => void;

export class DaemonClient {
  private process: cp.ChildProcess | null = null;
  private pendingRequests = new Map<number, RpcCallback>();
  private nextId = 1;
  private buffer = "";
  private repoRoot: string;
  private restartCount = 0;
  private readonly maxRestarts = 5;
  private restartTimer: NodeJS.Timeout | undefined;
  private _onStatusChange: vscode.EventEmitter<string>;

  readonly onStatusChange: vscode.Event<string>;

  constructor(repoRoot: string) {
    this.repoRoot = repoRoot;
    this._onStatusChange = new vscode.EventEmitter<string>();
    this.onStatusChange = this._onStatusChange.event;
  }

  /** Start the daemon process. Resolves when `initialize` completes. */
  async start(): Promise<void> {
    const pythonPath = this._getPythonPath();
    this._emit("starting");

    await this._spawn(pythonPath);
    await this._call("initialize", {});
    this._emit("ready");
    this.restartCount = 0;
  }

  /** Stop the daemon process. */
  async stop(): Promise<void> {
    if (!this.process) return;
    try {
      await this._call("shutdown", {});
    } catch {
      // ignore — process may already be dead
    }
    this.process?.kill();
    this.process = null;
  }

  /** Check specific files for duplicates. */
  async checkFiles(files: string[]): Promise<CheckResult> {
    return (await this._call("check_files", { files })) as CheckResult;
  }

  /** Run a full workspace scan. */
  async scan(): Promise<ScanResult> {
    return (await this._call("scan", {})) as ScanResult;
  }

  /** Record a verdict for a finding. */
  async resolvefinding(
    findingId: string,
    verdict: "resolved" | "intentional" | "dismissed",
    note = ""
  ): Promise<void> {
    await this._call("resolve_finding", {
      finding_id: findingId,
      verdict,
      note,
    });
  }

  /** Get cached findings, optionally filtered to a single file. */
  async getFindings(file?: string): Promise<ScanResult> {
    return (await this._call("get_findings", file ? { file } : {})) as ScanResult;
  }

  /** Trigger an incremental reindex. */
  async reindex(): Promise<void> {
    this._emit("indexing");
    await this._call("reindex", {});
    this._emit("ready");
  }

  get isRunning(): boolean {
    return this.process !== null && !this.process.killed;
  }

  // ── Private ────────────────────────────────────────────────────────

  private _emit(status: string): void {
    this._onStatusChange.fire(status);
  }

  private _getPythonPath(): string {
    const config = vscode.workspace.getConfiguration("echoGuard");
    return config.get<string>("pythonPath") || "python3";
  }

  private _spawn(pythonPath: string): Promise<void> {
    return new Promise((resolve, reject) => {
      const proc = cp.spawn(
        pythonPath,
        ["-m", "echo_guard.cli", "daemon", "--path", this.repoRoot],
        {
          cwd: this.repoRoot,
          stdio: ["pipe", "pipe", "pipe"],
          env: { ...process.env },
        }
      );

      this.process = proc;
      this.buffer = "";

      proc.stdout?.on("data", (chunk: Buffer) => {
        this.buffer += chunk.toString();
        this._drainBuffer();
      });

      proc.stderr?.on("data", (chunk: Buffer) => {
        // Daemon logs to stderr — surface critical errors only
        const msg = chunk.toString().trim();
        if (msg.includes("ERROR") || msg.includes("Traceback")) {
          console.error("[Echo Guard daemon]", msg);
        }
      });

      proc.on("error", (err) => {
        this._rejectAll(err);
        reject(err);
      });

      proc.on("exit", (code) => {
        const wasRunning = this.process !== null;
        this.process = null;
        this._rejectAll(new Error(`Daemon exited with code ${code}`));
        if (wasRunning && this.restartCount < this.maxRestarts) {
          this._scheduleRestart(pythonPath);
        } else if (wasRunning) {
          this._emit("stopped");
          vscode.window.showErrorMessage(
            "Echo Guard daemon stopped unexpectedly. Run 'Echo Guard: Activate' to restart."
          );
        }
      });

      // The daemon is ready once it writes the lockfile, but we detect
      // readiness via the RPC `initialize` response — just resolve here.
      resolve();
    });
  }

  private _scheduleRestart(pythonPath: string): void {
    const delay = Math.min(1000 * Math.pow(2, this.restartCount), 30000);
    this.restartCount++;
    this._emit("restarting");
    this.restartTimer = setTimeout(async () => {
      try {
        await this._spawn(pythonPath);
        await this._call("initialize", {});
        this._emit("ready");
        this.restartCount = 0;
      } catch (err) {
        console.error("[Echo Guard] restart failed:", err);
      }
    }, delay);
  }

  private _call(method: string, params: Record<string, unknown>): Promise<unknown> {
    return new Promise((resolve, reject) => {
      if (!this.process) {
        reject(new Error("Daemon not running"));
        return;
      }
      const id = this.nextId++;
      const request = JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n";
      this.pendingRequests.set(id, (err, result) => {
        if (err) reject(err);
        else resolve(result);
      });
      this.process.stdin?.write(request);
    });
  }

  private _drainBuffer(): void {
    const lines = this.buffer.split("\n");
    // Keep the last (potentially incomplete) chunk
    this.buffer = lines.pop() ?? "";

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      try {
        const msg = JSON.parse(trimmed);
        const cb = this.pendingRequests.get(msg.id);
        if (!cb) continue;
        this.pendingRequests.delete(msg.id);
        if (msg.error) {
          cb(new Error(msg.error.message || "RPC error"), null);
        } else {
          cb(null, msg.result);
        }
      } catch {
        // Not valid JSON — could be a startup message, ignore
      }
    }
  }

  private _rejectAll(err: Error): void {
    for (const cb of this.pendingRequests.values()) {
      cb(err, null);
    }
    this.pendingRequests.clear();
  }

  dispose(): void {
    if (this.restartTimer) clearTimeout(this.restartTimer);
    this._onStatusChange.dispose();
    this.stop().catch(() => undefined);
  }
}
