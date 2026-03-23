# Fine-Tuning Roadmap: Improving Type-4 Detection

## Current State

Echo Guard detects Type-4 (semantic) clones using zero-shot UniXcoder embeddings. This works well for AI-generated clones (96% recall on GPTCloneBench) but poorly for independently written human code (0% on BigCloneBench Type-4). See [TYPE4-ANALYSIS.md](TYPE4-ANALYSIS.md) for a detailed explanation of why.

## The Goal

Fine-tune the embedding model so functions with the same *intent* produce similar embeddings, even when the code looks completely different. This would close the gap between GPTCloneBench (96%) and BigCloneBench (0%) performance.

## Available Fine-Tuning Datasets

### CodeXGLUE BigCloneBench (Java, binary classification)

| Property | Value |
|---|---|
| Training pairs | 901,028 |
| Validation pairs | 415,416 |
| Unique functions | 9,134 |
| Format | `data.jsonl` (functions) + `train.txt` (pair labels) |
| Task | Binary classification: clone or not |
| Fine-tuned UniXcoder F1 | 95.2% |
| Scripts | [CodeXGLUE/Clone-detection-BigCloneBench](https://github.com/microsoft/CodeXGLUE/tree/main/Code-Code/Clone-detection-BigCloneBench) |

**Caveat**: BCB is heavily skewed toward Type-1/2/3. The 95.2% F1 is driven by structural clones. A [2025 study](https://arxiv.org/abs/2505.04311) found 93% of Type-4 labels are incorrect, so fine-tuning on BCB alone won't meaningfully improve Type-4 detection.

### CodeXGLUE POJ-104 (C/C++, contrastive learning)

| Property | Value |
|---|---|
| Programs | 52,000 (500 solutions × 104 problems) |
| Format | JSONL: `{"label": "problem_id", "code": "..."}` |
| Task | Contrastive: same-problem → similar embeddings |
| Fine-tuned UniXcoder MAP@R | 90.5% |
| Scripts | [UniXcoder/clone-detection/POJ-104](https://github.com/microsoft/CodeBERT/tree/master/UniXcoder/downstream-tasks/clone-detection/POJ-104) |

**This is the most promising path for Type-4 improvement.** POJ-104 is purely semantic — every pair is two different implementations of the same algorithm. Fine-tuning on this teaches the model to recognize functional equivalence beyond syntactic similarity.

### GPTCloneBench (Python/Java, AI-generated)

| Property | Value |
|---|---|
| Pairs | 37,000+ (true + false semantic clones) |
| Format | Text files with function pairs |
| Task | Binary classification |

Useful for validating that fine-tuning doesn't regress on AI-generated clone detection (our primary use case).

## Fine-Tuning Approaches

### Approach 1: Contrastive Learning on POJ-104 (Recommended First Step)

Fine-tune UniXcoder using contrastive loss on POJ-104 training data. This teaches the model that different solutions to the same problem should have similar embeddings.

**What you need:**
```bash
# Download POJ-104 training data (already in benchmarks/data/poj104/)
pip install torch transformers

# Fine-tune (from CodeXGLUE scripts)
python run.py \
    --model_name_or_path microsoft/unixcoder-base \
    --train_data_file data/train.jsonl \
    --eval_data_file data/valid.jsonl \
    --output_dir saved_models/unixcoder-poj104 \
    --num_train_epochs 2 \
    --learning_rate 2e-5 \
    --per_device_train_batch_size 16
```

**Expected improvement:** MAP@R on POJ-104 should jump from ~80% (zero-shot) to ~90% (fine-tuned). More importantly, the model learns to associate *functional intent* with embeddings, which transfers to other languages.

**Risk:** Fine-tuning on C competitive programming code may not transfer well to enterprise Python/Java. That's why Approach 2 exists.

### Approach 2: Contrastive Learning on User Feedback (Long-Term)

Use the training data collected from your own codebase through:
1. `resolve_finding` verdicts (`fixed` → clone, `false_positive` → not clone)
2. `respond_to_probe` verdicts (low-confidence exploration)
3. The existing `training_pairs` table in DuckDB

**Minimum data needed:** ~1,000-5,000 labeled pairs for meaningful improvement. At typical usage rates, this takes months of real-world use.

**Framework:** [sentence-transformers](https://www.sbert.net/) supports contrastive fine-tuning with `CosineSimilarityLoss` or `ContrastiveLoss`. Format: `(code_a, code_b, label)` where label is 0 or 1.

```python
from sentence_transformers import SentenceTransformer, InputExample, losses
from torch.utils.data import DataLoader

model = SentenceTransformer("microsoft/unixcoder-base")

# Load from DuckDB training_pairs table
train_examples = [
    InputExample(texts=[pair["code_a"], pair["code_b"]], label=1.0 if pair["verdict"] == "clone" else 0.0)
    for pair in training_pairs
]

train_dataloader = DataLoader(train_examples, shuffle=True, batch_size=16)
train_loss = losses.CosineSimilarityLoss(model)

model.fit(
    train_objectives=[(train_dataloader, train_loss)],
    epochs=2,
    warmup_steps=100,
)
```

### Approach 3: Multi-Stage Fine-Tuning (Best Results)

1. Fine-tune on POJ-104 (general semantic understanding)
2. Then fine-tune on collected user data (domain-specific calibration)
3. Then fine-tune on BCB (structural clone awareness)

Each stage uses the previous stage's model as the starting point.

## Data Collection Strategy

Echo Guard collects training data through three channels:

### 1. Resolution Feedback (Passive)

Every time an agent calls `resolve_finding`, the code pair and verdict are stored in the `training_pairs` table. This happens naturally during normal usage.

- `fixed` → labeled as "clone" (the agent consolidated the duplicate)
- `acknowledged` or `false_positive` → labeled as "not_clone"

### 2. Low-Confidence Probes (Active Exploration)

20% of `check_for_duplicates` calls include a **probe** — a code pair that scored below the detection threshold but above the noise floor. The agent evaluates it and calls `respond_to_probe`:

- `clone` → positive training example (a clone we missed)
- `not_clone` → negative training example (correct rejection)

This is critical because it collects data on the *boundary region* where the model is uncertain — exactly the data that improves the model the most.

### 3. Manual Labeling (Batch)

Export pairs and label them manually:

```bash
# Export collected pairs
echo-guard training-data --export training_pairs.jsonl

# After manual labeling, import back (future feature)
```

### How Many Pairs Are Needed?

| Pairs | Expected Improvement |
|---|---|
| 100 | Not enough — random noise dominates |
| 500 | Marginal — can calibrate thresholds but not retrain model |
| 1,000 | Minimum viable — contrastive fine-tuning starts to work |
| 5,000 | Good — meaningful improvement on domain-specific clones |
| 50,000+ | Excellent — competitive with academic fine-tuned models |

At typical usage (10 resolutions + 2 probes per day), reaching 1,000 pairs takes ~3 months.

## Data Storage

All training data is stored locally in `.echo-guard/index.duckdb`:

```sql
training_pairs (
    id INTEGER PRIMARY KEY,
    verdict VARCHAR,         -- "clone" or "not_clone"
    language VARCHAR,
    source_code_a TEXT,      -- Full source code
    source_code_b TEXT,
    function_name_a VARCHAR,
    function_name_b VARCHAR,
    filepath_a VARCHAR,
    filepath_b VARCHAR,
    embedding_score DOUBLE,  -- Current model's score for this pair
    clone_type VARCHAR,      -- "resolution", "type4_probe", etc.
    probe_type VARCHAR,      -- "user", "probe", "resolution"
    recorded_at TIMESTAMP
)
```

### Privacy & Data Handling

Echo Guard will use a three-tier consent model (planned for Phase 4 — VS Code extension):

| Tier | Config value | What's shared | Code included? |
|---|---|---|---|
| **Private** (default) | `feedback_consent: private` | Anonymized decisions only | **No** — just structural metadata |
| **Public** | `feedback_consent: public` | Code pairs + decisions | Yes — from public repos only |
| **None** | `feedback_consent: none` | Nothing | No |

**Current state**: The `feedback_consent` setting is not yet implemented in `EchoGuardConfig`. All data stays local in `.echo-guard/index.duckdb` — nothing is uploaded. The consent model, `feedback_consent` parsing, and upload mechanism are planned for the VS Code extension (Phase 4). Today, `echo-guard training-data --export` exports code pairs for local fine-tuning only.

**Key principles:**
- **Default is private** — anonymized decisions (no code) are collected unless the user opts out
- **Private repos never share code** — even with opt-in, only anonymized structural features are collected
- **Public repo detection** — `git remote` is used to auto-detect whether a repo is public
- **If you fine-tune on proprietary code**, keep the model private — published models could memorize code snippets

## CLI Commands

```bash
# View training data collection stats
echo-guard training-data

# Export for fine-tuning
echo-guard training-data --export pairs.jsonl
```

## MCP Tools

| Tool | Purpose |
|---|---|
| `check_for_duplicates` | Returns findings + occasional probes |
| `resolve_finding` | Records verdict → collects training data |
| `respond_to_probe` | Evaluates low-confidence candidate → collects training data |

## Important: Contrastive vs Classification Fine-Tuning

There are two ways to fine-tune a model for clone detection:

1. **Binary classification** (BCB-style): Train a classifier head that takes two code inputs and outputs clone/not-clone. This is what CodeXGLUE's BCB scripts do.
2. **Contrastive embedding** (POJ-104-style): Train the model so same-intent code produces similar embeddings. This is what `sentence-transformers` with `MultipleNegativesRankingLoss` does.

**Use contrastive, not classification.** Echo Guard needs an embedding model for similarity *search* — we compute embeddings independently and compare them via cosine similarity. A classifier-fine-tuned model optimizes for pairwise decisions, but its embeddings are not necessarily better for retrieval. Research confirms: "UniXcoder fine-tuned on a code search task produces better similarity scores than when fine-tuned on a clone detection classification task."

## Pre-Fine-Tuned Models

A few fine-tuned clone detection models exist on HuggingFace (`mrm8488/codebert-finetuned-clone-detection`, `Lazyhope/python-clone-detection`), but these are **classifier** models, not embedding models — their embeddings are not optimized for retrieval. No pre-fine-tuned UniXcoder *embedding* model for clone detection is currently published. This is an opportunity for Echo Guard to be the first.

## References

- [CodeXGLUE BigCloneBench](https://github.com/microsoft/CodeXGLUE/tree/main/Code-Code/Clone-detection-BigCloneBench)
- [CodeXGLUE POJ-104](https://github.com/microsoft/CodeXGLUE/tree/main/Code-Code/Clone-detection-POJ-104)
- [UniXcoder fine-tuning scripts](https://github.com/microsoft/CodeBERT/tree/master/UniXcoder/downstream-tasks/clone-detection)
- [sentence-transformers contrastive learning](https://www.sbert.net/docs/training/overview.html)
- [Active learning for code clone detection](https://arxiv.org/abs/2506.10995) — evaluating small-scale models (2025)
