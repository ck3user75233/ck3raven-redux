"""
IPC Server — QBuilder Daemon's API for MCP clients.

This module implements the NDJSON socket server that MCP tools use
to request database mutations.

See docs/SINGLE_WRITER_ARCHITECTURE.md for the canonical IPC contract.

Transport:
    - TCP socket on localhost (configurable port)
    - NDJSON framing (one JSON object per line)
    - No authentication required (localhost only)

Usage:
    from qbuilder.ipc_server import DaemonIPCServer
    
    server = DaemonIPCServer(port=9876, conn=db_connection)
    server.start()  # Runs in background thread
    ...
    server.stop()
"""

from __future__ import annotations

import json
import logging
import os
import socket
import sqlite3
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

# Default port for daemon IPC
DEFAULT_IPC_PORT = 19876  # High port, unlikely to conflict
CLIENT_LEASE_TTL_SECONDS = 90.0
_MUTATING_METHODS = frozenset({"enqueue_files", "retry_failed", "playset_scan", "delete_data"})


class RunActivity:
    """
    Thread-safe tracker for daemon run activity.
    
    Shared between the build worker (main thread, writes) and the IPC server
    (handler thread, reads). All access is guarded by a lock.
    
    This solves the problem where `ck3_qbuilder(command="status")` returns
    "idle, pending:0" with no evidence of what work was done. Now the status
    response includes recent_activity showing items processed, timing, etc.
    """
    
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._run_id: str = ""
        self._items_processed: int = 0
        self._completed: int = 0
        self._errors: int = 0
        self._first_item_at: Optional[str] = None
        self._last_item_at: Optional[str] = None
        self._idle_since: Optional[str] = None
        self._started_at: Optional[str] = None
        self._state: str = "starting"  # starting, processing, idle, shutdown
        self._mods_discovered: list[str] = []
    
    def set_run_id(self, run_id: str) -> None:
        """Set the daemon run ID."""
        with self._lock:
            self._run_id = run_id
            self._started_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    
    def record_item(self, status: str = "completed") -> None:
        """Record a processed item. Called by build worker after each item."""
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        with self._lock:
            self._items_processed += 1
            if status == "completed":
                self._completed += 1
            else:
                self._errors += 1
            if self._first_item_at is None:
                self._first_item_at = now
            self._last_item_at = now
            self._state = "processing"
            self._idle_since = None
    
    def set_idle(self) -> None:
        """Mark the daemon as idle. Called when worker enters poll-wait."""
        with self._lock:
            if self._state != "idle":
                self._state = "idle"
                self._idle_since = time.strftime("%Y-%m-%dT%H:%M:%S")
    
    def set_state(self, state: str) -> None:
        """Set daemon state (starting, processing, idle, shutdown).
        
        If transitioning away from idle, clears idle_since.
        """
        with self._lock:
            self._state = state
            if state != "idle":
                self._idle_since = None
    
    def record_discovery(self, mod_summary: str) -> None:
        """Record a mod discovered during scan. E.g. 'Dev Debuff (4 files)'."""
        with self._lock:
            self._mods_discovered.append(mod_summary)
    
    def get_snapshot(self) -> dict:
        """Return a snapshot of current activity for IPC responses."""
        with self._lock:
            result: dict[str, Any] = {
                "state": self._state,
            }
            if self._run_id:
                result["run_id"] = self._run_id
            if self._started_at:
                result["started_at"] = self._started_at
            if self._items_processed > 0:
                result["items_processed"] = self._items_processed
                result["completed"] = self._completed
                result["errors_this_run"] = self._errors
                if self._first_item_at:
                    result["first_item_at"] = self._first_item_at
                if self._last_item_at:
                    result["last_item_at"] = self._last_item_at
            if self._idle_since:
                result["idle_since"] = self._idle_since
            if self._mods_discovered:
                result["mods_discovered"] = list(self._mods_discovered)
            return result

# Socket file for Unix domain sockets (alternative to TCP)
DEFAULT_SOCKET_PATH = Path.home() / ".ck3raven" / "daemon.sock"

# Protocol version
PROTOCOL_VERSION = 1

logger = logging.getLogger(__name__)


