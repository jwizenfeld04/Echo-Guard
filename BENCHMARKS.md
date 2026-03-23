# Echo Guard Benchmark Results

Generated: 2026-03-23

## Overview

Echo Guard is evaluated against three established clone detection benchmarks:

| Benchmark | Language | Clone Types | Focus |
|-----------|----------|-------------|-------|
| [BigCloneBench](https://github.com/clonebench/BigCloneBench) | Java | T1-T4 | Largest academic benchmark (8M+ pairs) |
| [GPTCloneBench](https://github.com/srlabUsask/GPTCloneBench) | Python, Java | T3-T4 | AI-generated clone pairs |
| [POJ-104](https://github.com/microsoft/CodeXGLUE/tree/main/Code-Code/Clone-detection-POJ-104) | C | T4 (semantic) | Competitive programming solutions |

## Results Summary

| Dataset | Precision | Recall | F1 | Type-4 Recall | Pairs |
|---------|-----------|--------|----|----|-------|
| BigCloneBench | 95.2% | 63.4% | 76.1% | 0.0% | 1200 |
| GPTCloneBench | 67.2% | 97.2% | 79.5% | 96.0% | 600 |
| POJ-104 | 76.5% | 78.6% | 77.5% | 78.6% | 381 |

### BigCloneBench

Threshold: 0.5 | Pairs evaluated: 1200 | Time: 293.0s

Severity distribution: high: 421, medium: 245

| Clone Type | Precision | Recall | F1 | TP | FP | TN | FN |
|------------|-----------|--------|----|----|----|----|-----|
| negative | 0.0% | 0.0% | 0.0% | 0 | 32 | 168 | 0 |
| type1 | 100.0% | 100.0% | 100.0% | 200 | 0 | 0 | 0 |
| type2 | 100.0% | 100.0% | 100.0% | 200 | 0 | 0 | 0 |
| type3 | 100.0% | 58.5% | 73.8% | 234 | 0 | 0 | 166 |
| type4 | 0.0% | 0.0% | 0.0% | 0 | 0 | 0 | 200 |

### GPTCloneBench

Threshold: 0.5 | Pairs evaluated: 600 | Time: 81.5s

Severity distribution: high: 310, medium: 269

| Clone Type | Precision | Recall | F1 | TP | FP | TN | FN |
|------------|-----------|--------|----|----|----|----|-----|
| negative | 0.0% | 0.0% | 0.0% | 0 | 190 | 10 | 0 |
| type3 | 100.0% | 98.5% | 99.2% | 197 | 0 | 0 | 3 |
| type4 | 100.0% | 96.0% | 98.0% | 192 | 0 | 0 | 8 |

### POJ-104

Threshold: 0.5 | Pairs evaluated: 381 | Time: 85.8s

Severity distribution: high: 26, medium: 263

| Clone Type | Precision | Recall | F1 | TP | FP | TN | FN |
|------------|-----------|--------|----|----|----|----|-----|
| negative | 0.0% | 0.0% | 0.0% | 0 | 68 | 32 | 0 |
| type4 | 100.0% | 78.6% | 88.0% | 221 | 0 | 0 | 60 |

## Improvement over TF-IDF Baseline

The two-tier architecture (AST hash + UniXcoder embeddings) replaces the previous LSH + TF-IDF pipeline. Improvements on each benchmark:

| Metric | TF-IDF (v0.1) | Embeddings (v0.2) | Change |
|--------|--------------|-------------------|--------|
| **BCB Type-3 recall** | 2.0% | **58.5%** | **+29x** |
| **BCB Type-4 recall** | 0.0% | 0.0% | — (requires fine-tuning) |
| **BCB precision** | 100% | 95.2% | -5% (32 FPs from negatives) |
| **BCB F1** | 58.0% | **76.1%** | **+18pp** |
| **GCB Type-3 recall** | 95.5% | **98.5%** | +3pp |
| **GCB Type-4 recall** | 82.0% | **96.0%** | **+14pp** |
| **GCB precision** | 64.3% | 67.2% | +3pp |
| **POJ-104 Type-4 recall** | 10.9% | **78.6%** | **+7x** |
| **POJ-104 F1** | 19.4% | **77.5%** | **+4x** |

**Key insight**: The biggest improvements are on Type-3/Type-4 detection — exactly what embeddings are designed for. Type-1/Type-2 remain perfect (AST hash matching is unchanged). BCB Type-4 stays at 0% because these pairs are human-written Java functions with completely different algorithms — even fine-tuned models struggle here (a [2025 study](https://arxiv.org/abs/2505.04311) found 93% of BCB WT3/T4 labels are incorrect).

## Type-4 (Semantic) Detection Analysis

Type-4 clones have the same semantics but completely different implementation.
Echo Guard uses UniXcoder embeddings (768-dim) with per-language similarity
thresholds to detect these. Performance varies by language and dataset.

### BigCloneBench

- **Total Type-4 pairs:** 200
- **Detected:** 0
- **Missed:** 200
- **Recall:** 0.0%
- **Recommendation:** Embedding threshold tuning may improve Type-4 detection

### GPTCloneBench

- **Total Type-4 pairs:** 200
- **Detected:** 192
- **Missed:** 8
- **Recall:** 96.0%
- **Avg score (detected):** 0.888
- **Recommendation:** Current embedding approach handles Type-4 adequately

### POJ-104

- **Total Type-4 pairs:** 281
- **Detected:** 221
- **Missed:** 60
- **Recall:** 78.6%
- **Avg score (detected):** 0.913
- **Recommendation:** Current embedding approach handles Type-4 adequately

## Methodology

Benchmarks use the same two-tier pipeline as `echo-guard scan`:

1. All benchmark functions are extracted via tree-sitter (same as `echo-guard index`)
2. All functions are embedded via UniXcoder (ONNX INT8, 768-dim vectors)
3. ALL functions are loaded into a single `SimilarityEngine`
4. `find_all_matches()` runs the two-tier pipeline:
   - **Tier 1**: AST hash grouping → Type-1/Type-2 exact clone detection
   - **Tier 2**: Embedding cosine similarity with per-language thresholds → Type-3/Type-4 detection
   - **Intent filters**: Domain-aware false positive suppression
5. Engine output is mapped back to labeled pairs to compute precision/recall/F1

This matches real-world usage where the engine must find correct matches among
many candidate functions while avoiding false positives from unrelated code.

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
