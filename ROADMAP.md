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

## Phase 2 — GitHub PR Integration (v0.3.0)

Surface duplicate detection directly in pull request reviews.

- [x] GitHub Action that runs `echo-guard check` on changed files
- [x] Post inline PR annotations on detected duplicates (filtered by severity)
- [x] Summary comment with findings table, severity breakdown, and suggested fixes
- [x] Configurable: fail PR on high-severity matches (`fail-on` input)
- [ ] Publish to GitHub Marketplace with stable versioned releases
- [ ] Support for monorepo path filters (only scan specific directories)

**Why this matters:** Catches AI-generated duplicates at review time, before they merge. Works with any CI provider via the existing CLI.

---

## Phase 3 — Semantic Detection Upgrade (v0.4.0)

Add optional learned embeddings for Type-4 (semantic) clone detection.

- [x] Two-tier retrieval architecture:
  - Tier 1: AST hash matching for Type-1/Type-2 (O(1))
  - Tier 2: UniXcoder embeddings for Type-3/Type-4 (cosine similarity)
- [x] Evaluate embedding models — selected UniXcoder (95.18% MAP@R on POJ-104, Apache-2.0)
- [x] Embeddings included in base install — all clone types detected out of the box
- [x] ONNX Runtime with INT8 quantization (~125MB model, ~10-20ms/function on CPU)
- [x] Model downloads on first use, cached locally in `~/.cache/echo-guard/models/`
- [x] Disk-backed embedding storage via NumPy memmap (`.echo-guard/embeddings.npy`)
- [x] Incremental embedding computation (only new/changed functions re-embedded)
- [ ] Re-benchmark with embeddings enabled to measure improvement (infrastructure ready)

**Why this matters:** AI agents frequently generate semantically identical code with completely different structure (recursive vs iterative, different variable names AND control flow). AST hashing alone misses these. Code embeddings catch them — UniXcoder achieves 95.18% MAP@R on POJ-104 semantic clones.

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
