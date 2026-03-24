#!/usr/bin/env python3
"""Train the duplicate classifier on GPTCloneBench + synthetic pairs.

Usage:
    python scripts/train_classifier.py
    python scripts/train_classifier.py --max-pairs 1000   # faster, smaller dataset
    python scripts/train_classifier.py --synthetic-only    # skip GPTCloneBench

Outputs:
    echo_guard/data/classifier_weights.json

Requires: scikit-learn (pip install -e ".[train]")
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

import numpy as np
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).parent.parent))

from echo_guard.ast_distance import normalized_ast_similarity
from echo_guard.classifier import _split_name_tokens, FEATURE_NAMES
from echo_guard.languages import ExtractedFunction, extract_functions_universal


# ── Language detection for GPTCloneBench files ─────────────────────────

LANG_MAP = {
    "py": ("python", ".py"),
    "java": ("java", ".java"),
    "c": ("c", ".c"),
    "cs": ("c", ".c"),  # C# not supported, treat as C
}


# ── Feature extraction ─────────────────────────────────────────────────

def _extract_features(func_a: ExtractedFunction, func_b: ExtractedFunction,
                       match_type: str, score: float) -> np.ndarray:
    """Extract features for a pair using the classifier's 14-feature extraction."""
    from echo_guard.classifier import extract_features
    return extract_features(func_a, func_b, match_type, score, score)


def _make_func(name: str, filepath: str, source: str, language: str = "python") -> ExtractedFunction:
    """Create an ExtractedFunction with AST tokens."""
    funcs = extract_functions_universal(filepath, source, language)
    if funcs:
        f = funcs[0]
        if f.name != name:
            f = ExtractedFunction(
                name=name, filepath=filepath, language=f.language,
                lineno=f.lineno, end_lineno=f.end_lineno, source=f.source,
                ast_hash=f.ast_hash, ast_tokens=f.ast_tokens,
                param_count=f.param_count, has_return=f.has_return,
                visibility=f.visibility,
            )
        return f
    lines = source.strip().splitlines()
    return ExtractedFunction(
        name=name, filepath=filepath, language=language,
        lineno=1, end_lineno=len(lines), source=source,
    )


# ── GPTCloneBench loading ──────────────────────────────────────────────

def _parse_pair_file(filepath: Path, language: str, ext: str):
    """Parse a GPTCloneBench pair file into two ExtractedFunctions."""
    import re
    try:
        content = filepath.read_text(encoding="utf-8")
    except Exception:
        return None

    parts = re.split(r"\n\s*\n\s*\n", content, maxsplit=1)
    if len(parts) < 2:
        return None

    funcs_a = extract_functions_universal(f"{filepath.stem}_a{ext}", parts[0].strip(), language)
    funcs_b = extract_functions_universal(f"{filepath.stem}_b{ext}", parts[1].strip(), language)

    if funcs_a and funcs_b:
        return funcs_a[0], funcs_b[0]
    return None


