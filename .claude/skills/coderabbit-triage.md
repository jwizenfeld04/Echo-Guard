# coderabbit-triage

Review the active GitHub PR using CodeRabbit comments.

Process:

1. Read all CodeRabbit comments before making changes.
2. Triage each comment as:
   - fix
   - skip
   - needs attention

Rules:

- Only fix comments that materially improve correctness, reliability, security, or maintainability.
- Be skeptical of suggestions that are purely stylistic, redundant, or increase complexity without clear benefit.
- Do not blindly follow CodeRabbit — use your own judgment.
- Preserve the intended architecture and behavior.

Execution:

- Apply only high-value fixes.
- Keep changes minimal and localized.
- Avoid unrelated refactors or rewrites.
- If multiple comments overlap, consolidate into a single clean fix.

Output:

- Fixed: what was changed and why
- Skipped: what was ignored and why
- Needs attention: anything uncertain or requiring human judgment
