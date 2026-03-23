"""BigCloneBench benchmark adapter.

BigCloneBench (Svajlenko & Roy, ICSE 2014) is the largest clone detection benchmark,
containing 8M+ labeled Java clone pairs across Type-1 through Type-4.

Dataset structure (after H2 export):
- clonepairs.csv: Stratified sample of clone pairs from the CLONES table
- false_positives.csv: Sample of non-clone pairs from the FALSE_POSITIVES table
- functions.csv: All 22M functions with file/line metadata
- bcb_reduced/: IJaDataset Java source files organized by functionality ID
  - {functionality_id}/{type}/{filename}.java

Reference: https://github.com/jeffsvajlenko/BigCloneEval
"""

from __future__ import annotations

import csv
from pathlib import Path

from benchmarks.base import BenchmarkAdapter, BenchmarkPair


def _classify_bcb_type(syntactic_type: int, similarity_token: float) -> str:
    """Classify a BCB pair's clone type using syntactic_type + token similarity.

    BigCloneBench uses:
    - syntactic_type 1: Type-1 (exact clones)
    - syntactic_type 2: Type-2 (renamed identifiers)
    - syntactic_type 3: Type-3 or Type-4 depending on similarity
      - token similarity >= 0.7: Type-3 (modified statements)
      - token similarity >= 0.5: Type-3 moderate (borderline)
      - token similarity < 0.5: Type-4 (semantic clones)
    """
    if syntactic_type == 1:
        return "type1"
    if syntactic_type == 2:
        return "type2"
    if similarity_token >= 0.5:
        return "type3"
    return "type4"