def load_gptclonebench(max_per_category: int = 3000):
    """Load balanced GPTCloneBench pairs across all languages."""
    bench_dir = Path(__file__).parent.parent / "benchmarks" / "data" / "gptclonebench" / "GPTCloneBench"
    if not bench_dir.exists():
        print(f"  GPTCloneBench not found at {bench_dir}")
        print(f"  Download: cd benchmarks/data/gptclonebench && git clone https://github.com/srlabUsask/GPTCloneBench.git")
        return [], []

    features_list = []
    labels = []
    stats = {"true": 0, "false": 0, "errors": 0}

    random.seed(42)

    # ── Collect file paths ──
    true_dir = bench_dir / "standalone" / "true_semantic_clones"
    false_dir = bench_dir / "standalone" / "false_semantic_clones"

    true_files = []
    false_files = []
    for lang_code, (language, ext) in LANG_MAP.items():
        for f in (true_dir / lang_code).rglob(f"*{ext}") if (true_dir / lang_code).exists() else []:
            true_files.append((f, language, ext))
        for f in (false_dir / lang_code).rglob(f"*{ext}") if (false_dir / lang_code).exists() else []:
            false_files.append((f, language, ext))

    random.shuffle(true_files)
    random.shuffle(false_files)
    true_files = true_files[:max_per_category]
    false_files = false_files[:max_per_category]

    print(f"\n  Found {len(true_files)} true clone files, {len(false_files)} false clone files")
    print(f"  Loading {len(true_files)} true + {len(false_files)} false (capped at {max_per_category} each)")

    # ── Parse true clones ──
    print()
    for pair_file, language, ext in tqdm(true_files, desc="  Parsing true clones", unit="file", ncols=80):
        result = _parse_pair_file(pair_file, language, ext)
        if result:
            func_a, func_b = result
            sim_score = 0.90 + random.random() * 0.09
            features_list.append(_extract_features(func_a, func_b, "embedding_semantic", sim_score))
            labels.append(1)
            stats["true"] += 1
        else:
            stats["errors"] += 1

    # ── Parse false clones ──
    for pair_file, language, ext in tqdm(false_files, desc="  Parsing false clones", unit="file", ncols=80):
        result = _parse_pair_file(pair_file, language, ext)
        if result:
            func_a, func_b = result
            sim_score = 0.80 + random.random() * 0.15
            features_list.append(_extract_features(func_a, func_b, "embedding_semantic", sim_score))
            labels.append(0)
            stats["false"] += 1
        else:
            stats["errors"] += 1

    print(f"\n  Results: {stats['true']} true, {stats['false']} false, {stats['errors']} parse errors")
    return features_list, labels


# ── Synthetic pairs ────────────────────────────────────────────────────

