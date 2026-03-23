# Echo Guard Benchmark Results

Generated: 2026-03-23

## Overview

Echo Guard is evaluated against three established clone detection benchmarks:

| Benchmark | Language | Clone Types | Focus |
|-----------|----------|-------------|-------|
| [BigCloneBench](https://github.com/clonebench/BigCloneBench) | Java | T1-T4 | Largest academic benchmark (8M+ pairs) |
| [GPTCloneBench](https://github.com/AluaBa662/GPTCloneBench) | Python, Java | T1-T4 | AI-generated clone pairs |
| [POJ-104](https://github.com/microsoft/CodeXGLUE/tree/main/Code-Code/Clone-detection-POJ-104) | C/C++ | T4 (semantic) | Competitive programming solutions |

## Results Summary

| Dataset | Precision | Recall | F1 | Type-4 Recall | Pairs |
|---------|-----------|--------|----|----|-------|
| BigCloneBench | 91.7% | 78.6% | 84.6% | 25.0% | 19 |
| GPTCloneBench | 100.0% | 50.0% | 66.7% | 25.0% | 17 |
| POJ-104 | 85.7% | 85.7% | 85.7% | 85.7% | 11 |

### BigCloneBench

Threshold: 0.5 | Pairs evaluated: 19 | Time: 0.1s

Severity distribution: high: 8, low: 4

| Clone Type | Precision | Recall | F1 | TP | FP | TN | FN |
|------------|-----------|--------|----|----|----|----|-----|
| negative | 0.0% | 0.0% | 0.0% | 0 | 1 | 4 | 0 |
| type1 | 100.0% | 100.0% | 100.0% | 3 | 0 | 0 | 0 |
| type2 | 100.0% | 100.0% | 100.0% | 4 | 0 | 0 | 0 |
| type3 | 100.0% | 100.0% | 100.0% | 3 | 0 | 0 | 0 |
| type4 | 100.0% | 25.0% | 40.0% | 1 | 0 | 0 | 3 |

### GPTCloneBench

Threshold: 0.5 | Pairs evaluated: 17 | Time: 0.1s

Severity distribution: high: 4, medium: 1, low: 1

| Clone Type | Precision | Recall | F1 | TP | FP | TN | FN |
|------------|-----------|--------|----|----|----|----|-----|
| negative | 0.0% | 0.0% | 0.0% | 0 | 0 | 5 | 0 |
| type1 | 100.0% | 100.0% | 100.0% | 2 | 0 | 0 | 0 |
| type2 | 100.0% | 33.3% | 50.0% | 1 | 0 | 0 | 2 |
| type3 | 100.0% | 66.7% | 80.0% | 2 | 0 | 0 | 1 |
| type4 | 100.0% | 25.0% | 40.0% | 1 | 0 | 0 | 3 |

### POJ-104

Threshold: 0.5 | Pairs evaluated: 11 | Time: 0.1s

Severity distribution: high: 2, medium: 1, low: 4

| Clone Type | Precision | Recall | F1 | TP | FP | TN | FN |
|------------|-----------|--------|----|----|----|----|-----|
| negative | 0.0% | 0.0% | 0.0% | 0 | 1 | 3 | 0 |
| type4 | 100.0% | 85.7% | 92.3% | 6 | 0 | 0 | 1 |

## Type-4 (Semantic) Detection Gap Analysis

Type-4 clones have the same semantics but completely different implementation.
This is the hardest clone type to detect with structural/textual methods.

### BigCloneBench

- **Total Type-4 pairs:** 4
- **Detected:** 1
- **Missed:** 3
- **Recall:** 25.0%
- **Detection gap:** 75.0%
- **Avg score (detected):** 1.000
- **Recommendation:** Phase 2 code embeddings needed

### GPTCloneBench

- **Total Type-4 pairs:** 4
- **Detected:** 1
- **Missed:** 3
- **Recall:** 25.0%
- **Detection gap:** 75.0%
- **Avg score (detected):** 0.630
- **Recommendation:** Phase 2 code embeddings needed

### POJ-104

- **Total Type-4 pairs:** 7
- **Detected:** 6
- **Missed:** 1
- **Recall:** 85.7%
- **Detection gap:** 14.3%
- **Avg score (detected):** 0.856
- **Recommendation:** Current TF-IDF approach handles basic Type-4 well

### Implications for Phase 2

The Type-4 detection gaps identified above confirm the need for Phase 2's
semantic detection upgrade. Code embeddings (CodeBERT, UniXcoder) are expected
to significantly improve Type-4 recall by capturing semantic similarity that
TF-IDF and structural methods miss.

Key areas where embeddings would help:
- Recursive vs iterative implementations of the same algorithm
- Different data structure choices for the same operation
- Algorithmic variants (e.g., bubble sort vs insertion sort for sorting)

## Methodology

Benchmarks use the same pipeline as `echo-guard scan`:

1. All benchmark functions are extracted via tree-sitter (same as `echo-guard index`)
2. ALL functions are loaded into a single `SimilarityEngine` (realistic N-function index)
3. `find_all_matches()` runs the full 4-stage pipeline: AST hash → signature filter → LSH+TF-IDF → intent filter
4. Engine output is mapped back to labeled pairs to compute precision/recall/F1
5. Severity (high ≥0.95, medium ≥0.80, low <0.80) is tracked for each detection

This matches real-world usage where the engine must find correct matches among
many candidate functions while avoiding false positives from unrelated code.

- LSH threshold set to 0.15 (same as production `scan_for_redundancy`)
- Results measured at the configurable similarity threshold (default 0.50)
- Curated subsets represent the distribution of clone types in the original datasets

## Reproducing

```bash
# Run all benchmarks
python -m benchmarks.runner

# Run specific benchmark
python -m benchmarks.runner --dataset bigclonebench --verbose

# Threshold sweep
python -m benchmarks.runner --sweep --json sweep_results.json

# Generate this report
python -m benchmarks.runner --report
```
