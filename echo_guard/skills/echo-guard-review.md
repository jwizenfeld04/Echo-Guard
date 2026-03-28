You are running the `/echo-guard-review` skill. Interactively triage all unresolved Echo Guard findings.

## Steps

1. **Get all unresolved findings**:
   ```bash
   echo-guard scan --output json
   ```
   Parse the JSON. Filter out any already-suppressed findings (finding IDs not in the output are already suppressed).

2. **Walk through each finding** one at a time:
   - Show: finding number / total, severity, clone type, similarity %
   - Show: source location (`file:line  function()`)
   - Show: existing location (`file:line  function()`)
   - Show: first 6 lines of each function's source
   - Show: import suggestion or reuse guidance if present

3. **For each finding, ask**:
   > "How should I handle this?
   > [r] resolved — I'll fix it  [i] intentional — keep both  [d] dismiss — not a duplicate  [s] skip  [q] quit"

4. **Record each decision immediately**:
   - `resolved` → `echo-guard acknowledge <id> --verdict resolved`
   - `intentional` → `echo-guard acknowledge <id> --verdict intentional`
   - `dismissed` → `echo-guard acknowledge <id> --verdict dismissed`
   - `skip` → move to next finding without recording
   - `quit` → stop review and show summary

5. **After all findings are processed** (or after quit), run once:
   ```bash
   echo-guard notify
   ```
   This triggers a daemon rescan → VS Code diagnostics update.

6. **Show final summary**:
   ```text
   Review complete: X resolved, Y intentional, Z dismissed, W skipped.
   echo-guard.yml updated — commit to suppress in CI.
   ```

## Notes
- Present findings in severity order: EXTRACT first, then REVIEW.
- If there are no unresolved findings, report that and exit.
- You can batch the `echo-guard acknowledge` calls if the user wants to dismiss/mark-intentional all findings of a given type at once.
- `resolved` means "I plan to fix the code" — it's transient and will re-surface if the code isn't actually changed.
- `intentional` re-surfaces if the function's AST hash changes; `dismissed` is permanent.
