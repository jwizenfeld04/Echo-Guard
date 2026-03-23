# Echo Guard Benchmark Results

Generated: 2026-03-23

## How to Read These Results

Echo Guard uses a 4-stage detection pipeline: AST hash matching → signature filtering → LSH+TF-IDF similarity → intent filtering. The benchmarks run this exact pipeline — all functions are indexed together in a single `SimilarityEngine`, then `find_all_matches()` produces results, just like `echo-guard scan` does on a real codebase.

Each detection is assigned a **severity**:

- **High** (score ≥ 0.95): Near-identical code — strongest signal for duplication
- **Medium** (score ≥ 0.80): Structurally very similar — likely copy-paste with modifications
- **Low** (score ≥ threshold): Detectable similarity — may or may not warrant refactoring

---

## Results Summary

| Dataset                     | Precision | Recall | F1    | Pairs |
| --------------------------- | --------- | ------ | ----- | ----- |
| BigCloneBench (Java)        | 100.0%    | 40.8%  | 58.0% | 1,200 |
| GPTCloneBench (Java/Python) | 64.3%     | 88.8%  | 74.6% | 600   |
| POJ-104 (C)                 | 86.1%     | 10.9%  | 19.4% | 382   |

### BigCloneBench (1,200 pairs)

| Clone Type  | Precision | Recall    | F1        | TP   | FN     |
| ----------- | --------- | --------- | --------- | ---- | ------ |
| **Overall** | **100%**  | **40.8%** | **58.0%** | 408  | 592    |
| Type-1      | 100%      | 100%      | 100%      | 200  | 0      |
| Type-2      | 100%      | 100%      | 100%      | 200  | 0      |
| Type-3      | 100%      | 2.0%      | 3.9%      | 8    | 392    |
| Type-4      | —         | 0.0%      | 0.0%      | 0    | 200    |
| Negative    | —         | —         | —         | 0 FP | 200 TN |

Stratified sample of 1,000 clone pairs — 200 each for Type-1, Type-2, and Type-4, plus 400 for Type-3 (200 strong ≥0.7, 200 moderate 0.5-0.7) — plus 200 false positive pairs as negatives, from the BigCloneBench H2 database with source from the IJaDataset.

### GPTCloneBench (600 pairs)

| Clone Type   | Precision | Recall    | F1        | TP     | FN   |
| ------------ | --------- | --------- | --------- | ------ | ---- |
| **Overall**  | **64.3%** | **88.8%** | **74.6%** | 355    | 45   |
| Type-3 (MT3) | 100%      | 95.5%     | 97.7%     | 191    | 9    |
| Type-4       | 100%      | 82.0%     | 90.1%     | 164    | 36   |
| Negative     | —         | —         | —         | 197 FP | 3 TN |

200 pairs each of Type-3, Type-4, and false semantic clones from the GPTCloneBench standalone dataset (Java and Python, GPT-3/GPT-4 generated).

**Why Type-4 recall is high here (82%) but 0% on BCB:** GPT-generated "different implementations" still share vocabulary from the original seed function. Human-written Type-4 clones share almost no tokens.

**Why precision is low (64.3%):** 197 out of 200 false semantic clone pairs were flagged as matches. GPTCloneBench's "false" pairs are functions from the same SemanticCloneBench category — they share significant domain vocabulary despite being labeled as non-clones.

### POJ-104 (382 pairs)

| Clone Type  | Precision | Recall    | F1        | TP   | FN    |
| ----------- | --------- | --------- | --------- | ---- | ----- |
| **Overall** | **86.1%** | **10.9%** | **19.4%** | 31   | 253   |
| Type-4      | 100%      | 10.9%     | 19.7%     | 31   | 253   |
| Negative    | —         | —         | —         | 5 FP | 93 TN |

284 Type-4 pairs (same-problem solutions) plus 98 negative pairs (different-problem solutions) from the full 52,000-solution POJ-104 dataset. POJ-104 is purely Type-4 — every pair is two completely different C implementations of the same competitive programming problem. TF-IDF cannot detect semantic equivalence between fundamentally different algorithms, consistent with traditional tools scoring 0-2% on Type-4.

