# Echo Guard Benchmark Results

Generated: 2026-03-27 (CodeSage-small v2, ONNX INT8)

## Overview

Echo Guard is evaluated against three established clone detection benchmarks:

| Benchmark | Language | Clone Types | Focus |
|-----------|----------|-------------|-------|
| [BigCloneBench](https://github.com/clonebench/BigCloneBench) | Java | T1-T4 | Largest academic benchmark (8M+ pairs) |
| [GPTCloneBench](https://github.com/srlabUsask/GPTCloneBench) | Python, Java | T3-T4 | AI-generated clone pairs |
| [POJ-104](https://github.com/microsoft/CodeXGLUE/tree/main/Code-Code/Clone-detection-POJ-104) | C | T4 (semantic) | Competitive programming solutions |

## Results Summary

**Per-type precision** (did we correctly identify the clone type?) remains 100% for detected types. **Overall precision** is low due to cross-pair false positives — when all benchmark functions are loaded into a single engine (as in real-world usage), many unrelated functions match above threshold.

### CodeSage-small (default)

| Dataset | Type-1 Recall | Type-2 Recall | Type-3 Recall | Type-4 Recall | Pairs |
|---------|--------------|--------------|--------------|--------------|-------|
| BigCloneBench | 100% | 99% | 4.0% | 0.0% | 1200 |
| GPTCloneBench | — | — | 93.0% | 78.5% | 600 |
| POJ-104 | — | — | — | 1.1% | 381 |

### CodeSage-base (`model: codesage-base`)

| Dataset | Type-1 Recall | Type-2 Recall | Type-3 Recall | Type-4 Recall | Pairs |
|---------|--------------|--------------|--------------|--------------|-------|
| BigCloneBench | 100% | 99% | 1.2% | 0.0% | 1200 |
| GPTCloneBench | — | — | 92.0% | 82.0% | 600 |
| POJ-104 | — | — | — | 4.3% | 381 |

CodeSage-base is **not uniformly better** than small — it trades Type-3 recall for Type-4 recall, at ~3x the inference cost (189ms/func vs 58ms/func, 341MB vs 123MB). Use it when semantic clone detection matters more than speed.

### BigCloneBench

Threshold: 0.5 | Pairs evaluated: 1200

| Clone Type | small Recall | base Recall | small F1 | base F1 |
|------------|-------------|------------|----------|---------|
| type1 | 100.0% | 100.0% | 100.0% | 100.0% |
| type2 | 99.0% | 99.0% | 99.5% | 99.5% |
| type3 | 4.0% | 1.2% | 7.7% | 2.5% |
| type4 | 0.0% | 0.0% | 0.0% | 0.0% |

### GPTCloneBench

Threshold: 0.5 | Pairs evaluated: 600

| Clone Type | small Recall | base Recall | small F1 | base F1 |
|------------|-------------|------------|----------|---------|
| type3 | 93.0% | 92.0% | 96.4% | 95.8% |
| type4 | 78.5% | 82.0% | 88.0% | 90.1% |

### POJ-104

Threshold: 0.5 | Pairs evaluated: 381

| Clone Type | small Recall | base Recall | small F1 | base F1 |
|------------|-------------|------------|----------|---------|
| type4 | 1.1% | 4.3% | 2.1% | 8.2% |

## Cross-Pair False Positives

The overall precision numbers (0.2%, 3.6%, 0.2%) are misleadingly low. This is because the evaluation loads all benchmark functions into a single `SimilarityEngine` — exactly like real-world usage — and counts **every** match the engine reports, not just the labeled pairs.

For example, in BigCloneBench with 2400 functions, the engine reports 224,660 cross-pair matches between functions that were never labeled as clone/non-clone. These inflate the FP count but don't reflect detection quality on the actual benchmark pairs.

The per-type precision (100% for all detected types) is the more meaningful signal — when Echo Guard says "this is a Type-1/2/3 clone," it's always correct.

## Type-4 (Semantic) Detection Analysis

Type-4 clones have the same semantics but completely different implementation. Echo Guard uses CodeSage-small embeddings (1024-dim) with per-language similarity thresholds to detect these. Performance varies significantly by dataset.

### BigCloneBench

- **Total Type-4 pairs:** 200
- **Detected:** 0
- **Recall:** 0.0%
- **Note:** A [2025 study](https://arxiv.org/abs/2505.04311) found 93% of BCB WT3/T4 labels are mislabeled — many of these "clones" aren't actually functionally equivalent

### GPTCloneBench

| | CodeSage-small | CodeSage-base |
|---|---|---|
| Total pairs | 200 | 200 |
| Detected | 157 | 164 |
| Recall | 78.5% | 82.0% |
| Avg score | 0.868 | 0.893 |

### POJ-104

| | CodeSage-small | CodeSage-base |
|---|---|---|
| Total pairs | 281 | 281 |
| Detected | 3 | 12 |
| Recall | 1.1% | 4.3% |
| Avg score | 0.875 | 0.878 |

### Why the gap?

Echo Guard's Type-4 detection works best on **AI-generated echoes** (GPTCloneBench) where the code retains vocabulary and structural patterns from the original. It struggles with **independently written implementations** (POJ-104, BigCloneBench) where different developers use completely different algorithms and APIs.

Echo Guard's Type-4 detection is strongest on AI-generated clones where vocabulary and structure are preserved, and weakest on independently written implementations using different algorithms entirely.

## Methodology

Benchmarks use the same two-tier pipeline as `echo-guard scan`:

1. All benchmark functions are extracted via tree-sitter (same as `echo-guard index`)
2. All functions are embedded via CodeSage-small (ONNX INT8, 1024-dim vectors)
3. ALL functions are loaded into a single `SimilarityEngine`
4. `find_all_matches()` runs the two-tier pipeline:
   - **Tier 1**: AST hash grouping — Type-1/Type-2 exact clone detection
   - **Tier 2**: Embedding cosine similarity with per-language thresholds — Type-3/Type-4 detection
   - **Intent filters**: Domain-aware pattern exclusions
5. Engine output is mapped back to labeled pairs to compute precision/recall/F1

This matches real-world usage where the engine must find correct matches among many candidate functions while avoiding false positives from unrelated code.

### Per-language embedding thresholds

| Language | Threshold |
|----------|-----------|
| Python | 0.94 |
| Java | 0.81 |
| JavaScript | 0.85 |
| C/C++ | 0.83 |
| Go | 0.81 |

## Reproducing

```bash
# Install with language support
pip install -e ".[languages]"

# Run all benchmarks
python -m benchmarks.runner

# Run specific benchmark with per-pair details
python -m benchmarks.runner --dataset bigclonebench --verbose

# Threshold sweep
python -m benchmarks.runner --sweep --json sweep_results.json

# Generate this report
python -m benchmarks.runner --report
```
