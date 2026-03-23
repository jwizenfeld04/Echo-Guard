"""Code embedding infrastructure for Tier 2 semantic clone detection.

Provides embedding computation via UniXcoder (ONNX Runtime, INT8) and
disk-backed embedding storage via NumPy memmap.

Architecture:
    Tier 1 (AST hash): Catches Type-1/Type-2 clones in O(1)
    Tier 2 (this module): Catches Type-3/Type-4 via learned code embeddings

The embedding pipeline:
1. EmbeddingModel loads UniXcoder (ONNX Runtime, INT8 quantized, ~125MB)
2. Functions are embedded into 768-dim vectors via mean pooling (~15ms/function)
3. Vectors are stored in a NumPy memmap file on disk (.echo-guard/embeddings.npy)
4. Similarity search uses brute-force cosine similarity (NumPy dot product)
   - At 100K functions: ~1-2ms search latency
   - For >500K functions: install echo-guard[scale] for USearch ANN
5. Results are merged with Tier 1 (AST hash) matches
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path

import numpy as np

from echo_guard.languages import ExtractedFunction

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────

DEFAULT_MODEL_ID = "microsoft/unixcoder-base"
DEFAULT_EMBEDDING_DIM = 768
MODEL_CACHE_DIR_NAME = "model_cache"
EMBEDDINGS_FILE = "embeddings.npy"
EMBEDDING_META_FILE = "embedding_meta.json"

# Per-language embedding similarity thresholds for Tier 2 detection.
# Calibrated empirically: for each language, we measured cosine similarity
# between known clone pairs and known non-clone pairs, then set the threshold
# at the midpoint of the gap, biased toward precision (fewer false positives).
#
# Python needs a higher threshold because UniXcoder was heavily trained on
# Python, causing all Python functions to cluster tightly in embedding space.
# Other languages have cleaner separation between clones and non-clones.
LANGUAGE_EMBEDDING_THRESHOLDS: dict[str, float] = {
    "python": 0.94,
    "java": 0.81,
    "javascript": 0.85,
    "typescript": 0.83,
    "go": 0.81,
    "rust": 0.83,
    "c": 0.83,
    "cpp": 0.83,
    "ruby": 0.85,
}

# Fallback threshold for unknown languages or cross-language matches
DEFAULT_EMBEDDING_THRESHOLD = 0.85

def get_embedding_threshold(language_a: str, language_b: str | None = None) -> float:
    """Get the embedding similarity threshold for a language pair.

    For same-language pairs, uses the language-specific threshold.
    For cross-language pairs, uses the lower of the two thresholds
    (cross-language clones score lower due to syntax differences).
    """
    t_a = LANGUAGE_EMBEDDING_THRESHOLDS.get(language_a, DEFAULT_EMBEDDING_THRESHOLD)
    if language_b is None or language_b == language_a:
        return t_a
    t_b = LANGUAGE_EMBEDDING_THRESHOLDS.get(language_b, DEFAULT_EMBEDDING_THRESHOLD)
    return min(t_a, t_b)


# Maximum tokens per function for the model. UniXcoder uses RoBERTa tokenizer
# with max 512 tokens. Functions longer than this are truncated.
MAX_TOKENS = 512


# ── Availability check ────────────────────────────────────────────────────

def _usearch_available() -> bool:
    """Check if the USearch ANN library is installed.

    USearch provides approximate nearest neighbor search for large indexes
    (>500K functions). Install via: pip install "echo-guard[scale]"
    """
    try:
        from usearch.index import Index  # noqa: F401
        return True
    except ImportError:
        return False




# ── Embedding Model ───────────────────────────────────────────────────────

class EmbeddingModel:
    """Loads and runs a code embedding model for semantic similarity.

    Uses UniXcoder (microsoft/unixcoder-base) by default, exported to ONNX
    format with INT8 dynamic quantization for fast CPU inference.

    Typical per-function latency: ~10-20ms on modern CPU (ONNX INT8).

    Usage:
        model = EmbeddingModel(cache_dir=Path(".echo-guard/model_cache"))
        embedding = model.embed_function(func)  # -> np.ndarray of shape (768,)
        embeddings = model.embed_functions(funcs)  # -> np.ndarray of shape (N, 768)
    """

    def __init__(
        self,
        model_id: str = DEFAULT_MODEL_ID,
        cache_dir: Path | None = None,
        embedding_dim: int = DEFAULT_EMBEDDING_DIM,
    ):
        self.model_id = model_id
        self.embedding_dim = embedding_dim
        self.cache_dir = cache_dir or Path.home() / ".cache" / "echo-guard" / "models"
        self._session = None
        self._tokenizer = None
        self._ready = False

    def ensure_ready(self) -> None:
        """Download model (if needed) and initialize ONNX session.

        Called lazily on first embed call. The model is downloaded once
        and cached locally (~500MB for PyTorch, ~125MB for quantized ONNX).
        """
        if self._ready:
            return

        import onnxruntime as ort
        from transformers import AutoTokenizer

        onnx_dir = self.cache_dir / self.model_id.replace("/", "--") / "onnx"
        onnx_path = onnx_dir / "model_quantized.onnx"

        if not onnx_path.exists():
            logger.info("Downloading and converting %s to ONNX (first-time setup)...", self.model_id)
            self._export_to_onnx(onnx_dir)

        # Load tokenizer
        self._tokenizer = AutoTokenizer.from_pretrained(
            self.model_id,
            cache_dir=str(self.cache_dir),
        )

        # Load ONNX session with optimizations
        sess_options = ort.SessionOptions()
        sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        sess_options.intra_op_num_threads = os.cpu_count() or 4
        sess_options.inter_op_num_threads = 1

        self._session = ort.InferenceSession(
            str(onnx_path),
            sess_options=sess_options,
            providers=["CPUExecutionProvider"],
        )

        self._ready = True
        logger.info("Embedding model ready: %s (ONNX INT8)", self.model_id)

    def _export_to_onnx(self, onnx_dir: Path) -> None:
        """Export the HuggingFace model to ONNX with INT8 quantization.

        This runs once on first use. The quantized ONNX model is ~125MB
        (vs ~500MB for the PyTorch model) and runs 3-5x faster on CPU.
        """
        onnx_dir.mkdir(parents=True, exist_ok=True)

        try:
            from optimum.onnxruntime import ORTModelForFeatureExtraction
            from optimum.onnxruntime.configuration import AutoQuantizationConfig

            # Export to ONNX
            logger.info("Exporting %s to ONNX...", self.model_id)
            model = ORTModelForFeatureExtraction.from_pretrained(
                self.model_id,
                export=True,
                cache_dir=str(self.cache_dir),
            )
            model.save_pretrained(str(onnx_dir))

            # Apply dynamic INT8 quantization
            logger.info("Applying INT8 quantization...")
            qconfig = AutoQuantizationConfig.avx2(is_static=False)

            from optimum.onnxruntime import ORTQuantizer

            quantizer = ORTQuantizer.from_pretrained(str(onnx_dir))
            quantizer.quantize(
                save_dir=str(onnx_dir),
                quantization_config=qconfig,
            )
            logger.info("ONNX model exported and quantized at %s", onnx_dir)

        except ImportError:
            # optimum not available — fall back to direct torch export
            logger.info("optimum not available, using direct torch ONNX export...")
            self._export_torch_onnx(onnx_dir)

    def _export_torch_onnx(self, onnx_dir: Path) -> None:
        """Fallback ONNX export using torch directly (no quantization)."""
        import torch
        from transformers import AutoModel, AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(
            self.model_id, cache_dir=str(self.cache_dir)
        )
        model = AutoModel.from_pretrained(
            self.model_id, cache_dir=str(self.cache_dir)
        )
        model.eval()

        dummy = tokenizer("def hello(): pass", return_tensors="pt", padding=True)
        onnx_path = onnx_dir / "model_quantized.onnx"

        torch.onnx.export(
            model,
            (dummy["input_ids"], dummy["attention_mask"]),
            str(onnx_path),
            input_names=["input_ids", "attention_mask"],
            output_names=["last_hidden_state"],
            dynamic_axes={
                "input_ids": {0: "batch", 1: "seq"},
                "attention_mask": {0: "batch", 1: "seq"},
                "last_hidden_state": {0: "batch", 1: "seq"},
            },
            opset_version=14,
        )

        # Apply dynamic quantization via onnxruntime
        try:
            from onnxruntime.quantization import quantize_dynamic, QuantType

            unquantized = onnx_dir / "model_unquantized.onnx"
            shutil.move(str(onnx_path), str(unquantized))
            quantize_dynamic(
                str(unquantized),
                str(onnx_path),
                weight_type=QuantType.QInt8,
            )
            unquantized.unlink()
            logger.info("Applied INT8 dynamic quantization")
        except ImportError:
            logger.warning("onnxruntime quantization not available, using FP32 model")

        # Save tokenizer alongside model
        tokenizer.save_pretrained(str(onnx_dir))

    def embed_function(self, func: ExtractedFunction) -> np.ndarray:
        """Compute embedding for a single function.

        Returns a unit-normalized 768-dim vector (float32).
        Typical latency: ~10-20ms on CPU with ONNX INT8.
        """
        self.ensure_ready()
        return self._embed_code(func.source)

    def embed_functions(
        self,
        funcs: list[ExtractedFunction],
        batch_size: int = 32,
        show_progress: bool = False,
    ) -> np.ndarray:
        """Compute embeddings for multiple functions in batches.

        Returns an (N, 768) array of unit-normalized vectors.

        Args:
            funcs: List of functions to embed.
            batch_size: Number of functions per batch (higher = more memory).
            show_progress: If True, print progress updates.

        Returns:
            np.ndarray of shape (len(funcs), embedding_dim), dtype float32.
        """
        self.ensure_ready()

        n = len(funcs)
        embeddings = np.zeros((n, self.embedding_dim), dtype=np.float32)

        for start in range(0, n, batch_size):
            end = min(start + batch_size, n)
            batch_sources = [f.source for f in funcs[start:end]]
            batch_embeddings = self._embed_batch(batch_sources)
            embeddings[start:end] = batch_embeddings

            if show_progress and n > batch_size:
                pct = min(100, int(end / n * 100))
                logger.info("Embedding progress: %d/%d (%d%%)", end, n, pct)

        return embeddings

    def _embed_code(self, source: str) -> np.ndarray:
        """Embed a single code string. Returns a unit-normalized vector."""
        result = self._embed_batch([source])
        return result[0]

    def _embed_batch(self, sources: list[str]) -> np.ndarray:
        """Embed a batch of code strings. Returns (N, dim) normalized vectors."""
        assert self._tokenizer is not None
        assert self._session is not None

        # Tokenize
        inputs = self._tokenizer(
            sources,
            padding=True,
            truncation=True,
            max_length=MAX_TOKENS,
            return_tensors="np",
        )

        # Run ONNX inference
        ort_inputs = {
            "input_ids": inputs["input_ids"].astype(np.int64),
            "attention_mask": inputs["attention_mask"].astype(np.int64),
        }

        outputs = self._session.run(None, ort_inputs)
        hidden_states = outputs[0]  # (batch, seq_len, hidden_dim)

        # Mean pooling (mask out padding tokens)
        mask = inputs["attention_mask"][:, :, np.newaxis].astype(np.float32)
        masked = hidden_states * mask
        summed = masked.sum(axis=1)
        counts = mask.sum(axis=1).clip(min=1e-9)
        pooled = summed / counts

        # L2 normalize for cosine similarity via dot product
        norms = np.linalg.norm(pooled, axis=1, keepdims=True).clip(min=1e-9)
        normalized = pooled / norms

        return normalized.astype(np.float32)


# ── Embedding Store ───────────────────────────────────────────────────────

class EmbeddingStore:
    """Disk-backed embedding storage using NumPy memmap.

    Stores pre-computed, unit-normalized embeddings in a memory-mapped file.
    This allows the OS to page in only the portions needed for a query,
    keeping RAM usage low even for large codebases.

    Storage layout:
        .echo-guard/
        ├── embeddings.npy         # NumPy memmap, shape (capacity, dim), float32
        └── embedding_meta.json    # Metadata: model, dim, count, deletions

    Performance at 100K functions (768-dim):
        - Storage: ~300 MB on disk
        - Single query (brute-force cosine): ~1-2ms
        - Batch all-pairs (chunked): ~2-5s

    For >500K functions, install echo-guard[scale] for USearch ANN acceleration.
    """

    def __init__(
        self,
        store_dir: Path,
        embedding_dim: int = DEFAULT_EMBEDDING_DIM,
        use_usearch: bool | None = None,
    ):
        self.store_dir = Path(store_dir)
        self.embedding_dim = embedding_dim
        self._embeddings_path = self.store_dir / EMBEDDINGS_FILE
        self._meta_path = self.store_dir / EMBEDDING_META_FILE
        self._mmap: np.ndarray | None = None
        self._meta: dict | None = None
        # USearch ANN acceleration (optional, for >500K functions)
        # Auto-detect if not specified: use USearch when installed
        if use_usearch is None:
            self._use_usearch = _usearch_available()
        else:
            self._use_usearch = use_usearch
        self._usearch_index = None

    def _load_meta(self) -> dict:
        """Load or create embedding metadata."""
        if self._meta is not None:
            return self._meta

        if self._meta_path.exists():
            with open(self._meta_path) as f:
                self._meta = json.load(f)
        else:
            self._meta = {
                "model_id": DEFAULT_MODEL_ID,
                "embedding_dim": self.embedding_dim,
                "count": 0,
                "capacity": 0,
                "version": 1,
                "deleted_rows": [],
            }
        return self._meta

    def _save_meta(self) -> None:
        """Persist metadata to disk."""
        self.store_dir.mkdir(parents=True, exist_ok=True)
        with open(self._meta_path, "w") as f:
            json.dump(self._meta, f, indent=2)

    def _get_mmap(self, mode: str = "r") -> np.ndarray:
        """Get or create the memory-mapped embedding array."""
        meta = self._load_meta()
        capacity = meta.get("capacity", 0)

        if capacity == 0:
            return np.zeros((0, self.embedding_dim), dtype=np.float32)

        if self._mmap is None or (mode != "r" and not self._mmap.flags.writeable):
            if not self._embeddings_path.exists():
                return np.zeros((0, self.embedding_dim), dtype=np.float32)
            self._mmap = np.memmap(
                str(self._embeddings_path),
                dtype=np.float32,
                mode=mode,
                shape=(capacity, self.embedding_dim),
            )

        return self._mmap

    @property
    def count(self) -> int:
        """Number of active (non-deleted) embeddings."""
        meta = self._load_meta()
        return meta["count"] - len(meta.get("deleted_rows", []))

    @property
    def capacity(self) -> int:
        """Total allocated slots (including deleted)."""
        return self._load_meta().get("capacity", 0)

    def add_embeddings(
        self,
        embeddings: np.ndarray,
    ) -> list[int]:
        """Add embeddings to the store. Returns assigned row indices.

        Args:
            embeddings: (N, dim) array of unit-normalized vectors.

        Returns:
            List of row indices assigned to each embedding.
        """
        meta = self._load_meta()
        n = embeddings.shape[0]

        if n == 0:
            return []

        # Reuse deleted rows first
        deleted = meta.get("deleted_rows", [])
        reused_rows: list[int] = []
        new_rows: list[int] = []

        for i in range(n):
            if deleted:
                row = deleted.pop(0)
                reused_rows.append((row, i))
            else:
                new_rows.append(i)

        # Grow capacity if needed
        current_capacity = meta.get("capacity", 0)
        needed = len(new_rows)
        if needed > 0:
            new_capacity = current_capacity + max(needed, 256)  # Grow by at least 256
            self._resize(current_capacity, new_capacity)
            meta["capacity"] = new_capacity

        # Write embeddings
        mmap = np.memmap(
            str(self._embeddings_path),
            dtype=np.float32,
            mode="r+",
            shape=(meta["capacity"], self.embedding_dim),
        )

        assigned_rows: list[int] = [0] * n

        # Write reused rows
        for row, emb_idx in reused_rows:
            mmap[row] = embeddings[emb_idx]
            assigned_rows[emb_idx] = row

        # Write new rows
        for offset, emb_idx in enumerate(new_rows):
            row = current_capacity + offset
            mmap[row] = embeddings[emb_idx]
            assigned_rows[emb_idx] = row

        mmap.flush()
        self._mmap = None  # Reset so next read picks up new data
        self._usearch_index = None  # Invalidate ANN index
        usearch_path = self.store_dir / "embeddings.usearch"
        if usearch_path.exists():
            usearch_path.unlink(missing_ok=True)

        meta["count"] = meta.get("count", 0) + len(new_rows)
        meta["deleted_rows"] = deleted
        self._save_meta()

        return assigned_rows

    def _resize(self, old_capacity: int, new_capacity: int) -> None:
        """Grow the memmap file to accommodate more embeddings."""
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self._mmap = None  # Close any existing mmap

        if old_capacity == 0 or not self._embeddings_path.exists():
            # Create new file
            mmap = np.memmap(
                str(self._embeddings_path),
                dtype=np.float32,
                mode="w+",
                shape=(new_capacity, self.embedding_dim),
            )
            mmap.flush()
        else:
            # Grow existing file by appending zeros
            growth = new_capacity - old_capacity
            with open(self._embeddings_path, "ab") as f:
                zeros = np.zeros((growth, self.embedding_dim), dtype=np.float32)
                f.write(zeros.tobytes())

    def get_embedding(self, row: int) -> np.ndarray | None:
        """Get a single embedding by row index."""
        meta = self._load_meta()
        if row < 0 or row >= meta.get("capacity", 0):
            return None
        if row in set(meta.get("deleted_rows", [])):
            return None
        mmap = self._get_mmap()
        if mmap.shape[0] == 0:
            return None
        return np.array(mmap[row])

    def delete_rows(self, rows: list[int]) -> None:
        """Mark rows as deleted (lazy deletion)."""
        meta = self._load_meta()
        deleted = set(meta.get("deleted_rows", []))
        deleted.update(rows)
        meta["deleted_rows"] = sorted(deleted)
        self._save_meta()

    def search(
        self,
        query: np.ndarray,
        k: int = 10,
        threshold: float = DEFAULT_EMBEDDING_THRESHOLD,
        exclude_rows: set[int] | None = None,
    ) -> list[tuple[int, float]]:
        """Find the top-k most similar embeddings to a query vector.

        Search backend selection:
        - Default: NumPy brute-force cosine similarity (~1-2ms at 100K)
        - With echo-guard[scale]: USearch ANN (<1ms, scales to 1M+)

        Args:
            query: Unit-normalized query vector of shape (dim,).
            k: Maximum number of results.
            threshold: Minimum cosine similarity.
            exclude_rows: Row indices to skip (e.g., the query function itself).

        Returns:
            List of (row_index, similarity_score) tuples, sorted by score descending.
        """
        # Use USearch ANN if available and index is large enough to benefit
        if self._use_usearch and self.count > 1000:
            return self._search_usearch(query, k, threshold, exclude_rows)

        meta = self._load_meta()
        if meta.get("capacity", 0) == 0:
            return []

        mmap = self._get_mmap()
        if mmap.shape[0] == 0:
            return []

        # Compute cosine similarity (query is unit-normalized, so dot product = cosine)
        scores = mmap @ query  # (capacity,)

        # Build exclusion mask
        deleted = set(meta.get("deleted_rows", []))
        if exclude_rows:
            deleted = deleted | exclude_rows

        # Mask out deleted/excluded rows
        if deleted:
            for row in deleted:
                if 0 <= row < len(scores):
                    scores[row] = -1.0

        # Find top-k above threshold
        # Use argpartition for efficiency (avoids full sort)
        above_threshold = np.where(scores >= threshold)[0]
        if len(above_threshold) == 0:
            return []

        if len(above_threshold) <= k:
            top_indices = above_threshold
        else:
            # Get top-k from the above-threshold subset
            subset_scores = scores[above_threshold]
            top_in_subset = np.argpartition(subset_scores, -k)[-k:]
            top_indices = above_threshold[top_in_subset]

        # Sort by score descending
        sorted_order = np.argsort(scores[top_indices])[::-1]
        top_indices = top_indices[sorted_order]

        return [(int(idx), float(scores[idx])) for idx in top_indices]

    def _search_usearch(
        self,
        query: np.ndarray,
        k: int,
        threshold: float,
        exclude_rows: set[int] | None,
    ) -> list[tuple[int, float]]:
        """ANN search using USearch (for >500K functions).

        USearch uses HNSW (Hierarchical Navigable Small World) graphs for
        approximate nearest neighbor search. Sub-millisecond latency even
        at 1M+ vectors.

        Install: pip install "echo-guard[scale]"
        """
        if self._usearch_index is None:
            self._build_usearch_index()

        if self._usearch_index is None:
            # Fall back to brute-force if index build failed
            return self._search_bruteforce(query, k, threshold, exclude_rows)

        from usearch.index import Index

        # USearch returns (keys, distances) where distance = 1 - cosine_sim
        # Search for more than k to account for exclusions
        search_k = k + len(exclude_rows or set()) + 10
        results = self._usearch_index.search(query, search_k)

        meta = self._load_meta()
        deleted = set(meta.get("deleted_rows", []))
        if exclude_rows:
            deleted = deleted | exclude_rows

        matches = []
        for key, distance in zip(results.keys, results.distances):
            row = int(key)
            if row in deleted:
                continue
            # USearch cosine distance = 1 - similarity
            similarity = 1.0 - float(distance)
            if similarity >= threshold:
                matches.append((row, similarity))
            if len(matches) >= k:
                break

        matches.sort(key=lambda x: x[1], reverse=True)
        return matches

    def _build_usearch_index(self) -> None:
        """Build or load the USearch HNSW index."""
        try:
            from usearch.index import Index
        except ImportError:
            self._use_usearch = False
            return

        meta = self._load_meta()
        total_written = meta.get("count", 0)
        if total_written == 0:
            return

        usearch_path = self.store_dir / "embeddings.usearch"

        # Check if we can load an existing index
        if usearch_path.exists():
            try:
                idx = Index(ndim=self.embedding_dim, metric="cos")
                idx.load(str(usearch_path))
                self._usearch_index = idx
                return
            except Exception:
                usearch_path.unlink(missing_ok=True)

        # Build new index from memmap
        mmap = self._get_mmap()
        if mmap.shape[0] == 0:
            return

        deleted = set(meta.get("deleted_rows", []))
        active_rows = [i for i in range(total_written) if i not in deleted]

        if not active_rows:
            return

        idx = Index(ndim=self.embedding_dim, metric="cos")
        keys = np.array(active_rows, dtype=np.int64)
        vectors = np.array(mmap[active_rows])
        idx.add(keys, vectors)

        # Persist to disk
        idx.save(str(usearch_path))
        self._usearch_index = idx
        logger.info("Built USearch index: %d vectors", len(active_rows))

    def _search_bruteforce(
        self,
        query: np.ndarray,
        k: int,
        threshold: float,
        exclude_rows: set[int] | None,
    ) -> list[tuple[int, float]]:
        """Brute-force search fallback (identical to the main search path)."""
        meta = self._load_meta()
        mmap = self._get_mmap()
        if mmap.shape[0] == 0:
            return []

        scores = mmap @ query
        deleted = set(meta.get("deleted_rows", []))
        if exclude_rows:
            deleted = deleted | exclude_rows
        if deleted:
            for row in deleted:
                if 0 <= row < len(scores):
                    scores[row] = -1.0

        above_threshold = np.where(scores >= threshold)[0]
        if len(above_threshold) == 0:
            return []

        if len(above_threshold) <= k:
            top_indices = above_threshold
        else:
            subset_scores = scores[above_threshold]
            top_in_subset = np.argpartition(subset_scores, -k)[-k:]
            top_indices = above_threshold[top_in_subset]

        sorted_order = np.argsort(scores[top_indices])[::-1]
        top_indices = top_indices[sorted_order]
        return [(int(idx), float(scores[idx])) for idx in top_indices]

    def batch_search(
        self,
        threshold: float = DEFAULT_EMBEDDING_THRESHOLD,
        chunk_size: int = 1000,
    ) -> list[tuple[int, int, float]]:
        """Find all pairs above threshold using chunked matrix multiplication.

        For batch scan mode (echo-guard scan). Processes in chunks to avoid
        N² memory usage.

        Args:
            threshold: Minimum cosine similarity.
            chunk_size: Number of rows per chunk (controls memory usage).

        Returns:
            List of (row_a, row_b, similarity_score) tuples.
        """
        meta = self._load_meta()
        total_written = meta.get("count", 0)
        if total_written == 0:
            return []

        mmap = self._get_mmap()
        if mmap.shape[0] == 0:
            return []

        deleted = set(meta.get("deleted_rows", []))
        active_rows = [i for i in range(total_written) if i not in deleted]
        if len(active_rows) < 2:
            return []

        # NOTE: Fancy indexing copies data into RAM, negating memmap benefit.
        # For >500K functions, use USearch ANN (pip install echo-guard[scale])
        # instead of batch_search to avoid this copy.
        active_embeddings = mmap[active_rows]  # (N_active, dim) — copied into RAM
        pairs: list[tuple[int, int, float]] = []

        n = len(active_rows)
        for start in range(0, n, chunk_size):
            end = min(start + chunk_size, n)
            chunk = active_embeddings[start:end]  # (chunk_size, dim)

            # Compute similarity of this chunk against all subsequent rows
            # Only compute upper triangle to avoid duplicate pairs
            remaining = active_embeddings[start:]  # (n - start, dim)
            sim_matrix = chunk @ remaining.T  # (chunk_size, n - start)

            for i in range(end - start):
                row_a = active_rows[start + i]
                # Start from i+1 to avoid self-matches and duplicate pairs
                for j in range(i + 1, sim_matrix.shape[1]):
                    score = float(sim_matrix[i, j])
                    if score >= threshold:
                        row_b = active_rows[start + j]
                        pairs.append((row_a, row_b, score))

        return pairs

    def compact(self) -> dict[int, int]:
        """Compact the store by removing deleted rows and rewriting the file.

        Returns a mapping of old_row -> new_row for updating references.
        Call this periodically when many deletions have accumulated.
        """
        meta = self._load_meta()
        deleted = set(meta.get("deleted_rows", []))
        total_written = meta.get("count", 0)  # High water mark of written rows

        if not deleted or total_written == 0:
            return {}

        mmap = self._get_mmap()
        # Only consider rows that were actually written to (0..count-1),
        # not empty pre-allocated slots beyond the count.
        active_rows = [i for i in range(total_written) if i not in deleted]

        if not active_rows:
            # All deleted — reset
            self._mmap = None
            if self._embeddings_path.exists():
                self._embeddings_path.unlink()
            meta["count"] = 0
            meta["capacity"] = 0
            meta["deleted_rows"] = []
            self._save_meta()
            return {}

        # Read active embeddings
        active_data = np.array(mmap[active_rows])
        self._mmap = None

        # Rewrite file
        new_count = len(active_rows)
        new_mmap = np.memmap(
            str(self._embeddings_path),
            dtype=np.float32,
            mode="w+",
            shape=(new_count, self.embedding_dim),
        )
        new_mmap[:] = active_data
        new_mmap.flush()

        # Build remapping
        row_map = {old: new for new, old in enumerate(active_rows)}

        meta["count"] = new_count
        meta["capacity"] = new_count
        meta["deleted_rows"] = []
        self._save_meta()

        # Invalidate USearch index
        self._usearch_index = None
        usearch_path = self.store_dir / "embeddings.usearch"
        if usearch_path.exists():
            usearch_path.unlink(missing_ok=True)

        return row_map

    def clear(self) -> None:
        """Remove all embeddings and metadata."""
        self._mmap = None
        self._usearch_index = None
        if self._embeddings_path.exists():
            self._embeddings_path.unlink()
        self._meta = None
        if self._meta_path.exists():
            self._meta_path.unlink()
        usearch_path = self.store_dir / "embeddings.usearch"
        if usearch_path.exists():
            usearch_path.unlink(missing_ok=True)

    def get_model_info(self) -> dict:
        """Return metadata about the stored embeddings."""
        meta = self._load_meta()
        return {
            "model_id": meta.get("model_id", DEFAULT_MODEL_ID),
            "embedding_dim": meta.get("embedding_dim", self.embedding_dim),
            "count": self.count,
            "capacity": self.capacity,
            "deleted_rows": len(meta.get("deleted_rows", [])),
            "storage_mb": round(
                self.capacity * self.embedding_dim * 4 / (1024 * 1024), 1
            ),
        }