def generate_synthetic_pairs():
    """Generate pairs for patterns GPTCloneBench doesn't cover."""
    features_list = []
    labels = []

    positives = [
        ("fetchJson", "async function fetchJson(url) { const r = await fetch(url); if (!r.ok) throw new Error(); return r.json(); }",
         "fetchJson", "async function fetchJson(u) { const res = await fetch(u); if (!res.ok) throw new Error(); return res.json(); }",
         "a.ts", "b.ts", "typescript", "exact_structure", 1.0),
        ("formatDuration", "function formatDuration(ms) { const s = Math.floor(ms/1000); const m = Math.floor(s/60); return `${m}m ${s%60}s`; }",
         "formatDuration", "function formatDuration(millis) { const sec = Math.floor(millis/1000); const min = Math.floor(sec/60); return `${min}m ${sec%60}s`; }",
         "a.ts", "b.ts", "typescript", "exact_structure", 1.0),
        ("init_pool", "async def init_pool():\n    return await asyncpg.create_pool(os.getenv('DATABASE_URL'))",
         "init_pool", "async def init_pool():\n    return await asyncpg.create_pool(os.getenv('DB_URL'))",
         "svc_a/db.py", "svc_b/db.py", "python", "exact_structure", 1.0),
        ("timeAgo", "function timeAgo(date) { const s = (Date.now()-date)/1000; if (s<60) return 'just now'; if (s<3600) return Math.floor(s/60)+'m ago'; return Math.floor(s/3600)+'h ago'; }",
         "timeAgo", "function timeAgo(d) { const sec = (Date.now()-d)/1000; if (sec<60) return 'just now'; if (sec<3600) return Math.floor(sec/60)+'m ago'; return Math.floor(sec/3600)+'h ago'; }",
         "a.tsx", "b.tsx", "typescript", "embedding_semantic", 0.98),
        ("validate_email", "def validate_email(email):\n    pattern = r'^[a-zA-Z0-9+_.-]+@[a-zA-Z0-9.-]+$'\n    return bool(re.match(pattern, email))",
         "validate_email", "def validate_email(addr):\n    regex = r'^[a-zA-Z0-9+_.-]+@[a-zA-Z0-9.-]+$'\n    return bool(re.match(regex, addr))",
         "utils_a.py", "utils_b.py", "python", "exact_structure", 1.0),
        ("compute_hash", "def compute_hash(data):\n    if not data:\n        raise ValueError('required')\n    result = hashlib.sha256(data)\n    formatted = result.hexdigest()\n    prefix = formatted[:8]\n    suffix = formatted[8:]\n    combined = f'{prefix}-{suffix}'\n    validated = _validate(combined)\n    logger.info('hash: %s', validated)\n    return validated",
         "compute_digest", "def compute_digest(val):\n    if not val:\n        raise ValueError('required')\n    output = hashlib.sha256(val)\n    formatted = output.hexdigest()\n    prefix = formatted[:8]\n    suffix = formatted[8:]\n    combined = f'{prefix}-{suffix}'\n    validated = _validate(combined)\n    logger.info('digest: %s', validated)\n    return validated",
         "utils.py", "utils.py", "python", "exact_structure", 1.0),
        ("parseLiteralValue", "def parseLiteralValue(val):\n    try:\n        return json.loads(val)\n    except (json.JSONDecodeError, TypeError):\n        return val",
         "parseLiteralValue", "def parseLiteralValue(value):\n    try:\n        return json.loads(value)\n    except (json.JSONDecodeError, TypeError):\n        return value",
         "builder.py", "builder.py", "python", "exact_structure", 1.0),
    ]

    negatives = [
        ("reset_session", "async def reset_session(sid):\n    pool = get_pool()\n    async with pool.acquire() as c:\n        await c.execute('DELETE FROM msgs', sid)\n        await c.execute('UPDATE sessions SET s=empty', sid)",
         "delete_session", "async def delete_session(sid):\n    pool = get_pool()\n    async with pool.acquire() as c:\n        await c.execute('DELETE FROM msgs', sid)\n        await c.execute('DELETE FROM sessions', sid)",
         "lab.py", "lab.py", "python", "exact_structure", 1.0),
        ("create_user", "async def create_user(data):\n    pool = get_pool()\n    async with pool.acquire() as c:\n        await c.execute('INSERT INTO users', data['name'], data['email'])\n        return {'created': True}",
         "update_user", "async def update_user(uid, data):\n    pool = get_pool()\n    async with pool.acquire() as c:\n        await c.execute('UPDATE users SET name=$2', uid, data['name'])\n        return {'updated': True}",
         "users.py", "users.py", "python", "embedding_semantic", 0.96),
        ("on_error", "def on_error(self, ctx):\n    self.logger.error('Error', exc_info=ctx.error)\n    self._notify(ctx)",
         "on_tool_recovery", "def on_tool_recovery(self, ctx):\n    self.logger.info('Recovery', exc_info=ctx.error)\n    self._notify(ctx)",
         "observers.py", "observers.py", "python", "exact_structure", 1.0),
        ("log_allowed", "async def log_allowed(tool, action, user):\n    pool = get_pool()\n    async with pool.acquire() as c:\n        await c.execute('INSERT INTO audit', tool, action, user)\n        logger.info('Allowed %s %s', tool, action)",
         "insert_tool_audit", "async def insert_tool_audit(tool_id, result, ts):\n    pool = get_pool()\n    async with pool.acquire() as c:\n        await c.execute('INSERT INTO tool_audits', tool_id, result, ts)",
         "logger.py", "db.py", "python", "embedding_semantic", 0.95),
        ("save_message", "async def save_message(session_id, role, content):\n    pool = get_pool()\n    async with pool.acquire() as c:\n        await c.execute('INSERT INTO messages', session_id, role, content)\n        return {'saved': True}",
         "record_turn", "async def record_turn(run_id, step, output):\n    pool = get_pool()\n    async with pool.acquire() as c:\n        await c.execute('INSERT INTO turns', run_id, step, output)\n        return {'recorded': True}",
         "chat.py", "runs.py", "python", "embedding_semantic", 0.95),
        ("get_automation_by_id", "async def get_automation_by_id(aid):\n    pool = get_pool()\n    async with pool.acquire() as c:\n        row = await c.fetchrow('SELECT * FROM automations WHERE id=$1', aid)\n        return dict(row) if row else None",
         "get_trigger_by_id", "async def get_trigger_by_id(tid):\n    pool = get_pool()\n    async with pool.acquire() as c:\n        row = await c.fetchrow('SELECT * FROM triggers WHERE id=$1', tid)\n        return dict(row) if row else None",
         "automations.py", "triggers.py", "python", "exact_structure", 1.0),
        ("parse_config", "def parse_config(path):\n    with open(path) as f:\n        return yaml.safe_load(f)",
         "send_email", "async def send_email(to, subject, body):\n    msg = MIMEText(body)\n    msg['Subject'] = subject\n    smtp.send(msg)",
         "config.py", "email.py", "python", "embedding_semantic", 0.85),
        ("hash_password", "def hash_password(pw, salt=None):\n    if salt is None:\n        salt = os.urandom(32)\n    return hashlib.pbkdf2_hmac('sha256', pw.encode(), salt, 100000)",
         "calculate_tax", "def calculate_tax(amount, rate):\n    tax = amount * rate\n    return round(tax, 2)",
         "auth.py", "billing.py", "python", "embedding_semantic", 0.82),
    ]

    print(f"  Generating {len(positives)} positive + {len(negatives)} negative synthetic pairs")
    for name_a, src_a, name_b, src_b, file_a, file_b, lang, mtype, score in positives:
        fa = _make_func(name_a, file_a, src_a, lang)
        fb = _make_func(name_b, file_b, src_b, lang)
        features_list.append(_extract_features(fa, fb, mtype, score))
        labels.append(1)

    for name_a, src_a, name_b, src_b, file_a, file_b, lang, mtype, score in negatives:
        fa = _make_func(name_a, file_a, src_a, lang)
        fb = _make_func(name_b, file_b, src_b, lang)
        features_list.append(_extract_features(fa, fb, mtype, score))
        labels.append(0)

    return features_list, labels


