"""Configuration file support for Echo Guard (.echoguard.yml)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


CONFIG_FILENAMES = [".echoguard.yml", ".echoguard.yaml", "echoguard.yml"]

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

    # Scan exclusion patterns (gitignore-style)
    ignore: list[str] = field(default_factory=list)

    # Acknowledged finding IDs (suppressed in CI)
    acknowledged: list[str] = field(default_factory=list)

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
        if "ignore" in raw:
            config.ignore = list(raw["ignore"])
        if "acknowledged" in raw:
            config.acknowledged = list(raw["acknowledged"])

        return config

    def add_acknowledged(self, finding_id: str) -> None:
        """Add a finding ID to the acknowledged list and save to config file."""
        if finding_id in self.acknowledged:
            return
        self.acknowledged.append(finding_id)
        self._save_acknowledged()

    def _save_acknowledged(self) -> None:
        """Write acknowledged list back to the config file."""
        if self._config_path is None:
            return

        with open(self._config_path) as f:
            raw = yaml.safe_load(f) or {}

        raw["acknowledged"] = self.acknowledged

        with open(self._config_path, "w") as f:
            yaml.dump(raw, f, default_flow_style=False, sort_keys=False)

    def should_fail(self, severity: str) -> bool:
        """Check if a match severity should cause a non-zero exit.

        Severity levels (derived from clone type):
        - high: Type-1/Type-2 exact clones, or Type-3 with ≥90% similarity
        - medium: Type-3 modified clones, or Type-4 semantic clones
        """
        levels = ["medium", "high"]
        if self.fail_on == "none":
            return False
        try:
            fail_idx = levels.index(self.fail_on)
            sev_idx = levels.index(severity)
            return sev_idx >= fail_idx
        except ValueError:
            return severity == "high"
