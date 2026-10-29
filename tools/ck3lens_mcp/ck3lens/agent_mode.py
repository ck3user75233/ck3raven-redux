"""
Process-bound agent mode with a per-instance UI projection.

The in-memory value is authoritative for policy decisions. It naturally resets
when the MCP subprocess is replaced, so a stale JSON file can never restore an
initialized mode. The per-instance JSON file exists only for AgentView display
and lifecycle diagnostics.
"""
from pathlib import Path
import json
import os
import tempfile
from typing import Optional, Literal
import uuid

from .paths import ROOT_CK3RAVEN_DATA
from .logging import error as log_error

VALID_MODES = frozenset({"ck3lens", "ck3raven-dev"})
_PROCESS_GENERATION = uuid.uuid4().hex

AgentMode = Optional[Literal["ck3lens", "ck3raven-dev"]]
_agent_mode: AgentMode = None


def _get_mode_file(instance_id: Optional[str] = None) -> Path:
    """
    Get the mode file path for a specific instance.
    
    Args:
        instance_id: The instance ID (from CK3LENS_INSTANCE_ID env var).
                     If None, reads from environment or uses "default".
    
    Returns:
        Path to the instance-specific mode file.
    """
    if instance_id is None:
        instance_id = os.environ.get("CK3LENS_INSTANCE_ID", "default")
    
    # Sanitize instance_id for filesystem
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in instance_id)
    
    return ROOT_CK3RAVEN_DATA / f"agent_mode_{safe_id}.json"


def get_agent_mode(instance_id: Optional[str] = None) -> AgentMode:
    """Return this MCP process's initialized mode, or None if uninitialized."""
    return _agent_mode


def set_agent_mode(mode: AgentMode, instance_id: Optional[str] = None) -> None:
    """
    Set this process's mode and publish it for AgentView. Pass None to reset.
    
    Args:
        mode: "ck3lens", "ck3raven-dev", or None to clear
        instance_id: The instance ID. If None, reads from CK3LENS_INSTANCE_ID env var.
    """
    global _agent_mode

    _agent_mode = None
    mode_file = _get_mode_file(instance_id)
    mode_file.parent.mkdir(parents=True, exist_ok=True)
    
    data = {
        "mode": mode,
        "instance_id": instance_id or os.environ.get("CK3LENS_INSTANCE_ID", "default"),
        "process_generation": _PROCESS_GENERATION,
        "process_id": os.getpid(),
        "set_at": None,
    }
    # Add timestamp if setting a mode
    if mode is not None:
        from datetime import datetime, timezone
        data["set_at"] = datetime.now(timezone.utc).isoformat()
    
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=mode_file.parent,
            prefix=f".{mode_file.name}.", suffix=".tmp", delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(data, temporary, indent=2)
        os.replace(temporary_path, mode_file)
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError as error:
                log_error("mcp.mode", "Failed to remove mode publication temporary file",
                          path=str(temporary_path), error=str(error))
    _agent_mode = mode


def clear_agent_mode(instance_id: Optional[str] = None) -> None:
    """
    Clear agent mode for this instance (called by extension on startup).
    
    This ensures each VS Code session starts fresh and the agent
    must explicitly initialize its mode.
    
    Args:
        instance_id: The instance ID. If None, reads from CK3LENS_INSTANCE_ID env var.
    """
    set_agent_mode(None, instance_id)


def get_mode_file_path(instance_id: Optional[str] = None) -> str:
    """
    Return the path to the mode file for this instance.
    
    Args:
        instance_id: The instance ID. If None, reads from CK3LENS_INSTANCE_ID env var.
    """
    return str(_get_mode_file(instance_id))
