# Training the Duplicate Classifier

Echo Guard uses a logistic regression classifier as the final scoring gate to decide whether a candidate pair is a real duplicate or noise. The model combines 14 features into a single duplicate probability score, replacing hand-tuned heuristic filters with one learned decision boundary.

## Architecture

```
Tier 1: AST Hash (exact matches)  ─┐
Tier 2: Embedding Cosine Search   ─┤
                                    ↓
                          ~200 candidate pairs
                                    ↓
                    Framework Rules (6 deterministic checks)
                                    ↓
                    Structural Pattern Rules
                    (verb+noun suppression, UI wrapper, short-body)
                                    ↓
                    Feature Classifier (this model)
                    ├── AST edit distance
                    ├── Embedding score
                    ├── Name token overlap
                    ├── Body identifier overlap
                    ├── Call token overlap
                    ├── Literal overlap
                    ├── Control flow similarity
                    ├── Parameter signature similarity
                    ├── Return shape similarity
                    ├── Same file flag
                    ├── Async match
                    ├── Line count metrics (2)
                    └── Exact structure flag
                                    ↓
                        probability > 0.5?
                           ↓           ↓
                        accept       reject
```

## Model Details

- **Algorithm**: Logistic regression (scikit-learn)
- **Parameters**: 14 coefficients + 1 bias = 15 numbers
- **Runtime dependency**: None — inference is `sigmoid(features @ coef + intercept)` in pure NumPy
- **Training dependency**: scikit-learn (dev only, `pip install -e ".[train]"`)
- **Weights file**: `echo_guard/data/classifier_weights.json`

### Features

| # | Feature | Description |
|---|---------|-------------|
| 1 | `ast_similarity` | Zhang-Shasha tree edit distance on normalized ASTs (0.0-1.0) |
| 2 | `embedding_score` | UniXcoder cosine similarity (0.0-1.0) |
| 3 | `name_token_overlap` | Jaccard similarity of function name tokens |
| 4 | `body_identifier_overlap` | Jaccard of meaningful identifiers in function body (excluding common tokens) |
| 5 | `call_token_overlap` | Jaccard of called function/method names |
| 6 | `literal_overlap` | Jaccard of string literals in source |
| 7 | `control_flow_similarity` | Cosine similarity of control flow vectors (ifs, loops, tries, returns, switches) |
| 8 | `parameter_signature_similarity` | Parameter count match + parameter name overlap |
| 9 | `return_shape_similarity` | Whether both functions return the same type (JSX, dict, boolean, etc.) |
| 10 | `same_file` | Whether both functions are in the same file |
| 11 | `async_match` | Whether both functions are async (or both sync) |
| 12 | `line_count_min` | Line count of the shorter function |
| 13 | `line_count_ratio` | Ratio of shorter to longer function |
| 14 | `is_exact_structure` | Whether AST hashes match (Tier 1 match) |

### Severity Model (DRY-based)

Severity is based on actionability, not just clone confidence:

- **HIGH**: `FindingGroup` with 3+ copies — extract to shared module now
- **MEDIUM**: 2 exact copies (pairwise match) — worth noting, defer per Rule of Three
- **LOW**: Lower-confidence semantic match — hidden by default

### What the Classifier Replaced

The classifier replaced these hand-tuned filters that were previously hardcoded:

- UI wrapper component detection
- UI directory path suppression
- Same-file CRUD operation detection
- Antonym pair detection
- Structural template detection
- Low-value variant detection (SVG paths)
- Hardcoded same-file embedding threshold
- Cross-language threshold
- MEDIUM group demotion hack

Framework rules that encode what CAN'T be imported (not similarity judgments) are kept as deterministic checks before the classifier runs. Structural pattern rules (verb+noun suppression, short-body exact-structure, UI wrapper same-file) also run before the classifier as they encode domain facts.

## Quick Start

```bash
# Install training dependency
pip install -e ".[train]"

# Train (uses custom pairs from echo_guard/data/training/)
python scripts/train_classifier.py --synthetic-only

# Train with GPTCloneBench for broader coverage
python scripts/train_classifier.py --max-pairs 500
```

## Training Data

The classifier ships with pre-trained weights in `echo_guard/data/classifier_weights.json`. The training data used to produce these weights is private and not included in the open-source repository.

The model was trained on thousands of labeled code pairs covering positive patterns (exact clones, renamed clones, parameterized variants, near-exact modifications) and negative patterns (CRUD boilerplate, UI wrapper components, structural templates, observer callbacks, async boilerplate, and more).

### Custom Training

If you want to retrain with your own data:

1. Create JSONL files in `echo_guard/data/training/positive/` and `echo_guard/data/training/negative/`
2. Each line is a JSON object with fields: `func_a_name`, `func_a_source`, `func_a_file`, `func_b_name`, `func_b_source`, `func_b_file`, `language`, `label` (1=duplicate, 0=not), `category`, `reasoning`
3. Run `python scripts/train_classifier.py --synthetic-only`

The training script also supports [GPTCloneBench](https://github.com/srlabUsask/GPTCloneBench) as supplemental data.

## When to Retrain

- After adding new training pairs for patterns the model gets wrong
- After adding new features to the classifier
- After finding systematic false positives on a new codebase
- When upgrading the embedding model
