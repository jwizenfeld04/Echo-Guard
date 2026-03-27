You are running the `/echo-guard` skill. Scan for redundant/duplicate code and present a structured summary.

## Steps

1. **Detect context**: Check if any source files are mentioned in the conversation or attached.
   - If files are present → run `echo-guard check <files> --output json`
   - Otherwise → run `echo-guard scan --output json`

2. **Parse the JSON output** and display a structured summary:
   - Severity breakdown: HIGH / MEDIUM / LOW counts
   - For each HIGH/MEDIUM finding:
     - Finding ID (short: first 16 chars + "...")
     - Clone type label and similarity %
     - Source location: `file:line  function()`
     - Existing location: `file:line  function()`
     - Import suggestion or reuse guidance (if present)

3. **For HIGH severity findings**, offer:
   > "Would you like me to refactor any of these? Run `/echo-guard-refactor` with a finding ID."

4. **After displaying results**, touch the signal file to sync VS Code:
   ```
   echo-guard notify
   ```

## Output format example

```
Echo Guard scan complete — 3 findings

HIGH (2):
  • [abc123...] Exact structural clone (97%)
    src/utils/format.py:42  format_date()
    src/helpers/dates.py:18  format_date()
    Import: from src.helpers.dates import format_date

MEDIUM (1):
  • [def456...] Renamed clone (81%)
    services/api/auth.py:105  validate_token()
    services/worker/auth.py:67  check_token()
    ⚠ Cross-service — extract to shared library

To refactor: /echo-guard-refactor <finding_id>
```

## Notes
- If `echo-guard` is not installed or the index doesn't exist, tell the user to run `echo-guard setup` first.
- LOW findings are hidden by default; mention the count if any exist.
- Finding IDs come from the `finding_id` field in JSON output.
