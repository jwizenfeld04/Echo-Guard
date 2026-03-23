# Roadmap

Echo Guard's development roadmap, organized by release phase.

For the changelog, see [CHANGELOG.md](CHANGELOG.md).

---

## Phase 1 — Benchmarking & Validation (v0.2.0)

Prove detection quality against established academic datasets.

- [ ] Benchmark adapter for [BigCloneBench](https://github.com/clonebench/BigCloneBench) (8M+ Java clone pairs, Type-1 through Type-4)
- [ ] Benchmark adapter for [GPTCloneBench](https://github.com/AluaBa662/GPTCloneBench) (AI-generated clone pairs — Python, Java)
- [ ] Benchmark adapter for [POJ-104](https://github.com/microsoft/CodeXGLUE/tree/main/Code-Code/Clone-detection-POJ-104) (semantic clones, C/C++)
- [ ] Publish precision/recall/F1 results per clone type in README
- [ ] Identify Type-4 (semantic) detection gaps to guide Phase 2

**Why this matters:** No CLI clone detection tool publishes benchmark results. This builds credibility and shows exactly where Echo Guard excels (Type-1/2/3, cross-language) and where it needs improvement (Type-4 semantic clones).

---

## Phase 2 — Semantic Detection Upgrade (v0.3.0)

Add optional learned embeddings for Type-4 (semantic) clone detection.

- [ ] Two-tier retrieval architecture:
  - Tier 1 (current): LSH + TF-IDF for fast candidate retrieval (milliseconds)
  - Tier 2 (new): Code embeddings for high-accuracy re-ranking (seconds, opt-in)
- [ ] Evaluate embedding models (UniXcoder, CodeBERT, sentence-transformers)
- [ ] Optional install: `pip install "echo-guard[embeddings]"` — keeps base tool lightweight
- [ ] Model downloads on first use (~500MB), cached locally
- [ ] Re-benchmark with embeddings enabled to measure improvement

**Why this matters:** AI agents frequently generate semantically identical code with completely different structure (recursive vs iterative, different variable names AND control flow). TF-IDF misses these. Code embeddings catch them — CodeBERT-class models score ~97% F1 on BigCloneBench Type-4.

---

## Phase 3 — GitHub PR Integration (v0.4.0)

Surface duplicate detection directly in pull request reviews.

- [ ] GitHub Action that runs `echo-guard check` on changed files
- [ ] Post inline PR annotations on detected duplicates
- [ ] Summary comment with match count, severity breakdown, and suggestions
- [ ] Configurable: fail PR on high-severity matches (like CI linting)

**Why this matters:** Catches AI-generated duplicates at review time, before they merge. Works with any CI provider via the existing CLI.

---

## Phase 4 — VS Code Extension (v0.5.0)

Real-time duplicate detection in the editor.

- [ ] Extension built on Echo Guard's MCP server (no separate implementation)
- [ ] Inline diagnostics (squiggly underlines on duplicate functions)
- [ ] Quick actions: "Show existing implementation", "Replace with import"
- [ ] Status bar health score
- [ ] Auto-index on workspace open, incremental re-index on save

**Why this matters:** Catches duplicates at write time, not after commit. This is the tightest possible feedback loop — before the code even leaves the editor.

---

## Phase 5 — LLM-Assisted Refactoring (v0.6.0)

Automated consolidation suggestions powered by LLMs.

- [ ] `echo-guard scan --refactor` flag sends high-confidence matches to an LLM with full context (both functions, callers, dependency graph clusters)
- [ ] Outputs concrete patch/diff for consolidation
- [ ] Supports multiple LLM backends (Claude API, OpenAI, local models)
- [ ] Respects service boundaries — suggests shared libraries for cross-service duplicates

**Why this matters:** Detection without actionable fixes creates toil. LLM-generated patches close the loop from "you have a duplicate" to "here's the refactored code."

---

## Phase 6 — Scale & Performance (v0.7.0+)

Optimize for large monorepos and enterprise codebases.

- [ ] Shard LSH index per domain cluster
- [ ] Parallelize TF-IDF computation across workers
- [ ] Cache dependency graph between scans (currently rebuilt every run)
- [ ] Streaming scan mode for 100K+ function codebases
- [ ] Incremental MCP server — re-index only changed files on each query

---

## Research Directions

Longer-term explorations that could become features:

- **Contrastive learning on user feedback**: If users confirm/reject matches (via VS Code extension), fine-tune a small model specifically for AI-generated clone detection. No one has done this yet.
- **Cross-language refactoring**: When the same logic exists in Python and TypeScript, suggest consolidating to one language with a shared API.
- **Codebase evolution tracking**: Use health score history to detect redundancy trends over time and alert when duplication rate accelerates.
- **Framework-specific detection**: Deeper understanding of Next.js, Django, NestJS, Spring Boot patterns to reduce false positives and surface framework-idiomatic consolidation opportunities.

---

## Competitive Landscape

Echo Guard occupies a unique position in the clone detection space:

| Capability | Traditional Tools (PMD CPD, jscpd, SonarQube) | Academic Models (CodeBERT, UniXcoder) | Echo Guard |
|---|---|---|---|
| Type-1/2 detection | Yes | Yes | Yes |
| Type-3 near-miss | Some (NiCad) | Yes | Yes |
| Type-4 semantic | No | Yes | Planned (Phase 2) |
| Real-time pre-write | No | No | **Yes** (MCP) |
| AI-agent awareness | No | No | **Yes** |
| Refactoring suggestions | No | No | **Yes** |
| Cross-language | No | Partial | **Yes** (9 languages) |
| Incremental indexing | No | No | **Yes** |
| MCP integration | No | No | **Yes** |

Key references:
- [BigCloneBench](https://github.com/clonebench/BigCloneBench) — Svajlenko & Roy, ICSE 2014
- [GPTCloneBench](https://github.com/AluaBa662/GPTCloneBench) — Alam et al., 2024
- [CodeBERT](https://github.com/microsoft/CodeBERT) — Feng et al., EMNLP 2020
- [UniXcoder](https://github.com/microsoft/CodeBERT/tree/master/UniXcoder) — Guo et al., ACL 2022
- [Aroma](https://arxiv.org/abs/1812.01158) — Luan et al., OOPSLA 2019
