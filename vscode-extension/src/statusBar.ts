/**
 * Status bar item — shows Echo Guard state and finding count.
 *
 * States:
 *   $(loading~spin) Echo Guard: Starting...
 *   $(loading~spin) Echo Guard: Indexing...
 *   $(shield)       Echo Guard: ✓ Clean
 *   $(warning)      Echo Guard: 3 findings
 *   $(error)        Echo Guard: Stopped
 */

import * as vscode from "vscode";

export class EchoGuardStatusBar {
  private item: vscode.StatusBarItem;

  constructor() {
    this.item = vscode.window.createStatusBarItem(
      vscode.StatusBarAlignment.Right,
      100
    );
    this.item.command = "echoGuard.reviewFindings";
    this.item.tooltip = "Echo Guard — click to review findings";
    this.item.show();
    this.setStarting();
  }

  setStarting(): void {
    this.item.text = "$(loading~spin) Echo Guard: Starting...";
    this.item.backgroundColor = undefined;
    this.item.color = undefined;
  }

  setIndexing(): void {
    this.item.text = "$(loading~spin) Echo Guard: Indexing...";
    this.item.backgroundColor = undefined;
    this.item.color = undefined;
  }

  setReady(findingCount: number): void {
    if (findingCount === 0) {
      this.item.text = "$(shield) Echo Guard: Clean";
      this.item.backgroundColor = undefined;
      this.item.color = new vscode.ThemeColor("statusBar.foreground");
    } else {
      const icon = findingCount > 0 ? "$(warning)" : "$(shield)";
      this.item.text = `${icon} Echo Guard: ${findingCount} finding${findingCount === 1 ? "" : "s"}`;
      this.item.backgroundColor = new vscode.ThemeColor(
        "statusBarItem.warningBackground"
      );
      this.item.color = undefined;
    }
  }

  setStopped(): void {
    this.item.text = "$(error) Echo Guard: Stopped";
    this.item.backgroundColor = new vscode.ThemeColor(
      "statusBarItem.errorBackground"
    );
    this.item.color = undefined;
    this.item.tooltip =
      "Echo Guard daemon stopped — click to restart";
    this.item.command = "echoGuard.activate";
  }

  setRestarting(): void {
    this.item.text = "$(loading~spin) Echo Guard: Restarting...";
    this.item.backgroundColor = undefined;
    this.item.color = undefined;
  }

  dispose(): void {
    this.item.dispose();
  }
}