def _combined_queue_counts(counts: dict) -> dict:
    """Combine get_queue_counts() output across build and discovery queues.

    Returns combined totals (pending/processing/failed) plus per-queue
    breakdowns. Pending/processing work in EITHER queue means the daemon
    is not idle.
    """
    def _split(queue: dict) -> dict:
        return {
            "pending": queue.get('pending', 0),
            "processing": queue.get('processing', 0),
            "failed": queue.get('error', 0),
        }

    build = _split(counts.get('build', {}))
    discovery = _split(counts.get('discovery', {}))

    return {
        "pending": build["pending"] + discovery["pending"],
        "processing": build["processing"] + discovery["processing"],
        "failed": build["failed"] + discovery["failed"],
        "build": build,
        "discovery": discovery,
    }


@dataclass
class IPCRequest:
    """Parsed IPC request."""
    version: int
    id: str
    method: str
    params: dict = field(default_factory=dict)


@dataclass
class IPCResponse:
    """IPC response to be sent back to client."""
    id: str
    ok: bool
    result: Optional[dict] = None
    error: Optional[dict] = None
    
    def to_json(self) -> str:
        resp = {"v": PROTOCOL_VERSION, "id": self.id, "ok": self.ok}
        if self.ok:
            resp["result"] = self.result or {}
        else:
            resp["error"] = self.error or {"code": "UNKNOWN", "message": "Unknown error"}
        return json.dumps(resp)


