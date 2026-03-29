# Roadmap

Echo Guard's development roadmap, organized by release phase.

For the changelog, see [CHANGELOG.md](CHANGELOG.md).

---

## Phase 1 — Benchmarking & Validation (v0.2.0) ✓

Prove detection quality against established academic datasets.

- [x] Benchmark adapter for [BigCloneBench](https://github.com/clonebench/BigCloneBench) (8M+ Java clone pairs, Type-1 through Type-4)
- [x] Benchmark adapter for [GPTCloneBench](https://github.com/srlabUsask/GPTCloneBench) (AI-generated clone pairs — Python, Java)
- [x] Benchmark adapter for [POJ-104](https://github.com/microsoft/CodeXGLUE/tree/main/Code-Code/Clone-detection-POJ-104) (semantic clones, C/C++)
- [x] Publish precision/recall/F1 results per clone type in README
- [x] Identify Type-4 (semantic) detection gaps to guide Phase 2

---

## Phase 2 — GitHub PR Integration (v0.2.0) ✓

Surface duplicate detection directly in pull request reviews.

- [x] GitHub Action that runs `echo-guard check` on changed files
- [x] Post inline PR annotations on detected duplicates (filtered by severity)
- [x] Summary comment with findings table, severity breakdown, and suggested fixes
- [x] Configurable: fail PR on extract-severity matches (`fail-on` input)

---

## Phase 3 — Intent Filters & DRY Severity (v0.3.0) ✓

AST edit distance, intent-aware filtering, and DRY-based severity model.

- [x] **AST edit distance** — Zhang-Shasha algorithm on normalized token sequences for precise structural similarity
- [x] **Intent filters** — verb+noun suppression, UI wrapper suppression, CRUD operations, constructor exclusion, observer pattern, framework exports, service boilerplate
- [x] **DRY-based severity model**: `extract` = 3+ copies (extract now), `review` = 2 copies (worth noting)
- [x] **Action-based report output** — grouped by Extract Now / Worth Noting / Cross-Service / Cross-Language with summary block (top targets + hotspot files)
- [x] Test file exclusion by default (`--include-tests` to opt in)
- [x] Dotfile directory exclusion (`.claude/`, `.codex/`, etc.)
- [x] Progress bars with elapsed time and ETA for all scan phases
- [x] Setup wizard improvements: detects existing config/index/scan, Ctrl+C handling, directory previews
- [x] Config renamed to `echo-guard.yml` (consistent with `.echo-guard/` data directory)
- [x] MCP response includes `severity`, `copies_in_codebase`, DRY-aligned action guidance

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

---

## Phase 5 — Feedback Consent (v0.4.1)

Begin collecting real-world signal to improve detection quality over time.

- [x] **Repo visibility detection** — auto-detect public/private via GitHub/GitLab API (`echo_guard/repo_detect.py`)
- [x] **Smart-default consent** — public repos default to public tier, private repos to private tier
- [x] **Setup wizard consent prompt** — data sharing level selection during `echo-guard setup`
- [x] **`echo-guard consent` command** — view or change tier anytime
- [x] **`echo-guard feedback-preview` command** — preview exactly what would be uploaded
- [x] **Automatic batched uploads** — fire-and-forget upload after scan/review/check sessions
- [x] **Upload module** — payload preparation, path stripping, JSONL POST (`echo_guard/upload.py`)
- [x] **DuckDB `uploaded_at` tracking** — rows marked as uploaded, retry on next session if failed
- [x] **Daemon integration** — `get_config` RPC method, upload after every 5 verdicts
- [x] **VS Code extension** — consent tier shown in status bar tooltip
- [x] **Cloudflare Worker + R2 backend** — lightweight POST handler with R2 storage (`worker/`)
- [x] **Feedback schema documentation** — field-level docs at `docs/FEEDBACK_SCHEMA.md`

### Feedback & Data Consent Model

Users choose their data sharing level during setup. This is how Echo Guard improves over time while respecting code privacy.

| Tier | Label | What's collected | Who it's for |
|---|---|---|---|
| **Private** (default for private repos) | "Share decisions, not code" | Anonymized structural features + verdicts only: language, line counts, param counts, similarity score, verdict. **No source code, no file paths, no function names.** | All users — this is the default because nothing sensitive is collected |
| **Public** (default for public repos) | "Share code samples" | Anonymized code pairs + verdicts. Function source is included but file paths and repo identifiers are stripped. Only collected from public repositories (auto-detected via `git remote`). | Open source projects willing to contribute training data |
| **None** | "No data sharing" | Nothing leaves the machine. Training data and feedback stay in local DuckDB only. | Users who explicitly opt out |

**How consent works:**
- First run: `echo-guard setup` shows the data sharing tier (defaults based on repo visibility)
- Stored in `echo-guard.yml` as `feedback_consent: private | public | none`
- Can be changed anytime via `echo-guard consent`
- Run `echo-guard feedback-preview` to see exactly what would be uploaded
- VS Code extension shows the setting in the status bar tooltip

**What the data is used for:**
- **Private tier**: Calibrate per-language embedding thresholds, train a lightweight false-positive classifier (no code needed — just decision patterns)
- **Public tier**: Fine-tune CodeSage-small via contrastive learning on real clone/not-clone pairs, then publish the improved model for everyone

**Why this matters:** Real-world false-positive signal is the only way to improve per-language thresholds beyond manual calibration. Without it, detection quality is frozen at whatever was set during development.

---

## Phase 6 — Intra-Function Detection (v0.6.0)

Detect similar multi-line code blocks *within* functions, not just whole-function duplicates.

- [ ] **Block-level clone detection** — identify repeated code snippets (3+ lines) across functions that could be extracted into helpers
- [ ] **Pattern extraction** — detect repeated try/catch wrappers, validation blocks, response formatting, logging boilerplate within function bodies
- [ ] **Inline refactoring hints** — "lines 42-48 in handler_a() are identical to lines 15-21 in handler_b() — extract to a shared helper"
- [ ] **Sliding window AST matching** — compare AST subtrees within function bodies, not just whole-function hashes
- [ ] Works alongside whole-function detection (Tiers 1-2) as a complementary analysis
- [ ] **Parallelize embedding computation** — worker pool for initial index; meaningfully speeds up first-run on large repos
- [ ] **Cache dependency graph between scans** — currently rebuilt on every scan; persist to DuckDB and invalidate on file change

**Why this matters:** AI agents often copy-paste code blocks within functions, not just entire functions. A 30-line function with 10 lines of boilerplate repeated across 5 handlers is a real DRY violation that whole-function detection misses. This also directly improves Type-3 recall, which sits at 15% on human-written clones. The performance improvements reduce latency for users who scan frequently.

---

## Phase 7 — Finding History & Lifecycle (v0.7.0)

Track finding state over time — mark stale findings, show trends, maintain an audit trail.

- [ ] **Finding timeline** — track when each finding was first detected, when code changed, when it was resolved
- [ ] **Stale finding detection** — automatically mark findings as outdated when the underlying code changes (file deleted, function renamed, logic modified)
- [ ] **Resolution history** — full audit trail: when resolved, what verdict, what commit
- [ ] **Trend dashboard** — `echo-guard trends` shows redundancy over time: new findings introduced per sprint, findings resolved, net DRY improvement
- [ ] **Regression detection** — alert when a previously fixed finding reappears (someone re-introduced the duplicate)
- [ ] **Health score history** with sparkline visualization in CLI

**Why this matters:** DRY is a continuous process, not a one-time scan. Teams need to see whether redundancy is improving or getting worse over time, and stale findings clutter the report with noise about code that no longer exists. This is pure DuckDB work — no new models or APIs required.

---

## Phase 8 — v1.0 Publishing

Stable public release after the feature set is complete and breaking changes are done.

- [ ] **VS Code Marketplace publish** — versioned release, icon, README, marketplace listing
- [ ] **GitHub Marketplace publish** — stable versioned Action release with monorepo path filter support
- [ ] **Update benchmarks** — re-run BigCloneBench, GPTCloneBench, POJ-104 with CodeSage-small (current README numbers reflect UniXcoder)
- [ ] **v1.0 release** — semver stability commitment, migration guide from 0.x

**Why publish last:** Early adopters are the most valuable cohort for feedback consent data. Publishing before consent collection is implemented loses that signal permanently. Intra-function detection may also change output format and severity groupings — better to ship one stable v1.0 than create churn for early users.

---

## Research Directions

Longer-term explorations that could become features once feedback data is available.

- **Learned false-positive classifier**: Train a lightweight classifier on real labeled pairs collected via the feedback consent system. Features: sql_verb_match, http_method_match, hook_overlap (React), jsx_tag_overlap, uncommon_token_overlap (TF-IDF weighted), statement_type_histogram. Unlike the intent filter rules, this would generalize to patterns not explicitly enumerated.
- **Fine-tune CodeSage-small for semantic detection**: Cross-language contrastive fine-tuning with explicit Python↔Java positive pairs to improve cross-language clone retrieval. Requires public-tier feedback data at scale.
- **Framework-specific detection**: Deeper understanding of Next.js, Django, NestJS, Spring Boot patterns to reduce false positives and surface framework-idiomatic consolidation opportunities.
- **Type signature pre-filtering**: Extract function signatures as a cheap pre-filter before embedding comparison. Two functions with incompatible signatures can't be semantic clones.

---

## Competitive Landscape

Echo Guard occupies a unique position in the clone detection space:

| Capability | Traditional Tools (PMD CPD, jscpd, SonarQube) | Academic Models (CodeBERT, CodeSage) | Echo Guard |
|---|---|---|---|
| Type-1/2 detection | Yes | Yes | Yes (100% recall) |
| Type-3 near-miss | Some (NiCad: 95%) | Yes | **Partial** (82% on AI-generated, 15% on human-written) |
| Type-4 semantic | No | Limited | **Partial** (69.5% on AI echoes, 17% on independent implementations) |
| Intent-aware filtering | No | No | **Yes** (domain-aware intent filters) |
| Real-time editor integration | No | No | **Yes** (VS Code extension with daemon) |
| Real-time pre-write | No | No | **Yes** (MCP) |
| AI-agent awareness | No | No | **Yes** |
| Cross-language | No | Partial | **Yes** (9 languages) |
| Incremental indexing | No | No | **Yes** |
| MCP integration | No | No | **Yes** |

Key references:
- [BigCloneBench](https://github.com/clonebench/BigCloneBench) — Svajlenko & Roy, ICSE 2014
- [GPTCloneBench](https://github.com/srlabUsask/GPTCloneBench) — Alam et al., ICSME 2023
- [CodeBERT](https://github.com/microsoft/CodeBERT) — Feng et al., EMNLP 2020
- [CodeSage](https://github.com/amazon-science/CodeSage) — Dai et al., ICLR 2024
- [UniXcoder](https://github.com/microsoft/CodeBERT/tree/master/UniXcoder) — Guo et al., ACL 2022
- [Aroma](https://arxiv.org/abs/1812.01158) — Luan et al., OOPSLA 2019
