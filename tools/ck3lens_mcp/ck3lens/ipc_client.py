"""
VS Code IPC Client

TCP client that connects to the VS Code extension's diagnostics server.
Allows MCP tools to access VS Code APIs like diagnostics, open files, etc.

Protocol: JSON-RPC over TCP (newline-delimited)
Default port: 9847 (auto-discovered from port file)
"""

import json
import socket
import os
from typing import Any, Optional, Dict, List
from .paths import ROOT_CK3RAVEN_DATA


class VSCodeIPCError(Exception):
    """Error communicating with VS Code IPC server"""
    pass


class VSCodeIPCClient:
    """
    Client for communicating with VS Code extension's diagnostics server.
    
    Usage:
        client = VSCodeIPCClient()
        client.connect()
        result = client.get_diagnostics("/path/to/file.py")
        client.close()
        
    Or as context manager:
        with VSCodeIPCClient() as client:
            result = client.get_diagnostics("/path/to/file.py")
    """
    
    HOST = "127.0.0.1"
    TIMEOUT = 10.0
    
    def __init__(self, port: Optional[int] = None, timeout: float = TIMEOUT):
        """
        Initialize the IPC client.
        
        Args:
            port: TCP port to connect to. If None, auto-discovers from port file.
            timeout: Socket timeout in seconds.
        """
        self.port = port
        self.timeout = timeout
        self._socket: Optional[socket.socket] = None
        self._request_id = 0
    
    def __enter__(self) -> "VSCodeIPCClient":
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
    
    def _discover_port(self, retry: bool = True) -> int:
        """Discover the owning VS Code window's injected or instance-scoped port."""
        import time

        env_port = os.environ.get("CK3LENS_IPC_PORT", "")
        if env_port:
            try:
                port = int(env_port)
            except ValueError as exc:
                raise VSCodeIPCError("CK3LENS_IPC_PORT must be an integer") from exc
            if not 1 <= port <= 65535:
                raise VSCodeIPCError("CK3LENS_IPC_PORT must be between 1 and 65535")
            return port

        instance_id = os.environ.get("CK3LENS_INSTANCE_ID", "")
        if not instance_id:
            raise VSCodeIPCError(
                "IPC discovery requires CK3LENS_IPC_PORT or CK3LENS_INSTANCE_ID; "
                "cross-window fallback discovery is disabled"
            )

        port_file = ROOT_CK3RAVEN_DATA / f"ipc_port_{instance_id}"
        max_attempts = 60 if retry else 1

        for attempt in range(max_attempts):
            if port_file.exists():
                try:
                    data = json.loads(port_file.read_text(encoding="utf-8"))
                    file_instance_id = data.get("instanceId")
                    port = data.get("port")
                    if file_instance_id not in (None, instance_id):
                        raise VSCodeIPCError(
                            f"IPC port file belongs to {file_instance_id}, expected {instance_id}"
                        )
                    if isinstance(port, int) and not isinstance(port, bool) and 1 <= port <= 65535:
                        return port
                except VSCodeIPCError:
                    raise
                except (json.JSONDecodeError, OSError, TypeError):
                    pass

            if retry and attempt < max_attempts - 1:
                time.sleep(2.0)

        raise VSCodeIPCError(f"Instance-scoped IPC port file unavailable: {port_file}")
    
    def connect(self) -> None:
        """Establish connection to the VS Code IPC server."""
        if self._socket:
            return
        
        port = self.port or self._discover_port()
        
        try:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._socket.settimeout(self.timeout)
            self._socket.connect((self.HOST, port))
        except ConnectionRefusedError:
            raise VSCodeIPCError(
                f"Cannot connect to VS Code IPC server on port {port}. "
                "Make sure the CK3 Lens extension is active in VS Code."
            )
        except socket.timeout:
            raise VSCodeIPCError(f"Connection to VS Code IPC server timed out")
    
    def close(self) -> None:
        """Close the connection."""
        if self._socket:
            try:
                self._socket.close()
            except:
                pass
            self._socket = None

    def wait_for_disconnect(self) -> None:
        """Block until the connected VS Code diagnostics server closes the socket."""
        lifecycle_socket = self._socket
        if lifecycle_socket is None:
            raise VSCodeIPCError("Not connected. Call connect() first.")

        lifecycle_socket.settimeout(None)
        while self._socket is lifecycle_socket:
            try:
                data = lifecycle_socket.recv(1)
            except OSError:
                return
            if not data:
                return
    
    def _send_request(self, method: str, params: Optional[Dict] = None) -> Any:
        """Send a JSON-RPC request and return the result."""
        if not self._socket:
            raise VSCodeIPCError("Not connected. Call connect() first.")
        
        self._request_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params or {}
        }
        
        # Send request
        message = json.dumps(request) + "\n"
        try:
            self._socket.sendall(message.encode("utf-8"))
        except socket.error as e:
            raise VSCodeIPCError(f"Failed to send request: {e}")
        
        # Receive response
        buffer = ""
        try:
            while "\n" not in buffer:
                chunk = self._socket.recv(65536).decode("utf-8")
                if not chunk:
                    raise VSCodeIPCError("Connection closed by server")
                buffer += chunk
        except socket.timeout:
            raise VSCodeIPCError("Request timed out waiting for response")
        except socket.error as e:
            raise VSCodeIPCError(f"Failed to receive response: {e}")
        
        # Parse response
        line = buffer.split("\n")[0]
        try:
            response = json.loads(line)
        except json.JSONDecodeError:
            raise VSCodeIPCError(f"Invalid JSON response: {line[:200]}")
        
        if "error" in response:
            error = response["error"]
            raise VSCodeIPCError(f"RPC error {error.get('code')}: {error.get('message')}")
        
        return response.get("result")
    
    # -------------------------------------------------------------------------
    # Public API Methods
    # -------------------------------------------------------------------------
    
    def ping(self) -> Dict:
        """Test connection to the server."""
        return self._send_request("ping")
    
    def get_diagnostics(self, path: str) -> Dict:
        """
        Get diagnostics for a specific file.
        
        Args:
            path: Absolute file path.
            
        Returns:
            Dict with:
                - uri: File URI
                - path: File path
                - diagnostics: List of diagnostic objects
        """
        return self._send_request("getDiagnostics", {"path": path})
    
    def get_all_diagnostics(
        self,
        severity: Optional[str] = None,
        source: Optional[str] = None,
        limit: Optional[int] = None
    ) -> Dict:
        """
        Get all diagnostics across all open files.
        
        Args:
            severity: Filter by severity ('error', 'warning', 'info', 'hint')
            source: Filter by source (e.g., 'Pylance', 'CK3 Lens')
            limit: Max number of files to return
            
        Returns:
            Dict with:
                - fileCount: Number of files with diagnostics
                - totalDiagnostics: Total number of diagnostics
                - files: List of {uri, path, diagnostics}
        """
        params = {}
        if severity:
            params["severity"] = severity
        if source:
            params["source"] = source
        if limit:
            params["limit"] = limit
        return self._send_request("getAllDiagnostics", params)
    
    def get_workspace_errors(self, include_warnings: bool = True) -> Dict:
        """
        Get workspace-wide error summary.
        
        Args:
            include_warnings: Include warnings in the summary.
            
        Returns:
            Dict with:
                - summary: {errors, warnings, info, filesWithErrors, filesWithWarnings}
                - bySource: Error counts by source
                - topErrorFiles: Files with most errors
                - sources: List of diagnostic sources
        """
        return self._send_request("getWorkspaceErrors", {
            "includeWarnings": include_warnings
        })
    
    def validate_file(self, path: str) -> Dict:
        """
        Trigger validation for a specific file.
        
        This opens the file (if not already open) to trigger language server validation.
        
        Args:
            path: Absolute file path.
            
        Returns:
            Dict with diagnostics for the file.
        """
        return self._send_request("validateFile", {"path": path})
    
    def get_open_files(self) -> Dict:
        """
        Get list of currently open files in VS Code.
        
        Returns:
            Dict with:
                - count: Number of open files
                - files: List of {uri, path, languageId, isDirty, lineCount}
        """
        return self._send_request("getOpenFiles")
    
    def get_active_file(self) -> Dict:
        """
        Get information about the currently active file.
        
        Returns:
            Dict with file info and diagnostics, or {active: false} if no file.
        """
        return self._send_request("getActiveFile")
    
    def execute_command(self, command: str, args: Optional[List] = None) -> Dict:
        """
        Execute a whitelisted VS Code command.
        
        Args:
            command: VS Code command ID (must be in whitelist).
            args: Command arguments.
            
        Returns:
            Dict with execution result.
        """
        return self._send_request("executeCommand", {
            "command": command,
            "args": args or []
        })

    # -------------------------------------------------------------------------
    # Lifecycle IPC Methods
    # -------------------------------------------------------------------------

    def send_startup_hello(self, instance_id: str, pid: int) -> Dict:
        """
        Notify the extension that this MCP subprocess has started.

        Used by the extension to detect subprocess restarts and invalidate
        stale in-memory state (e.g., blank mode file, rotate instance ID).

        Args:
            instance_id: CK3LENS_INSTANCE_ID value for this subprocess.
            pid: This subprocess's OS PID.

        Returns:
            Dict with acknowledgement from extension.
        """
        return self._send_request("mcpStarted", {
            "instanceId": instance_id,
            "pid": pid,
            "ownerToken": os.environ.get("CK3LENS_HTTP_TOKEN"),
        })

    # -------------------------------------------------------------------------
    # Script Signing IPC Methods
    # -------------------------------------------------------------------------

    def open_script_for_review(self, path: str, content_sha256: str, *, canonical_path: str) -> Dict:
        """
        Open a script in VS Code with CodeLens Approve/Reject buttons.

        Args:
            path: Absolute host path to the script file (for VS Code to open).
            content_sha256: SHA-256 hash of the script content.
            canonical_path: Session-absolute canonical address (e.g.,
                root:ck3raven_data/wip/foo.py). Used in the HMAC signing payload.

        Returns:
            Dict with {opened: true} on success.
        """
        return self._send_request("openScriptForReview", {
            "path": path,
            "content_sha256": content_sha256,
            "canonical_path": canonical_path,
        })

    def get_script_sign_approval(self, path: str) -> Dict:
        """
        Check whether the user has approved or rejected a script.

        Args:
            path: Absolute host path to the script (same as openScriptForReview).

        Returns:
            Dict with one of:
              {pending: true}
              {approved: true, signature: str, content_sha256: str, signed_at: str}
              {approved: false}
              {error: str}
        """
        return self._send_request("getScriptSignApproval", {
            "path": path,
        })

    def cancel_script_sign(self, path: str) -> Dict:
        """
        Cancel a pending script signing request, removing CodeLens.

        Args:
            path: Absolute host path to the script.

        Returns:
            Dict with {cancelled: true}.
        """
        return self._send_request("cancelScriptSign", {
            "path": path,
        })