# ── Custom training pairs ──────────────────────────────────────────────

def load_custom_pairs(training_dir: Path) -> tuple[list[np.ndarray], list[int]]:
    """Load labeled pairs from the training directory structure.

    Loads from:
        echo_guard/data/training/positive/*.jsonl  (label=1)
        echo_guard/data/training/negative/*.jsonl  (label=0)

    Also loads the legacy flat file if it exists:
        echo_guard/data/training_pairs.jsonl
    """
    features_list: list[np.ndarray] = []
    labels: list[int] = []
    errors = 0
    category_counts: dict[str, int] = {}

    # Collect all JSONL files from the directory structure
    all_lines: list[str] = []
    sources: list[str] = []

    if training_dir.exists():
        for jsonl_file in sorted(training_dir.rglob("*.jsonl")):
            rel = jsonl_file.relative_to(training_dir)
            with open(jsonl_file) as f:
                file_lines = [l.strip() for l in f if l.strip()]
            all_lines.extend(file_lines)
            sources.append(f"{rel} ({len(file_lines)})")

    # Also check legacy flat file
    legacy_path = training_dir.parent / "training_pairs.jsonl"
    if legacy_path.exists():
        with open(legacy_path) as f:
            legacy_lines = [l.strip() for l in f if l.strip()]
        if legacy_lines:
            all_lines.extend(legacy_lines)
            sources.append(f"training_pairs.jsonl ({len(legacy_lines)})")

    if not all_lines:
        print(f"\n[1/5] No training data found in {training_dir}")
        return [], []

    print(f"\n[1/5] Loading {len(all_lines)} custom training pairs...")
    for src in sources:
        print(f"    {src}")
    print()

    for pair in tqdm(all_lines, desc="  Parsing pairs", unit="pair", ncols=80):
        try:
            obj = json.loads(pair)
        except json.JSONDecodeError:
            errors += 1
            continue

        lang = obj.get("language", "python")
        label = obj["label"]
        cat = obj.get("category", "unknown")
        category_counts[cat] = category_counts.get(cat, 0) + 1

        func_a = _make_func(obj["func_a_name"], obj["func_a_file"], obj["func_a_source"], lang)
        func_b = _make_func(obj["func_b_name"], obj.get("func_b_file", obj["func_a_file"]), obj["func_b_source"], lang)

        if label == 1:
            match_type = "exact_structure" if func_a.ast_hash == func_b.ast_hash else "embedding_semantic"
            score = 1.0 if match_type == "exact_structure" else (0.90 + random.random() * 0.09)
        else:
            match_type = "embedding_semantic"
            score = 0.80 + random.random() * 0.15

        features_list.append(_extract_features(func_a, func_b, match_type, score))
        labels.append(label)

    pos = sum(labels)
    neg = len(labels) - pos
    print(f"\n  Loaded: {pos} positive, {neg} negative, {errors} errors")
    print(f"  Categories ({len(category_counts)}):")
    for cat, count in sorted(category_counts.items(), key=lambda x: -x[1])[:10]:
        print(f"    {cat:35s} {count:>4d}")
    if len(category_counts) > 10:
        print(f"    ... and {len(category_counts) - 10} more")

    return features_list, labels


