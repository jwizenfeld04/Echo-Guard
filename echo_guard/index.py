"""DuckDB-backed persistent index for function metadata and vectors.

Stores extracted function data from any supported language.
Supports incremental indexing via file metadata tracking.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb

from echo_guard.languages import ExtractedFunction


DEFAULT_INDEX_DIR = ".echo-guard"
DEFAULT_DB_NAME = "index.duckdb"


class FunctionIndex:
    """Persistent storage for indexed function metadata."""

    def __init__(self, repo_root: str | Path | None = None):
        if repo_root is None:
            repo_root = Path.cwd()
        self.repo_root = Path(repo_root)
        self.index_dir = self.repo_root / DEFAULT_INDEX_DIR
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.index_dir / DEFAULT_DB_NAME
        self.conn = duckdb.connect(str(self.db_path))
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS functions (
                qualified_name VARCHAR PRIMARY KEY,
                name VARCHAR NOT NULL,
                filepath VARCHAR NOT NULL,
                language VARCHAR NOT NULL DEFAULT 'python',
                lineno INTEGER NOT NULL,
                end_lineno INTEGER,
                source TEXT NOT NULL,
                ast_hash VARCHAR,
                param_count INTEGER,
                has_return BOOLEAN,
                return_type VARCHAR,
                class_name VARCHAR,
                imports_used TEXT,  -- JSON array
                decorators TEXT,    -- JSON array
                calls_made TEXT,    -- JSON array
                signature_key VARCHAR,
                visibility VARCHAR DEFAULT 'public',
                is_nested BOOLEAN DEFAULT FALSE,
                indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_ast_hash ON functions (ast_hash)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_filepath ON functions (filepath)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_language ON functions (language)")

        # Schema migrations: add columns to existing databases
        for migration in [
            "ALTER TABLE functions ADD COLUMN is_nested BOOLEAN DEFAULT FALSE",
            "ALTER TABLE functions ADD COLUMN embedding_row INTEGER",
            "ALTER TABLE functions ADD COLUMN embedding_version VARCHAR",
        ]:
            try:
                self.conn.execute(migration)
            except duckdb.CatalogException:
                pass  # Column already exists

        # ── File metadata for incremental indexing ──
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS file_metadata (
                filepath VARCHAR PRIMARY KEY,
                mtime DOUBLE NOT NULL,
                size_bytes BIGINT NOT NULL,
                git_sha VARCHAR,
                function_count INTEGER DEFAULT 0,
                indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ── User feedback for match quality ──
        self.conn.execute("""
            CREATE SEQUENCE IF NOT EXISTS feedback_id_seq;
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY DEFAULT nextval('feedback_id_seq'),
                verdict VARCHAR NOT NULL,
                match_type VARCHAR,
                similarity_score DOUBLE,
                severity VARCHAR,
                reuse_type VARCHAR,
                source_language VARCHAR,
                source_param_count INTEGER,
                source_has_return BOOLEAN,
                source_line_count INTEGER,
                source_call_count INTEGER,
                source_visibility VARCHAR,
                source_is_nested BOOLEAN,
                source_has_class BOOLEAN,
                existing_language VARCHAR,
                existing_param_count INTEGER,
                existing_has_return BOOLEAN,
                existing_line_count INTEGER,
                existing_call_count INTEGER,
                existing_visibility VARCHAR,
                existing_is_nested BOOLEAN,
                existing_has_class BOOLEAN,
                same_language BOOLEAN,
                same_file BOOLEAN,
                same_class BOOLEAN,
                same_cluster BOOLEAN,
                crosses_service_boundary BOOLEAN,
                ast_hash_match BOOLEAN,
                name_similarity DOUBLE,
                param_count_diff INTEGER,
                shared_calls_ratio DOUBLE,
                line_count_ratio DOUBLE,
                dismissed_reason VARCHAR DEFAULT '',
                filter_matched VARCHAR DEFAULT '',
                extra TEXT DEFAULT '',
                recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ── Finding resolutions (MCP agent feedback loop) ──
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS finding_resolutions (
                finding_id VARCHAR PRIMARY KEY,
                verdict VARCHAR NOT NULL,
                source_filepath VARCHAR NOT NULL,
                source_function VARCHAR NOT NULL,
                source_lineno INTEGER,
                existing_filepath VARCHAR NOT NULL,
                existing_function VARCHAR NOT NULL,
                existing_lineno INTEGER,
                clone_type VARCHAR,
                similarity_score DOUBLE,
                note VARCHAR DEFAULT '',
                resolved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ── Health score history ──
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS health_history (
                recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                score INTEGER NOT NULL,
                total_functions INTEGER,
                total_redundancies INTEGER,
                high_severity INTEGER,
                medium_severity INTEGER,
                low_severity INTEGER,
                details TEXT  -- JSON
            )
        """)

    # ── Function CRUD ─────────────────────────────────────────────────────

    def upsert_function(self, func: ExtractedFunction) -> None:
        """Insert or update a function in the index."""
        self.conn.execute(
            """
            INSERT OR REPLACE INTO functions (
                qualified_name, name, filepath, language, lineno, end_lineno, source,
                ast_hash, param_count, has_return, return_type, class_name,
                imports_used, decorators, calls_made, signature_key, visibility, is_nested
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                func.qualified_name,
                func.name,
                func.filepath,
                func.language,
                func.lineno,
                func.end_lineno,
                func.source,
                func.ast_hash,
                func.param_count,
                func.has_return,
                func.return_type,
                func.class_name,
                json.dumps(func.imports_used),
                json.dumps(func.decorators),
                json.dumps(func.calls_made),
                func.signature_key,
                func.visibility,
                func.is_nested,
            ],
        )

    def upsert_functions(self, functions: list[ExtractedFunction]) -> None:
        for func in functions:
            self.upsert_function(func)

    def get_all_functions(self) -> list[ExtractedFunction]:
        rows = self.conn.execute(
            "SELECT * FROM functions ORDER BY filepath, lineno"
        ).fetchall()
        return [self._row_to_func(row) for row in rows]

    def get_functions_by_file(self, filepath: str) -> list[ExtractedFunction]:
        rows = self.conn.execute(
            "SELECT * FROM functions WHERE filepath = ? ORDER BY lineno",
            [filepath],
        ).fetchall()
        return [self._row_to_func(row) for row in rows]

    def get_functions_by_language(self, language: str) -> list[ExtractedFunction]:
        rows = self.conn.execute(
            "SELECT * FROM functions WHERE language = ? ORDER BY filepath, lineno",
            [language],
        ).fetchall()
        return [self._row_to_func(row) for row in rows]

    def remove_file(self, filepath: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM functions WHERE filepath = ?", [filepath]
        ).fetchone()
        count = row[0] if row else 0
        self.conn.execute("DELETE FROM functions WHERE filepath = ?", [filepath])
        self.conn.execute("DELETE FROM file_metadata WHERE filepath = ?", [filepath])
        return count

    def get_stats(self) -> dict:
        total_row = self.conn.execute("SELECT COUNT(*) FROM functions").fetchone()
        total = total_row[0] if total_row else 0
        files_row = self.conn.execute(
            "SELECT COUNT(DISTINCT filepath) FROM functions"
        ).fetchone()
        files = files_row[0] if files_row else 0
        lang_counts = self.conn.execute(
            "SELECT language, COUNT(*) FROM functions GROUP BY language ORDER BY language"
        ).fetchall()
        vis_counts = self.conn.execute(
            "SELECT visibility, COUNT(*) FROM functions GROUP BY visibility ORDER BY visibility"
        ).fetchall()
        return {
            "total_functions": total,
            "total_files": files,
            "by_language": {row[0]: row[1] for row in lang_counts},
            "by_visibility": {row[0]: row[1] for row in vis_counts},
        }

    def clear(self) -> None:
        self.conn.execute("DELETE FROM functions")
        self.conn.execute("DELETE FROM file_metadata")

    def close(self) -> None:
        self.conn.close()

    # ── Embedding row management ─────────────────────────────────────────

    def set_embedding_row(
        self, qualified_name: str, row: int, version: str,
    ) -> None:
        """Associate a function with its row in the embedding store."""
        self.conn.execute(
            "UPDATE functions SET embedding_row = ?, embedding_version = ? "
            "WHERE qualified_name = ?",
            [row, version, qualified_name],
        )

    def set_embedding_rows(
        self, updates: list[tuple[str, int, str]],
    ) -> None:
        """Batch update embedding rows. Each tuple: (qualified_name, row, version)."""
        for qname, row, version in updates:
            self.set_embedding_row(qname, row, version)

    def get_functions_needing_embeddings(self, version: str) -> list[ExtractedFunction]:
        """Get functions that don't have embeddings or have stale embeddings."""
        rows = self.conn.execute(
            "SELECT * FROM functions WHERE embedding_row IS NULL "
            "OR embedding_version IS NULL OR embedding_version != ? "
            "ORDER BY filepath, lineno",
            [version],
        ).fetchall()
        return [self._row_to_func(row) for row in rows]

    def get_embedding_row_map(self) -> dict[str, int]:
        """Get mapping of qualified_name -> embedding_row for all embedded functions."""
        rows = self.conn.execute(
            "SELECT qualified_name, embedding_row FROM functions "
            "WHERE embedding_row IS NOT NULL"
        ).fetchall()
        return {row[0]: row[1] for row in rows}

    def clear_embedding_rows(self) -> None:
        """Clear all embedding associations (e.g., when model changes)."""
        self.conn.execute(
            "UPDATE functions SET embedding_row = NULL, embedding_version = NULL"
        )

    # ── Finding resolutions ────────────────────────────────────────────────

    @staticmethod
    def make_finding_id(
        source_filepath: str, source_name: str,
        existing_filepath: str, existing_name: str,
    ) -> str:
        """Create a stable ID for a finding based on the two function locations.

        The ID is deterministic and order-independent so the same pair always
        produces the same ID regardless of which side is "source" vs "existing".
        """
        pair = sorted([
            f"{source_filepath}:{source_name}",
            f"{existing_filepath}:{existing_name}",
        ])
        return f"{pair[0]}||{pair[1]}"

    def resolve_finding(
        self,
        finding_id: str,
        verdict: str,
        source_filepath: str,
        source_function: str,
        source_lineno: int | None,
        existing_filepath: str,
        existing_function: str,
        existing_lineno: int | None,
        clone_type: str = "",
        similarity_score: float = 0.0,
        note: str = "",
    ) -> None:
        """Record a resolution for a finding.

        Verdicts:
        - "fixed": The duplicate was consolidated/refactored
        - "acknowledged": Intentional duplication, suppress in future scans
        - "false_positive": Not actually a duplicate, suppress in future scans
        """
        self.conn.execute(
            """
            INSERT OR REPLACE INTO finding_resolutions (
                finding_id, verdict, source_filepath, source_function,
                source_lineno, existing_filepath, existing_function,
                existing_lineno, clone_type, similarity_score, note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [finding_id, verdict, source_filepath, source_function,
             source_lineno, existing_filepath, existing_function,
             existing_lineno, clone_type, similarity_score, note],
        )

    def get_resolved_finding_ids(self) -> set[str]:
        """Get all finding IDs that have been resolved (any verdict)."""
        rows = self.conn.execute(
            "SELECT finding_id FROM finding_resolutions"
        ).fetchall()
        return {row[0] for row in rows}

    def get_resolution(self, finding_id: str) -> dict | None:
        """Get the resolution for a specific finding."""
        row = self.conn.execute(
            "SELECT * FROM finding_resolutions WHERE finding_id = ?",
            [finding_id],
        ).fetchone()
        if row is None:
            return None
        cols = [desc[0] for desc in self.conn.description]
        return dict(zip(cols, row))

    def get_all_resolutions(self) -> list[dict]:
        """Get all resolutions for observability."""
        rows = self.conn.execute(
            "SELECT * FROM finding_resolutions ORDER BY resolved_at DESC"
        ).fetchall()
        cols = [desc[0] for desc in self.conn.description]
        return [dict(zip(cols, row)) for row in rows]

    def get_resolution_stats(self) -> dict:
        """Summary statistics of finding resolutions."""
        rows = self.conn.execute(
            "SELECT verdict, COUNT(*) FROM finding_resolutions GROUP BY verdict"
        ).fetchall()
        total = self.conn.execute(
            "SELECT COUNT(*) FROM finding_resolutions"
        ).fetchone()
        return {
            "total": total[0] if total else 0,
            "by_verdict": {row[0]: row[1] for row in rows},
        }

    # ── Incremental indexing support ──────────────────────────────────────

    def get_file_metadata(self, filepath: str) -> dict | None:
        """Get stored metadata for a file."""
        row = self.conn.execute(
            "SELECT filepath, mtime, size_bytes, git_sha, function_count FROM file_metadata WHERE filepath = ?",
            [filepath],
        ).fetchone()
        if row is None:
            return None
        return {
            "filepath": row[0],
            "mtime": row[1],
            "size_bytes": row[2],
            "git_sha": row[3],
            "function_count": row[4],
        }

    def upsert_file_metadata(
        self, filepath: str, mtime: float, size_bytes: int,
        git_sha: str | None, function_count: int,
    ) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO file_metadata
            (filepath, mtime, size_bytes, git_sha, function_count)
            VALUES (?, ?, ?, ?, ?)
            """,
            [filepath, mtime, size_bytes, git_sha, function_count],
        )

    def get_all_indexed_files(self) -> set[str]:
        """Get all filepaths currently in the index."""
        rows = self.conn.execute("SELECT DISTINCT filepath FROM functions").fetchall()
        return {row[0] for row in rows}

    def file_needs_reindex(self, filepath: str, abs_path: Path) -> bool:
        """Check if a file needs re-indexing based on mtime and size."""
        meta = self.get_file_metadata(filepath)
        if meta is None:
            return True
        try:
            stat = abs_path.stat()
            if stat.st_mtime != meta["mtime"]:
                return True
            if stat.st_size != meta["size_bytes"]:
                return True
        except OSError:
            return True
        return False

    # ── Health score history ──────────────────────────────────────────────

    def record_health_score(self, score: int, details: dict) -> None:
        self.conn.execute(
            """
            INSERT INTO health_history
            (score, total_functions, total_redundancies, high_severity, medium_severity, low_severity, details)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                score,
                details.get("total_functions", 0),
                details.get("total_redundancies", 0),
                details.get("high", 0),
                details.get("medium", 0),
                details.get("low", 0),
                json.dumps(details),
            ],
        )

    def get_health_history(self, limit: int = 30) -> list[dict]:
        rows = self.conn.execute(
            "SELECT recorded_at, score, total_functions, total_redundancies, "
            "high_severity, medium_severity, low_severity "
            "FROM health_history ORDER BY recorded_at DESC LIMIT ?",
            [limit],
        ).fetchall()
        return [
            {
                "recorded_at": str(row[0]),
                "score": row[1],
                "total_functions": row[2],
                "total_redundancies": row[3],
                "high": row[4],
                "medium": row[5],
            }
            for row in rows
        ]

    # ── Feedback ──────────────────────────────────────────────────────────

    def record_feedback(self, record: dict) -> None:
        """Save an anonymized feedback record."""
        # Only insert columns that are present in the record
        _FEEDBACK_COLS = {
            "verdict", "match_type", "similarity_score", "severity", "reuse_type",
            "source_language", "source_param_count", "source_has_return",
            "source_line_count", "source_call_count", "source_visibility",
            "source_is_nested", "source_has_class",
            "existing_language", "existing_param_count", "existing_has_return",
            "existing_line_count", "existing_call_count", "existing_visibility",
            "existing_is_nested", "existing_has_class",
            "same_language", "same_file", "same_class", "same_cluster",
            "crosses_service_boundary", "ast_hash_match", "name_similarity",
            "param_count_diff", "shared_calls_ratio", "line_count_ratio",
            "dismissed_reason", "filter_matched", "extra",
        }
        cols = [c for c in record if c in _FEEDBACK_COLS and record[c] is not None]
        placeholders = ", ".join("?" for _ in cols)
        col_names = ", ".join(cols)
        values = [record[c] for c in cols]
        self.conn.execute(
            f"INSERT INTO feedback ({col_names}) VALUES ({placeholders})",
            values,
        )

    def get_feedback(self, limit: int = 1000) -> list[dict]:
        """Get all feedback records."""
        rows = self.conn.execute(
            "SELECT * FROM feedback ORDER BY recorded_at DESC LIMIT ?",
            [limit],
        ).fetchall()
        cols = [desc[0] for desc in self.conn.description]
        return [dict(zip(cols, row)) for row in rows]

    def get_feedback_stats(self) -> dict:
        """Get summary statistics of collected feedback."""
        total_row = self.conn.execute("SELECT COUNT(*) FROM feedback").fetchone()
        total = total_row[0] if total_row else 0
        if total == 0:
            return {"total": 0, "by_verdict": {}, "by_severity": {}}

        verdict_rows = self.conn.execute(
            "SELECT verdict, COUNT(*) FROM feedback GROUP BY verdict"
        ).fetchall()
        severity_rows = self.conn.execute(
            "SELECT severity, COUNT(*) FROM feedback GROUP BY severity"
        ).fetchall()
        return {
            "total": total,
            "by_verdict": {row[0]: row[1] for row in verdict_rows},
            "by_severity": {row[0]: row[1] for row in severity_rows},
        }

    def export_feedback_jsonl(self) -> list[dict]:
        """Export all feedback as a list of dicts (for JSONL export)."""
        return self.get_feedback(limit=100000)

    # ── Row conversion ────────────────────────────────────────────────────

    def _row_to_func(self, row: tuple) -> ExtractedFunction:
        return ExtractedFunction(
            name=row[1],
            filepath=row[2],
            language=row[3],
            lineno=row[4],
            end_lineno=row[5] or row[4],
            source=row[6],
            ast_hash=row[7] or "",
            param_count=row[8] or 0,
            has_return=bool(row[9]),
            return_type=row[10],
            class_name=row[11],
            imports_used=json.loads(row[12]) if row[12] else [],
            decorators=json.loads(row[13]) if row[13] else [],
            calls_made=json.loads(row[14]) if row[14] else [],
            signature_key=row[15] or "",
            visibility=row[16] or "public",
            is_nested=bool(row[17]) if len(row) > 17 else False,
        )
