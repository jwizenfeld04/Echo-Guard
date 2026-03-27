You are running the `/echo-guard-search` skill. Search the Echo Guard function index.

## Input

Accept a search query — can be:
- A function name or partial name (e.g. `validate_token`)
- An implementation description (e.g. `jwt decode`)
- A call name used inside functions (e.g. `requests.get`)
- A class method pattern (e.g. `UserManager.create`)

Optionally accept `--language <lang>` to filter results.

## Steps

1. **Run the search**:
   ```
   echo-guard search "<query>" [--language <lang>] --output json
   ```

2. **Display results** in a readable table:
   ```
   Results for "validate_token" (5 found)

   1. validate_token()         python   src/auth/middleware.py:42
      def validate_token(token: str) -> bool:
          ...

   2. validateToken()          typescript   frontend/src/api/auth.ts:18
      function validateToken(token: string): boolean {
          ...
   ```
   - Show function name, language, file path with line number
   - Show first 3 lines of source as a preview

3. **Offer to open/read a found function**:
   > "Would you like me to read the full source of any of these?"
   - If yes: read the relevant file section and display it.

## Notes
- If no index exists, tell the user to run `echo-guard index` first.
- If no results are found, suggest broadening the query or checking the language filter.
- Results are ordered by name match first, then by file path.
- Maximum 20 results are returned by default; mention if results may be truncated.