class BigCloneBenchAdapter(BenchmarkAdapter):
    """Adapter for the BigCloneBench dataset.

    Requires the real BCB data exported from the H2 database (clonepairs.csv,
    false_positives.csv, functions.csv, bcb_reduced/ source tree).

    See benchmarks/SETUP.md for setup instructions.
    """

    def __init__(self, data_dir: Path | None = None):
        super().__init__(data_dir)
        self._func_index: dict[str, dict] | None = None

    @property
    def name(self) -> str:
        return "BigCloneBench"

    @property
    def dataset_id(self) -> str:
        return "bigclonebench"

    def is_available(self) -> bool:
        """Check if the real dataset is available."""
        bcb_dir = self.data_dir / "bigclonebench"
        return (bcb_dir / "clonepairs.csv").exists()

    def download(self, force: bool = False) -> None:
        """Print instructions for manual dataset setup."""
        print("  BigCloneBench requires manual setup:")
        print("  1. Download BigCloneBench H2 DB + IJaDataset from BigCloneEval")
        print("  2. Run: ./benchmarks/setup_datasets.sh bigclonebench")
        print("  3. See benchmarks/SETUP.md for full instructions")

    def load_pairs(self, max_pairs: int | None = None) -> list[BenchmarkPair]:
        """Load pairs from BigCloneBench."""
        bcb_dir = self.data_dir / "bigclonebench"
        clonepairs_csv = bcb_dir / "clonepairs.csv"

        if not clonepairs_csv.exists():
            raise FileNotFoundError(
                "BigCloneBench dataset not found.\n"
                "  1. Download the BigCloneBench H2 DB and IJaDataset tar files\n"
                "  2. Place them in benchmarks/data/bigclonebench/\n"
                "  3. Run: ./benchmarks/setup_datasets.sh bigclonebench\n"
                "  See benchmarks/SETUP.md for full instructions."
            )

        return self._load_from_dataset(bcb_dir, max_pairs)

    # ── Real dataset loading ───────────────────────────────────────────

    def _build_func_index(self, bcb_dir: Path) -> dict[str, dict]:
        """Build an in-memory index from functions.csv for fast lookups.

        Returns: {function_id: {name, type, startline, endline}}
        """
        if self._func_index is not None:
            return self._func_index

        func_csv = bcb_dir / "functions.csv"
        if not func_csv.exists():
            raise FileNotFoundError(
                f"functions.csv not found at {func_csv}\n"
                "  Re-run: ./benchmarks/setup_datasets.sh bigclonebench"
            )

        index: dict[str, dict] = {}
        with open(func_csv, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                fid = row["ID"]
                index[fid] = {
                    "name": row["NAME"],
                    "type": row["TYPE"],
                    "startline": int(row["STARTLINE"]),
                    "endline": int(row["ENDLINE"]),
                }

        self._func_index = index
        return index

    def _load_function_source(
        self, bcb_dir: Path, func_id: str, functionality_id: str,
        func_index: dict[str, dict],
    ) -> str | None:
        """Load a Java function's source code by its BCB function ID.

        Path: bcb_reduced/{functionality_id}/{type}/{filename}
        Then extract lines startline..endline.
        """
        info = func_index.get(func_id)
        if info is None:
            return None

        filepath = (
            bcb_dir / "bcb_reduced" / str(functionality_id)
            / info["type"] / info["name"]
        )
        if not filepath.exists():
            return None

        try:
            lines = filepath.read_text(errors="replace").splitlines()
            start = info["startline"] - 1
            end = info["endline"]
            func_lines = lines[start:end]
            if not func_lines:
                return None
            return "\n".join(func_lines)
        except (OSError, UnicodeDecodeError):
            return None

    def _load_from_dataset(
        self, bcb_dir: Path, max_pairs: int | None
    ) -> list[BenchmarkPair]:
        """Load pairs from the exported BigCloneBench CSV files."""
        func_index = self._build_func_index(bcb_dir)

        pairs: list[BenchmarkPair] = []

        # Reserve ~80% of budget for positives, ~20% for negatives
        pos_limit = int(max_pairs * 0.8) if max_pairs else None
        neg_limit = (max_pairs - pos_limit) if max_pairs and pos_limit else None

        # Load positive pairs (true clones)
        clonepairs_csv = bcb_dir / "clonepairs.csv"
        with open(clonepairs_csv, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if pos_limit and len(pairs) >= pos_limit:
                    break

                fid1 = row["FUNCTION_ID_ONE"]
                fid2 = row["FUNCTION_ID_TWO"]
                func_id = row["FUNCTIONALITY_ID"]
                syntactic_type = int(row["SYNTACTIC_TYPE"])
                sim_token = float(row["SIMILARITY_TOKEN"])

                clone_type = _classify_bcb_type(syntactic_type, sim_token)

                code_a = self._load_function_source(
                    bcb_dir, fid1, func_id, func_index
                )
                code_b = self._load_function_source(
                    bcb_dir, fid2, func_id, func_index
                )

                if code_a is None or code_b is None:
                    continue

                pairs.append(
                    BenchmarkPair(
                        pair_id=f"bcb_{fid1}_{fid2}",
                        code_a=code_a,
                        code_b=code_b,
                        language_a="java",
                        language_b="java",
                        is_clone=True,
                        clone_type=clone_type,
                        source_dataset="bigclonebench",
                        metadata={
                            "syntactic_type": syntactic_type,
                            "similarity_token": sim_token,
                            "functionality_id": func_id,
                            "function_id_1": fid1,
                            "function_id_2": fid2,
                        },
                    )
                )

        # Load negative pairs (false positives = same functionality, not clones)
        fp_csv = bcb_dir / "false_positives.csv"
        remaining = neg_limit
        if fp_csv.exists():
            with open(fp_csv, "r") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if remaining is not None and remaining <= 0:
                        break

                    fid1 = row["FUNCTION_ID_ONE"]
                    fid2 = row["FUNCTION_ID_TWO"]
                    func_id = row["FUNCTIONALITY_ID"]

                    code_a = self._load_function_source(
                        bcb_dir, fid1, func_id, func_index
                    )
                    code_b = self._load_function_source(
                        bcb_dir, fid2, func_id, func_index
                    )

                    if code_a is None or code_b is None:
                        continue

                    pairs.append(
                        BenchmarkPair(
                            pair_id=f"bcb_neg_{fid1}_{fid2}",
                            code_a=code_a,
                            code_b=code_b,
                            language_a="java",
                            language_b="java",
                            is_clone=False,
                            clone_type="negative",
                            source_dataset="bigclonebench",
                            metadata={
                                "functionality_id": func_id,
                                "function_id_1": fid1,
                                "function_id_2": fid2,
                            },
                        )
                    )
                    if remaining is not None:
                        remaining -= 1

        return pairs
