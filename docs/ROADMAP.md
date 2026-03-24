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

## Phase 4 — VS Code Extension (v0.4.0)

Real-time duplicate detection in the editor.

- [ ] Extension built on Echo Guard's MCP server (no separate implementation)
- [ ] Inline diagnostics (squiggly underlines on duplicate functions)
- [ ] Quick actions: "Show existing implementation", "Replace with import"
- [ ] Status bar health score
- [ ] Auto-index on workspace open, incremental re-index on save
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

## Phase 5 — LLM-Assisted Refactoring (v0.5.0)

Automated consolidation suggestions powered by LLMs.

- [ ] `echo-guard scan --refactor` flag sends high-confidence matches to an LLM with full context (both functions, callers, dependency graph clusters)
- [ ] Outputs concrete patch/diff for consolidation
- [ ] Supports multiple LLM backends (Claude API, OpenAI, local models)
- [ ] Respects service boundaries — suggests shared libraries for cross-service duplicates

**Why this matters:** Detection without actionable fixes creates toil. LLM-generated patches close the loop from "you have a duplicate" to "here's the refactored code."

---

## Phase 6 — Scale & Performance (v0.6.0+)

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

Longer-term explorations that could become features:

- **Fine-tune UniXcoder on clone detection pairs**: The feature classifier currently compensates for embedding noise. Fine-tuning the embedding model itself on labeled clone/not-clone pairs would improve Tier 2 precision and reduce the classifier's burden. Training data is collected from `resolve_finding` and `respond_to_probe` verdicts.
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
| Type-1/2 detection | Yes | Yes | Yes |
| Type-3 near-miss | Some (NiCad: 95%) | Yes | **Yes** (embeddings, Phase 3) |
| Type-4 semantic | No | Yes | **Yes** (embeddings, Phase 3) |
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