class IPCHandlerError(Exception):
    """Structured handler failure that preserves machine-readable code."""

    def __init__(self, code: str, message: str, details: Optional[dict] = None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


class DaemonIPCServer:
    """
    NDJSON socket server for daemon IPC.
    
    Handles requests from MCP clients and dispatches to handlers.
    Runs in a background thread to not block the build worker.
    """
    
    def __init__(
        self,
        conn: sqlite3.Connection,
        port: int = DEFAULT_IPC_PORT,
        host: str = "127.0.0.1",
        db_path: Optional[Path] = None,
        shutdown_callback: Optional[Callable[[], None]] = None,
        run_activity: Optional[RunActivity] = None,
        mutation_lock: Optional[threading.RLock] = None,
    ):
        self.conn = conn  # Main thread connection (not used in handlers)
        self.port = port
        self.host = host
        self.db_path = db_path  # Store for thread-local connections
        self.shutdown_callback = shutdown_callback  # Called on shutdown request
        self.run_activity = run_activity  # Shared activity tracker (thread-safe)
        self.mutation_lock = mutation_lock or threading.RLock()
        
        self._server_socket: Optional[socket.socket] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._handlers: dict[str, Callable] = {}
        self._thread_local = threading.local()  # Thread-local storage
        self._clients_lock = threading.Lock()
        self._clients: dict[str, dict[str, Any]] = {}
        
        # Register built-in handlers
        self._register_handlers()
    
    def _get_handler_conn(self) -> sqlite3.Connection:
        """Get thread-local read-only connection for handler queries.
        
        IPC handlers run in a different thread than the main daemon.
        SQLite connections are not thread-safe, so we create a dedicated
        read-only connection for the handler thread.
        """
        if not hasattr(self._thread_local, 'conn') or self._thread_local.conn is None:
            if self.db_path:
                # Open read-only - handlers only query, never write
                db_uri = f"file:{self.db_path}?mode=ro"
                self._thread_local.conn = sqlite3.connect(db_uri, uri=True, timeout=30.0)
            else:
                # Fallback: try to get path from main conn (may not work)
                raise RuntimeError("db_path not set - cannot create handler connection")
        return self._thread_local.conn
    
    def _get_handler_write_conn(self) -> sqlite3.Connection:
        """Get a NEW read-write connection for handler operations that need to write.
        
        Unlike _get_handler_conn(), this creates a fresh connection each call.
        The caller MUST close this connection when done (use context manager).
        
        This follows the per-request connection model for write operations,
        which is simpler and safer than thread-local caching for writes.
        
        Uses busy_timeout to handle concurrent access during rebuilds.
        """
        if not self.db_path:
            raise RuntimeError("db_path not set - cannot create write connection")

        from qbuilder.writer_lock import require_current_process_writer
        require_current_process_writer(self.db_path)
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.execute("PRAGMA busy_timeout = 30000")  # 30 second busy wait
        return conn
    
    def _register_handlers(self) -> None:
        """Register IPC method handlers."""
        self._handlers = {
            "health": self._handle_health,
            "get_status": self._handle_get_status,
            "enqueue_files": self._handle_enqueue_files,
            "retry_failed": self._handle_retry_failed,
            "playset_scan": self._handle_playset_scan,
            "await_idle": self._handle_await_idle,
            "shutdown": self._handle_shutdown,
            "unregister_client": self._handle_unregister_client,
            "delete_data": self._handle_delete_data,
        }
    
    def _prune_stale_clients(self) -> None:
        """Drop client leases that have not heartbeated recently."""
        cutoff = time.time() - CLIENT_LEASE_TTL_SECONDS
        with self._clients_lock:
            stale_ids = [
                client_id
                for client_id, info in self._clients.items()
                if float(info.get("last_seen", 0.0)) < cutoff
            ]
            for client_id in stale_ids:
                self._clients.pop(client_id, None)

    def _touch_client(self, request: IPCRequest) -> None:
        """Refresh client lease from request metadata when present."""
        client_id = str(request.params.get("client_id", "") or "").strip()
        if not client_id:
            return

        now = time.time()
        client_info = {
            "client_id": client_id,
            "pid": request.params.get("client_pid"),
            "server_name": request.params.get("client_name"),
            "last_seen": now,
        }
        with self._clients_lock:
            self._clients[client_id] = client_info

    def _get_active_clients(self, *, exclude: str | None = None) -> list[dict[str, Any]]:
        """Return current live client leases, optionally excluding one client."""
        self._prune_stale_clients()
        with self._clients_lock:
            result = []
            for client_id, info in self._clients.items():
                if exclude and client_id == exclude:
                    continue
                result.append(dict(info))
            return result

    def start(self) -> None:
        """Start the IPC server in a background thread."""
        if self._running:
            return
        
        self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_socket.bind((self.host, self.port))
        self.port = int(self._server_socket.getsockname()[1])
        self._server_socket.listen(5)
        self._server_socket.settimeout(1.0)  # Allow periodic shutdown checks
        
        self._running = True
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()
        
        logger.info(f"IPC server listening on {self.host}:{self.port}")
    
    def stop(self) -> None:
        """Stop the IPC server."""
        self._running = False
        if self._server_socket:
            try:
                self._server_socket.close()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=5.0)
    
    def _accept_loop(self) -> None:
        """Main accept loop running in background thread."""
        assert self._server_socket is not None, "start() must be called first"
        while self._running:
            try:
                client_sock, addr = self._server_socket.accept()
                # Handle each client in a separate thread
                client_thread = threading.Thread(
                    target=self._handle_client,
                    args=(client_sock, addr),
                    daemon=True,
                )
                client_thread.start()
            except socket.timeout:
                continue  # Check _running flag
            except Exception as e:
                if self._running:
                    logger.error(f"Accept error: {e}")
    
    def _handle_client(self, sock: socket.socket, addr: tuple) -> None:
        """Handle a single client connection."""
        logger.debug(f"Client connected from {addr}")
        
        try:
            sock.settimeout(30.0)  # Client timeout
            buffer = ""
            
            while self._running:
                # Read data
                try:
                    data = sock.recv(4096)
                    if not data:
                        break  # Client disconnected
                    buffer += data.decode('utf-8')
                except socket.timeout:
                    continue
                
                # Process complete lines (NDJSON)
                while '\n' in buffer:
                    line, buffer = buffer.split('\n', 1)
                    line = line.strip()
                    if not line:
                        continue
                    
                    response = self._process_request(line)
                    sock.sendall((response + '\n').encode('utf-8'))
                    
        except Exception as e:
            logger.error(f"Client error: {e}")
        finally:
            sock.close()
            logger.debug(f"Client disconnected: {addr}")
    
    def _process_request(self, line: str) -> str:
        """Process a single request line and return response JSON."""
        try:
            data = json.loads(line)
            request = IPCRequest(
                version=data.get("v", 1),
                id=data.get("id", "unknown"),
                method=data.get("method", ""),
                params=data.get("params", {}),
            )
        except json.JSONDecodeError as e:
            return IPCResponse(
                id="unknown",
                ok=False,
                error={"code": "BAD_JSON", "message": str(e)},
            ).to_json()
        
        if request.method not in {"shutdown", "unregister_client"}:
            self._touch_client(request)

        # Dispatch to handler
        handler = self._handlers.get(request.method)
        if not handler:
            return IPCResponse(
                id=request.id,
                ok=False,
                error={"code": "UNKNOWN_METHOD", "message": f"Unknown method: {request.method}"},
            ).to_json()
        
        try:
            if request.method in _MUTATING_METHODS:
                with self.mutation_lock:
                    result = handler(request)
            else:
                result = handler(request)
            return IPCResponse(id=request.id, ok=True, result=result).to_json()
        except IPCHandlerError as e:
            return IPCResponse(
                id=request.id,
                ok=False,
                error={"code": e.code, "message": e.message, "details": e.details},
            ).to_json()
        except Exception as e:
            logger.error(f"Handler error: {e}\n{traceback.format_exc()}")
            return IPCResponse(
                id=request.id,
                ok=False,
                error={"code": "INTERNAL", "message": str(e)},
            ).to_json()
    
    # =========================================================================
    # IPC Method Handlers
    # =========================================================================
    
    def _handle_health(self, request: IPCRequest) -> dict:
        """Handle health check request."""
        from qbuilder.schema import get_queue_counts
        
        handler_conn = self._get_handler_conn()
        queue = _combined_queue_counts(get_queue_counts(handler_conn))
        
        # Get state from run_activity if available, else fallback
        activity_snapshot = self.run_activity.get_snapshot() if self.run_activity else {}
        state = activity_snapshot.get("state", "idle")
        
        # Queued work in either queue means the daemon is busy, even when the
        # activity snapshot still says "idle" (stale between runs).
        if state == "idle":
            if queue["discovery"]["pending"] > 0 or queue["discovery"]["processing"] > 0:
                state = "discovering"
            elif queue["build"]["pending"] > 0 or queue["build"]["processing"] > 0:
                state = "building"
        
        active_clients = self._get_active_clients()
        result = {
            "daemon_pid": os.getpid(),
            "run_id": activity_snapshot.get("run_id", ""),
            "ipc_port": self.port,
            "db_path": str(self.db_path) if self.db_path else 'unknown',
            "writer_lock": "held",
            "state": state,
            "queue": {
                "pending": queue["pending"],
                "leased": queue["processing"],
                "failed": queue["failed"],
                "discovery": queue["discovery"],
                "build": queue["build"],
            },
            "clients": {
                "count": len(active_clients),
                "active": active_clients,
            },
            "versions": {
                "protocol": PROTOCOL_VERSION,
            },
        }
        
        if activity_snapshot:
            result["recent_activity"] = activity_snapshot
        
        return result
    
    def _handle_get_status(self, request: IPCRequest) -> dict:
        """Handle status query."""
        from qbuilder.schema import get_queue_counts
        
        handler_conn = self._get_handler_conn()
        queue = _combined_queue_counts(get_queue_counts(handler_conn))
        
        activity_snapshot = self.run_activity.get_snapshot() if self.run_activity else {}
        state = activity_snapshot.get("state", "idle")
        
        # Queued work in either queue means the daemon is busy, even when the
        # activity snapshot still says "idle" (stale between runs).
        if state == "idle":
            if queue["discovery"]["pending"] > 0 or queue["discovery"]["processing"] > 0:
                state = "discovering"
            elif queue["build"]["pending"] > 0 or queue["build"]["processing"] > 0:
                state = "building"
        
        result = {
            "state": state,
            "active_job": None,
            "queue": {
                "pending": queue["pending"],
                "leased": queue["processing"],
                "failed": queue["failed"],
                "discovery": queue["discovery"],
                "build": queue["build"],
            },
        }
        
        if activity_snapshot:
            result["recent_activity"] = activity_snapshot
        
        return result
    
    def _handle_enqueue_files(self, request: IPCRequest) -> dict:
        """Handle file enqueue request.

        Paths must already be caller-resolved relative paths. The daemon does not
        perform mod-root lookup or canonical address resolution.
        """
        from qbuilder.api import enqueue_file

        paths = request.params.get("paths", [])
        priority = request.params.get("priority", "normal")
        reason = request.params.get("reason", "user_request")
        mod_name = request.params.get("mod_name", "unknown")

        priority_int = 1 if priority == "high" else 0

        enqueued = 0
        deduped = 0
        failures: list[dict[str, str]] = []

        if not mod_name or mod_name == "unknown":
            return {
                "enqueued": 0,
                "deduped": 0,
                "reason": reason,
                "failures": [{
                    "path": "",
                    "reason": "enqueue_files requires mod_name from the caller session context",
                }],
            }

        for path_str in paths:
            rel_path = str(path_str).replace("\\", "/").strip("/")
            raw_path = Path(path_str)

            if not rel_path:
                failures.append({"path": path_str, "reason": "empty relative path"})
                continue

            if raw_path.is_absolute() or rel_path.startswith("mod:") or rel_path.startswith("root:"):
                failures.append({
                    "path": path_str,
                    "reason": "enqueue_files expects caller-resolved relative paths, not host or canonical paths",
                })
                continue

            result = enqueue_file(
                mod_name=mod_name,
                rel_path=rel_path,
                priority=priority_int,
            )
            if result.success:
                if result.already_queued:
                    deduped += 1
                else:
                    enqueued += 1
            else:
                failures.append({
                    "path": path_str,
                    "reason": result.message,
                })

        return {
            "enqueued": enqueued,
            "deduped": deduped,
            "reason": reason,
            "failures": failures,
        }
    
    def _handle_retry_failed(self, request: IPCRequest) -> dict:
        """Reset failed build_queue rows back to pending."""
        from qbuilder.api import retry_failed_builds

        limit = request.params.get("limit")
        return retry_failed_builds(db_path=self.db_path, limit=limit)

    def _handle_playset_scan(self, request: IPCRequest) -> dict:
        """Prepare an active or explicitly targeted playset for activation.

        CVID creation and discovery_queue enqueue commit synchronously, so the
        response can report identity readiness immediately. File enumeration
        itself runs asynchronously in the daemon-owned discovery worker, which
        drains discovery_queue and populates build_queue.
        """
        from ck3lens.paths import ROOT_CK3RAVEN_DATA
        from qbuilder.discovery import IdentityConflictError, playset_scan

        playset_file = request.params.get("playset_file")
        playset_path = None
        if playset_file:
            filename = Path(str(playset_file)).name
            playset_path = ROOT_CK3RAVEN_DATA / "playsets" / filename

        write_conn = self._get_handler_write_conn()
        try:
            try:
                scan_result = playset_scan(write_conn, playset_path=playset_path)
            except IdentityConflictError as exc:
                raise IPCHandlerError(
                    code="IDENTITY_CONFLICT",
                    message=str(exc),
                    details={
                        "hint": "Resolve the duplicate mod identity, then retry the scan",
                        "action": "Run ck3_qbuilder(command='discover') after repair",
                    },
                ) from exc
            write_conn.commit()
            return {
                **scan_result,
                "scheduled": True,
                "ingestion_started": True,
                "discovery_queued": scan_result.get("discovery_tasks_enqueued", 0),
            }
        finally:
            write_conn.close()

    def _handle_await_idle(self, request: IPCRequest) -> dict:
        """Handle await_idle request - waits for all queues to drain."""
        from qbuilder.schema import get_queue_counts
        
        timeout_ms = request.params.get("timeout_ms", 30000)
        deadline = time.time() + (timeout_ms / 1000.0)
        
        handler_conn = self._get_handler_conn()
        
        while time.time() < deadline:
            queue = _combined_queue_counts(get_queue_counts(handler_conn))
            
            if queue["pending"] == 0 and queue["processing"] == 0:
                return {
                    "idle": True,
                    "queue_pending": 0,
                    "queue_processing": 0,
                    "discovery": queue["discovery"],
                    "build": queue["build"],
                }
            
            time.sleep(0.5)  # Poll interval
        
        queue = _combined_queue_counts(get_queue_counts(handler_conn))
        return {
            "idle": False,
            "queue_pending": queue["pending"],
            "queue_processing": queue["processing"],
            "timeout": True,
            "discovery": queue["discovery"],
            "build": queue["build"],
        }
    
    def _handle_unregister_client(self, request: IPCRequest) -> dict:
        """Remove a client lease when an MCP instance exits."""
        client_id = str(request.params.get("client_id", "") or "").strip()
        if client_id:
            with self._clients_lock:
                self._clients.pop(client_id, None)
        remaining = self._get_active_clients()
        return {
            "unregistered": bool(client_id),
            "remaining_clients": len(remaining),
            "active_clients": remaining,
        }

    def _handle_shutdown(self, request: IPCRequest) -> dict:
        """Handle shutdown request."""
        graceful = request.params.get("graceful", True)
        requester_id = str(request.params.get("client_id", "") or "").strip()
        other_clients = self._get_active_clients(exclude=requester_id or None)
        if other_clients:
            return {
                "acknowledged": False,
                "graceful": graceful,
                "denied_reason": "other_clients_active",
                "active_clients": other_clients,
            }
        
        # Signal shutdown (the main loop should check this)
        self._running = False
        
        # Invoke callback to signal main daemon/worker
        if self.shutdown_callback:
            self.shutdown_callback()
        
        return {"acknowledged": True, "graceful": graceful, "active_clients": []}

    def _handle_delete_data(self, request: IPCRequest) -> dict:
        """Handle delete_data request — executes DB mutations for MCP.

        This is the ONLY path for MCP-initiated deletions. The MCP server
        sends preview counts (read-only SELECTs) itself; this handler only
        runs the confirmed DELETE statements.

        Params:
            target: str — what to delete (asts, symbols, refs, files,
                    content_versions, lookups, build_tracking)
            scope: str — all | by_ids | by_content_version
            ids: list[int|str] — required when scope=by_ids
            content_version_ids: list[str] — required when scope=by_content_version
        """
        target = request.params.get("target")
        scope = request.params.get("scope", "all")
        ids = request.params.get("ids")
        content_version_ids = request.params.get("content_version_ids")

        if not target:
            raise ValueError("'target' parameter required")

        write_conn = self._get_handler_write_conn()
        try:
            cur = write_conn.cursor()
            cur.execute("PRAGMA foreign_keys = ON")

            # ── helpers ──────────────────────────────────────────────
            def expand_ids(id_list: list) -> list[int]:
                expanded = []
                for item in id_list:
                    if isinstance(item, int):
                        expanded.append(item)
                    elif isinstance(item, str) and "-" in item:
                        start, end = item.split("-", 1)
                        expanded.extend(range(int(start), int(end) + 1))
                    else:
                        expanded.append(int(item))
                return expanded

            def where(table: str, id_col: str) -> tuple[str, list]:
                if scope == "all":
                    return "", []
                if scope == "by_ids":
                    if not ids:
                        raise ValueError("scope='by_ids' requires 'ids'")
                    exp = [str(item) for item in ids] if table == "content_versions" else expand_ids(ids)
                    ph = ",".join("?" * len(exp))
                    return f"WHERE {id_col} IN ({ph})", exp
                if scope == "by_content_version":
                    if not content_version_ids:
                        raise ValueError("scope='by_content_version' requires 'content_version_ids'")
                    ph = ",".join("?" * len(content_version_ids))
                    if table == "content_versions":
                        return f"WHERE content_version_id IN ({ph})", list(content_version_ids)
                    if table == "files":
                        return f"WHERE content_version_id IN ({ph})", list(content_version_ids)
                    if table == "asts":
                        return (f"WHERE file_id IN (SELECT file_id FROM files "
                                f"WHERE content_version_id IN ({ph}))", list(content_version_ids))
                    if table in ("symbols", "refs"):
                        return (f"WHERE ast_id IN (SELECT ast_id FROM asts "
                                f"WHERE file_id IN (SELECT file_id FROM files "
                                f"WHERE content_version_id IN ({ph})))", list(content_version_ids))
                    return (f"WHERE file_id IN (SELECT file_id FROM files "
                            f"WHERE content_version_id IN ({ph}))", list(content_version_ids))
                raise ValueError(f"Unknown scope: {scope}")

            # ── target dispatch ──────────────────────────────────────
            result: dict[str, Any] = {"target": target, "scope": scope}

            if target in ("asts", "symbols", "refs"):
                w, p = where(target, f"{target[:-1]}_id")  # ast_id, symbol_id, ref_id
                cur.execute(f"DELETE FROM {target} {w}", p)
                result["rows_deleted"] = cur.rowcount

            elif target == "files":
                w, p = where("files", "file_id")
                # Cascade counts before delete
                cur.execute(f"SELECT COUNT(*) FROM asts WHERE file_id IN (SELECT file_id FROM files {w})", p)
                asts_count = cur.fetchone()[0]
                cur.execute(f"SELECT COUNT(*) FROM symbols WHERE ast_id IN (SELECT ast_id FROM asts WHERE file_id IN (SELECT file_id FROM files {w}))", p)
                symbols_count = cur.fetchone()[0]
                cur.execute(f"SELECT COUNT(*) FROM refs WHERE ast_id IN (SELECT ast_id FROM asts WHERE file_id IN (SELECT file_id FROM files {w}))", p)
                refs_count = cur.fetchone()[0]
                cur.execute(f"DELETE FROM files {w}", p)
                result["rows_deleted"] = cur.rowcount
                result["cascade"] = {"asts_deleted": asts_count, "symbols_deleted": symbols_count, "refs_deleted": refs_count}

            elif target == "content_versions":
                w, p = where("content_versions", "content_version_id")
                cur.execute(f"SELECT content_version_id FROM content_versions {w}", p)
                cv_ids = [r[0] for r in cur.fetchall()]
                if cv_ids:
                    ph = ",".join("?" * len(cv_ids))
                    cur.execute(f"SELECT COUNT(*) FROM files WHERE content_version_id IN ({ph})", cv_ids)
                    files_count = cur.fetchone()[0]
                    cur.execute(f"SELECT COUNT(*) FROM asts WHERE file_id IN (SELECT file_id FROM files WHERE content_version_id IN ({ph}))", cv_ids)
                    asts_count = cur.fetchone()[0]
                    cur.execute(f"SELECT COUNT(*) FROM symbols WHERE ast_id IN (SELECT ast_id FROM asts WHERE file_id IN (SELECT file_id FROM files WHERE content_version_id IN ({ph})))", cv_ids)
                    symbols_count = cur.fetchone()[0]
                    cur.execute(f"SELECT COUNT(*) FROM refs WHERE ast_id IN (SELECT ast_id FROM asts WHERE file_id IN (SELECT file_id FROM files WHERE content_version_id IN ({ph})))", cv_ids)
                    refs_count = cur.fetchone()[0]
                    cur.execute(f"DELETE FROM files WHERE content_version_id IN ({ph})", cv_ids)
                    cur.execute(f"DELETE FROM content_versions {w}", p)
                    result["rows_deleted"] = cur.rowcount
                    result["cascade"] = {"files_deleted": files_count, "asts_deleted": asts_count, "symbols_deleted": symbols_count, "refs_deleted": refs_count}
                else:
                    result["rows_deleted"] = 0

            elif target == "lookups":
                lookup_tables = ["trait_lookups", "event_lookups", "decision_lookups", "culture_lookups", "religion_lookups"]
                total = 0
                details: dict[str, int] = {}
                for tbl in lookup_tables:
                    try:
                        cur.execute(f"DELETE FROM {tbl}")
                        details[tbl] = cur.rowcount
                        total += cur.rowcount
                    except Exception:
                        details[tbl] = 0
                result["rows_deleted"] = total
                result["details"] = details

            elif target == "build_tracking":
                cur.execute("DELETE FROM build_lock")
                cur.execute("DELETE FROM builder_steps")
                steps = cur.rowcount
                cur.execute("DELETE FROM builder_runs")
                runs = cur.rowcount
                result["rows_deleted"] = runs + steps
                result["details"] = {"builder_runs": runs, "builder_steps": steps}

            else:
                raise ValueError(f"Unknown target: {target}")

            write_conn.commit()
            result["success"] = True
            return result
        except Exception:
            write_conn.rollback()
            raise
        finally:
            write_conn.close()


def get_requested_ipc_port() -> int:
    """Return a configured port, or 0 for an OS-assigned daemon endpoint."""
    return int(os.environ.get("CK3RAVEN_IPC_PORT", "0"))


def get_ipc_port() -> int:
    """Return the legacy fixed port for compatibility-only callers."""
    return int(os.environ.get("CK3RAVEN_IPC_PORT", DEFAULT_IPC_PORT))


def get_ipc_address() -> tuple[str, int]:
    """Get the IPC server address."""
    return ("127.0.0.1", get_ipc_port())
