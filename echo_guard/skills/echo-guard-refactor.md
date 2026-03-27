You are running the `/echo-guard-refactor` skill. AI-assisted refactoring of duplicate code found by Echo Guard.

## Input

Accept one of:
- A **finding ID** (from `echo-guard scan --output json` or `/echo-guard` skill output)
- Two **function references** as `file:function` pairs

## Steps

1. **Locate the functions**:
   - If a finding ID was given, run `echo-guard scan --output json` and find the matching entry.
   - Read both source files to extract the full function source.

2. **Show a side-by-side comparison**:
   - Function A: name, location, full source
   - Function B: name, location, full source
   - Similarity score and clone type
   - Any existing import suggestion from Echo Guard

3. **Generate a consolidated replacement**:
   - Analyze both implementations for differences (parameter names, error handling, edge cases).
   - Produce a single merged function that preserves all behavior.
   - Show where it should live (prefer the existing function's module).
   - Show updated call sites for the function being removed.

4. **Offer to apply**:
   > "Apply this refactor? I'll edit the files and update the callers."
   - If yes: use Edit to apply changes to source files.
   - If no: just show the suggestion.

5. **After confirmation**, record the verdict and notify VS Code:
   ```
   echo-guard acknowledge <finding_id> --verdict resolved --note "AI-refactored via skill"
   echo-guard notify
   ```
   This clears the squiggles in VS Code within ~2 seconds.

## Notes
- Always read both files before suggesting a refactor — do not rely on the short preview.
- If the functions are in different languages, note that a direct import isn't possible and suggest a shared API contract instead.
- If callers exist in other files, list them and offer to update them too.
- For cross-service findings (different service directories), suggest extracting to a shared library module.
