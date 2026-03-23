# Echo Guard Architecture

## Two-Tier Detection Pipeline

Echo Guard uses a two-tier architecture for code clone detection. Both tiers are included in the base install — no optional dependencies needed.

### Overview

```
┌──────────────────────────────────────────────────────────────────┐
│  Input: codebase functions                                       │
├──────────────────────┬───────────────────────────────────────────┤
│                      │                                           │
│  Tier 1              │  Tier 2                                   │
│  ┌────────────────┐  │  ┌─────────────────────────────────────┐ │
│  │ AST Hash Match │  │  │ UniXcoder Embedding (ONNX INT8)     │ │
│  │ O(1) lookup    │  │  │ ~15ms per function on CPU           │ │
│  │                │  │  ├─────────────────────────────────────┤ │
│  │ Catches:       │  │  │ Cosine Similarity Search            │ │
│  │  Type-1: 100%  │  │  │ NumPy brute-force: ~1-2ms @100K    │ │
│  │  Type-2: 100%  │  │  │ USearch ANN: <1ms @500K+ [scale]   │ │
│  └──────┬─────────┘  │  ├─────────────────────────────────────┤ │
│         │            │  │ Per-language thresholds              │ │
│         │            │  │ + Intent Filters                    │ │
│         │            │  │                                     │ │
│         │            │  │ Catches:                            │ │
│         │            │  │  Type-3 (modified clones)           │ │
│         │            │  │  Type-4 (semantic clones)           │ │
│         │            │  └──────────┬──────────────────────────┘ │
│         │            │             │                             │
├─────────┴────────────┴─────────────┴─────────────────────────────┤
│  Merge results (non-overlapping — no dedup needed)               │
│  Apply scope penalties + domain-aware filters                    │
│  Sort by score, return SimilarityMatch list                      │
└──────────────────────────────────────────────────────────────────┘
```

### Tier 1: AST Hash Matching (Type-1/Type-2)

**Always active. Zero dependencies beyond tree-sitter.**

Tier 1 uses structural AST hashing to detect exact clones (Type-1) and renamed clones (Type-2) in O(1) time.

How it works:
1. Each function's AST is normalized: identifiers replaced with positional placeholders, comments/strings stripped, control flow structure preserved.
2. The normalized AST is SHA-256 hashed to a 16-char fingerprint.
3. Functions with identical AST hashes are exact structural clones (score = 1.0).

Performance:
- **100% recall** on Type-1 and Type-2 clones (BigCloneBench benchmark)
- **Zero false positives** — AST hash identity means the code is structurally identical
- O(n) indexing, O(1) lookup via hash-map grouping

### Tier 2: Embedding Similarity (Type-3/Type-4)

