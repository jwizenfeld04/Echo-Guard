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

        # Schema migration: add is_nested column to existing databases
        try:
            self.conn.execute("ALTER TABLE functions ADD COLUMN is_nested BOOLEAN DEFAULT FALSE")
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
                "low": row[6],
            }
            for row in rows
        ]

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
