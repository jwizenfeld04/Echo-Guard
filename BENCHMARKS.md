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
| BigCloneBench | 92.9% | 92.9% | 92.9% | 75.0% | 19 |
| GPTCloneBench | 100.0% | 83.3% | 90.9% | 50.0% | 17 |
| POJ-104 | 87.5% | 100.0% | 93.3% | 100.0% | 11 |

### BigCloneBench

Threshold: 0.5 | Pairs evaluated: 19 | Time: 0.4s

| Clone Type | Precision | Recall | F1 | TP | FP | TN | FN |
|------------|-----------|--------|----|----|----|----|-----|
| negative | 0.0% | 0.0% | 0.0% | 0 | 1 | 4 | 0 |
| type1 | 100.0% | 100.0% | 100.0% | 3 | 0 | 0 | 0 |
| type2 | 100.0% | 100.0% | 100.0% | 4 | 0 | 0 | 0 |
| type3 | 100.0% | 100.0% | 100.0% | 3 | 0 | 0 | 0 |
| type4 | 100.0% | 75.0% | 85.7% | 3 | 0 | 0 | 1 |

### GPTCloneBench

Threshold: 0.5 | Pairs evaluated: 17 | Time: 0.3s

| Clone Type | Precision | Recall | F1 | TP | FP | TN | FN |
|------------|-----------|--------|----|----|----|----|-----|
| negative | 0.0% | 0.0% | 0.0% | 0 | 0 | 5 | 0 |
| type1 | 100.0% | 100.0% | 100.0% | 2 | 0 | 0 | 0 |
| type2 | 100.0% | 100.0% | 100.0% | 3 | 0 | 0 | 0 |
| type3 | 100.0% | 100.0% | 100.0% | 3 | 0 | 0 | 0 |
| type4 | 100.0% | 50.0% | 66.7% | 2 | 0 | 0 | 2 |

### POJ-104

Threshold: 0.5 | Pairs evaluated: 11 | Time: 0.2s

| Clone Type | Precision | Recall | F1 | TP | FP | TN | FN |
|------------|-----------|--------|----|----|----|----|-----|
| negative | 0.0% | 0.0% | 0.0% | 0 | 1 | 3 | 0 |
| type4 | 100.0% | 100.0% | 100.0% | 7 | 0 | 0 | 0 |

## Type-4 (Semantic) Detection Gap Analysis

Type-4 clones have the same semantics but completely different implementation.
This is the hardest clone type to detect with structural/textual methods.

### BigCloneBench

- **Total Type-4 pairs:** 4
- **Detected:** 3
- **Missed:** 1
- **Recall:** 75.0%
- **Detection gap:** 25.0%
- **Avg score (detected):** 0.887
- **Recommendation:** Current TF-IDF approach handles basic Type-4 well

### GPTCloneBench

- **Total Type-4 pairs:** 4
- **Detected:** 2
- **Missed:** 2
- **Recall:** 50.0%
- **Detection gap:** 50.0%
- **Avg score (detected):** 0.666
- **Recommendation:** Current TF-IDF approach handles basic Type-4 well

### POJ-104

- **Total Type-4 pairs:** 7
- **Detected:** 7
- **Missed:** 0
- **Recall:** 100.0%
- **Detection gap:** 0.0%
- **Avg score (detected):** 0.807
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

- Each pair is evaluated independently using Echo Guard's `SimilarityEngine`
- Function A is indexed, then Function B is queried against it
- LSH threshold set to 0.2 (permissive) to maximize recall for evaluation
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
