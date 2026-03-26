# Semantic Code Clone Detection: Research & Approaches

Research notes on approaches to detecting semantically equivalent code (Type-4 clones) — code that does the same thing but is implemented differently.

**Context:** Echo-Guard's current pipeline (UniXcoder embeddings + classifier) catches AI-generated echoes partially but struggles with truly independent implementations of the same functionality. Current benchmark results (March 2025):

| Dataset | Type-4 Recall | Per-Type Precision |
|---------|--------------|-------------------|
| BigCloneBench (Java) | 0% | — |
| GPTCloneBench (AI-generated) | 69.5% | 100% |
| POJ-104 (C algorithms) | 17.1% | 100% |

---

## Why Current Embeddings Hit a Ceiling

UniXcoder (and similar code embedding models) learn **what code looks like**, not **what code does**. The classifier's trained weights confirm this — `embedding_score` has a coefficient of 60.35 while the next strongest feature is 4.66. When embeddings fail, nothing compensates.

The core problem: two functions that solve the same problem with different algorithms (iterative vs. recursive, brute-force vs. dynamic programming) share almost no syntactic or structural similarity. Embeddings trained on code tokens/structure fundamentally cannot bridge this gap.

---

## BigCloneBench Mislabeling (Important Caveat)

Krinke et al. (2025) found **93% of Type-4 clone pairs in BigCloneBench are mislabeled** — only 27 of 406 manually inspected Weak Type-3/Type-4 pairs were genuine clones. This means 139 papers that used BCB to evaluate Type-4 detection have compromised results. A paper reporting "precision 99.8%, recall 88.3%, F1 93.7%" actually achieves precision 6.7%, recall 5.9%, F1 6.3% when corrected.

**Better benchmarks:**
- **SemanticCloneBench**: 4,000 manually validated pairs across Java, C, C#, Python
- **GPTCloneBench**: 37,149 pairs with manual validation by nine judges (but biased toward AI-generated variants)
- **POJ-104/OJClones**: 52,000 C/C++ samples across 104 programming problems (ground truth from online judge acceptance)
- **Google Code Jam**: Competition submissions solving the same problem

