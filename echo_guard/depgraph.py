"""Dependency graph routing (Stage 4).

Clusters modules by domain (auth, database, API layer, etc.) so new code
is compared against its relevant cluster first. This prevents false positives
across unrelated domains and speeds up comparison.

The graph is built from:
1. Import relationships between modules
2. Shared function calls (modules calling the same functions)
3. Directory structure (files in the same package are related)
4. Naming conventions (files with similar names are likely in the same domain)
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from echo_guard.languages import ExtractedFunction


@dataclass
class ModuleNode:
    """A node in the dependency graph representing a file/module."""
    filepath: str
    # Domain cluster this module belongs to
    cluster: str = ""
    # Edges: other modules this module imports from or calls into
    imports_from: set[str] = field(default_factory=set)
    imported_by: set[str] = field(default_factory=set)
    # Functions defined in this module
    function_names: set[str] = field(default_factory=set)
    # External calls made (unresolved)
    external_calls: set[str] = field(default_factory=set)
    # Keywords extracted from path and function names
    keywords: set[str] = field(default_factory=set)


# Common domain keywords for cluster detection
DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "auth": ["auth", "login", "logout", "password", "token", "session", "jwt",
             "oauth", "credential", "permission", "role", "user", "signup",
             "register", "verify", "hash"],
    "database": ["db", "database", "model", "schema", "migration", "query",
                 "repository", "repo", "orm", "sql", "table", "record",
                 "entity", "persist", "store", "crud"],
    "api": ["api", "route", "router", "endpoint", "handler", "controller",
            "request", "response", "middleware", "http", "rest", "graphql",
            "webhook", "view"],
    "util": ["util", "utils", "helper", "helpers", "common", "shared", "lib",
             "tool", "tools", "misc", "support"],
    "test": ["test", "tests", "spec", "specs", "fixture", "mock", "assert",
             "conftest", "factory"],
    "config": ["config", "configuration", "settings", "env", "environment",
               "setup", "init", "bootstrap"],
    "data": ["data", "transform", "pipeline", "etl", "process", "parse",
             "parser", "serializer", "deserializer", "convert", "format",
             "validate", "validation", "schema"],
    "ui": ["component", "widget", "view", "template", "render", "display",
           "page", "screen", "layout", "style", "theme"],
    "network": ["client", "server", "socket", "connection", "transport",
                "protocol", "proxy", "fetch", "download", "upload"],
    "file": ["file", "path", "directory", "folder", "io", "read", "write",
             "stream", "buffer", "fs"],
}


def _extract_keywords(filepath: str, function_names: set[str]) -> set[str]:
    """Extract domain-relevant keywords from a filepath and its functions."""
    keywords = set()
    # From filepath
    parts = Path(filepath).stem.lower().replace("-", "_").split("_")
    keywords.update(parts)
    # From directory names
    for part in Path(filepath).parts[:-1]:
        keywords.update(part.lower().replace("-", "_").split("_"))
    # From function names — split camelCase and snake_case
    for name in function_names:
        subwords = re.findall(r"[a-z]+", re.sub(r"([A-Z])", r"_\1", name).lower())
        keywords.update(w for w in subwords if len(w) > 2)
    return keywords


def _detect_cluster(keywords: set[str]) -> str:
    """Determine which domain cluster a module belongs to based on keywords."""
    scores: dict[str, int] = defaultdict(int)
    for domain, domain_words in DOMAIN_KEYWORDS.items():
        for kw in keywords:
            if kw in domain_words:
                scores[domain] += 1
    if not scores:
        return "general"
    return max(scores, key=lambda k: scores[k])


class DependencyGraph:
    """Module dependency graph for domain-aware routing."""

    def __init__(self) -> None:
        self.nodes: dict[str, ModuleNode] = {}
        self._clusters: dict[str, list[str]] = defaultdict(list)

    def add_module(self, filepath: str, functions: list[ExtractedFunction]) -> None:
        """Add a module and its functions to the graph."""
        func_names = {f.name for f in functions}
        all_calls = set()
        all_imports = set()

        for func in functions:
            all_calls.update(func.calls_made)
            all_imports.update(func.imports_used)

        keywords = _extract_keywords(filepath, func_names)

        node = ModuleNode(
            filepath=filepath,
            function_names=func_names,
            external_calls=all_calls - func_names,  # Calls to functions not in this module
            imports_from=all_imports,
            keywords=keywords,
        )
        node.cluster = _detect_cluster(keywords)
        self.nodes[filepath] = node

    def build(self) -> None:
        """Build the full dependency graph — resolve import edges and cluster."""
        # Resolve import edges
        func_to_module: dict[str, str] = {}
        for filepath, node in self.nodes.items():
            for fname in node.function_names:
                func_to_module[fname] = filepath

        for filepath, node in self.nodes.items():
            for call in node.external_calls:
                if call in func_to_module:
                    target = func_to_module[call]
                    if target != filepath:
                        node.imports_from.add(target)
                        if target in self.nodes:
                            self.nodes[target].imported_by.add(filepath)

        # Build cluster index
        self._clusters.clear()
        for filepath, node in self.nodes.items():
            self._clusters[node.cluster].append(filepath)

    def get_cluster(self, filepath: str) -> str:
        """Get the domain cluster for a file."""
        node = self.nodes.get(filepath)
        return node.cluster if node else "general"

    def get_cluster_members(self, cluster: str) -> list[str]:
        """Get all files in a cluster."""
        return self._clusters.get(cluster, [])

    def get_related_files(self, filepath: str, max_depth: int = 2) -> set[str]:
        """Get files related to the given file through imports and shared cluster.

        Returns files that should be prioritized for comparison.
        """
        related = set()
        node = self.nodes.get(filepath)
        if node is None:
            return related

        # Same cluster files
        related.update(self.get_cluster_members(node.cluster))

        # Direct import neighbors
        related.update(node.imports_from)
        related.update(node.imported_by)

        # BFS for transitive dependencies
        if max_depth > 1:
            frontier = set(related)
            for _ in range(max_depth - 1):
                next_frontier = set()
                for f in frontier:
                    n = self.nodes.get(f)
                    if n:
                        next_frontier.update(n.imports_from)
                        next_frontier.update(n.imported_by)
                related.update(next_frontier)
                frontier = next_frontier

        related.discard(filepath)
        return related

    def get_comparison_candidates(
        self,
        filepath: str,
        all_functions: dict[str, list[ExtractedFunction]],
    ) -> list[ExtractedFunction]:
        """Get functions that should be compared against a given file.

        Priority order:
        1. Same cluster (highest chance of semantic overlap)
        2. Direct import neighbors
        3. Transitive neighbors (depth 2)
        4. Everything else (fallback)

        Returns functions sorted by relevance.
        """
        related = self.get_related_files(filepath)
        cluster = self.get_cluster(filepath)

        # Tier 1: Same cluster
        tier1 = []
        # Tier 2: Import neighbors but different cluster
        tier2 = []
        # Tier 3: Everything else
        tier3 = []

        for fpath, funcs in all_functions.items():
            if fpath == filepath:
                continue
            if fpath in related:
                fnode = self.nodes.get(fpath)
                if fnode and fnode.cluster == cluster:
                    tier1.extend(funcs)
                else:
                    tier2.extend(funcs)
            else:
                tier3.extend(funcs)

        return tier1 + tier2 + tier3

    def get_stats(self) -> dict:
        """Get graph statistics."""
        clusters = defaultdict(int)
        for node in self.nodes.values():
            clusters[node.cluster] += 1
        return {
            "total_modules": len(self.nodes),
            "clusters": dict(clusters),
            "total_edges": sum(len(n.imports_from) for n in self.nodes.values()),
        }