# ── Training ───────────────────────────────────────────────────────────

def train_and_export(max_pairs: int = 3000, synthetic_only: bool = False) -> None:
    """Train the classifier and export weights."""
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.model_selection import cross_val_score
        from sklearn.metrics import classification_report, confusion_matrix
        from sklearn.preprocessing import StandardScaler
    except ImportError:
        print("Error: scikit-learn required — pip install -e '.[train]'")
        sys.exit(1)

    start_time = time.time()

    # ── Load data ──
    print("=" * 60)
    print("ECHO GUARD — CLASSIFIER TRAINING")
    print("=" * 60)

    all_features = []
    all_labels = []

    # ── Custom training pairs (primary dataset) ──
    training_dir = Path(__file__).parent.parent / "echo_guard" / "data" / "training"
    custom_features, custom_labels = load_custom_pairs(training_dir)
    all_features.extend(custom_features)
    all_labels.extend(custom_labels)

    if not synthetic_only:
        print("\n[2/5] Loading GPTCloneBench...")
        bench_features, bench_labels = load_gptclonebench(max_per_category=max_pairs)
        all_features.extend(bench_features)
        all_labels.extend(bench_labels)

    print("\n[3/5] Generating synthetic pairs...")
    synth_features, synth_labels = generate_synthetic_pairs()
    all_features.extend(synth_features)
    all_labels.extend(synth_labels)

    X = np.array(all_features, dtype=np.float32)
    y = np.array(all_labels, dtype=np.int32)

    n_pos = int(sum(y))
    n_neg = len(y) - n_pos

    print(f"\n{'─' * 60}")
    print(f"  Dataset: {len(y)} total pairs")
    print(f"  Positive (clones):     {n_pos:>5d}  ({n_pos/len(y)*100:.0f}%)")
    print(f"  Negative (not clones): {n_neg:>5d}  ({n_neg/len(y)*100:.0f}%)")
    print(f"{'─' * 60}")

    # ── Train ──
    print("\n[4/5] Training classifier...")

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    model = LogisticRegression(
        C=1.0, max_iter=1000, random_state=42,
        class_weight="balanced",
    )

    # Cross-validation
    n_folds = min(5, min(n_pos, n_neg))
    if n_folds >= 2:
        print(f"\n  Running {n_folds}-fold cross-validation...")
        scores = cross_val_score(model, X_scaled, y, cv=n_folds, scoring="accuracy")
        print(f"  CV Accuracy: {scores.mean():.3f} (+/- {scores.std():.3f})")
        print(f"  Per fold:    {', '.join(f'{s:.3f}' for s in scores)}")
    else:
        scores = np.array([0.0])
        print("  (Not enough data for cross-validation)")

    # Final training on full dataset
    model.fit(X_scaled, y)
    train_acc = model.score(X_scaled, y)
    y_pred = model.predict(X_scaled)

    print(f"\n  Training Accuracy: {train_acc:.3f}")
    print(f"\n  Confusion Matrix:")
    cm = confusion_matrix(y, y_pred)
    print(f"                  Predicted")
    print(f"                  NOT    DUP")
    print(f"    Actual NOT  [{cm[0][0]:>5d}  {cm[0][1]:>5d}]")
    print(f"    Actual DUP  [{cm[1][0]:>5d}  {cm[1][1]:>5d}]")

    print(f"\n  Classification Report:")
    report = classification_report(y, y_pred, target_names=["NOT duplicate", "IS duplicate"], digits=3)
    for line in report.strip().splitlines():
        print(f"    {line}")

    # ── Feature weights ──
    print(f"\n  Feature Weights (standardized):")
    print(f"  {'Feature':<25s}  {'Weight':>8s}  {'Direction'}")
    print(f"  {'─' * 50}")
    for name, weight in sorted(zip(FEATURE_NAMES, model.coef_[0]), key=lambda x: abs(x[1]), reverse=True):
        direction = "→ more likely dup" if weight > 0 else "→ less likely dup"
        print(f"  {name:<25s}  {weight:>+8.4f}  {direction}")
    print(f"  {'intercept':<25s}  {model.intercept_[0]:>+8.4f}")

    # ── Export ──
    print(f"\n[5/5] Exporting weights...")

    coef_unstd = model.coef_[0] / scaler.scale_
    intercept_unstd = model.intercept_[0] - np.sum(model.coef_[0] * scaler.mean_ / scaler.scale_)

    weights = {
        "coef": coef_unstd.tolist(),
        "intercept": float(intercept_unstd),
        "feature_names": FEATURE_NAMES,
        "version": "trained-v2",
        "training_samples": len(y),
        "positive_samples": n_pos,
        "negative_samples": n_neg,
        "cv_accuracy": float(scores.mean()),
        "training_accuracy": float(train_acc),
    }

    output_path = Path(__file__).parent.parent / "echo_guard" / "data" / "classifier_weights.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(weights, f, indent=2)

    elapsed = time.time() - start_time

    # ── Verification on synthetic pairs ──
    print(f"\n  Verification (synthetic pairs):")
    correct = 0
    total = len(synth_features)
    for feat, label in zip(synth_features, synth_labels):
        prob = 1.0 / (1.0 + np.exp(-(feat @ coef_unstd + intercept_unstd)))
        predicted = prob > 0.5
        status = "✓" if predicted == bool(label) else "✗"
        tag = "DUP" if label else "NOT"
        print(f"    {status} [{tag}] prob={prob:.3f}")
        if predicted == bool(label):
            correct += 1
    print(f"    Synthetic accuracy: {correct}/{total} ({correct/total*100:.0f}%)")

    # ── Summary ──
    print(f"\n{'=' * 60}")
    print(f"  TRAINING COMPLETE")
    print(f"  Time:     {elapsed:.1f}s")
    print(f"  Samples:  {len(y)} ({n_pos} pos / {n_neg} neg)")
    print(f"  CV Acc:   {scores.mean():.3f}")
    print(f"  Output:   {output_path}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train Echo Guard duplicate classifier")
    parser.add_argument("--max-pairs", type=int, default=3000,
                        help="Max pairs per category from GPTCloneBench (default: 3000)")
    parser.add_argument("--synthetic-only", action="store_true",
                        help="Skip GPTCloneBench, use only synthetic pairs")
    args = parser.parse_args()

    train_and_export(max_pairs=args.max_pairs, synthetic_only=args.synthetic_only)