# Convenience function for one-shot queries
def get_vscode_diagnostics(
    path: Optional[str] = None,
    severity: Optional[str] = None,
    source: Optional[str] = None
) -> Dict:
    """
    Convenience function to get diagnostics from VS Code.
    
    Args:
        path: Specific file path (if None, gets all diagnostics)
        severity: Filter by severity
        source: Filter by source
        
    Returns:
        Diagnostic results from VS Code.
    """
    try:
        with VSCodeIPCClient() as client:
            if path:
                return client.get_diagnostics(path)
            else:
                return client.get_all_diagnostics(severity=severity, source=source)
    except VSCodeIPCError as e:
        return {
            "error": True,
            "message": str(e),
            "suggestion": "Ensure VS Code is running with CK3 Lens extension active"
        }


def get_vscode_error_summary() -> Dict:
    """
    Get workspace error summary from VS Code.
    
    Returns:
        Error summary or error message.
    """
    try:
        with VSCodeIPCClient() as client:
            return client.get_workspace_errors()
    except VSCodeIPCError as e:
        return {
            "error": True,
            "message": str(e),
            "suggestion": "Ensure VS Code is running with CK3 Lens extension active"
        }


def is_vscode_available() -> bool:
    """Check if VS Code IPC server is available."""
    try:
        with VSCodeIPCClient(timeout=2.0) as client:
            client.ping()
            return True
    except:
        return False


def announce_subprocess_start(instance_id: str, pid: int) -> VSCodeIPCClient:
    """Announce startup and retain the process-lifetime IPC connection."""
    client = VSCodeIPCClient(timeout=2.0)
    try:
        client.connect()
        response = client.send_startup_hello(instance_id, pid)
        if not isinstance(response, dict) or response.get("ok") is not True:
            reason = (
                response.get("reason", "missing ok=true acknowledgement")
                if isinstance(response, dict)
                else "invalid acknowledgement"
            )
            raise VSCodeIPCError(f"Lifecycle startup not accepted: {reason}")
    except Exception:
        client.close()
        raise
    return client
