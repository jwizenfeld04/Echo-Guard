"""POJ-104 benchmark adapter.

POJ-104 (from Microsoft CodeXGLUE) is a semantic clone detection benchmark
based on solutions to 104 programming problems from an online judge.

Multiple solutions to the same problem are considered semantic clones (Type-4),
as they implement the same functionality but often with completely different
code structure.

Dataset structure:
- Raw: ProgramData/{problem_id}/{solution_files} (52,000 C files)
- JSONL: train.jsonl / valid.jsonl / test.jsonl with {label, index, code}

Reference: https://github.com/microsoft/CodeXGLUE/tree/main/Code-Code/Clone-detection-POJ-104
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from benchmarks.base import BenchmarkAdapter, BenchmarkPair


class POJ104Adapter(BenchmarkAdapter):
    """Adapter for the POJ-104 semantic clone detection dataset.

    POJ-104 organizes code by problem ID. Solutions to the same problem
    are semantic clones; solutions to different problems are not.

    Requires the real POJ-104 data (directory of C files or JSONL).

    See benchmarks/SETUP.md for setup instructions.
    """

    @property
    def name(self) -> str:
        return "POJ-104"

    @property
    def dataset_id(self) -> str:
        return "poj104"

    def is_available(self) -> bool:
        poj_dir = self.data_dir / "poj104"
        return (
            (poj_dir / "ProgramData").exists()
            or (poj_dir / "programs").exists()
            or (poj_dir / "test.jsonl").exists()
        )

    def download(self, force: bool = False) -> None:
        print("  POJ-104 requires manual setup:")
        print("  1. pip install gdown")
        print("  2. Run: ./benchmarks/setup_datasets.sh poj104")
        print("  3. See benchmarks/SETUP.md for full instructions")

    def load_pairs(self, max_pairs: int | None = None) -> list[BenchmarkPair]:
        poj_dir = self.data_dir / "poj104"

        # Try JSONL format first (CodeXGLUE preprocessed)
        test_jsonl = poj_dir / "test.jsonl"
        if test_jsonl.exists():
            return self._load_from_jsonl(test_jsonl, max_pairs)

        # Try raw directory format
        for dirname in ("ProgramData", "programs"):
            programs_dir = poj_dir / dirname
            if programs_dir.exists():
                return self._load_from_directory(programs_dir, max_pairs)

        raise FileNotFoundError(
            "POJ-104 dataset not found.\n"
            "  1. pip install gdown\n"
            "  2. Run: ./benchmarks/setup_datasets.sh poj104\n"
            "  Or download programs.tar.gz manually from Google Drive:\n"
            "    https://drive.google.com/file/d/0B2i-vWnOu7MxVlJwQXN6eVNONUU/view\n"
            "  Place in benchmarks/data/poj104/ and re-run the setup script.\n"
            "  See benchmarks/SETUP.md for full instructions."
        )

    # ── Real dataset loading ───────────────────────────────────────────

    def _load_from_jsonl(
        self, jsonl_path: Path, max_pairs: int | None
    ) -> list[BenchmarkPair]:
        """Load pairs from CodeXGLUE preprocessed JSONL format.

        Each line: {"label": "problem_id", "index": "global_id", "code": "source"}
        """
        problems: dict[str, list[dict]] = {}
        with open(jsonl_path, "r") as f:
            for line in f:
                entry = json.loads(line.strip())
                label = entry["label"]
                problems.setdefault(label, []).append(entry)

        return self._generate_pairs_from_groups(problems, max_pairs)

    def _load_from_directory(
        self, programs_dir: Path, max_pairs: int | None
    ) -> list[BenchmarkPair]:
        """Load pairs from raw directory structure.

        Expected: programs_dir/{problem_id}/{solution_files}
        """
        problems: dict[str, list[dict]] = {}
        for problem_dir in sorted(programs_dir.iterdir()):
            if not problem_dir.is_dir():
                continue
            solutions = list(problem_dir.iterdir())
            for sol in solutions:
                if not sol.is_file():
                    continue
                try:
                    code = sol.read_text(encoding="latin-1", errors="replace")
                except Exception:
                    continue
                problems.setdefault(problem_dir.name, []).append({
                    "label": problem_dir.name,
                    "index": sol.name,
                    "code": code,
                })

        return self._generate_pairs_from_groups(problems, max_pairs)

    def _generate_pairs_from_groups(
        self, problems: dict[str, list[dict]], max_pairs: int | None
    ) -> list[BenchmarkPair]:
        """Generate positive and negative pairs from grouped solutions."""
        pairs: list[BenchmarkPair] = []
        rng = random.Random(42)

        # Positive pairs: same problem = Type-4 semantic clones
        # Sample up to 3 pairs per problem to keep balanced
        for problem_id, solutions in sorted(problems.items()):
            if len(solutions) < 2:
                continue
            sampled = rng.sample(solutions, min(len(solutions), 6))
            for i in range(0, len(sampled) - 1, 2):
                if max_pairs and len(pairs) >= max_pairs:
                    break
                a, b = sampled[i], sampled[i + 1]
                pairs.append(BenchmarkPair(
                    pair_id=f"poj_{problem_id}_{a['index']}_{b['index']}",
                    code_a=a["code"],
                    code_b=b["code"],
                    language_a="c",
                    language_b="c",
                    is_clone=True,
                    clone_type="type4",
                    source_dataset="poj104",
                    metadata={"problem_id": problem_id},
                ))

        # Negative pairs: different problems (~25% of total)
        problem_ids = [pid for pid, sols in problems.items() if len(sols) >= 1]
        neg_target = max(len(pairs) // 3, 20)
        neg_count = 0
        while neg_count < neg_target and len(problem_ids) >= 2:
            if max_pairs and len(pairs) >= max_pairs:
                break
            p1, p2 = rng.sample(problem_ids, 2)
            sol1 = rng.choice(problems[p1])
            sol2 = rng.choice(problems[p2])
            pairs.append(BenchmarkPair(
                pair_id=f"poj_neg_{p1}_{p2}_{neg_count}",
                code_a=sol1["code"],
                code_b=sol2["code"],
                language_a="c",
                language_b="c",
                is_clone=False,
                clone_type="negative",
                source_dataset="poj104",
                metadata={"problem_id_a": p1, "problem_id_b": p2},
            ))
            neg_count += 1

        return pairs
