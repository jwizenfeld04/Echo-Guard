"""GPTCloneBench benchmark adapter.

GPTCloneBench (Alam et al., ICSME 2023) is a benchmark of AI-generated clone pairs,
built by having GPT-3/GPT-4 generate semantically equivalent code from functions in
SemanticCloneBench.

Dataset structure (from GPTCloneBench_semantic_clone_pairs.zip):
  GPTCloneBench/standalone/
  ├── true_semantic_clones/{java,py,c,cs}/prompt_{1,2}/{T4,MT3}/Clone_*.ext
  └── false_semantic_clones/{java,py,c,cs}/Gpt_false_pair_*.ext

Each clone file contains two functions separated by blank lines:
the original on top, the GPT-generated version below.

Reference: https://github.com/srlabUsask/GPTCloneBench
"""

from __future__ import annotations

import re
from pathlib import Path

from benchmarks.base import BenchmarkAdapter, BenchmarkPair


# Language directory name → echo-guard language name
_LANG_MAP = {"java": "java", "py": "python", "c": "c", "cs": "c"}


def _parse_pair_file(filepath: Path) -> tuple[str, str] | None:
    """Parse a GPTCloneBench clone pair file.

    Each file has two functions separated by 2+ consecutive blank lines.
    Returns (code_a, code_b) or None if parsing fails.
    """
    text = filepath.read_text(errors="replace").strip()
    if not text:
        return None

    # Split on 2+ consecutive blank lines
    parts = re.split(r"\n\s*\n\s*\n", text, maxsplit=1)
    if len(parts) < 2:
        parts = re.split(r"\n\s*\n", text, maxsplit=1)
    if len(parts) < 2:
        return None

    code_a = parts[0].strip()
    code_b = parts[1].strip()
    if len(code_a) < 10 or len(code_b) < 10:
        return None
    return code_a, code_b


class GPTCloneBenchAdapter(BenchmarkAdapter):
    """Adapter for the GPTCloneBench dataset.

    Requires the real GPTCloneBench data extracted from the semantic clone pairs zip.

    See benchmarks/SETUP.md for setup instructions.
    """

    @property
    def name(self) -> str:
        return "GPTCloneBench"

    @property
    def dataset_id(self) -> str:
        return "gptclonebench"

    def is_available(self) -> bool:
        gcb_dir = self.data_dir / "gptclonebench"
        standalone = gcb_dir / "GPTCloneBench" / "standalone"
        return (standalone / "true_semantic_clones").exists()

    def download(self, force: bool = False) -> None:
        print("  GPTCloneBench requires manual setup:")
        print("  1. Download from https://github.com/srlabUsask/GPTCloneBench")
        print("  2. Run: ./benchmarks/setup_datasets.sh gptclonebench")
        print("  3. See benchmarks/SETUP.md for full instructions")

    def load_pairs(self, max_pairs: int | None = None) -> list[BenchmarkPair]:
        gcb_dir = self.data_dir / "gptclonebench"
        standalone = gcb_dir / "GPTCloneBench" / "standalone"

        if not (standalone / "true_semantic_clones").exists():
            raise FileNotFoundError(
                "GPTCloneBench dataset not found.\n"
                "  1. Run: ./benchmarks/setup_datasets.sh gptclonebench\n"
                "  Or download manually from https://github.com/srlabUsask/GPTCloneBench\n"
                "  and extract into benchmarks/data/gptclonebench/\n"
                "  See benchmarks/SETUP.md for full instructions."
            )

        return self._load_from_dataset(standalone, max_pairs)

    # ── Real dataset loading ───────────────────────────────────────────

    def _load_from_dataset(
        self,
        standalone_dir: Path,
        max_pairs: int | None,
    ) -> list[BenchmarkPair]:
        """Load pairs from the real GPTCloneBench dataset.

        Structure:
          standalone/true_semantic_clones/{java,py,c,cs}/prompt_{1,2}/{T4,MT3}/Clone_*.ext
          standalone/false_semantic_clones/{java,py,c,cs}/Gpt_false_pair_*.ext
        """
        pairs: list[BenchmarkPair] = []
        true_dir = standalone_dir / "true_semantic_clones"
        false_dir = standalone_dir / "false_semantic_clones"

        # Budget: split evenly among type3, type4, negatives
        if max_pairs:
            per_type = max_pairs // 3
        else:
            per_type = 200  # Default sample size per type

        # Load Type-3 (MT3) pairs
        t3_count = 0
        for lang_name, eg_lang in _LANG_MAP.items():
            if eg_lang not in ("python", "java"):
                continue
            lang_dir = true_dir / lang_name
            if not lang_dir.exists():
                continue
            for mt3_dir in sorted(lang_dir.glob("*/MT3")):
                for f in sorted(mt3_dir.iterdir()):
                    if t3_count >= per_type:
                        break
                    if not f.is_file():
                        continue
                    result = _parse_pair_file(f)
                    if result is None:
                        continue
                    code_a, code_b = result
                    pairs.append(BenchmarkPair(
                        pair_id=f"gcb_t3_{lang_name}_{f.stem}",
                        code_a=code_a,
                        code_b=code_b,
                        language_a=eg_lang,
                        language_b=eg_lang,
                        is_clone=True,
                        clone_type="type3",
                        source_dataset="gptclonebench",
                        metadata={"source_file": f.name, "language_dir": lang_name},
                    ))
                    t3_count += 1

        # Load Type-4 pairs
        t4_count = 0
        for lang_name, eg_lang in _LANG_MAP.items():
            if eg_lang not in ("python", "java"):
                continue
            lang_dir = true_dir / lang_name
            if not lang_dir.exists():
                continue
            for t4_dir in sorted(lang_dir.glob("*/T4")):
                for f in sorted(t4_dir.iterdir()):
                    if t4_count >= per_type:
                        break
                    if not f.is_file():
                        continue
                    result = _parse_pair_file(f)
                    if result is None:
                        continue
                    code_a, code_b = result
                    pairs.append(BenchmarkPair(
                        pair_id=f"gcb_t4_{lang_name}_{f.stem}",
                        code_a=code_a,
                        code_b=code_b,
                        language_a=eg_lang,
                        language_b=eg_lang,
                        is_clone=True,
                        clone_type="type4",
                        source_dataset="gptclonebench",
                        metadata={"source_file": f.name, "language_dir": lang_name},
                    ))
                    t4_count += 1

        # Load false semantic clones (negatives)
        neg_count = 0
        for lang_name, eg_lang in _LANG_MAP.items():
            if eg_lang not in ("python", "java"):
                continue
            neg_dir = false_dir / lang_name
            if not neg_dir.exists():
                continue
            for f in sorted(neg_dir.iterdir()):
                if neg_count >= per_type:
                    break
                if not f.is_file():
                    continue
                result = _parse_pair_file(f)
                if result is None:
                    continue
                code_a, code_b = result
                pairs.append(BenchmarkPair(
                    pair_id=f"gcb_neg_{lang_name}_{f.stem}",
                    code_a=code_a,
                    code_b=code_b,
                    language_a=eg_lang,
                    language_b=eg_lang,
                    is_clone=False,
                    clone_type="negative",
                    source_dataset="gptclonebench",
                    metadata={"source_file": f.name, "language_dir": lang_name},
                ))
                neg_count += 1

        return pairs
