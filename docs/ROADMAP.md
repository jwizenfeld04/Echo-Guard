# Roadmap

Echo Guard's development roadmap, organized by release phase.

For the changelog, see [CHANGELOG.md](CHANGELOG.md).

---

## Phase 1 — Benchmarking & Validation (v0.2.0)

Prove detection quality against established academic datasets.

- [x] Benchmark adapter for [BigCloneBench](https://github.com/clonebench/BigCloneBench) (8M+ Java clone pairs, Type-1 through Type-4)
- [x] Benchmark adapter for [GPTCloneBench](https://github.com/srlabUsask/GPTCloneBench) (AI-generated clone pairs — Python, Java)
- [x] Benchmark adapter for [POJ-104](https://github.com/microsoft/CodeXGLUE/tree/main/Code-Code/Clone-detection-POJ-104) (semantic clones, C/C++)
- [x] Publish precision/recall/F1 results per clone type in README
- [x] Identify Type-4 (semantic) detection gaps to guide Phase 2

**Why this matters:** No CLI clone detection tool publishes benchmark results. This builds credibility and shows exactly where Echo Guard excels (Type-1/2) and where it needs improvement (Type-3/4 semantic clones).

---

## Phase 2 — GitHub PR Integration (v0.2.0) ✓

Surface duplicate detection directly in pull request reviews.

- [x] GitHub Action that runs `echo-guard check` on changed files
- [x] Post inline PR annotations on detected duplicates (filtered by severity)
- [x] Summary comment with findings table, severity breakdown, and suggested fixes
- [x] Configurable: fail PR on high-severity matches (`fail-on` input)
- [ ] Publish to GitHub Marketplace with stable versioned releases
- [ ] Support for monorepo path filters (only scan specific directories)

---

## Phase 3 — Classifier & DRY Severity (v0.3.0) ✓

Feature classifier, AST edit distance, and DRY-based severity model.

- [x] **Three-tier detection pipeline**: AST hash (Tier 1) → Embeddings (Tier 2) → Feature classifier (Tier 3)
- [x] **AST edit distance** — Zhang-Shasha algorithm on normalized token sequences for precise structural similarity
- [x] **14-feature classifier** — logistic regression combining AST distance, embedding score, name/body/call/literal overlap, control flow, parameter signatures, return shape, and context flags
- [x] **DRY-based severity model**: HIGH = 3+ copies (extract now), MEDIUM = 2 copies (worth noting), LOW = semantic matches (hidden by default)
- [x] **Action-based report output** — grouped by Extract Now / Worth Noting / Cross-Service / Cross-Language with summary block (top targets + hotspot files)
- [x] **Structural pattern rules** — verb+noun suppression, UI wrapper same-file suppression, short-body exact-structure filter
- [x] Test file exclusion by default (`--include-tests` to opt in)
- [x] Dotfile directory exclusion (`.claude/`, `.codex/`, etc.)
- [x] Progress bars with elapsed time and ETA for all scan phases
- [x] Setup wizard improvements: detects existing config/index/scan, Ctrl+C handling, directory previews
- [x] Config renamed to `echo-guard.yml` (consistent with `.echo-guard/` data directory)
- [x] MCP response includes `priority`, `copies_in_codebase`, DRY-aligned action guidance
- [x] 93% signal rate on real-world monorepo (up from 84% in v0.2.0)

**Why this matters:** The classifier replaces fragile hand-tuned filters with a learned model that improves with training data. The DRY severity model means CI only fails on findings that actually need fixing (3+ copies), not every exact match.

---

## Phase 4 — VS Code Extension (v0.4.0) ✓

Real-time duplicate detection in the editor.

- [x] Extension built on JSON-RPC daemon architecture (long-lived Python process)
- [x] Inline diagnostics (squiggly underlines on duplicate functions)
- [x] Quick actions: mark intentional, dismiss, jump to duplicate, side-by-side diff, send to AI
- [x] Findings tree view — sidebar panel with redundancy clusters, top targets, hotspot files
- [x] Review panel — webview with severity badges, clone types, and inline verdicts
- [x] Cross-language CodeLens — annotations showing matches in other languages
- [x] Status bar with daemon state and finding count
- [x] Auto-index on workspace open, incremental re-index on save and every 5 minutes
- [x] Branch-switch reindex — watches `.git/HEAD` for branch changes
- [x] MCP sync — resolve_finding routes through daemon, diagnostics clear immediately
- [x] ESLint + TypeScript linting for extension code
- [ ] Publish to VS Code Marketplace
- [ ] **Consent-based feedback collection** with three tiers:

### Feedback & Data Consent Model

Users choose their data sharing level during setup. This is how Echo Guard improves over time while respecting code privacy.

| Tier | Label | What's collected | Who it's for |
|---|---|---|---|
| **Private** (default) | "Share decisions, not code" | Anonymized structural features + verdicts only: language, line counts, param counts, similarity score, verdict. **No source code, no file paths, no function names.** | All users — this is the default because nothing sensitive is collected |
| **Public** | "Share code samples" | Anonymized code pairs + verdicts. Function source is included but file paths and repo identifiers are stripped. Only collected from public repositories (auto-detected via `git remote`). | Open source projects willing to contribute training data |
| **None** | "No data sharing" | Nothing leaves the machine. Training data and feedback stay in local DuckDB only. | Users who explicitly opt out |

**How consent works:**
- First run: `echo-guard setup` shows the data sharing tier (defaults to **private**)
- Stored in `echo-guard.yml` as `feedback_consent: private | public | none`
- Can be changed anytime via `echo-guard init` or editing the config
- VS Code extension shows the setting in the status bar
- **Default is private** — anonymized decisions are collected (no code, no paths, no names). Users can opt out if they choose, but clicking through setup collects by default.

**What the data is used for:**
- **Private tier**: Calibrate per-language embedding thresholds, train a lightweight false-positive classifier (no code needed — just decision patterns)
- **Public tier**: Fine-tune the UniXcoder embedding model via contrastive learning on real clone/not-clone pairs, then publish the improved model for everyone

See [FINE-TUNING.md](FINE-TUNING.md) for the full technical roadmap.

**Why this matters:** Catches duplicates at write time, not after commit. The feedback loop improves detection quality over time — the more people use it, the better it gets for everyone, with clear consent boundaries.

---

## Phase 5 — Intra-Function Detection (v0.5.0)

Detect similar multi-line code blocks *within* functions, not just whole-function duplicates.

- [ ] **Block-level clone detection** — identify repeated code snippets (3+ lines) across functions that could be extracted into helpers
- [ ] **Pattern extraction** — detect repeated try/catch wrappers, validation blocks, response formatting, logging boilerplate within function bodies
- [ ] **Inline refactoring hints** — "lines 42-48 in handler_a() are identical to lines 15-21 in handler_b() — extract to a shared helper"
- [ ] **Sliding window AST matching** — compare AST subtrees within function bodies, not just whole-function hashes
- [ ] Works alongside whole-function detection (Tiers 1-3) as a complementary analysis

**Why this matters:** AI agents often copy-paste code blocks within functions, not just entire functions. A 30-line function with 10 lines of boilerplate repeated across 5 handlers is a real DRY violation that whole-function detection misses.

---

## Phase 6 — AI-Powered Fixes (v0.6.0)

Full linting with automated fix generation, sent directly to the terminal or AI agent.

- [ ] `echo-guard scan --fix` generates concrete patches for HIGH findings (extract to shared module, update imports)
- [ ] `echo-guard scan --fix --apply` applies patches directly (with git safety — creates a branch)
- [ ] **MCP fix integration** — `suggest_refactor` returns a complete diff that AI agents can apply via terminal
- [ ] **Agent loop** — MCP agent detects duplicate → generates fix → applies fix → re-scans to verify, all in one flow
- [ ] Supports multiple LLM backends for fix generation (Claude API, local models via Ollama)
- [ ] Respects service boundaries — suggests shared libraries for cross-service, import statements for same-service

**Why this matters:** Detection without actionable fixes creates toil. Going from "you have a duplicate" to "here's the refactored code, applied" closes the loop entirely.

---

## Phase 7 — Finding History & Lifecycle (v0.7.0)

Track finding state over time — mark stale findings, show trends, maintain an audit trail.

- [ ] **Finding timeline** — track when each finding was first detected, when code changed, when it was resolved
- [ ] **Stale finding detection** — automatically mark findings as outdated when the underlying code changes (file deleted, function renamed, logic modified)
- [ ] **Resolution history** — full audit trail: who resolved it, when, what verdict, what commit
- [ ] **Trend dashboard** — `echo-guard trends` shows redundancy over time: new findings introduced per sprint, findings resolved, net DRY improvement
- [ ] **Regression detection** — alert when a previously fixed finding reappears (someone re-introduced the duplicate)
- [ ] **Health score history** with sparkline visualization in CLI
- [ ] Export finding lifecycle data for integration with project management tools (Linear, Jira, GitHub Issues)

**Why this matters:** DRY is a continuous process, not a one-time scan. Teams need to see whether redundancy is improving or getting worse over time, and stale findings clutter the report with noise about code that no longer exists.

---

## Phase 8 — Scale & Performance (v0.8.0+)

Optimize for large monorepos and enterprise codebases.

- [x] Disk-backed embedding storage via NumPy memmap (OS pages in on demand)
- [x] Memory-efficient SimilarityEngine (embeddings stored on disk, not in RAM)
- [x] USearch ANN index for >500K function codebases (`pip install "echo-guard[scale]"`)
- [x] Incremental embedding computation (only embed new/changed functions)
- [x] DuckDB schema for embedding row tracking and model version invalidation
- [ ] Parallelize embedding computation across workers
- [ ] Cache dependency graph between scans (currently rebuilt every run)
- [ ] Streaming scan mode for 100K+ function codebases
- [ ] Incremental MCP server — re-index only changed files on each query
- [ ] Full [BigCloneEval](https://github.com/jeffsvajlenko/BigCloneEval) integration — run Echo Guard as a registered tool against all 8.5M clone pairs using the standard academic evaluation protocol for direct comparison with published results

---

## Research Directions

Longer-term explorations that could become features. See [SEMANTIC-DETECTION-RESEARCH.md](SEMANTIC-DETECTION-RESEARCH.md) for detailed analysis.

- **Replace or fine-tune embeddings for semantic detection**: Current UniXcoder embeddings learn code structure, not behavior. Two promising approaches:
  - **CodeSage** (Amazon, 2024): 1.3B parameter encoder with contrastive training. 41% better than OpenAI embeddings on code search. Drop-in replacement.
  - **TransformCode-style contrastive fine-tuning**: Generate equivalent code variants via AST transformations, train with contrastive loss. Unsupervised, uses tree-sitter (already available). F1 82% on BigCloneBench.
- **Execution-based Tier 4**: For Python pure functions, generate test inputs via LLM, run both candidates in sandbox, compare outputs. HyClone (2025) achieved 1224% recall improvement over LLM-only detection with this approach.
- **LLM-as-judge verification**: Use LLM to evaluate top-N borderline pairs from Tier 2. o3-mini achieves F1 0.94 on CodeNet. Better suited for CI/PR checks than continuous scanning due to API cost.
- **Type signature pre-filtering**: Extract function signatures and use as cheap pre-filter. Two functions with incompatible signatures can't be semantic clones.
- **ONNX cross-encoder reranker**: A small (~30M param) cross-encoder that sees both functions simultaneously, producing more accurate similarity than independent embedding comparison. Would run on the ~200 candidate pairs as a Tier 2.5.
- **Cross-language refactoring**: When the same logic exists in Python and TypeScript, suggest consolidating to one language with a shared API.
- **Codebase evolution tracking**: Use health score history to detect redundancy trends over time and alert when duplication rate accelerates.
- **Framework-specific detection**: Deeper understanding of Next.js, Django, NestJS, Spring Boot patterns to reduce false positives and surface framework-idiomatic consolidation opportunities.
- **Additional classifier features**: sql_verb_match, http_method_match, hook_overlap (React), jsx_tag_overlap, uncommon_token_overlap (TF-IDF weighted), statement_type_histogram.

---

## Competitive Landscape

Echo Guard occupies a unique position in the clone detection space:

| Capability | Traditional Tools (PMD CPD, jscpd, SonarQube) | Academic Models (CodeBERT, UniXcoder) | Echo Guard |
|---|---|---|---|
| Type-1/2 detection | Yes | Yes | Yes (100% recall) |
| Type-3 near-miss | Some (NiCad: 95%) | Yes | **Partial** (82% on AI-generated, 15% on human-written) |
| Type-4 semantic | No | Limited | **Partial** (69.5% on AI echoes, 17% on independent implementations) |
| Intent-aware filtering | No | No | **Yes** (14-feature classifier + domain rules) |
| Real-time editor integration | No | No | **Yes** (VS Code extension with daemon) |
| Real-time pre-write | No | No | **Yes** (MCP) |
| AI-agent awareness | No | No | **Yes** |
| Refactoring suggestions | No | No | **Yes** |
| Cross-language | No | Partial | **Yes** (9 languages) |
| Incremental indexing | No | No | **Yes** |
| MCP integration | No | No | **Yes** |

Key references:
- [BigCloneBench](https://github.com/clonebench/BigCloneBench) — Svajlenko & Roy, ICSE 2014
- [GPTCloneBench](https://github.com/srlabUsask/GPTCloneBench) — Alam et al., ICSME 2023
- [CodeBERT](https://github.com/microsoft/CodeBERT) — Feng et al., EMNLP 2020
- [UniXcoder](https://github.com/microsoft/CodeBERT/tree/master/UniXcoder) — Guo et al., ACL 2022
- [Aroma](https://arxiv.org/abs/1812.01158) — Luan et al., OOPSLA 2019
