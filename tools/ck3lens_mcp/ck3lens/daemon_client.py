"""
Daemon Client — IPC client for MCP tools to communicate with QBuilder daemon.

This is the ONLY way MCP tools should request database mutations.
All write operations go through this client to the daemon.

See docs/SINGLE_WRITER_ARCHITECTURE.md for the canonical design.

Usage:
    from ck3lens.daemon_client import daemon
    
    # Check if daemon is running
    if daemon.is_available():
        status = daemon.health()
        
    # Request file processing
    result = daemon.enqueue_files(["/path/to/file.txt"], priority="high")
    
    # Wait for processing to complete
    daemon.await_idle(timeout_ms=30000)
"""

from __future__ import annotations

import json
import logging
import os
import socket
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from qbuilder.endpoint import EndpointInfo, load_endpoint

# Legacy fallback for daemons started before shared endpoint publication.
DEFAULT_IPC_PORT = 19876
PROTOCOL_VERSION = 1

# Connection timeout
CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 30.0

logger = logging.getLogger(__name__)


class DaemonNotAvailableError(Exception):
    """Raised when daemon is not running or unreachable."""
    pass


class DaemonError(Exception):
    """Raised when daemon returns an error response."""
    def __init__(self, code: str, message: str, details: Optional[dict] = None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(f"{code}: {message}")


@dataclass
class DaemonStatus:
    """Status information from daemon health check."""
    daemon_pid: int
    db_path: str
    state: str
    queue_pending: int
    queue_leased: int
    queue_failed: int
    run_id: str = ""
    ipc_port: int = 0
    recent_activity: Optional[dict] = None  # RunActivity snapshot from daemon
    active_clients: Optional[list[dict]] = None
    
    @property
    def is_idle(self) -> bool:
        return self.queue_pending == 0 and self.queue_leased == 0


class DaemonClient:
    """
    IPC client for communicating with the QBuilder daemon.
    
    Singleton pattern - use the global `daemon` instance.
    """
    
    _instance: Optional["DaemonClient"] = None
    
    def __new__(cls) -> "DaemonClient":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._host = "127.0.0.1"
        port_override = os.environ.get("CK3RAVEN_IPC_PORT")
        self._port_override = int(port_override) if port_override else None
        self._port = self._port_override or DEFAULT_IPC_PORT
        self._endpoint: EndpointInfo | None = None
        self._last_check: float = 0
        self._cached_available: bool = False
        self._last_error: str | None = None
        self._client_id = os.environ.get("CK3LENS_INSTANCE_ID", "default")
        self._client_pid = os.getpid()
        self._client_name = f"ck3lens-{self._client_id}"
    
    # =========================================================================
    # Connection Management
    # =========================================================================
    
    def is_available(self, force_check: bool = False) -> bool:
        """
        Check if the daemon is running and reachable.
        
        Caches result for 5 seconds unless force_check=True.
        """
        now = time.time()
        if not force_check and (now - self._last_check) < 5.0:
            return self._cached_available
        
        try:
            self.health()
            self._cached_available = True
            self._last_error = None
        except Exception as exc:
            self._cached_available = False
            self._last_error = f"{type(exc).__name__}: {exc}"

        self._last_check = now
        return self._cached_available

    def diagnostic_state(self) -> dict[str, Any]:
        """Return non-sensitive endpoint and last-failure state for startup errors."""
        published = load_endpoint()

        def endpoint_data(endpoint: EndpointInfo | None) -> dict[str, Any] | None:
            if endpoint is None:
                return None
            return {
                "host": endpoint.host,
                "port": endpoint.port,
                "pid": endpoint.pid,
                "run_id": endpoint.run_id,
            }

        return {
            "port_override": self._port_override,
            "cached_endpoint": endpoint_data(self._endpoint),
            "published_endpoint": endpoint_data(published),
            "last_error": self._last_error,
        }
    
    def _resolve_address(
        self,
        *,
        force_refresh: bool = False,
    ) -> tuple[str, int, EndpointInfo | None]:
        """Resolve the one shared daemon endpoint for this machine."""
        if self._port_override is not None:
            return self._host, self._port_override, None
        if force_refresh or self._endpoint is None:
            self._endpoint = load_endpoint()
        if self._endpoint is not None:
            return self._endpoint.host, self._endpoint.port, self._endpoint
        return self._host, self._port, None

    def _send_once(
        self,
        request: dict,
        *,
        host: str,
        port: int,
        read_timeout: Optional[float],
    ) -> dict:
        try:
            with socket.create_connection((host, port), timeout=CONNECT_TIMEOUT) as sock:
                sock.settimeout(read_timeout if read_timeout is not None else READ_TIMEOUT)
                sock.sendall((json.dumps(request) + '\n').encode('utf-8'))

                buffer = ""
                while '\n' not in buffer:
                    data = sock.recv(4096)
                    if not data:
                        raise DaemonNotAvailableError("Daemon closed connection")
                    buffer += data.decode('utf-8')
        except socket.error as exc:
            raise DaemonNotAvailableError(f"Cannot connect to daemon: {exc}") from exc

        try:
            response = json.loads(buffer.split('\n', 1)[0])
        except json.JSONDecodeError as exc:
            raise DaemonError("BAD_RESPONSE", f"Invalid JSON from daemon: {exc}") from exc

        if not response.get("ok", False):
            error = response.get("error", {})
            raise DaemonError(
                code=error.get("code", "UNKNOWN"),
                message=error.get("message", "Unknown error"),
                details=error.get("details"),
            )
        return response.get("result", {})

    def _send_request(
        self,
        method: str,
        params: Optional[dict] = None,
        *,
        read_timeout: Optional[float] = None,
    ) -> dict:
        """Send one request, refreshing shared endpoint discovery once on failure."""
        request_params = dict(params or {})
        request_params.setdefault("client_id", self._client_id)
        request_params.setdefault("client_pid", self._client_pid)
        request_params.setdefault("client_name", self._client_name)
        request = {
            "v": PROTOCOL_VERSION,
            "id": str(uuid.uuid4())[:8],
            "method": method,
            "params": request_params,
        }

        last_error: DaemonNotAvailableError | None = None
        for force_refresh in (False, True):
            host, port, endpoint = self._resolve_address(force_refresh=force_refresh)
            try:
                result = self._send_once(
                    request,
                    host=host,
                    port=port,
                    read_timeout=read_timeout,
                )
                if method == "health" and endpoint is not None:
                    identity = (
                        int(result.get("daemon_pid", 0)),
                        str(result.get("run_id", "")),
                        int(result.get("ipc_port", 0)),
                    )
                    expected = (endpoint.pid, endpoint.run_id, endpoint.port)
                    if identity != expected:
                        raise DaemonNotAvailableError(
                            "Daemon health identity does not match the published endpoint"
                        )
                return result
            except DaemonNotAvailableError as exc:
                last_error = exc
                self._endpoint = None
                if self._port_override is not None:
                    break

        assert last_error is not None
        raise last_error
    
    # =========================================================================
    # IPC Methods
    # =========================================================================
    
    def health(self) -> DaemonStatus:
        """
        Get daemon health status.
        
        Returns:
            DaemonStatus with daemon info and queue state.
        
        Raises:
            DaemonNotAvailableError: If daemon is not running
        """
        result = self._send_request("health")
        queue = result.get("queue", {})
        
        return DaemonStatus(
            daemon_pid=result.get("daemon_pid", 0),
            db_path=result.get("db_path", ""),
            state=result.get("state", "unknown"),
            queue_pending=queue.get("pending", 0),
            queue_leased=queue.get("leased", 0),
            queue_failed=queue.get("failed", 0),
            run_id=result.get("run_id", ""),
            ipc_port=result.get("ipc_port", 0),
            recent_activity=result.get("recent_activity"),
            active_clients=result.get("clients", {}).get("active", []),
        )
    
    def get_status(self) -> dict:
        """
        Get lightweight daemon status.
        
        Returns:
            Dict with state, active_job, and queue info.
        """
        return self._send_request("get_status")
    
    def enqueue_files(
        self,
        paths: list[str],
        mod_name: Optional[str] = None,
        priority: str = "normal",
        reason: str = "user_request",
    ) -> dict:
        """
        Request daemon to process files.
        
        Args:
            paths: List of absolute file paths to process
            mod_name: Optional mod name (for path resolution)
            priority: "normal" or "high"
            reason: Why files need processing
        
        Returns:
            Dict with enqueued/deduped counts
        """
        params = {
            "paths": [str(p) for p in paths],
            "priority": priority,
            "reason": reason,
        }
        if mod_name:
            params["mod_name"] = mod_name
        
        return self._send_request("enqueue_files", params)
    
    def playset_scan(self, playset_file: Optional[str] = None) -> dict:
        """Prepare the active or explicitly targeted canonical playset."""
        params = {"playset_file": playset_file} if playset_file else {}
        return self._send_request("playset_scan", params)
    
    def await_idle(self, timeout_ms: int = 30000) -> dict:
        """
        Wait for daemon to finish processing all pending work.
        
        Args:
            timeout_ms: Maximum time to wait in milliseconds
        
        Returns:
            Dict with idle status
        
        The socket read timeout tracks the caller's wait budget plus a small
        margin, so the daemon's own deadline — not a hardcoded socket
        timeout — terminates the wait. (A hardcoded 30s read timeout would
        false-disconnect for caller timeouts above 30s.)
        """
        read_timeout = (timeout_ms / 1000.0) + 5.0
        return self._send_request(
            "await_idle",
            {"timeout_ms": timeout_ms},
            read_timeout=read_timeout,
        )

    def retry_failed(self, limit: Optional[int] = None) -> dict:
        """Reset failed build_queue rows back to pending inside the daemon."""
        params: dict[str, Any] = {}
        if limit is not None:
            params["limit"] = limit
        return self._send_request("retry_failed", params)
    
    def shutdown(self, graceful: bool = True) -> dict:
        """
        Request daemon shutdown.
        
        Args:
            graceful: If True, wait for current work to complete
        
        Returns:
            Acknowledgment dict
        """
        return self._send_request("shutdown", {"graceful": graceful})

    def unregister_client(self) -> dict:
        """Best-effort release of this MCP instance's daemon lease."""
        return self._send_request("unregister_client", {})
    
    # =========================================================================
    # Convenience Methods for MCP Tools
    # =========================================================================
    
    def notify_file_changed(self, path: str, mod_name: Optional[str] = None) -> dict:
        """
        Notify daemon that a file was changed (after MCP file write).
        
        This is the method MCP tools should call after writing to mod files.
        """
        return self.enqueue_files(
            paths=[path],
            mod_name=mod_name,
            priority="high",
            reason="file_changed",
        )

    def delete_data(
        self,
        target: str,
        scope: str,
        ids: Optional[list] = None,
        content_version_ids: Optional[list[str]] = None,
    ) -> dict:
        """
        Request daemon to delete database records.

        This is the ONLY way MCP tools should perform DB mutations.
        Preview (SELECT counts) is done locally by MCP; confirmed deletes
        are routed here to the daemon which holds the write connection.

        Args:
            target: What to delete (asts, symbols, refs, files,
                    content_versions, lookups, build_tracking)
            scope: all | by_ids | by_content_version
            ids: Required when scope=by_ids
            content_version_ids: Required when scope=by_content_version

        Returns:
            Dict with rows_deleted, cascade info, etc.

        Raises:
            DaemonNotAvailableError: If daemon is not running
            DaemonError: If daemon returns an error
        """
        params: dict[str, Any] = {"target": target, "scope": scope}
        if ids is not None:
            params["ids"] = ids
        if content_version_ids is not None:
            params["content_version_ids"] = content_version_ids
        return self._send_request("delete_data", params)
    
    def request_rebuild(self, paths: list[str]) -> dict:
        """
        Request rebuild of specific files.
        
        This is for user-initiated rebuilds.
        """
        return self.enqueue_files(
            paths=paths,
            priority="normal",
            reason="user_request",
        )
    
    def get_queue_status(self) -> dict:
        """
        Get queue status for UI display.
        
        Returns a dict suitable for status bar display.
        Includes recent_activity snapshot from RunActivity tracker
        when daemon is connected.
        """
        try:
            status = self.health()
            result = {
                "connected": True,
                "daemon_state": status.state,
                "daemon_pid": status.daemon_pid,
                "run_id": status.run_id,
                "ipc_port": status.ipc_port,
                "queue": {
                    "pending": status.queue_pending,
                    "leased": status.queue_leased,
                    "failed": status.queue_failed,
                },
            }
            if status.recent_activity:
                result["recent_activity"] = status.recent_activity
            result["active_clients"] = status.active_clients or []
            return result
        except DaemonNotAvailableError:
            return {
                "connected": False,
                "daemon_state": "not_running",
                "queue": {"pending": 0, "leased": 0, "failed": 0},
            }


# Singleton instance - import this in MCP tools
daemon = DaemonClient()
