"""Long-running JSON-RPC daemon for VS Code extension integration.

Holds the FunctionIndex, EmbeddingModel, EmbeddingStore, and SimilarityEngine
in memory to avoid the ~2-3s cold start cost of loading ONNX model + DuckDB on
every file save.

Communicates via JSON-RPC 2.0 over stdin/stdout. Each request is a
newline-delimited JSON object; each response is a newline-delimited JSON object.

Writes a lockfile at .echo-guard/daemon.lock so that the MCP server can
detect whether a daemon is running and route through it for consistency.

Usage:
    echo-guard daemon [--repo-root PATH]

Supported RPC methods:
    initialize          Load index, model, run incremental reindex
    check_file          Re-parse, re-embed, check file against full index
    check_files         Batch variant of check_file
    scan                Full scan, return all findings
    resolve_finding     Record a verdict (resolved/intentional/dismissed)
    get_findings        Return current findings (optionally filtered by file)
    reindex             Incremental reindex
    shutdown            Clean exit
"""

from __future__ import annotations

import json
import logging
import os
import socket
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler as _FSEventHandler
    _WATCHDOG_AVAILABLE = True
except ImportError:
    _WATCHDOG_AVAILABLE = False
    Observer = None  # type: ignore
    _FSEventHandler = object  # type: ignore

log = logging.getLogger("echo_guard.daemon")


# ── JSON-RPC helpers ────────────────────────────────────────────────────


