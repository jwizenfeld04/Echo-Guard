# Benchmark Dataset Setup

This guide covers downloading and preparing the benchmark datasets for Echo Guard evaluation. The datasets must be downloaded before running benchmarks — the runner will error with setup instructions if they're missing.

## Requirements

- **Python 3.10+** with `pip install -e ".[languages,dev]"`
- **Java 11+** (BigCloneBench only — for H2 database export)
- **git** (GPTCloneBench, POJ-104 — for cloning repos)

## Quick Start

```bash
# Install Echo Guard with language support
pip install -e ".[languages,dev]"

# Setup one dataset at a time:
./benchmarks/setup_datasets.sh bigclonebench    # Requires manual download first
./benchmarks/setup_datasets.sh gptclonebench    # Automatic (clones from GitHub)
./benchmarks/setup_datasets.sh poj104           # Automatic (needs gdown or manual download)

# Or all at once:
./benchmarks/setup_datasets.sh all

# Run benchmarks:
python -m benchmarks.runner                            # All datasets
python -m benchmarks.runner --dataset bigclonebench -v  # Single dataset, verbose
python -m benchmarks.runner --sweep                     # Threshold sweep
python -m benchmarks.runner --json results.json         # Export results
```

---

## BigCloneBench

The largest clone detection benchmark — 8.5M labeled Java clone pairs across Type-1 through Type-4, sourced from 25,000+ SourceForge projects.

### Step 1: Download (manual — hosted on OneDrive)