---

## How Echo Guard Compares

### vs. Traditional Tools (BigCloneBench)

Published results from [BigCloneEval](https://github.com/jeffsvajlenko/BigCloneEval) (Svajlenko & Roy):

| Tool           | T1       | T2       | VST3   | ST3   | MT3   | T4     | Precision |
| -------------- | -------- | -------- | ------ | ----- | ----- | ------ | --------- |
| NiCad          | 100%     | 100%     | 100%   | 95%   | 1%    | 0%     | 98%       |
| SourcererCC    | 100%     | 98%      | 93%    | 61%   | 6%    | 0%     | 86%       |
| CCAligner      | 100%     | 99%      | 97%    | 88%   | 63%   | 1%     | 83%       |
| Oreo           | 100%     | 100%     | 99%    | 94%   | 66%   | 2%     | —         |
| **Echo Guard** | **100%** | **100%** | **—**  | **—** | **—** | **0%** | **100%**  |

*Echo Guard's combined Type-3 recall (VST3+ST3+MT3) is 2% (8/400). We cannot report per-subtype breakdowns because our stratified sample mixes all Type-3 similarity ranges. Published tools use BigCloneEval's per-subtype protocol.*

_Sources: [SourcererCC paper](https://arxiv.org/abs/1603.01661), [LVMapper (arxiv:1909.04238)](https://arxiv.org/pdf/1909.04238), [StoneDetector (arxiv:2508.03435)](https://arxiv.org/pdf/2508.03435), [TACC (ICSE 2023)](https://wu-yueming.github.io/Files/ICSE2023_TACC.pdf)_

**Echo Guard's position:** Perfect on Type-1/2 with zero false positives. Type-3 recall (2% combined) is significantly lower than traditional tools that use line/token-level normalization. Type-4 is 0%, consistent with most traditional tools. The 100% precision on BigCloneBench means Echo Guard never flags non-clones — but this does not hold on GPTCloneBench (64.3% precision) where "false" pairs share significant domain vocabulary.

### vs. ML/Embedding Models (BigCloneBench — binary classification F1)

Published F1 scores from [CodeXGLUE](https://github.com/microsoft/CodeXGLUE/tree/main/Code-Code/Clone-detection-BigCloneBench) and [SCDetector (ASE 2020)](https://wu-yueming.github.io/Files/ASE2020_SCDetector.pdf):

| Model         | F1    | Per-type: T1 | T2   | ST3  | MT3  | WT3/T4 |
| ------------- | ----- | ------------ | ---- | ---- | ---- | ------ |
| SourcererCC   | —     | 1.00         | 1.00 | 0.65 | 0.20 | 0.02   |
| ASTNN         | 0.93  | 1.00         | 1.00 | 0.99 | 0.98 | 0.92   |
| CodeBERT      | 0.941 | —            | —    | —    | —    | —      |
| GraphCodeBERT | 0.950 | —            | —    | —    | —    | —      |
| SCDetector    | 0.98  | 1.00         | 1.00 | 0.99 | 0.99 | 0.97   |
| UniXcoder     | ~0.96 | —            | —    | —    | —    | —      |

_Sources: [CodeXGLUE (arXiv:2102.04664)](https://arxiv.org/abs/2102.04664), [SCDetector (Wu et al., ASE 2020)](https://wu-yueming.github.io/Files/ASE2020_SCDetector.pdf)_

> **Important caveat:** A [2025 study by Krinke et al.](https://arxiv.org/abs/2505.04311) found that **93% of WT3/T4 pairs in BigCloneBench are mislabeled** — they are not actually functionally similar. This means the high WT3/T4 F1 scores above (0.92-0.97) may partly reflect dataset artifacts. BigCloneBench remains valid for T1, T2, and T3 evaluation.

---

## Detailed Results by Clone Type

### Type-1: Exact Clones (whitespace/comment changes only)

| Dataset       | Recall | Pairs |
| ------------- | ------ | ----- |
| BigCloneBench | 100%   | 200   |

**How it works:** AST hash matching (Stage 1) catches these in O(1). Tree-sitter normalizes whitespace and strips comments during AST construction, so two functions that differ only in formatting produce identical AST hashes.

### Type-2: Renamed Identifiers

| Dataset       | Recall | Pairs |
| ------------- | ------ | ----- |
| BigCloneBench | 100%   | 200   |

**How it works:** AST hash normalization replaces identifiers with positional placeholders during hashing. `copyFile(src, dst)` and `copyData(source, destination)` produce the same normalized AST hash if their structure is identical.

### Type-3: Modified Statements (added/removed/changed lines)

| Dataset       | Recall          | Pairs |
| ------------- | --------------- | ----- |
| BigCloneBench | 2.0% (8/400)    | 400   |
| GPTCloneBench | 95.5% (191/200) | 200   |

**BCB recall is low** because real Type-3 pairs span a wide similarity range (0.5-0.7 token similarity). The TF-IDF cosine similarity drops below threshold for most of these. Traditional tools like CCAligner (63% MT3) and Oreo (66% MT3) use specialized token-level normalization that handles this better.

**GCB recall is high** because GPT-generated Type-3 clones retain most vocabulary from the original function — modifications are additive (extra parameters, error handling) rather than structural rewrites.

### Type-4: Semantic Clones (same functionality, different implementation)

| Dataset       | Recall          | Pairs |
| ------------- | --------------- | ----- |
| BigCloneBench | 0.0% (0/200)    | 200   |
| GPTCloneBench | 82.0% (164/200) | 200   |
| POJ-104       | 10.9% (31/284)  | 284   |

**This is the fundamental limitation of TF-IDF.** Type-4 clones share almost no tokens by definition. Results vary by dataset because GPT-generated "different implementations" still share vocabulary from the seed function, while human-written clones (BCB, POJ-104) share almost nothing.

All traditional tools also score 0-2% on Type-4. Meaningful Type-4 detection requires code embeddings (Phase 2).

### Negative Pairs

| Dataset       | True Negatives | False Positives |
| ------------- | -------------- | --------------- |
| BigCloneBench | 200/200 (100%) | 0               |
| GPTCloneBench | 3/200 (1.5%)   | 197             |
| POJ-104       | 93/98 (94.9%)  | 5               |

**BCB: Zero false positives** — Echo Guard correctly rejected all 200 pairs from the same functionality that human judges determined are NOT clones.

**GCB: High false positive rate** — GPTCloneBench's "false" pairs are functions from the same SemanticCloneBench category. They share significant domain vocabulary despite being labeled as non-clones, causing Echo Guard to flag them.

**POJ-104: Low false positive rate** — Only 5 cross-problem pairs flagged, likely solutions sharing common C idioms.

---

## Type-4 Gap Analysis

### Why This Matters for Phase 2

The Type-4 gap is the strongest argument for adding code embeddings:

1. **TF-IDF measures token overlap, not meaning.** Recursive fibonacci and iterative fibonacci solve the same problem but share almost no tokens.
2. **Traditional tools also score 0-2% on Type-4.** This is a fundamental limitation of syntactic approaches, not Echo Guard-specific.
3. **Code embedding models score 82-95% on semantic clones.** CodeBERT achieves 82.67% MAP@R on POJ-104; UniXcoder achieves 95.18%.
4. **Phase 2 architecture:** Tier 1 (current LSH+TF-IDF) for fast candidate retrieval, Tier 2 (CodeBERT/UniXcoder) for semantic re-ranking.

---

## Methodology

Benchmarks use the same pipeline as `echo-guard scan`:

1. All benchmark functions are extracted via tree-sitter (same as `echo-guard index`)
2. ALL functions are loaded into a single `SimilarityEngine` (realistic N-function index)
3. `find_all_matches()` runs the full 4-stage pipeline: AST hash → signature filter → LSH+TF-IDF → intent filter
4. Engine output is mapped back to labeled pairs to compute precision/recall/F1
5. Severity (high ≥0.95, medium ≥0.80, low <0.80) is tracked for each detection

- LSH threshold: 0.15 (same as production `scan_for_redundancy`)
- Similarity threshold: 0.50 (default)

### Dataset Details

**BigCloneBench:** Stratified sample of 1,000 clone pairs from the H2 database (200 per: Type-1, Type-2, Type-3 strong, Type-3 moderate, Type-4) plus 200 false positives. Source loaded from IJaDataset at exact line ranges. ~6.2 GB peak RAM.

**GPTCloneBench:** 200 Type-3 (MT3) + 200 Type-4 + 200 false semantic clone pairs from the standalone dataset. Java and Python functions generated by GPT-3/GPT-4.

**POJ-104:** 284 Type-4 pairs (3 per problem, sampled from 52,000 solutions) + 98 negative pairs (cross-problem). All C code from competitive programming.

### Limitations

- **Stratified sampling, not exhaustive:** BigCloneBench uses 1,200 of 8.5M+ pairs; GPTCloneBench uses 600 of 37K+; POJ-104 uses 382 of millions of possible pairs.
- **Not directly comparable to published results:** Our evaluation measures pairwise detection; BigCloneEval uses a different recall methodology, and POJ-104 uses MAP@R. Direct comparison requires running the same evaluation protocol (planned for Phase 6).
- **GPTCloneBench false positives:** The high FP rate (197/200) reflects the dataset's labeling — "false" pairs share significant domain vocabulary. This inflates the false positive count relative to real-world usage.

## Dataset Setup

See `benchmarks/SETUP.md` for instructions on downloading and preparing the benchmark datasets.

## Reproducing

```bash
# Install with language support
pip install -e ".[languages,dev]"

# Run all benchmarks (requires datasets — see benchmarks/SETUP.md)
python -m benchmarks.runner

# Run specific benchmark with per-pair details
python -m benchmarks.runner --dataset bigclonebench --verbose
python -m benchmarks.runner --dataset gptclonebench --verbose
python -m benchmarks.runner --dataset poj104 --verbose

# Threshold sweep to find optimal operating point
python -m benchmarks.runner --sweep --json sweep_results.json

# Export results to JSON
python -m benchmarks.runner --json results.json
```

## References

**Benchmarks:**

- [BigCloneBench](https://github.com/clonebench/BigCloneBench) — Svajlenko & Roy, ICSE 2014
- [BigCloneEval](https://github.com/jeffsvajlenko/BigCloneEval) — Svajlenko & Roy, ICSME 2015
- [GPTCloneBench](https://github.com/srlabUsask/GPTCloneBench) — Alam et al., ICSME 2023 ([arXiv:2308.13963](https://arxiv.org/abs/2308.13963))
- [POJ-104 / CodeXGLUE](https://github.com/microsoft/CodeXGLUE/tree/main/Code-Code/Clone-detection-POJ-104) — Lu et al., NeurIPS 2021 ([arXiv:2102.04664](https://arxiv.org/abs/2102.04664))

**Models:**

- [CodeBERT](https://github.com/microsoft/CodeBERT) — Feng et al., EMNLP 2020
- [GraphCodeBERT](https://arxiv.org/abs/2009.08366) — Guo et al., ICLR 2021
- [UniXcoder](https://aclanthology.org/2022.acl-long.499.pdf) — Guo et al., ACL 2022
- [SCDetector](https://wu-yueming.github.io/Files/ASE2020_SCDetector.pdf) — Wu et al., ASE 2020

**Tools:**

- [SourcererCC](https://arxiv.org/abs/1603.01661) — Sajnani et al., ICSE 2016
- [NiCad](https://www.txl.ca/nicaddownload.html) — Roy & Cordy, ICPC 2008
- [CCAligner](https://dl.acm.org/doi/10.1109/ASE.2018.00019) — Wang et al., ASE 2018

**Analysis:**

- [BigCloneBench mislabeling critique](https://arxiv.org/abs/2505.04311) — Krinke et al., 2025
- ["Are Classical Clone Detectors Good Enough For the AI Era?"](https://arxiv.org/abs/2509.25754) — 2025
- [DL models on GPTCloneBench](https://arxiv.org/abs/2412.14739) — Nag Pinku et al., ICSME 2024