def _ok(id_: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _err(id_: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


_stdout_lock = threading.Lock()


def _send(obj: dict) -> None:
    with _stdout_lock:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()


# ── Finding serialization ───────────────────────────────────────────────


def _serialize_match(match: Any, finding_id: str) -> dict:
    """Convert a SimilarityMatch to a JSON-serializable dict."""
    src = match.source_func
    ext = match.existing_func
    is_cross_service = getattr(match, "reuse_type", "") == "cross_service_reference"
    return {
        "finding_id": finding_id,
        "severity": match.severity,
        "clone_type": getattr(match, "clone_type", ""),
        "clone_type_label": getattr(match, "clone_type_label", ""),
        "similarity": round(float(match.similarity_score), 3),
        "ast_similarity": round(float(getattr(match, "ast_similarity", 0.0)), 3),
        "cross_service": is_cross_service,
        "source": {
            "name": src.name,
            "filepath": src.filepath,
            "lineno": src.lineno,
            "language": src.language,
        },
        "existing": {
            "name": ext.name,
            "filepath": ext.filepath,
            "lineno": ext.lineno,
            "language": ext.language,
        },
        "reuse_type": getattr(match, "reuse_type", ""),
        "import_suggestion": getattr(match, "import_suggestion", ""),
        "reuse_guidance": getattr(match, "reuse_guidance", ""),
    }


def _serialize_group_member(func: Any, rep: Any, finding_id: str, severity: str, reuse_type: str) -> dict:
    """Serialize a FindingGroup member paired against the representative match."""
    src = func
    ext = rep.source_func
    is_cross_service = reuse_type == "cross_service_reference"
    return {
        "finding_id": finding_id,
        "severity": severity,
        "clone_type": getattr(rep, "clone_type", ""),
        "clone_type_label": getattr(rep, "clone_type_label", ""),
        "similarity": round(float(rep.similarity_score), 3),
        "cross_service": is_cross_service,
        "source": {
            "name": src.name,
            "filepath": src.filepath,
            "lineno": src.lineno,
            "language": src.language,
        },
        "existing": {
            "name": ext.name,
            "filepath": ext.filepath,
            "lineno": ext.lineno,
            "language": ext.language,
        },
        "reuse_type": reuse_type,
        "import_suggestion": getattr(rep, "import_suggestion", ""),
        "reuse_guidance": getattr(rep, "reuse_guidance", ""),
    }


# ── Daemon class ────────────────────────────────────────────────────────


class EchoGuardDaemon:
    """In-memory daemon that serves JSON-RPC requests from VS Code extension."""

    def __init__(self, repo_root: str | Path) -> None:
        self.repo_root = Path(repo_root)
        self._index = None
        self._config = None
        # Cache current findings per file: filepath -> list[dict]
        self._findings: dict[str, list[dict]] = {}
        # Transient set of finding IDs resolved in this session.  These are
        # filtered from scan results so a rescan of unchanged code doesn't
        # re-surface them.  Not persisted — the code change itself will make
        # the old finding ID unmatchable on future scans.
        self._resolved_ids: set[str] = set()
        self._lock_path = self.repo_root / ".echo-guard" / "daemon.lock"
        self._socket_path: str | None = None
        self._state_lock = threading.Lock()

    # ── Lifecycle ──────────────────────────────────────────────────────

    def start(self) -> None:
        """Write lockfile, start socket server, and begin serving stdio requests."""
        self._start_socket_server()
        self._write_lock()
        try:
            self._serve()
        finally:
            self._remove_lock()
            self._cleanup_socket()

    def _write_lock(self) -> None:
        lock_dir = self.repo_root / ".echo-guard"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_data: dict[str, Any] = {"pid": os.getpid(), "transport": "stdio"}
        if self._socket_path:
            lock_data["socket"] = self._socket_path
        self._lock_path.write_text(json.dumps(lock_data))

    def _remove_lock(self) -> None:
        try:
            self._lock_path.unlink(missing_ok=True)
        except Exception:
            pass

    def _start_socket_server(self) -> None:
        """Start a Unix domain socket server in a background thread.

        Each connection receives one JSON-RPC request and sends one response.
        Falls back silently if Unix sockets are unavailable (Windows < 3.9).
        """
        try:
            sock_fd, sock_path = tempfile.mkstemp(prefix="echo-guard-", suffix=".sock")
            os.close(sock_fd)
            os.unlink(sock_path)  # Remove so bind() can create it

            server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server_sock.bind(sock_path)
            server_sock.listen(5)
            server_sock.settimeout(1.0)  # Allow periodic shutdown checks
            self._socket_path = sock_path
            self._server_sock = server_sock

            def _serve_socket() -> None:
                while True:
                    try:
                        conn, _ = server_sock.accept()
                    except socket.timeout:
                        if not self._lock_path.exists():
                            break  # Daemon is shutting down
                        continue
                    except OSError:
                        break
                    threading.Thread(
                        target=self._handle_socket_connection,
                        args=(conn,),
                        daemon=True,
                    ).start()

            thread = threading.Thread(target=_serve_socket, daemon=True)
            thread.start()
        except Exception as exc:
            log.warning("Could not start socket server: %s", exc)
            self._socket_path = None

    def _handle_socket_connection(self, conn: socket.socket) -> None:
        """Handle a single MCP socket connection (one request → one response)."""
        try:
            data = b""
            while b"\n" not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                data += chunk
            line = data.split(b"\n", 1)[0]
            req = json.loads(line)
            method = req.get("method", "")
            params = req.get("params") or {}
            req_id = req.get("id")

            # For scan requests run the expensive work outside _state_lock so
            # concurrent stdio requests (e.g. get_findings polls) are not
            # blocked for the full scan duration.
            if method == "scan":
                try:
                    result = self._handle_scan_unlocked(params)
                    response = _ok(req_id, result)
                    _send({
                        "jsonrpc": "2.0",
                        "method": "findings_refreshed",
                        "params": {"total": result.get("total", 0)},
                    })
                except Exception as exc:
                    response = _err(req_id, -32603, str(exc))
                conn.sendall((json.dumps(response) + "\n").encode())
                return

            with self._state_lock:
                try:
                    result = self._dispatch(method, params)
                    response = _ok(req_id, result)
                    # Push notifications to the stdio client (VS Code extension)
                    # so it can update UI immediately after an external mutation.
                    if method == "resolve_finding" and isinstance(result, dict) and result.get("resolved"):
                        _send({
                            "jsonrpc": "2.0",
                            "method": "finding_resolved",
                            "params": {"finding_id": result["finding_id"]},
                        })
                except ShutdownRequested:
                    response = _ok(req_id, {"shutdown": True})
                except Exception as exc:
                    response = _err(req_id, -32603, str(exc))

            conn.sendall((json.dumps(response) + "\n").encode())
        except Exception as exc:
            log.debug("Socket connection error: %s", exc)
        finally:
            conn.close()

    def _cleanup_socket(self) -> None:
        try:
            sock = getattr(self, "_server_sock", None)
            if sock:
                sock.close()
        except Exception:
            pass
        if self._socket_path:
            try:
                os.unlink(self._socket_path)
            except Exception:
                pass

    def _trigger_background_rescan(self) -> None:
        """Run a full rescan in a background thread, then notify VS Code."""
        def _run() -> None:
            try:
                result = self._handle_scan_unlocked({})
                _send({
                    "jsonrpc": "2.0",
                    "method": "findings_refreshed",
                    "params": {"total": result.get("total", 0)},
                })
            except Exception as exc:
                log.warning("Signal-triggered rescan failed: %s", exc)

        threading.Thread(target=_run, daemon=True).start()

    def _serve(self) -> None:
        """Main loop: read newline-delimited JSON from stdin, dispatch, write response."""
        # Start signal file watcher if watchdog is available
        _observer = None
        if _WATCHDOG_AVAILABLE:
            index_dir = self.repo_root / ".echo-guard"
            index_dir.mkdir(parents=True, exist_ok=True)

            class _SignalHandler(_FSEventHandler):  # type: ignore[misc]
                def __init__(self, daemon_ref: "EchoGuardDaemon") -> None:
                    super().__init__()
                    self._daemon = daemon_ref

                def on_modified(self, event: Any) -> None:
                    if Path(event.src_path).name == "rescan.signal":
                        self._daemon._trigger_background_rescan()

                on_created = on_modified  # type: ignore[assignment]

            _observer = Observer()
            _observer.schedule(_SignalHandler(self), str(index_dir), recursive=False)
            _observer.start()

        try:
            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue
                try:
                    req = json.loads(line)
                except json.JSONDecodeError as exc:
                    _send(_err(None, -32700, f"Parse error: {exc}"))
                    continue

                req_id = req.get("id")
                method = req.get("method", "")
                params = req.get("params") or {}

                with self._state_lock:
                    try:
                        result = self._dispatch(method, params)
                        _send(_ok(req_id, result))
                    except ShutdownRequested:
                        _send(_ok(req_id, {"shutdown": True}))
                        return
                    except Exception as exc:
                        log.exception("RPC error in method %s", method)
                        _send(_err(req_id, -32603, str(exc)))
        finally:
            if _observer is not None:
                _observer.stop()
                _observer.join(timeout=2)

    def _dispatch(self, method: str, params: dict) -> Any:
        handlers = {
            "initialize": self._handle_initialize,
            "check_file": self._handle_check_file,
            "check_files": self._handle_check_files,
            "scan": self._handle_scan,
            "resolve_finding": self._handle_resolve_finding,
            "get_findings": self._handle_get_findings,
            "reindex": self._handle_reindex,
            "shutdown": self._handle_shutdown,
        }
        handler = handlers.get(method)
        if handler is None:
            raise ValueError(f"Unknown method: {method}")
        return handler(params)

    # ── Handlers ──────────────────────────────────────────────────────

    def _handle_initialize(self, params: dict) -> dict:
        """Load config, run incremental index, set up embedding infrastructure."""
        from echo_guard.config import EchoGuardConfig
        from echo_guard.scanner import index_repo

        self._config = EchoGuardConfig.load(self.repo_root)
        index, file_count, func_count, lang_counts = index_repo(
            self.repo_root, config=self._config, incremental=True
        )
        index.close()
        return {
            "ready": True,
            "files_indexed": file_count,
            "functions_indexed": func_count,
            "languages": lang_counts,
        }

    def _handle_check_file(self, params: dict) -> dict:
        """Re-parse, re-embed, and check a single file against the full index."""
        filepath = params.get("file") or params.get("filepath", "")
        if not filepath:
            raise ValueError("'file' parameter required")
        return self._check_files_impl([filepath])

    def _handle_check_files(self, params: dict) -> dict:
        """Batch file check."""
        files = params.get("files") or []
        if not files:
            raise ValueError("'files' parameter required")
        return self._check_files_impl(files)

    def _check_files_impl(self, files: list[str]) -> dict:
        from echo_guard.scanner import check_files
        from echo_guard.index import FunctionIndex

        config = self._config
        if config is None:
            from echo_guard.config import EchoGuardConfig
            config = EchoGuardConfig.load(self.repo_root)

        matches = check_files(self.repo_root, files, config=config)

        # Build finding IDs and filter suppressed
        findings_by_file: dict[str, list[dict]] = {f: [] for f in files}
        for match in matches:
            finding_id = FunctionIndex.make_finding_id(
                match.source_func.filepath,
                match.source_func.name,
                match.existing_func.filepath,
                match.existing_func.name,
                source_hash=match.source_func.ast_hash or "",
                existing_hash=match.existing_func.ast_hash or "",
            )
            if config.is_suppressed(
                finding_id,
                match.source_func.ast_hash or "",
                match.existing_func.ast_hash or "",
            ):
                continue
            serialized = _serialize_match(match, finding_id)
            # Associate with the checked file (source is in the checked files)
            for filepath in files:
                if match.source_func.filepath == filepath or match.source_func.filepath.endswith("/" + filepath):
                    findings_by_file.setdefault(filepath, []).append(serialized)
                    break
            else:
                # Fallback: associate with source filepath
                findings_by_file.setdefault(match.source_func.filepath, []).append(serialized)

        # Update cache
        for filepath, findings in findings_by_file.items():
            self._findings[filepath] = findings

        total = sum(len(v) for v in findings_by_file.values())
        return {
            "findings": findings_by_file,
            "total": total,
        }

    def _handle_scan_unlocked(self, params: dict) -> dict:
        """Run a full scan without holding _state_lock for the scan itself.

        The expensive scan work runs lock-free; _state_lock is acquired briefly
        at the end to atomically replace _findings.  This prevents concurrent
        stdio requests (e.g. get_findings polls from the extension) from
        blocking for the full scan duration.
        """
        from echo_guard.scanner import scan_for_redundancy
        from echo_guard.similarity import group_matches, FindingGroup
        from echo_guard.index import FunctionIndex
        from echo_guard.config import EchoGuardConfig

        # Read current config under the lock (fast).
        with self._state_lock:
            self._config = EchoGuardConfig.load(self.repo_root)
            config = self._config

        # Heavy scan work — no lock held here.
        raw_matches = scan_for_redundancy(self.repo_root, config=config)
        grouped = group_matches(raw_matches)

        # Build the new findings structures in local memory.
        new_findings: dict[str, list[dict]] = {}
        findings_list: list[dict] = []

        for item in grouped:
            if isinstance(item, FindingGroup):
                rep = item.representative_match
                pending: list[tuple] = []
                for func in item.functions:
                    if (func.filepath == rep.source_func.filepath
                            and func.name == rep.source_func.name):
                        continue
                    finding_id = FunctionIndex.make_finding_id(
                        func.filepath, func.name,
                        rep.source_func.filepath, rep.source_func.name,
                        source_hash=func.ast_hash or "",
                        existing_hash=rep.source_func.ast_hash or "",
                    )
                    if config.is_suppressed(finding_id, func.ast_hash or "", rep.source_func.ast_hash or ""):
                        continue
                    pending.append((func, finding_id))

                visible_copies = len(pending) + 1
                effective_severity = "extract" if visible_copies >= 3 else rep.severity

                for func, finding_id in pending:
                    serialized = _serialize_group_member(func, rep, finding_id, effective_severity, item.reuse_type)
                    findings_list.append(serialized)
                    new_findings.setdefault(func.filepath, []).append(serialized)
            else:
                match = item
                finding_id = FunctionIndex.make_finding_id(
                    match.source_func.filepath, match.source_func.name,
                    match.existing_func.filepath, match.existing_func.name,
                    source_hash=match.source_func.ast_hash or "",
                    existing_hash=match.existing_func.ast_hash or "",
                )
                if config.is_suppressed(
                    finding_id,
                    match.source_func.ast_hash or "",
                    match.existing_func.ast_hash or "",
                ):
                    continue
                serialized = _serialize_match(match, finding_id)
                findings_list.append(serialized)
                new_findings.setdefault(match.source_func.filepath, []).append(serialized)

        # Atomically replace _findings under the lock.  Re-read config to
        # pick up any suppressions that were added while the scan was running
        # (e.g. concurrent resolve_finding calls), and filter out transiently
        # resolved findings.
        with self._state_lock:
            fresh_config = EchoGuardConfig.load(self.repo_root)
            suppressed_ids = fresh_config.get_suppressed_ids() | self._resolved_ids
            if suppressed_ids:
                for filepath in list(new_findings.keys()):
                    new_findings[filepath] = [
                        f for f in new_findings[filepath]
                        if f["finding_id"] not in suppressed_ids
                    ]
                findings_list = [
                    f for f in findings_list
                    if f["finding_id"] not in suppressed_ids
                ]
            self._findings.clear()
            self._findings.update(new_findings)

        return {"findings": findings_list, "total": len(findings_list)}

    def _handle_scan(self, params: dict) -> dict:
        """Full scan of the repository."""
        from echo_guard.scanner import scan_for_redundancy
        from echo_guard.similarity import group_matches, FindingGroup
        from echo_guard.index import FunctionIndex
        from echo_guard.config import EchoGuardConfig

        # Always reload config to pick up external changes (e.g. MCP suppressions)
        self._config = EchoGuardConfig.load(self.repo_root)
        config = self._config

        raw_matches = scan_for_redundancy(self.repo_root, config=config)

        # Group matches so FindingGroup.severity correctly returns "extract" for 3+ copies
        grouped = group_matches(raw_matches)

        # Clear and rebuild findings cache
        self._findings.clear()
        findings_list = []

        for item in grouped:
            if isinstance(item, FindingGroup):
                rep = item.representative_match

                # Collect non-suppressed members first so we can recompute
                # severity based on visible copy count (a dismissed copy doesn't
                # count toward the 3+ threshold the user sees).
                pending: list[tuple] = []
                for func in item.functions:
                    if (func.filepath == rep.source_func.filepath
                            and func.name == rep.source_func.name):
                        continue
                    finding_id = FunctionIndex.make_finding_id(
                        func.filepath, func.name,
                        rep.source_func.filepath, rep.source_func.name,
                        source_hash=func.ast_hash or "",
                        existing_hash=rep.source_func.ast_hash or "",
                    )
                    if finding_id in self._resolved_ids or config.is_suppressed(
                        finding_id, func.ast_hash or "", rep.source_func.ast_hash or ""
                    ):
                        continue
                    pending.append((func, finding_id))

                # +1 for the representative itself
                visible_copies = len(pending) + 1
                effective_severity = "extract" if visible_copies >= 3 else rep.severity

                for func, finding_id in pending:
                    serialized = _serialize_group_member(func, rep, finding_id, effective_severity, item.reuse_type)
                    findings_list.append(serialized)
                    self._findings.setdefault(func.filepath, []).append(serialized)
            else:
                match = item
                finding_id = FunctionIndex.make_finding_id(
                    match.source_func.filepath, match.source_func.name,
                    match.existing_func.filepath, match.existing_func.name,
                    source_hash=match.source_func.ast_hash or "",
                    existing_hash=match.existing_func.ast_hash or "",
                )
                if finding_id in self._resolved_ids or config.is_suppressed(
                    finding_id,
                    match.source_func.ast_hash or "",
                    match.existing_func.ast_hash or "",
                ):
                    continue
                serialized = _serialize_match(match, finding_id)
                findings_list.append(serialized)
                self._findings.setdefault(match.source_func.filepath, []).append(serialized)

        return {"findings": findings_list, "total": len(findings_list)}

    def _handle_resolve_finding(self, params: dict) -> dict:
        """Record a verdict for a finding (resolved/intentional/dismissed)."""
        finding_id = params.get("finding_id", "")
        verdict = params.get("verdict", "")
        note = params.get("note", "")

        if not finding_id:
            raise ValueError("'finding_id' required")
        if verdict not in ("resolved", "intentional", "dismissed"):
            raise ValueError(f"Invalid verdict: {verdict}. Use: resolved, intentional, dismissed")

        config = self._config
        if config is None:
            from echo_guard.config import EchoGuardConfig
            config = EchoGuardConfig.load(self.repo_root)

        from echo_guard.index import FunctionIndex

        # Extract hashes from finding_id for re-surfacing logic
        src_hash = existing_hash_str = ""
        try:
            parts_split = finding_id.split("||")
            if len(parts_split) == 2:
                a = parts_split[0].rsplit(":", 1)
                b = parts_split[1].rsplit(":", 1)
                src_hash = a[1] if len(a) == 2 else ""
                existing_hash_str = b[1] if len(b) == 2 else ""
        except Exception:
            pass

        if verdict in ("intentional", "dismissed"):
            config.add_suppressed(finding_id, verdict, src_hash, existing_hash_str)
        elif verdict == "resolved":
            # Track in-memory only — the code is expected to change, so the
            # finding will naturally vanish on the next scan.  No need to
            # persist to echo-guard.yml (avoids config bloat).
            self._resolved_ids.add(finding_id)

        idx = FunctionIndex(self.repo_root)
        try:
            parts = finding_id.split("||")
            if len(parts) == 2:
                a_parts = parts[0].rsplit(":", 1)
                b_parts = parts[1].rsplit(":", 1)
                idx.resolve_finding(
                    finding_id=finding_id,
                    verdict=verdict,
                    source_filepath=a_parts[0] if len(a_parts) == 2 else "",
                    source_function=a_parts[1] if len(a_parts) == 2 else "",
                    source_lineno=None,
                    existing_filepath=b_parts[0] if len(b_parts) == 2 else "",
                    existing_function=b_parts[1] if len(b_parts) == 2 else "",
                    existing_lineno=None,
                    note=note,
                )
        finally:
            idx.close()

        # Remove from in-memory cache
        for filepath in list(self._findings.keys()):
            self._findings[filepath] = [
                f for f in self._findings[filepath]
                if f.get("finding_id") != finding_id
            ]

        return {"resolved": True, "finding_id": finding_id, "verdict": verdict}

    def _handle_get_findings(self, params: dict) -> dict:
        """Return cached findings, optionally filtered by file."""
        filepath = params.get("file") or params.get("filepath")
        if filepath:
            findings = self._findings.get(filepath, [])
        else:
            findings = [f for fs in self._findings.values() for f in fs]
        return {"findings": findings, "total": len(findings)}

    def _handle_reindex(self, params: dict) -> dict:
        """Run incremental reindex."""
        from echo_guard.scanner import index_repo
        from echo_guard.config import EchoGuardConfig

        self._config = EchoGuardConfig.load(self.repo_root)
        index, file_count, func_count, lang_counts = index_repo(
            self.repo_root, config=self._config, incremental=True
        )
        index.close()
        return {
            "reindexed": True,
            "files": file_count,
            "functions": func_count,
        }

    def _handle_shutdown(self, params: dict) -> dict:
        raise ShutdownRequested()


class ShutdownRequested(Exception):
    pass


# ── Entry point ─────────────────────────────────────────────────────────


def run_daemon(repo_root: str | Path) -> None:
    """Start the daemon. Called from the CLI `echo-guard daemon` command."""
    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )
    daemon = EchoGuardDaemon(repo_root)
    daemon.start()