Download these two files from the [BigCloneEval](https://github.com/jeffsvajlenko/BigCloneEval) project:

1. **BigCloneBench database** (~650 MB tar.gz → 5.6 GB H2 database)
   https://1drv.ms/u/s!AhXbM6MKt_yLj_NwwVacvUzmi6uorA?e=eMu0P4

2. **IJaDataset** (~109 MB tar.gz → Java source files)
   https://1drv.ms/u/s!AhXbM6MKt_yLj_N15CewgjM7Y8NLKA?e=cScoRJ

Place both `.tar.gz` files in `benchmarks/data/bigclonebench/`:

```bash
mkdir -p benchmarks/data/bigclonebench
# Move/copy the downloaded files here:
# benchmarks/data/bigclonebench/BigCloneBench_BCEvalVersion.tar.gz
# benchmarks/data/bigclonebench/IJaDataset_BCEvalVersion.tar.gz
```

### Step 2: Extract and Export

```bash
./benchmarks/setup_datasets.sh bigclonebench
```

This script will:
1. Extract both tar.gz files
2. Download the H2 database driver (2.3 MB)
3. Export a stratified sample of clone pairs to CSV (200 per clone type)
4. Export 200 false positive pairs as negatives
5. Export the 22M function index to CSV
6. Clean up the tar files and H2 jar

### Step 3: Verify

```bash
# Quick check — should show ~1200 pairs
python -m benchmarks.runner --dataset bigclonebench --max-pairs 20

# Full run (~7 minutes, ~6.2 GB RAM)
python -m benchmarks.runner --dataset bigclonebench --verbose
```

### Final Directory Structure

```
benchmarks/data/bigclonebench/
├── bcb.h2.db              # H2 database (5.6 GB, kept for re-exports)
├── bcb.trace.db           # H2 trace file
├── clonepairs.csv         # 1,000 stratified clone pairs
├── false_positives.csv    # 200 non-clone pairs
├── functions.csv          # 22M function index (~1.2 GB)
└── bcb_reduced/           # IJaDataset Java source files
    ├── 2/                 # Functionality ID directories
    │   ├── selected/      # Source variant directories
    │   ├── default/
    │   └── sample/
    ├── 3/
    └── ...                # 43 functionality directories total
```

### Adjusting Sample Size

To re-export with a different sample size, edit and re-run the H2 export queries in the setup script. Each `LIMIT` value controls how many pairs per clone type category.

---

## GPTCloneBench

AI-generated clone pairs from GPT-3/GPT-4, built from SemanticCloneBench. Focuses on Type-3 and Type-4 clones — the kinds of duplicates LLMs typically generate.

### Setup

```bash
./benchmarks/setup_datasets.sh gptclonebench
```

The script automatically clones the [GPTCloneBench repo](https://github.com/srlabUsask/GPTCloneBench), extracts the standalone semantic clone pair files, and cleans up.

If the repo doesn't contain the zip directly, download it from [Zenodo](https://doi.org/10.5281/zenodo.10198952) and place `GPTCloneBench_semantic_standalone_clones.zip` in `benchmarks/data/gptclonebench/` before running the script.

### Verify

```bash
python -m benchmarks.runner --dataset gptclonebench --verbose
```

### Directory Structure

```
benchmarks/data/gptclonebench/
└── GPTCloneBench/
    └── standalone/
        ├── true_semantic_clones/
        │   ├── java/prompt_{1,2}/{T4,MT3}/Clone_*.java
        │   ├── py/prompt_{1,2}/{T4,MT3}/Clone_*.py
        │   ├── c/prompt_{1,2}/{T4,MT3}/Clone_*.c
        │   └── cs/prompt_{1,2}/{T4,MT3}/Clone_*.cs
        └── false_semantic_clones/
            ├── java/Gpt_false_pair_*.java
            ├── py/Gpt_false_pair_*.py
            └── ...
```

Each clone file contains two functions separated by blank lines: the original on top, the GPT-generated version below.

---

## POJ-104

52,000 C solutions to 104 programming problems from an online judge. Solutions to the same problem are semantic clones (Type-4). This is the hardest benchmark — completely different implementations of the same algorithm.

### Setup

```bash
# Option 1: Automatic (requires gdown)
pip install gdown
./benchmarks/setup_datasets.sh poj104

# Option 2: Manual download
# Download programs.tar.gz from:
#   https://drive.google.com/file/d/0B2i-vWnOu7MxVlJwQXN6eVNONUU/view
# Place in benchmarks/data/poj104/
./benchmarks/setup_datasets.sh poj104
```

### Verify

```bash
python -m benchmarks.runner --dataset poj104 --verbose
```

### Directory Structure

```
benchmarks/data/poj104/
└── ProgramData/           # Raw source files
    ├── 1/                 # Problem ID
    │   ├── <solution1>    # Student solution (C code)
    │   ├── <solution2>
    │   └── ... (500 solutions per problem)
    ├── 2/
    └── ... (104 problems)
```

Alternatively, preprocessed JSONL files (`test.jsonl`, `train.jsonl`, `valid.jsonl`) are also supported. Each line: `{"label": "problem_id", "index": "id", "code": "source"}`.

---

## Resource Estimates

| Dataset | Disk (download) | Disk (extracted) | RAM (runtime) | Time |
|---------|-----------------|------------------|---------------|------|
| BigCloneBench | ~760 MB | ~7 GB | ~6.2 GB | ~7 min |
| GPTCloneBench | ~30 MB | ~50 MB | ~500 MB | ~2 min |
| POJ-104 | ~25 MB | ~200 MB | ~2 GB | ~5 min |

### BigCloneBench RAM Breakdown

| Stage | Memory | What's loaded |
|-------|--------|---------------|
| Load `functions.csv` | ~4.6 GB | 22M function entries (ID → filename, line range) for resolving source |
| Load 1,200 pairs | +12 MB | Source code strings from IJaDataset Java files |
| SimilarityEngine index | +1.6 GB | 2,400 extracted functions, MinHash signatures, LSH buckets |
| TF-IDF + matching | +28 MB | Sparse TF-IDF matrix, match results |
| **Total peak** | **~6.2 GB** | |

The dominant cost is loading the 22M-row function index into a Python dict for source file lookups. The actual similarity engine uses ~1.6 GB for 2,400 benchmark functions.

## Troubleshooting

**"0 pairs evaluated"**: The tree-sitter language grammars aren't installed. Run:
```bash
pip install -e ".[languages]"
```

**Out of memory**: The 22M function CSV takes ~4.6 GB to load into a Python dict. Ensure you have at least 6.2 GB free RAM. On constrained machines, use `--max-pairs 100` for a smaller run.

**H2 export fails**: Ensure you have Java 11+ installed (`java -version`). The H2 1.4.200 jar is compatible with Java 11-21.

**"Wrong user name or password"**: The BigCloneBench H2 database uses username `sa` with an empty password. The setup script handles this automatically.