Tier 2 uses learned code embeddings from [UniXcoder](https://github.com/microsoft/CodeBERT/tree/master/UniXcoder) to detect clones that share semantic meaning but have different syntax.

How it works:
1. Each function's source code is tokenized by the UniXcoder RoBERTa tokenizer (max 512 tokens).
2. The tokenized input runs through the ONNX-exported UniXcoder model (INT8 quantized, ~125MB).
3. The last hidden states are mean-pooled and L2-normalized to produce a 768-dim unit vector.
4. Cosine similarity between embeddings is computed via NumPy dot product.
5. Pairs above the per-language embedding threshold are reported as matches.

Why UniXcoder:
- **Best Type-4 accuracy**: 95.18% MAP@R on POJ-104 (semantic clone benchmark)
- **Apache-2.0 license**: Fully compatible with commercial use and Echo Guard's MIT license
- **Pre-trained with AST structure**: Aligns with Tier 1's AST-based approach
- **768-dim embeddings**: Rich representations, well-studied dimensionality

Performance:
- Per-function embedding: **~10-20ms** on CPU (ONNX INT8)
- Similarity search at 100K functions: **~1-2ms** (NumPy brute-force)
- Model cached locally after first download (~500MB PyTorch → ~125MB ONNX INT8)

---

## Storage Architecture

```
.echo-guard/
├── index.duckdb           # Function metadata, file metadata, feedback
├── embeddings.npy         # NumPy memmap: (N, 768) float32 embedding vectors
├── embedding_meta.json    # Model info, row count, deletion bitmap
├── embeddings.usearch     # USearch ANN index (only with [scale])
└── model_cache/           # Cached ONNX model (downloaded on first use)
    └── microsoft--unixcoder-base/
        └── onnx/
            └── model_quantized.onnx
```

### DuckDB Index (`index.duckdb`)

Persistent storage for all function metadata. Schema:

```sql
functions (
    qualified_name VARCHAR PRIMARY KEY,  -- "filepath:name:lineno"
    name VARCHAR,                         -- Function name
    filepath VARCHAR,                     -- Relative file path
    language VARCHAR,                     -- "python", "java", etc.
    lineno INTEGER,                       -- Start line
    end_lineno INTEGER,                   -- End line
    source TEXT,                          -- Full source code
    ast_hash VARCHAR,                     -- Normalized AST fingerprint
    param_count INTEGER,                  -- Parameter count
    has_return BOOLEAN,                   -- Has non-empty return?
    class_name VARCHAR,                   -- Enclosing class (if method)
    visibility VARCHAR,                   -- "public", "private", "internal"
    embedding_row INTEGER,                -- Index into embeddings.npy
    embedding_version VARCHAR,            -- Model version for invalidation
    ...
)
```

### Embedding Store (`embeddings.npy`)

Memory-mapped NumPy array of pre-computed, unit-normalized embedding vectors.

- Format: float32, shape (capacity, 768)
- Storage: ~300MB per 100K functions
- Access: OS pages in only needed portions (RAM-efficient)
- Deletion: Lazy via bitmap in `embedding_meta.json`; periodic compaction reclaims space

### USearch Index (`embeddings.usearch`) — Optional

For codebases with >500K functions, USearch provides HNSW-based approximate nearest neighbor search.

Install: `pip install "echo-guard[scale]"`

- Automatically used when installed and index has >1,000 vectors
- Sub-millisecond search even at 1M+ vectors
- Supports incremental add and remove (unlike FAISS HNSW or Annoy)

---

## Intent Filters

Both tiers share a common set of intent filters that eliminate false positives. These run after similarity scoring and before results are returned.

| Filter | What it catches | Example |
|---|---|---|
| Scope penalty | Private/internal functions that can't be imported | `_helper()` with 0.6x penalty |
| Same-file threshold | Co-located functions that are intentionally separate | Requires ≥95% similarity |
| Cross-language threshold | Structural shape matches across languages | Requires ≥80% similarity |
| Constructor exclusion | Unrelated classes with similar `__init__` | `UserModel.__init__` vs `OrderModel.__init__` |
| Observer pattern | N classes implementing same interface method | `Protocol.on_event()` implementations |
| CRUD operations | Same-file create/update/delete on same resource | `create_user()` vs `update_user()` |
| Antonym pairs | Semantically inverse functions | `encrypt()` vs `decrypt()` |
| Structural templates | Same verb pattern, different domain nouns | `get_user_by_id()` vs `get_order_by_id()` |
| Framework exports | Next.js/Flask required per-file exports | `GET()` in different `route.ts` files |
| UI wrappers | Design system components sharing wrapper pattern | `Panel()`, `Card()`, `Badge()` |
| Service boilerplate | Health endpoints across microservices | `health()` in different services |

---

## Data Flow

### `echo-guard scan` (Full Repo)

```
1. Load all functions from DuckDB index
2. Set up embeddings (if [embeddings] installed):
   a. Load EmbeddingModel (ONNX, cached)
   b. Compute embeddings for new/changed functions
   c. Store in memmap + update DuckDB embedding_row
3. Build SimilarityEngine with embedding store + model
4. Run find_all_matches():
   a. Tier 1: AST hash grouping → exact_structure matches
   b. Tier 2: batch_search() → embedding_semantic matches
   c. Apply intent filters to all candidates
   d. Merge (non-overlapping) and sort by score
5. Output results (rich/json/compact)
```

### `echo-guard check <files>` (Pre-commit)

```
1. Load existing index into SimilarityEngine
2. For each changed file:
   a. Extract functions via tree-sitter
   b. Compute embedding (if available, ~15ms)
   c. find_similar() against full index:
      - Tier 1: AST hash lookup (O(1))
      - Tier 2: embedding search (~2ms)
      - Apply intent filters
3. Report matches to functions outside changed files
```

### MCP Server (`check_for_duplicates`)

```
1. Receive proposed code from AI agent
2. Extract functions via tree-sitter
3. For each function:
   a. Compute embedding (~15ms)
   b. find_similar() against index (<50ms total)
4. Return matches + reuse suggestions
Total latency budget: <500ms
```

---

## Clone Type Classification

Every finding is classified by clone type, following the standard academic taxonomy:

| Clone Type | Detection Tier | Severity | What It Means | Action |
|---|---|---|---|---|
| **Type-1/Type-2** | Tier 1 (AST hash) | **HIGH** | Exact structural duplicate or renamed identifiers | Import the existing function |
| **Type-3** (raw score ≥ 0.96) | Tier 2 (embeddings) | **HIGH** | Very similar structure with minor modifications | Refactor into shared function |
| **Type-4** (raw score < 0.96) | Tier 2 (embeddings) | **MEDIUM** | Same intent, different implementation | Evaluate — may be intentional |

### Severity Model

Severity is **derived from clone type**, not from a raw score threshold:

- **HIGH**: Always actionable. Either an exact duplicate (Type-1/2, should import) or a near-duplicate with strong structural overlap (Type-3, should refactor). These represent clear technical debt.
- **MEDIUM**: Worth reviewing. Semantic clones (Type-4) where the same logic is implemented differently. Requires human judgment.

There is no "low" severity. Per-language embedding thresholds filter out false positives from shared language idioms. If a clone is detected, it's worth reporting.

Clone type classification uses the **raw embedding score** (before scope penalty), so a private exact clone is still classified as Type-1/Type-2 — the scope penalty only affects ranking, not classification.

### MCP Server Response Format

When the AI agent calls `check_for_duplicates`, each duplicate includes:

```json
{
  "clone_type": "type1_type2",
  "severity": "high",
  "similarity": 0.98,
  "your_function": "validate_email",
  "existing_function": "validate_email",
  "existing_file": "utils/validators.py:42",
  "existing_source": "def validate_email(email): ...",
  "action": "EXACT DUPLICATE. Import the existing function instead of rewriting it.",
  "fix": "from utils.validators import validate_email"
}
```

The `action` field gives the agent a single, unambiguous instruction. The `fix` field provides a ready-to-use import statement when applicable.

---

## Embedding Model Details

### UniXcoder (`microsoft/unixcoder-base`)

| Property | Value |
|---|---|
| Architecture | RoBERTa-based encoder-decoder |
| Parameters | ~125M |
| Embedding dimensions | 768 |
| Max tokens | 512 |
| License | Apache-2.0 |
| Pre-training data | Code + AST + NL comments (6 languages) |
| POJ-104 MAP@R | 95.18% (fine-tuned) |
| BigCloneBench F1 | ~93.7% |

### ONNX Optimization

The model is exported to ONNX format with INT8 dynamic quantization:

1. **Export**: HuggingFace Optimum or direct `torch.onnx.export`
2. **Quantize**: INT8 dynamic quantization (reduces model from ~500MB to ~125MB)
3. **Runtime**: ONNX Runtime with CPU ExecutionProvider
4. **Speedup**: 3-5x over vanilla PyTorch inference

First-time setup downloads and converts the model automatically. Subsequent runs use the cached ONNX model.

---

## Configuration

### Install Tiers

```bash
# Standard install — includes Tier 1 (AST hash) + Tier 2 (UniXcoder embeddings)
pip install echo-guard

# With language support (tree-sitter grammars)
pip install "echo-guard[languages]"

# Scale: Add USearch ANN for >500K function codebases
pip install "echo-guard[scale]"

# Full stack
pip install "echo-guard[languages,scale]"
```

Embeddings are included in the base install. The model (~500MB) is downloaded on first use and cached locally.

### Embedding Threshold

Embedding thresholds are **calibrated per language** using empirical measurements of clone vs non-clone similarity distributions. UniXcoder produces different cosine similarity ranges for different languages — Python functions cluster very tightly (due to heavy training data), while Java/Go have cleaner separation.

| Language | Threshold | Clone Range | Noise Ceiling | Gap |
|---|---|---|---|---|
| Python | **0.94** | 0.92-0.97 | 0.96 | Overlaps — highest threshold needed |
| JavaScript | **0.85** | 0.88-0.91 | 0.78 | Clean gap |
| TypeScript | **0.83** | 0.93 | 0.59 | Wide gap |
| Java | **0.81** | 0.87-0.94 | 0.66 | Wide gap |
| Go | **0.81** | 0.89 | 0.64 | Wide gap |
| C/C++ | **0.83** | 0.90 | 0.69 | Clean gap |
| Ruby/Rust | **0.85/0.83** | Estimated from similar languages |

For cross-language pairs, the lower threshold of the two languages is used.

These thresholds are defined in `echo_guard/embeddings.py:LANGUAGE_EMBEDDING_THRESHOLDS` and can be overridden in configuration.

### Model Selection

UniXcoder is the default model. To use a different model:

```python
from echo_guard.embeddings import EmbeddingModel

model = EmbeddingModel(
    model_id="microsoft/codebert-base",  # Any HuggingFace encoder model
    embedding_dim=768,
)
```

---

## Scaling Characteristics

| Metric | 1K functions | 10K functions | 100K functions | 1M functions |
|---|---|---|---|---|
| Embedding storage | ~3 MB | ~30 MB | ~300 MB | ~3 GB |
| Embedding computation | ~15s | ~2.5 min | ~25 min | ~4 hr |
| Brute-force search | <1ms | <1ms | ~2ms | ~20ms |
| USearch ANN search | <1ms | <1ms | <1ms | <1ms |
| RAM (engine) | ~10 MB | ~50 MB | ~400 MB | ~4 GB |
| RAM (with USearch) | ~10 MB | ~50 MB | ~200 MB | ~2 GB |

Notes:
- Embedding computation is **incremental** — only new/changed functions are embedded
- After first scan, subsequent scans only embed changed files
- Memory-mapped storage keeps RAM usage proportional to active queries, not total index size
