"""Configuration file support for Echo Guard (echo-guard.yml)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


CONFIG_FILENAMES = ["echo-guard.yml", "echo-guard.yaml"]

DEFAULT_EXCLUDE_DIRS = {
    ".git", ".echo-guard", "__pycache__", ".venv", "venv",
    "node_modules", ".tox", ".mypy_cache", ".pytest_cache",
    "dist", "build", ".eggs", "target", "vendor",
    ".next", ".nuxt", "coverage", ".cache",
}

DEFAULT_EXCLUDE_PATTERNS = {
    "*.min.js", "*.bundle.js", "*.generated.*",
    "*_pb2.py", "*.pb.go",
}

TEST_FILE_PATTERNS = {
    "test_*.py", "*_test.py", "conftest.py",
    "*.spec.ts", "*.test.ts", "*.spec.tsx", "*.test.tsx",
    "*.spec.js", "*.test.js", "*.spec.jsx", "*.test.jsx",
}

TEST_DIR_NAMES = {"tests", "test", "__tests__", "spec", "specs"}


@dataclass
class EchoGuardConfig:
    """Echo Guard configuration."""
    # Similarity detection
    threshold: float = 0.50
    min_function_lines: int = 3
    max_function_lines: int = 500

    # Languages
    languages: list[str] = field(default_factory=lambda: [
        "python", "javascript", "typescript", "go", "rust", "java", "ruby", "c", "cpp"
    ])

    # Paths
    include_paths: list[str] = field(default_factory=list)
    exclude_dirs: set[str] = field(default_factory=lambda: set(DEFAULT_EXCLUDE_DIRS))
    exclude_patterns: set[str] = field(default_factory=lambda: set(DEFAULT_EXCLUDE_PATTERNS))

    # Service boundaries — directories that represent separate deployable services.
    # Functions across service boundaries cannot be imported; suggestions are adjusted.
    # Example: ["services/worker", "services/tool-gateway", "services/dashboard"]
    service_boundaries: list[str] = field(default_factory=list)

    # Output
    output_format: str = "rich"  # "rich", "json", "compact"
    fail_on: str = "high"  # "high", "medium", "none"

    # Dependency graph
    enable_dep_graph: bool = True

    # Watcher
    watch_debounce_ms: int = 500

    # Test file inclusion (excluded by default — tests are intentionally repetitive)
    include_tests: bool = False

    # Scan exclusion patterns (gitignore-style)
    ignore: list[str] = field(default_factory=list)

    # Suppressed findings — list of dicts: {id, verdict, source_hash, existing_hash}
    # verdict="intentional": re-surfaces if AST hashes change
    # verdict="dismissed": permanently suppressed
    acknowledged: list[dict] = field(default_factory=list)

    # Feedback consent tier: "private" (anonymized features only, default),
    # "public" (code pairs from public repos), or "none" (local only)
    feedback_consent: str = "private"

    # Path to the config file (for writing back acknowledged findings)
    _config_path: Path | None = field(default=None, repr=False)

    @classmethod
    def load(cls, repo_root: str | Path) -> "EchoGuardConfig":
        """Load config from file, falling back to defaults."""
        repo_root = Path(repo_root)
        for filename in CONFIG_FILENAMES:
            config_path = repo_root / filename
            if config_path.exists():
                return cls._from_file(config_path)
        return cls()

    @classmethod
    def _from_file(cls, path: Path) -> "EchoGuardConfig":
        """Parse a config file."""
        with open(path) as f:
            raw = yaml.safe_load(f) or {}

        config = cls()
        config._config_path = path

        if "threshold" in raw:
            config.threshold = float(raw["threshold"])
        if "min_function_lines" in raw:
            config.min_function_lines = int(raw["min_function_lines"])
        if "max_function_lines" in raw:
            config.max_function_lines = int(raw["max_function_lines"])
        if "languages" in raw:
            config.languages = list(raw["languages"])
        if "include_paths" in raw:
            config.include_paths = list(raw["include_paths"])
        if "exclude_dirs" in raw:
            config.exclude_dirs = set(raw["exclude_dirs"]) | DEFAULT_EXCLUDE_DIRS
        if "exclude_patterns" in raw:
            config.exclude_patterns = set(raw["exclude_patterns"]) | DEFAULT_EXCLUDE_PATTERNS
        if "output_format" in raw:
            config.output_format = raw["output_format"]
        if "fail_on" in raw:
            config.fail_on = raw["fail_on"]
        if "service_boundaries" in raw:
            config.service_boundaries = list(raw["service_boundaries"])
        if "enable_dep_graph" in raw:
            config.enable_dep_graph = bool(raw["enable_dep_graph"])
        if "watch_debounce_ms" in raw:
            config.watch_debounce_ms = int(raw["watch_debounce_ms"])
        if "include_tests" in raw:
            config.include_tests = bool(raw["include_tests"])
        if "ignore" in raw:
            config.ignore = list(raw["ignore"])
        if "acknowledged" in raw:
            entries = []
            for entry in raw["acknowledged"]:
                if isinstance(entry, dict):
                    entries.append(entry)
                # Old plain-string format dropped — skip silently
            config.acknowledged = entries
        if "feedback_consent" in raw:
            config.feedback_consent = str(raw["feedback_consent"])

        return config

    def get_suppressed_ids(self) -> set[str]:
        """Return the set of finding IDs that are currently suppressed."""
        return {entry["id"] for entry in self.acknowledged if "id" in entry}

    def is_suppressed(self, finding_id: str, source_hash: str, existing_hash: str) -> bool:
        """Check if a finding should be suppressed.

        - dismissed: always suppressed (including when representative changes
          across rescans and generates a different pair ID for the same cluster)
        - intentional: suppressed only if both AST hashes still match
        """
        for entry in self.acknowledged:
            if entry.get("id") != finding_id:
                continue
            verdict = entry.get("verdict", "intentional")
            if verdict == "dismissed":
                return True
            # intentional: check that AST hashes haven't changed
            stored_src = entry.get("source_hash", "")
            stored_ext = entry.get("existing_hash", "")
            if stored_src and stored_ext:
                # The finding ID encodes hashes in sorted order — we need to
                # check both orderings because we don't know which side is which
                hashes_match = (
                    (source_hash[:8] == stored_src and existing_hash[:8] == stored_ext)
                    or (source_hash[:8] == stored_ext and existing_hash[:8] == stored_src)
                )
                return hashes_match
            # No hashes stored (edge case) — suppress anyway
            return True

        # Secondary check for dismissed findings: when a 3+ copy cluster is
        # dismissed, the representative function can change between rescans,
        # producing new pair IDs (e.g. fileA||fileC → fileB||fileC) that don't
        # match the stored IDs.  If EITHER function in this new pair appears in
        # any previously dismissed finding, suppress it — the user already said
        # these functions are false positives.
        new_parts = finding_id.split("||")
        if len(new_parts) == 2:
            func_a, func_b = new_parts[0], new_parts[1]
            for entry in self.acknowledged:
                if entry.get("verdict") != "dismissed":
                    continue
                stored_parts = entry.get("id", "").split("||")
                if len(stored_parts) == 2 and (
                    func_a in stored_parts or func_b in stored_parts
                ):
                    return True

        return False

    def add_suppressed(
        self,
        finding_id: str,
        verdict: str,
        source_hash: str = "",
        existing_hash: str = "",
    ) -> None:
        """Add or update a suppressed finding and save to config file."""
        # Remove any existing entry for this ID
        self.acknowledged = [e for e in self.acknowledged if e.get("id") != finding_id]
        entry: dict = {"id": finding_id, "verdict": verdict}
        if verdict == "intentional":
            entry["source_hash"] = source_hash[:8]
            entry["existing_hash"] = existing_hash[:8]
        self.acknowledged.append(entry)
        self._save_acknowledged()

    def add_acknowledged(self, finding_id: str, verdict: str = "intentional",
                         source_hash: str = "", existing_hash: str = "") -> None:
        """Compatibility shim — delegates to add_suppressed."""
        self.add_suppressed(finding_id, verdict, source_hash, existing_hash)

    def _save_acknowledged(self) -> None:
        """Write acknowledged list back to the config file."""
        if self._config_path is None:
            # No config file loaded — create one at the default location
            self._config_path = Path.cwd() / "echo-guard.yml"

        if self._config_path.exists():
            with open(self._config_path) as f:
                raw = yaml.safe_load(f) or {}
        else:
            raw = {}

        raw["acknowledged"] = self.acknowledged

        with open(self._config_path, "w") as f:
            yaml.dump(raw, f, default_flow_style=False, sort_keys=False)

    def should_fail(self, severity: str) -> bool:
        """Check if a match severity should cause a non-zero exit.

        Severity levels (based on DRY actionability):
        - high: 3+ copies of the same function — extract now
        - medium: 2 exact copies — worth noting, may defer per Rule of Three
        - low: Lower-confidence semantic matches — hidden by default
        """
        levels = ["low", "medium", "high"]
        if self.fail_on == "none":
            return False
        try:
            fail_idx = levels.index(self.fail_on)
            sev_idx = levels.index(severity)
            return sev_idx >= fail_idx
        except ValueError:
            return severity == "high"