Reference: [How the Misuse of a Dataset Harmed Semantic Clone Detection](https://arxiv.org/abs/2505.04311)

---

## Approach 1: Better Embeddings (Contrastive Learning)

Train embeddings specifically to pull functionally equivalent code together, rather than learning from syntax alone.

### TransformCode (IEEE TSE 2024) — Most Practical for Echo-Guard

**How it works:** Three-step unsupervised pipeline:
1. Normalize code (strip comments, rename variables)
2. Generate semantically equivalent variants via AST transformations (permute declarations, swap conditions, exchange while/for, add dummy statements)
3. Contrastive training with InfoNCE loss — the model learns that transformed variants are equivalent

**Results (unsupervised, BigCloneBench):** F1 82.36%, outperforming InferCode (75%), Code2vec (60%), and CodeBERT (16.43%).

**Why it fits Echo-Guard:** Encoder-agnostic, language-agnostic, uses tree-sitter (already in the pipeline), requires no labeled data. Could be applied to fine-tune UniXcoder or a replacement model. Trains in hours, not days.

Reference: [TransformCode: A Contrastive Learning Framework](https://arxiv.org/abs/2311.08157)

### CodeSage (Amazon, ICLR 2024) — Drop-in Replacement

**How it works:** 1.3B parameter encoder trained in two stages: masked language modeling then contrastive learning with text-code pairs. Available in 130M/356M/1.3B sizes.

**Results:** Outperforms OpenAI text-embedding-ada-002 by 41% on code-to-code search.

**Trade-off:** Much larger model than UniXcoder (~125MB quantized). The 130M variant might be viable. Would need ONNX export and quantization like the current UniXcoder setup.

Reference: [CodeSage on GitHub](https://github.com/amazon-science/CodeSage)

### TransformCode vs. CodeSage: What They Actually Are

These are frequently compared as alternatives, but they're fundamentally different things:

- **TransformCode** is a **training recipe**, not a model. It's a method for making any existing encoder better at semantic similarity through contrastive fine-tuning. You feed it an encoder (UniXcoder, CodeBERT, CodeSage — anything), it generates augmented code variants via AST transformations, and trains the encoder to recognize them as equivalent. The output is the same model architecture with better weights. No change to model size, inference speed, or download size.

- **CodeSage** is a **model** — a specific encoder architecture (130M / 356M / 1.3B parameters) pre-trained by Amazon with two-stage training: masked language modeling then contrastive learning on text-code pairs. It replaces UniXcoder entirely.

This distinction matters because **they're not mutually exclusive**.

#### Head-to-head comparison

| Dimension | TransformCode | CodeSage |
|-----------|--------------|----------|
| **What it is** | Training methodology | Pre-trained model |
| **Base requirement** | Needs an existing encoder to fine-tune | Standalone encoder |
| **Training data** | None (generates its own via AST transforms) | Large text-code corpus (pre-trained by Amazon) |
| **Training cost** | Hours on 1 GPU | Already done (use pre-trained weights) |
| **Model size impact** | Zero — same model, better weights | 130M variant: ~similar to UniXcoder (~125MB). 356M: ~350MB. 1.3B: ~1.3GB (too large) |
| **Inference latency** | Unchanged from base model | 130M: comparable to UniXcoder. 356M+: noticeably slower |
| **Embedding dim** | Inherits from base (768 for UniXcoder) | 1024 (all variants) |
| **What improves** | Robustness to code transformations (variable renaming, loop refactoring, statement reordering) | Overall code understanding (comments, docstrings, cross-modal semantics) |
| **Weakness** | Only learns equivalences expressible as AST transforms — can't learn that mergesort ≈ quicksort | Pre-trained for general code understanding, not specifically for clone detection |
| **Best BCB result** | F1 82.36% (unsupervised) | Not benchmarked on BCB directly; 41% better than ada-002 on code search |

#### Why not just use one?

They address different gaps in the embedding:

- **CodeSage** starts from a better foundation — its contrastive pre-training on text-code pairs means it understands what code *means* (via docstrings, comments, variable names) better than UniXcoder, which was primarily trained on code tokens. This helps with cases where two functions use completely different APIs but are described the same way.

- **TransformCode** teaches robustness to surface-level code changes — the model learns that `for` ↔ `while`, declaration reordering, condition inversion, and variable renaming don't change semantics. This helps with the AI-echo case where generated code uses the same algorithm but slightly different syntax.

UniXcoder's current weakness is both: it doesn't deeply understand semantics (CodeSage's strength) AND it's brittle to syntactic transformations (TransformCode's strength).

#### The recommended approach for Echo-Guard

**Apply TransformCode training to CodeSage-130M.** This gives you both improvements without blowing up model size:

```
Current:    UniXcoder (125M params, 768-dim, ~125MB ONNX quantized)
Proposed:   CodeSage-130M + TransformCode fine-tuning (130M params, 1024-dim, ~130MB ONNX quantized)
```

The pipeline change:

```
Step 1: Swap UniXcoder for CodeSage-130M
        → Better baseline semantic understanding
        → Minimal size increase (~5MB more after quantization)
        → Embedding dim 768 → 1024 (rebuild embeddings.npy, ~33% larger)
        → Latency: comparable (same parameter count)

Step 2: Apply TransformCode contrastive fine-tuning to CodeSage-130M
        → Use tree-sitter (already in Echo-Guard) to generate AST transforms
        → Train on the codebase's own code or public code corpora
        → No labeled data needed — fully unsupervised
        → Hours on a single GPU, or a cloud training job
        → Output: same 130M model with better weights for clone detection

Step 3: ONNX export + INT8 quantization (same as current UniXcoder pipeline)
        → Ship the fine-tuned model in model_cache/
        → No runtime dependency on PyTorch
```

**What this costs:**
- Download size: ~130MB (vs current ~125MB) — negligible increase
- Embedding storage: ~33% larger (1024-dim vs 768-dim per function)
- Inference latency: comparable to current (~15ms/function)
- One-time training: a few hours on a GPU

**What this doesn't solve:**
True algorithmic equivalence (mergesort vs. quicksort) still requires execution-based validation or much larger models. But for the AI-echo use case — same algorithm, different surface syntax — this combination should significantly close the gap.

#### Why not CodeSage-356M or 1.3B?

| Variant | Params | ONNX Size (est.) | Latency (est.) | Verdict |
|---------|--------|-------------------|----------------|---------|
| CodeSage-130M | 130M | ~130MB | ~15ms/fn | Good — similar to UniXcoder |
| CodeSage-356M | 356M | ~350MB | ~40ms/fn | Marginal — 2.5x slower, 2.5x larger download |
| CodeSage-1.3B | 1.3B | ~1.3GB | ~150ms/fn | Too large — 10x current download, unusable latency for real-time |

The 130M variant is the sweet spot. Echo-Guard is a CLI tool that needs to run on developer laptops without a GPU. The diminishing returns from 356M→1.3B aren't worth the latency and download cost.

#### Ensemble alternative (not recommended)

Running both UniXcoder and CodeSage in parallel and averaging/maxing their similarity scores is technically possible but doubles inference time and memory. For a CLI tool with real-time VS Code integration, this is the wrong trade-off. The contrastive fine-tuning approach gets you most of the benefit at zero additional runtime cost.

---

### CC2Vec (FSE 2024) — Typed Token Approach

**How it works:** Divides code tokens into categories by syntactic type, applies self-attention, trains with contrastive learning on known-equivalent pairs.

**Results (BigCloneBench Type-4):** 64% recall with 98% precision — though BCB numbers are unreliable per the mislabeling findings above.

Reference: [CC2Vec: Combining Typed Tokens with Contrastive Learning](https://dl.acm.org/doi/full/10.1145/3660777)

### GraphCodeBERT (Microsoft, ICLR 2021) — Data Flow Graphs

**How it works:** Incorporates data flow graphs (encoding "where-the-value-comes-from" relationships between variables) alongside token sequences during pre-training.

**Trade-off:** More expressive than token-only models but requires data flow extraction infrastructure. Heavier than UniXcoder.

Reference: [GraphCodeBERT: Pre-training Code Representations with Data Flow](https://arxiv.org/abs/2009.08366)

---

## Approach 2: Execution-Based Validation (Highest Signal)

The most reliable way to know if two functions do the same thing: run them on the same inputs and compare outputs.

### HyClone (2025) — LLM-Generated Test Inputs

**How it works:** Two-stage pipeline:
1. LLM screens code pairs to filter obvious non-clones
2. LLM generates 8-12 test inputs for both functions, runs cross-execution validation
3. Clone if output match rate >= 0.8

**Results (PyFuncEquivDataset, 751 Python pairs):**
| Model | LLM-only F1 | HyClone F1 | Recall boost |
|-------|------------|------------|-------------|
| GPT-4o-mini | 0.110 | 0.639 | +1224% |
| Deepseek-v3 | 0.563 | 0.629 | +45% |

**Precision ceiling:** ~50%. Two functions can agree on many inputs but diverge on edge cases, or handle different domains despite similar I/O patterns.

**Limitations:** Python only. Requires sandboxed execution. Can't handle functions with side effects, I/O, or external dependencies. Expensive (LLM calls per pair).

**How it could fit Echo-Guard:** As a selective Tier 4 — only trigger on pairs that pass embedding threshold but have borderline confidence. Would only work for Python pure functions initially.

Reference: [HyClone: Bridging LLM Understanding and Dynamic Execution](https://arxiv.org/abs/2508.01357)

### Property-Based Testing for Equivalence (Unexplored)

No dedicated tool exists that applies Hypothesis/QuickCheck-style property-based testing for clone detection. The concept: use one function as an oracle, generate random inputs via type-aware strategies, test whether a candidate produces identical output.

**Advantage over HyClone:** More systematic input generation. Hypothesis strategies can cover edge cases that LLM-generated inputs miss.

**Gap:** Requires type inference to generate appropriate input distributions. Nobody has built this yet.

---

## Approach 3: Program Analysis / Formal Methods

### LLVM IR Comparison

Compile both functions to LLVM IR, then compare at the intermediate representation level. The compiler's optimizer normalizes away many surface differences (variable naming, expression ordering, loop structure).

**Tools:**
- **IRBinDiff (2024):** Integrates pre-trained language models with graph neural networks on LLVM-IR
- **Optir-SBERT (2024):** 94.38% accuracy in cross-architecture binary matching
- **GraphBinMatch:** F1 0.79 with 20%+ improvement over baselines

**Limitation:** Only works for languages with LLVM frontends (C, C++, Rust, Swift). Not applicable to Python, JS, Ruby, Go. Better suited for vulnerability detection than developer-facing clone detection.

### SMT-Based Equivalence (Alive2)

Encodes function behavior into SMT formulas and either proves equivalence or produces a counterexample.

**Limitation:** Only works on loop-free LLVM IR. Computationally expensive (NP-hard). Not practical for scanning an entire codebase — could theoretically verify individual pairs flagged by an embedding-based filter.

Reference: [Alive2: Bounded Translation Validation for LLVM](https://users.cs.utah.edu/~regehr/alive2-pldi21.pdf)

### Bytecode-Level Detection (SeByte)

Operates on Java bytecode. Normalizes away source syntax differences by comparing at the compiled level.

**Trade-off:** JVM-only (Java, Kotlin, Scala). The bytecode approach is clever but limited in language scope.

---

## Approach 4: LLM-as-Judge

Prompt an LLM with two code snippets, ask whether they are functionally equivalent.

**Results (CodeNet + BigCloneBench):**
| Model | F1 |
|-------|-----|
| o3-mini | 0.943 |
| Mistral | 0.934 |
| GPT-4o | 0.899 |
| GPT-4o-mini | 0.832 |

**Critical finding:** Performance drops 0.1-0.52 F1 on BigCloneBench vs. CodeNet. Models show bias toward LLM-generated code. Even identical code pairs don't reach 1.0 recall when prompts emphasize functional equivalence.

**Feasibility for Echo-Guard:** O(n^2) pairwise API calls makes this unusable as a scanner. Could work as a verification stage on a small number of candidates (e.g., top-50 borderline pairs from Tier 2).

Reference: [An Empirical Study of LLM-Based Code Clone Detection](https://arxiv.org/abs/2511.01176v1)

---

## Approach 5: Specification / Contract-Based

### LLM-Generated Specifications (Clover, Stanford 2024)

**How it works:** LLM generates formal annotations (Dafny specifications) for functions, then a checker verifies logical equivalence of the annotations.

**Potential for clone detection:** Generate a spec for each function, compare specs for equivalence. But LLM-generated specs are often incomplete/incorrect, and formal equivalence checking of specs is itself undecidable in general.

**Lighter-weight variant:** Generate function type signatures + precondition/postcondition pairs via LLM as a cheap pre-filter. Functions with incompatible signatures can't be Type-4 clones.

Reference: [Clover: Closed-Loop Verifiable Code Generation](https://arxiv.org/abs/2310.17807)

---

## Other Relevant Tools & Papers

| Tool | Method | Type-4 Capability | Notes |
|------|--------|-------------------|-------|
| **MISIM** (Intel, 2021) | Context-Aware Semantics Structure + neural scoring | Strong | 8% better MAP@R than next best across 328K programs. Open source. |
| **DeepSim** (2018) | Deep learning on control flow + data flow graphs | First DL approach for Type-4 | Java only. Research prototype. High FP rate (15-25%). |
| **CCLearner** (2017) | Token features + supervised DNN | Limited Type-4 | Requires large labeled datasets. Java only. |
| **Code2vec** (2019) | AST path-context embeddings | Partial | Designed for method name prediction; embeddings usable for similarity. |
| **InferCode** (2021) | Self-supervised on AST subtrees | Moderate | No labeled data needed. Computationally intensive. |
| **Aroma** (Facebook, 2019) | Structural code search with pruning | Not Type-4 focused | Code recommendation, not clone detection. |

---

## Practical Recommendations for Echo-Guard

Ranked by effort/impact:

### 1. CodeSage-130M + TransformCode fine-tuning (Medium effort, highest impact)
Replace UniXcoder with CodeSage-130M for a better semantic foundation, then apply TransformCode-style contrastive fine-tuning using tree-sitter AST transformations (already in the pipeline). This is the recommended path — see the [detailed comparison above](#transformcode-vs-codesage-what-they-actually-are) for why these complement each other rather than compete. Negligible impact on download size (~130MB vs ~125MB) and inference latency. Directly improves the signal that dominates the classifier (embedding_score at 60.35 coefficient).

### 3. Type signature pre-filtering (Low effort, modest impact)
Extract function signatures (parameter types, return types) and use as a cheap pre-filter. Two functions with incompatible signatures can't be semantic clones. Reduces false positives and narrows the candidate set for expensive analysis.

### 4. Execution-based Tier 4 for Python (High effort, high impact for Python)
Selective execution-based validation for Python pure functions. Generate test inputs via LLM, run both candidates in sandbox, compare outputs. Only trigger on borderline-confidence pairs. Would definitively confirm semantic equivalence for pure functions.

### 5. LLM-as-judge verification (Low effort, moderate impact, ongoing cost)
Use an LLM to evaluate the top-N borderline pairs from Tier 2. Not a replacement for the pipeline, but a verification step. The ongoing API cost makes this better suited for CI/PR checks than continuous scanning.

---

## The Emerging Consensus: Multi-Stage Pipeline

The most effective architecture for Type-4 detection appears to be:

```
Stage 1: Embedding retrieval     (fast, high recall, O(n log n) with ANN)
Stage 2: Feature/structural filter (fast, improves precision)
Stage 3: LLM semantic screening   (medium cost, filters FPs)
Stage 4: Execution validation      (high cost, definitive for pure functions)
```

Echo-Guard already has Stages 1-2. The question is whether Stage 3 and/or 4 are worth adding, given the use case (AI-generated echoes vs. truly independent implementations).

---

## Key Takeaway

True Type-4 detection (mergesort vs. quicksort) requires understanding **behavior**, not syntax. There are only two proven ways to get at behavior:
1. **Run the code** (execution-based)
2. **Train models specifically on behavioral equivalence** (contrastive learning on functional pairs)

Embeddings trained on code tokens/structure will always hit a ceiling on semantically diverse implementations. The question for Echo-Guard is whether to chase the academic Type-4 problem or own the AI-echo niche where the current approach already works well.

---

## References

- Krinke et al. (2025) — [BigCloneBench mislabeling](https://arxiv.org/abs/2505.04311)
- TransformCode (2024) — [Contrastive learning via subtree transformation](https://arxiv.org/abs/2311.08157)
- CodeSage (Amazon, 2024) — [Code representation learning at scale](https://github.com/amazon-science/CodeSage)
- CC2Vec (FSE 2024) — [Typed tokens with contrastive learning](https://dl.acm.org/doi/full/10.1145/3660777)
- GraphCodeBERT (ICLR 2021) — [Pre-training with data flow](https://arxiv.org/abs/2009.08366)
- HyClone (2025) — [LLM + dynamic execution for semantic clones](https://arxiv.org/abs/2508.01357)
- Alive2 (PLDI 2021) — [SMT-based LLVM validation](https://users.cs.utah.edu/~regehr/alive2-pldi21.pdf)
- MISIM (Intel, 2021) — [Neural code semantics similarity](https://arxiv.org/abs/2006.05265)
- DeepSim (2018) — [Deep learning functional similarity](https://dl.acm.org/doi/10.1145/3236024.3236068)
- Clover (Stanford, 2024) — [Closed-loop verifiable code generation](https://arxiv.org/abs/2310.17807)
- LLM clone detection study (2025) — [Empirical study](https://arxiv.org/abs/2511.01176v1)
- GPTCloneBench (ICSME 2023) — [AI-generated clone benchmark](https://arxiv.org/abs/2308.13963)
- POJ-104 (NeurIPS 2021) — [CodeXGLUE benchmark](https://arxiv.org/abs/2102.04664)
