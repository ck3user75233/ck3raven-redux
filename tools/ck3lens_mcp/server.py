"""

CK3 Lens MCP Server

An MCP server providing CK3 modding tools:

- Symbol search (from ck3raven SQLite DB)

- Conflict detection

- Mod file operations (writes sandboxed to local_mods_folder)

- Validation

Architecture:

- ALL reads come from ck3raven's SQLite database (symbols, files, AST)

- Writes only allowed to mods under local_mods_folder

"""

from __future__ import annotations

import sys

import os

import json

import sqlite3

from pathlib import Path

from datetime import datetime

from typing import Optional, Literal, Any

from click import command

from ck3lens.log_rotation import rotate_logs

from ck3lens.logging import info, warn, bootstrap

import signal

import atexit

import threading

import anyio

from ck3lens.policy.contract_v1 import Operation

# =============================================================================

# STARTUP TRIPWIRE - Verify running from correct Python environment

# =============================================================================

def _is_stdio_disconnect(error: BaseException) -> bool:

    """Return whether an SDK task group contains only closed stdio resources."""

    if isinstance(error, anyio.BrokenResourceError):

        return True

    nested = getattr(error, "exceptions", None)

    return bool(nested) and all(_is_stdio_disconnect(child) for child in nested)


def _verify_python_environment() -> None:

    """

    Verify the MCP server is running from the repo .venv on Windows.

    If running from Windows Store Python stub, exit immediately with diagnostic info.

    This prevents "ghost" processes that waste debugging time.

    """

    if sys.platform != "win32":

        return  # Only enforce on Windows where the stub exists

    exe = Path(sys.executable).resolve()

    repo_root = Path(__file__).parent.parent.parent

    expected_venv = (repo_root / ".venv" / "Scripts" / "python.exe").resolve()

    # Check if running from WindowsApps (the stub location)

    is_windows_store = "WindowsApps" in str(exe) or "Microsoft\\WindowsApps" in str(exe)

    is_correct_venv = exe == expected_venv or str(exe).lower() == str(expected_venv).lower()

    if is_windows_store or not is_correct_venv:

        # Print diagnostic info and exit

        import site

        print("=" * 70, file=sys.stderr)

        print("FATAL: MCP Server started with wrong Python interpreter!", file=sys.stderr)

        print("=" * 70, file=sys.stderr)

        print(f"  sys.executable:    {sys.executable}", file=sys.stderr)

        print(f"  Expected venv:     {expected_venv}", file=sys.stderr)

        print(f"  sys.prefix:        {sys.prefix}", file=sys.stderr)

        print(f"  VIRTUAL_ENV:       {os.environ.get('VIRTUAL_ENV', '<not set>')}", file=sys.stderr)

        try:

            print(f"  site-packages:     {site.getsitepackages()}", file=sys.stderr)

        except Exception:

            print(f"  site-packages:     <unavailable>", file=sys.stderr)

        print("", file=sys.stderr)

        print("This is likely caused by a bare 'python' fallback in the launcher.", file=sys.stderr)

        print("Fix: Ensure VS Code extension uses absolute path to .venv Python.", file=sys.stderr)

        print("=" * 70, file=sys.stderr)

        # Write persistent breadcrumb before exit — structured logging not yet initialized

        try:

            from datetime import datetime, timezone

            breadcrumb_dir = Path.home() / ".ck3raven" / "logs"

            breadcrumb_dir.mkdir(parents=True, exist_ok=True)

            breadcrumb_file = breadcrumb_dir / "tripwire.log"

            ts = datetime.now(timezone.utc).isoformat()

            with open(breadcrumb_file, "a", encoding="utf-8") as f:

                f.write(f"{ts} | TRIPWIRE FIRED | exe={sys.executable} | expected={expected_venv} | VIRTUAL_ENV={os.environ.get('VIRTUAL_ENV', '<not set>')}\n")

        except Exception:

            pass  # Best effort — breadcrumb failure must not prevent exit

        sys.exit(1)

# Run tripwire at module load time

_verify_python_environment()

from mcp.server.fastmcp import FastMCP
from ck3lens.stdio_transport import diagnostic_stdio_server, guard_completed_responses

# =============================================================================

# PYTHON PATH BOOTSTRAP

# =============================================================================

# ck3raven.core lives in ROOT_REPO/src/ck3raven/core/ - we need to add src/ to sys.path.

#

# ROOT_REPO comes from config. If not configured, FAIL LOUDLY.

# No fallbacks, no __file__ computation (breaks in venvs).

#

def _setup_ck3raven_import_path() -> None:

    """Set up sys.path to enable imports from ck3raven.core."""

    from ck3lens.config_loader import load_config

    config = load_config()

    root_repo = config.paths.root_repo

    if root_repo is None:

        print("=" * 70, file=sys.stderr)

        print("FATAL: root_repo not configured in workspace.toml", file=sys.stderr)

        print("=" * 70, file=sys.stderr)

        print("", file=sys.stderr)

        print("Run: python -m ck3lens.paths_doctor", file=sys.stderr)

        print("Or add to ~/.ck3raven/config/workspace.toml:", file=sys.stderr)

        print("", file=sys.stderr)

        print("  [paths]", file=sys.stderr)

        print('  root_repo = "C:/path/to/ck3raven"', file=sys.stderr)

        print("", file=sys.stderr)

        print("=" * 70, file=sys.stderr)

        sys.exit(1)

    if not root_repo.exists():

        print("=" * 70, file=sys.stderr)

        print(f"FATAL: root_repo does not exist: {root_repo}", file=sys.stderr)

        print("=" * 70, file=sys.stderr)

        print("Fix root_repo in ~/.ck3raven/config/workspace.toml", file=sys.stderr)

        sys.exit(1)

    src_path = root_repo / "src"

    if not src_path.exists():

        print("=" * 70, file=sys.stderr)

        print(f"FATAL: src/ not found in root_repo: {src_path}", file=sys.stderr)

        print("=" * 70, file=sys.stderr)

        print("Verify root_repo points to ck3raven repository root", file=sys.stderr)

        sys.exit(1)

    if str(src_path) not in sys.path:

        sys.path.insert(0, str(src_path))

_setup_ck3raven_import_path()

# Canonical Reply System (Phase C migration)

# NOTE: Must be after path setup since ck3raven.core is in ROOT_REPO/src/

from ck3raven.core.reply import Reply, TraceInfo, MetaInfo

from ck3raven.core.trace import generate_trace_id, get_or_create_session_id

from .safety import mcp_safe_tool, mcp_safe_worker_tool, ReplyBuilder, get_current_trace_info, initialize_window_trace

from ck3lens.workspace import Session, LocalMod, _read_mod_descriptor

from ck3lens.db_queries import DBQueries

from ck3lens.db_api import db as db_api  # Database API Layer - THE interface for DB access

from ck3lens.validate import parse_content, validate_artifact_bundle

from ck3lens.contracts import ArtifactBundle

from ck3lens.trace import ToolTrace

# Canonical path constants - use these instead of computing paths from __file__

from ck3lens.paths import ROOT_REPO, ROOT_CK3RAVEN_DATA, ROOT_GAME

# =============================================================================

# Instance ID support for multi-window isolation

_instance_id = os.environ.get("CK3LENS_INSTANCE_ID", "default")

_server_name = f"ck3lens-{_instance_id}" if _instance_id != "default" else "ck3lens"

mcp = FastMCP(_server_name)

# Session state

_session: Optional[Session] = None

_db: Optional[DBQueries] = None

_trace: Optional[ToolTrace] = None

# World adapter v2 cache (v1 adapter deleted March 2026)

def _get_session() -> Session:

    global _session

    if _session is None:

        from ck3lens.workspace import load_session

        _session = load_session()

        if _session.load_warnings:

            for warning in _session.load_warnings:

                warn("mcp.init", warning)

    return _session

def _get_db() -> DBQueries:

    """

    Get database connection via db_api layer.

    NOTE: This function now delegates to db_api. If db_api.is_available()

    returns False, this will raise RuntimeError. Tools should check

    db_api.is_available() before calling operations that require DB.

    ARCHITECTURAL NOTE (January 2026):

    The db_api module is THE interface for database access. This function

    remains for backward compatibility but should not be used for new code.

    Use db_api methods directly (db_api.unified_search(), etc.)

    """

    global _db

    # Check if database access is disabled

    if not db_api.is_available():

        raise RuntimeError("Database is disabled for maintenance. Use ck3_db(command='enable') to reconnect.")

    if _db is None:

        session = _get_session()

        if session.db_path is None:

            raise RuntimeError("No database path configured. Check playset configuration.")

        # Configure db_api with path (if not already configured)

        db_api.configure(session.db_path)

        # Get connection via db_api

        _db = db_api._get_db()

    return _db


def _get_ingestion_status(cvids: list[str]) -> dict:
    """Read target-specific discovery/build queue state without mutating it."""
    if not cvids:
        return {
            "ready": False,
            "state": "unresolved",
            "pending": 0,
            "processing": 0,
            "failed": 0,
        }

    placeholders = ",".join("?" for _ in cvids)
    conn = _get_db().conn
    discovery_rows = conn.execute(
        f"SELECT status, COUNT(*) FROM discovery_queue "
        f"WHERE content_version_id IN ({placeholders}) GROUP BY status",
        cvids,
    ).fetchall()
    build_rows = conn.execute(
        f"SELECT bq.status, COUNT(*) FROM build_queue bq "
        f"JOIN files f ON f.file_id = bq.file_id "
        f"WHERE f.content_version_id IN ({placeholders}) GROUP BY bq.status",
        cvids,
    ).fetchall()

    def counts(rows) -> dict[str, int]:
        return {str(row[0]): int(row[1]) for row in rows}

    discovery = counts(discovery_rows)
    build = counts(build_rows)
    pending = discovery.get("pending", 0) + build.get("pending", 0)
    processing = discovery.get("processing", 0) + build.get("processing", 0)
    failed = discovery.get("error", 0) + build.get("error", 0)
    ready = pending == 0 and processing == 0
    return {
        "ready": ready,
        "state": "ready" if ready else "ingesting",
        "pending": pending,
        "processing": processing,
        "failed": failed,
        "discovery": discovery,
        "build": build,
    }


# BANNED: _get_lens, _get_cvids, _derive_search_cvids, db_visibility,

# _get_active_cvids (December 2025 / March 2026 purge)

# Database scopes read CVIDs directly from session.mods.

# REMOVED: _session_scope global (January 2026)

# The dual session system caused playset state to always be null.

# Use _get_session() instead - it returns a proper Session object.

# Playset folder - JSON files here define available playsets

# CANONICAL LOCATION: ~/.ck3raven/playsets/

def _get_playsets_dir() -> Path:

    """Get the canonical playsets directory."""

    return ROOT_CK3RAVEN_DATA / "playsets"

# For backward compatibility during migration

PLAYSETS_DIR = ROOT_CK3RAVEN_DATA / "playsets"

# Manifest file - points to which playset is currently active

# Lives in the playsets folder alongside the playset files

PLAYSET_MANIFEST_FILE = ROOT_CK3RAVEN_DATA / "playsets" / "playset_manifest.json"

def _list_available_playsets() -> list[dict]:

    """

    List all available playsets from the playsets folder.

    Returns list of {name, file_path, description, mod_count}

    """

    if not PLAYSETS_DIR.exists():

        return []

    playsets = []

    for f in PLAYSETS_DIR.glob("*.json"):

        if f.name.endswith(".schema.json"):

            continue  # Skip schema files

        try:

            data = json.loads(f.read_text(encoding="utf-8-sig"))

            playsets.append({

                "name": data.get("playset_name", f.stem),

                "file_path": str(f),

                "description": data.get("description", ""),

                "mod_count": len(data.get("mods", [])),

            })

        except Exception:

            pass

    return playsets

def _validate_playset_schema(data: dict, file_path: str) -> tuple[bool, list[str]]:

    """

    Validate playset JSON against required schema.

    FAIL CLOSED: If validation fails, returns (False, errors).

    The caller MUST NOT proceed with an invalid playset.

    Returns (is_valid, list of error messages).

    """

    errors = []

    # Required top-level fields

    if "playset_name" not in data:

        errors.append("Missing required field: playset_name")

    elif not isinstance(data["playset_name"], str):

        errors.append("playset_name must be a string")

    if "vanilla" not in data:

        errors.append("Missing required field: vanilla")

    elif not isinstance(data["vanilla"], dict):

        errors.append("vanilla must be an object")

    elif "path" not in data["vanilla"]:

        errors.append("Missing required field: vanilla.path")

    if "mods" not in data:

        errors.append("Missing required field: mods")

    elif not isinstance(data["mods"], list):

        errors.append("mods must be an array")

    else:

        # Validate each mod entry

        for i, mod in enumerate(data["mods"]):

            if not isinstance(mod, dict):

                errors.append(f"mods[{i}] must be an object")

                continue

            if "name" not in mod:

                errors.append(f"mods[{i}] missing required field: name")

            if "path" not in mod:

                errors.append(f"mods[{i}] missing required field: path")

            if "load_order" not in mod:

                errors.append(f"mods[{i}] missing required field: load_order")

    return (len(errors) == 0, errors)

def _load_playset_from_json(playset_file: Path) -> Optional[dict]:

    """

    Load playset data from a ck3raven playset JSON file.

    This is the NEW primary source for playset data.

    Reads the full playset format with agent_briefing support.

    IMPORTANT: Auto-migrates paths if they reference a different user profile.

    This handles the case where playset files are synced via git between machines.

    Returns dict with session scope data or None if file missing.

    """

    if not playset_file.exists():

        return None

    try:

        data = json.loads(playset_file.read_text(encoding='utf-8-sig'))

        # FAIL CLOSED: Validate schema before proceeding

        is_valid, schema_errors = _validate_playset_schema(data, str(playset_file))

        if not is_valid:

            warn("mcp.init", f"Playset schema validation failed: {playset_file}",

                 errors=schema_errors)

            # Return error dict instead of None to signal validation failure

            return {

                "error": "PLAYSET_SCHEMA_INVALID",

                "message": f"Playset failed schema validation: {playset_file.name}",

                "validation_errors": schema_errors,

                "reply_code": "WA-VIS-I-003",

            }

        active_mod_ids = set()

        active_roots = set()

        # Read mods array (new format)

        for mod in data.get("mods", []):

            if not mod.get("enabled", True):

                continue

            steam_id = mod.get("steam_id")

            if steam_id:

                active_mod_ids.add(str(steam_id))

            path = mod.get("path")

            if path:

                # Expand ~ in paths

                active_roots.add(str(Path(path).expanduser()))

        # Get vanilla info

        vanilla_config = data.get("vanilla", {})

        vanilla_root = vanilla_config.get("path", str(ROOT_GAME) if ROOT_GAME else "")

        # Get local_mods_folder path (editability is DERIVED from path containment)

        local_mods_folder_raw = data.get("local_mods_folder", "")

        local_mods_folder = Path(local_mods_folder_raw).expanduser() if local_mods_folder_raw else None

        # NOTE: No editable_mods list - enforcement.py determines writability at execution time

        # based on path containment under local_mods_folder. No pre-computed permission lists.

        # Get agent briefing

        agent_briefing = data.get("agent_briefing", {})

        return {

            "playset_id": None,  # No DB ID for JSON-based

            "playset_name": data.get("playset_name", "JSON Playset"),

            "active_mod_ids": active_mod_ids,

            "active_roots": active_roots,

            "vanilla_root": str(Path(vanilla_root).expanduser()),

            "source": "json",

            "file_path": str(playset_file),

            "local_mods_folder": str(local_mods_folder) if local_mods_folder else None,

            "agent_briefing": agent_briefing,

            "sub_agent_config": data.get("sub_agent_config", {}),

            "mod_list": data.get("mods", []),  # Full mod list for reference

        }

    except Exception as e:

        warn("mcp.init", f"Failed to load playset: {playset_file}", error=str(e))

        return None

# REMOVED: _load_legacy_playset - Legacy playset format BANNED (December 2025)

# All playsets must use the canonical mods[] format in playsets/*.json

# REMOVED: _get_session_scope() function (January 2026)

# This function was part of a dual session system that caused playset state to always be null.

# The bug: _get_session_scope() reimplemented session loading with a path mismatch bug.

# The fix: Use _get_session() which returns a proper Session object from load_session().

#

# If you need playset info, use:

#   session = _get_session()

#   session.playset_name  # Human-readable name

#   session.mods          # List of ModEntry objects

#   session.vanilla       # Vanilla entry (mods[0])

#   session.local_mods_folder  # Path to editable mods

def _get_trace_path() -> Path:

    """

    Get the trace log path based on agent mode.

    - ck3raven-dev mode: {repo}/.wip/traces/ck3lens_trace.jsonl

    - ck3lens mode: ~/.ck3raven/traces/ck3lens_trace.jsonl

    """

    # All traces go in ~/.ck3raven/traces/ (unified, no mode-specific location)

    trace_dir = ROOT_CK3RAVEN_DATA / "traces"

    trace_dir.mkdir(parents=True, exist_ok=True)

    return trace_dir / "ck3lens_trace.jsonl"

def _get_trace() -> ToolTrace:

    global _trace

    if _trace is None:

        _trace = ToolTrace(_get_trace_path())

    return _trace

# ============================================================================

# Session Management

# ============================================================================

@mcp.tool()

@mcp_safe_tool

def ck3_get_instance_info() -> Reply:

    """

    Get information about this MCP server instance.

    Use this to verify which server instance you're connected to.

    Each VS Code window should have a unique instance ID.

    Returns:

        Instance ID, server name, and process info

    """

    import os

    trace_info = get_current_trace_info()

    rb = ReplyBuilder(trace_info, tool="ck3_get_instance_info")

    # Instance info is infrastructure metadata - ungoverned -> MCP layer

    return rb.success(

        "MCP-SYS-S-001",

        data={

            "instance_id": _instance_id,

            "server_name": _server_name,

            "pid": os.getpid(),

            "is_isolated": _instance_id != "default",

        },

        message="Instance info retrieved.",

    )

@mcp.tool()

@mcp_safe_tool

def ck3_ping() -> Reply:

    """

    Simple health check - always returns success.

    Use this to verify MCP server connectivity is working.

    Unlike other tools, this has no dependencies and will

    always succeed if the server is reachable.

    Returns:

        Reply with status, instance_id, and timestamp in data

    """

    from datetime import datetime

    trace_info = get_current_trace_info()

    rb = ReplyBuilder(trace_info, tool="ck3_ping")

    # Ping is system health check - ungoverned -> MCP layer

    return rb.success(

        "MCP-SYS-S-001",

        data={

            "status": "ok",

            "instance_id": _instance_id,

            "timestamp": datetime.now().isoformat(),

        },

        message="Ping successful.",

    )

@mcp.tool()

@mcp_safe_tool

def debug_get_logs(

    lines: int = 50,

    level: Optional[Literal["DEBUG", "INFO", "WARN", "ERROR"]] = None,

    category: Optional[str] = None,

    trace_id: Optional[str] = None,

    source: Literal["all", "mcp", "ext", "daemon"] = "all",

) -> Reply:

    """

    Get recent logs from all ck3raven components, merged chronologically.

    This tool aggregates logs from:

    - MCP Server (Python): ~/.ck3raven/logs/ck3raven-mcp.log

    - VS Code Extension (Node): ~/.ck3raven/logs/ck3raven-ext.log

    - QBuilder Daemon: ~/.ck3raven/daemon/daemon.log

    Logs are returned in chronological order with source identification.

    Use this for debugging MCP lifecycle issues, tool failures, or 

    tracing operations across components via trace_id.

    This tool exists specifically so the user can copy-paste the agent's 

    output back to ChatGPT or other external review.

    Args:

        lines: Maximum total lines to return (default 50)

        level: Filter by minimum level (DEBUG, INFO, WARN, ERROR)

        category: Filter by category prefix (e.g., "mcp.", "ext.", "contract.")

        trace_id: Filter by trace ID (for debugging specific operations)

        source: Which log sources to include (all, mcp, ext, daemon)

    Returns:

        Merged, chronologically sorted log entries with source identification

    """

    trace_info = get_current_trace_info()

    rb = ReplyBuilder(trace_info, tool="debug_get_logs")

    log_files = {

        "mcp": ROOT_CK3RAVEN_DATA / "logs" / "ck3raven-mcp.log",

        "ext": ROOT_CK3RAVEN_DATA / "logs" / "ck3raven-ext.log",

        "daemon": ROOT_CK3RAVEN_DATA / "daemon" / "daemon.log",

    }

    level_order = {"DEBUG": 0, "INFO": 1, "WARN": 2, "ERROR": 3}

    entries = []

    files_read = []

    for src, log_path in log_files.items():

        if source != "all" and source != src:

            continue

        if not log_path.exists():

            continue

        files_read.append(str(log_path))

        try:

            with open(log_path, "r", encoding="utf-8") as f:

                for line_text in f:

                    try:

                        entry = json.loads(line_text.strip())

                        entry["_source"] = src

                        # Apply filters

                        if level:

                            entry_level = entry.get("level", "INFO")

                            if level_order.get(entry_level, 1) < level_order.get(level, 1):

                                continue

                        if category and not entry.get("cat", "").startswith(category):

                            continue

                        if trace_id and entry.get("trace_id") != trace_id:

                            continue

                        entries.append(entry)

                    except json.JSONDecodeError:

                        continue

        except Exception as e:

            # Log but continue - don't fail if one file is unreadable

            entries.append({

                "_source": src,

                "ts": datetime.now().isoformat() + "Z",

                "level": "WARN",

                "cat": "debug.get_logs",

                "msg": f"Could not read {log_path}: {e}",

            })

    # Sort by timestamp

    entries.sort(key=lambda e: e.get("ts", ""))

    # Return most recent entries

    result_entries = entries[-lines:] if len(entries) > lines else entries

    return rb.success(

        "WA-LOG-S-001",

        data={

            "entries": result_entries,

            "total_available": len(entries),

            "truncated": len(entries) > lines,

            "files_read": files_read,

            "filters_applied": {

                "level": level,

                "category": category,

                "trace_id": trace_id,

                "source": source,

            }

        },

        message=f"Retrieved {len(result_entries)} log entries from {len(files_read)} files.",

    )

def _init_session_internal(

    db_path: Optional[str] = None,

) -> dict:

    """

    Internal session initialization - called by ck3_get_mode_instructions.

    CANONICAL MODEL:

    - Session is loaded from active playset via load_session()

    - mods[] contains all mods from playset

    - local_mods_folder is the folder boundary for editable mods

    - "mods under local_mods_folder" are derived at runtime, not stored

    Returns session info dict.

    """

    from ck3lens.workspace import load_session

    global _session, _db, _trace, _playset_id

    # Reset playset cache

    _playset_id = None

    # Hydrate the session from the active playset through the canonical loader.

    _session = load_session()

    # Override DB path if provided

    if db_path:

        _session.db_path = Path(db_path)

    if _session.db_path is None:

        raise RuntimeError("No database path configured. Check playset configuration.")

    # Initialize trace with proper path based on mode

    _trace = ToolTrace(_get_trace_path())

    # Attempt DB connection — non-fatal if database file doesn't exist yet

    db_status = {"is_complete": False, "rebuild_reason": "database file not found"}

    try:

        db_api.configure(_session.db_path)

        _db = db_api._get_db()

        # Check database health

        _internal_rb = ReplyBuilder(TraceInfo(trace_id="internal", session_id="internal"), tool='_init_session')

        db_health_reply = _check_db_health(_db.conn, rb=_internal_rb)

        db_status = db_health_reply.data

        try:
            missing_mods = [mod for mod in _session.mods if mod.cvid is None]
            info(
                "session_hydration",
                f"resolved={len(_session.mods) - len(missing_mods)} missing={len(missing_mods)}",
            )

            if missing_mods:
                from ck3lens.daemon_client import daemon as daemon_client

                if not daemon_client.is_available(force_check=True):
                    raise RuntimeError("QBuilder availability invariant was not satisfied")
                scan_result = daemon_client.playset_scan()
                if not scan_result.get("ready", False):
                    raise RuntimeError(
                        f"Active playset contains missing mods: {scan_result.get('missing', [])}"
                    )
                _session = load_session(conn=_db.conn)
                unresolved = [mod.name for mod in _session.mods if mod.cvid is None]
                if unresolved:
                    raise RuntimeError(
                        f"QBuilder did not resolve CVIDs for: {unresolved}"
                    )

            hydrated_cvids = sorted(
                mod.cvid for mod in _session.mods if mod.cvid is not None
            )
            if not _get_ingestion_status(hydrated_cvids)["ready"]:
                warning = (
                    f"Active playset '{_session.playset_name}' is being ingested"
                )
                warn("session_hydration", warning)
                _session.load_warnings.append(warning)

        except Exception as e:
            warning = f"Active playset preparation failed: {e}"
            warn("session_hydration", warning)
            _session.load_warnings.append(warning)

    except Exception:

        # DB missing or corrupt — session still works, tools that need DB

        # will report errors individually

        _db = None

    # Always persist session snapshot for external consumers, even if DB init failed.
    # In that case CVIDs remain null, but mod identity/path data is still authoritative.
    try:

        _session._write_session_state()

    except Exception as e:

        warn("session_state", f"write failed (non-fatal): {e}")

    # CANONICAL: Get playset info from session - no database lookup needed

    playset_name = getattr(_session, "playset_name", "Active Playset")

    mod_count = len(_session.mods) if hasattr(_session, "mods") else 0

    result = {

        "db_path": str(_session.db_path),

        "playset_name": playset_name,

        "mod_count": mod_count,

        "db_status": db_status,

        "local_mods_folder": str(_session.local_mods_folder) if _session.local_mods_folder else None,

    }

    if not db_status.get("is_complete"):

        result["warning"] = f"Database incomplete: {db_status.get('rebuild_reason', 'unknown')}. Run: python builder/daemon.py start"

    return result

def _check_db_health(conn, *, rb: ReplyBuilder) -> Reply:

    """Check database build status and completeness.

    Works with qbuilder tables (build_queue, build_runs).

    Falls back gracefully if tables don't exist.

    """

    try:

        # Get counts first (these tables should always exist)

        files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]

        symbols = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]

        refs = conn.execute("SELECT COUNT(*) FROM refs").fetchone()[0]

        # Check which tables exist

        tables = {row[0] for row in conn.execute(

            "SELECT name FROM sqlite_master WHERE type='table'"

        )}

        # Check qbuilder queue status (preferred)

        queue_pending = 0

        queue_processing = 0

        queue_completed = 0

        queue_error = 0

        if 'build_queue' in tables:

            for row in conn.execute("""

                SELECT status, COUNT(*) as cnt FROM build_queue GROUP BY status

            """):

                if row[0] == 'pending':

                    queue_pending = row[1]

                elif row[0] == 'processing':

                    queue_processing = row[1]

                elif row[0] == 'completed':

                    queue_completed = row[1]

                elif row[0] == 'error':

                    queue_error = row[1]

        # Check build_runs table (qbuilder's run tracking)

        last_state = None

        last_updated = None

        if 'build_runs' in tables:

            run_row = conn.execute("""

                SELECT status, completed_at FROM build_runs 

                ORDER BY started_at DESC LIMIT 1

            """).fetchone()

            if run_row:

                last_state = run_row[0]

                last_updated = run_row[1]

        # Determine completion status

        # Complete if: no pending/processing items AND we have symbols/refs

        has_pending_work = queue_pending > 0 or queue_processing > 0

        if has_pending_work:

            is_complete = False

            phase = "building"

            needs_rebuild = False  # Work in progress, not a "rebuild" situation

            rebuild_reason = f"{queue_pending} pending, {queue_processing} processing"

        elif symbols == 0:

            is_complete = False

            phase = "no_symbols"

            needs_rebuild = True

            rebuild_reason = "No symbols extracted - run: python -m qbuilder.cli daemon"

        elif refs == 0:

            is_complete = False

            phase = "no_refs"

            needs_rebuild = True

            rebuild_reason = "No references extracted - run: python -m qbuilder.cli daemon"

        elif queue_error > 0:

            is_complete = False

            phase = "has_errors"

            needs_rebuild = False

            rebuild_reason = f"{queue_error} files had errors during build"

        else:

            is_complete = True

            phase = "complete"

            needs_rebuild = False

            rebuild_reason = None

        return rb.success("MCP-SYS-S-001", data={

            "is_complete": is_complete,

            "phase": phase,

            "last_updated": last_updated,

            "files_indexed": files,

            "symbols_extracted": symbols,

            "refs_extracted": refs,

            "needs_rebuild": needs_rebuild,

            "rebuild_reason": rebuild_reason,

            "queue": {

                "pending": queue_pending,

                "processing": queue_processing,

                "completed": queue_completed,

                "error": queue_error,

            },

        })

    except Exception as e:

        return rb.error("MCP-SYS-E-001", data={

            "is_complete": False,

            "error": str(e),

            "needs_rebuild": True,

            "rebuild_reason": f"Error checking database: {e}"

        })

# NOTE: ck3_get_db_status() DELETED - January 2026

# Functionality consolidated into ck3_qbuilder(command="status")

# This avoids redundant tools and eliminates qbuilder import dependency

# NOTE: ck3_close_db() DELETED - March 2026 (B2 consolidation)

# Duplicate of ck3_db(command="disable"). Use that instead.

@mcp.tool()

@mcp_safe_tool

def ck3_db(

    command: Literal["status", "disable", "enable", "delete", "query", "help"] = "status",

    # --- delete command params ---

    target: Literal["asts", "symbols", "refs", "files", "content_versions", "lookups", "playsets", "build_tracking"] | None = None,

    scope: Literal["all", "by_ids", "by_content_version"] | None = None,

    ids: Optional[list[int | str]] = None,

    content_version_ids: Optional[list[str]] = None,

    confirm: bool = False,

    # --- query command params ---

    table: str | None = None,

    filters: dict | None = None,

    columns: list[str] | None = None,

    sql: str | None = None,

    sql_file: str | None = None,

    limit: int = 100,

    unfiltered: bool = False,

) -> Reply:

    """

    Unified database management tool.

    Commands:

    command=status   → Check if database is enabled/connected

    command=disable  → Close connection, block reconnection, release file lock

    command=enable   → Re-enable database access

    command=delete   → Surgical deletion of indexed data (requires target + scope)

    command=query    → Structured table queries or raw SQL

    command=help     → Show queryable tables, schemas, and statistics

    Args:

        command: Operation to perform

        # For command="delete":

        target: What to delete (asts/symbols/refs/files/content_versions/lookups/playsets/build_tracking)

        scope: How to filter (all/by_ids/by_content_version)

        ids: List of IDs when scope="by_ids" (ints or "start-end" ranges)

        content_version_ids: List of cvids when scope="by_content_version"

        confirm: Must be True to actually delete; False = preview

        # For command="query":

        table: Table to query (use command="help" to see options)

        filters: {"column": "value"} dict. % in value triggers LIKE match.

        columns: List of columns to select (default: all)

        sql: Raw SELECT query (ck3raven-dev only, or unfiltered=True in ck3lens)

        sql_file: Path to .sql file

        limit: Max results (default 100)

        unfiltered: Bypass playset visibility filtering

    Examples:

        ck3_db(command="status")

        ck3_db(command="disable")

        ck3_db(command="enable")

        ck3_db(command="delete", target="content_versions", scope="by_ids", ids=[2,3,4], confirm=True)

        ck3_db(command="query", table="symbols", filters={"name": "%brave%", "symbol_type": "trait"})

        ck3_db(command="query", sql="SELECT COUNT(*) as cnt FROM symbols GROUP BY symbol_type")

        ck3_db(command="help")

    """

    global _db

    trace_info = get_current_trace_info()

    rb = ReplyBuilder(trace_info, tool="ck3_db")

    if command == "status":

        result = db_api.status()

        return rb.success("WA-DB-S-001", data=result, message="Database status retrieved.")

    elif command == "disable":

        # Use db_api to disable (handles WAL mode, close, blocks reconnect)

        result = db_api.disable()

        # Also clear module-level cache

        _db = None

        # Clear thread-local connections from schema module

        try:

            from ck3raven.db.schema import close_all_connections

            close_all_connections()

        except Exception:

            pass

        # Force garbage collection to release file handles

        import gc

        gc.collect()

        return rb.success("WA-DB-S-001", data=result, message="Database disabled.")

    elif command == "enable":

        result = db_api.enable()

        return rb.success("WA-DB-S-001", data=result, message="Database enabled.")

    elif command == "delete":

        if not target:

            return rb.invalid("MCP-SYS-I-001", data={"parameter": "target"}, message="'target' required for delete command")

        if not scope:

            return rb.invalid("MCP-SYS-I-001", data={"parameter": "scope"}, message="'scope' required for delete command")

        result = _ck3_db_delete_internal(target, scope, ids, content_version_ids, confirm)

        if result.get("error") or result.get("reply_type") == "D":

            if result.get("reply_type") == "D" and result.get("code"):

                return rb.denied(result["code"], data=result, message=result.get("error", "Policy denied"))

            err_msg = str(result.get("error", "")).lower()

            if "failed to" in err_msg or "timeout" in err_msg or "connection" in err_msg or "exception" in err_msg:

                return rb.error('MCP-SYS-E-001', data=result, message=result.get("error", "System error"))

            return rb.invalid('WA-DB-I-001', data=result, message=result.get("error", "Invalid input"))

        if result.get("success"):

            msg = f"Deleted {result.get('rows_deleted', 0)} rows from {target}."

            return rb.success('MCP-DB-S-001', data=result, message=msg)

        if result.get("preview") or result.get("rows_would_delete") is not None:

            msg = f"Would delete {result.get('rows_would_delete', 0)} rows from {target}. Use confirm=True to delete."

            return rb.success('MCP-DB-S-002', data=result, message=msg)

        return rb.success('MCP-DB-S-001', data=result, message="Operation complete.")

    elif command == "help":

        return rb.success(

            'WA-DB-S-001',

            data={

                "usage": "ck3_db(command='query', table=..., filters={...}, limit=N) or ck3_db(command='query', sql=...)",

                "tables": {

                    name: {

                        "columns": schema["columns"],

                        "filters": schema["filters"],

                        "examples": schema["examples"],

                    }

                    for name, schema in _DB_QUERY_SCHEMA.items()

                },

                "stats": _get_table_stats(rb=rb).data,

            },

            message="Database schema and statistics.",

        )

    elif command == "query":

        return _ck3_db_query_internal(

            rb=rb, table=table, filters=filters, columns=columns,

            sql=sql, sql_file=sql_file, limit=limit, unfiltered=unfiltered,

        )

    else:

        return rb.invalid("MCP-SYS-I-002", data={"command": command}, message=f"Unknown command: {command}")

# NOTE: ck3_get_playset_build_status() DELETED - January 2026

# Functionality consolidated into ck3_qbuilder(command="status")

# This avoids redundant tools and eliminates qbuilder import dependency

# NOTE: ck3_db_delete() DELETED - March 2026 (B2 consolidation)

# Use ck3_db(command="delete", target=..., scope=...) instead.

# _ck3_db_delete_internal() preserved below for the unified handler.

def _ck3_db_delete_internal(

    target: str,

    scope: str,

    ids: Optional[list[int | str]],

    content_version_ids: Optional[list[str]],

    confirm: bool,

) -> dict:

    """Internal implementation of ck3_db delete command.

    ARCHITECTURE (March 2026 — Priority 1 cleanup):

    - Preview mode (confirm=False): local read-only SELECTs via db_api

    - Confirmed deletes (confirm=True): delegated to daemon via IPC

    MCP is a read-only client. All mutations go through the daemon which

    holds the writable connection. See docs/SINGLE_WRITER_ARCHITECTURE.md.

    """

    trace = _get_trace()

    # ==================================================================

    # PREVIEW MODE — read-only SELECTs (stays local, no daemon needed)

    # ==================================================================

    if not confirm:

        return _ck3_db_delete_preview(target, scope, ids, content_version_ids, trace)

    # ==================================================================

    # CONFIRMED DELETE — delegate to daemon via IPC

    # ==================================================================

    from ck3lens.daemon_client import daemon, DaemonNotAvailableError, DaemonError, DaemonError

    try:

        result = daemon.delete_data(

            target=target,

            scope=scope,

            ids=ids,

            content_version_ids=content_version_ids,

        )

        result["confirm"] = True

        trace.log("mcp.tool", {"target": target, "scope": scope, "via": "daemon_ipc"}, result)

        return result

    except DaemonNotAvailableError:

        return {

            "error": "Daemon not running — required for database mutations",

            "hint": "Start daemon: python -m qbuilder.cli daemon",

            "target": target,

            "scope": scope,

            "confirm": True,

        }

    except DaemonError as e:

        return {

            "error": f"Daemon error: {e.message}",

            "code": e.code,

            "target": target,

            "scope": scope,

            "confirm": True,

        }

def _ck3_db_delete_preview(

    target: str,

    scope: str,

    ids: Optional[list[int | str]],

    content_version_ids: Optional[list[str]],

    trace,

) -> dict:

    """Preview what a delete would remove (read-only SELECTs only)."""

    db = _get_db()

    cur = db.conn.cursor()

    result: dict = {"target": target, "scope": scope, "confirm": False, "preview": True}

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

            if table in ("files",):

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

    try:

        if target in ("asts", "symbols", "refs"):

            w, p = where(target, f"{target[:-1]}_id")

            cur.execute(f"SELECT COUNT(*) FROM {target} {w}", p)

            result["rows_would_delete"] = cur.fetchone()[0]

        elif target == "files":

            w, p = where("files", "file_id")

            cur.execute(f"SELECT COUNT(*) FROM files {w}", p)

            result["rows_would_delete"] = cur.fetchone()[0]

        elif target == "content_versions":

            w, p = where("content_versions", "content_version_id")

            cur.execute(f"SELECT COUNT(*) FROM content_versions {w}", p)

            result["rows_would_delete"] = cur.fetchone()[0]

        elif target == "lookups":

            lookup_tables = ["trait_lookups", "event_lookups", "decision_lookups",

                             "culture_lookups", "religion_lookups"]

            details = {}

            for tbl in lookup_tables:

                try:

                    cur.execute(f"SELECT COUNT(*) FROM {tbl}")

                    details[tbl] = cur.fetchone()[0]

                except Exception:

                    details[tbl] = 0

            result["rows_would_delete"] = sum(details.values())

            result["details"] = details

        elif target == "build_tracking":

            cur.execute("SELECT COUNT(*) FROM builder_runs")

            runs = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM builder_steps")

            steps = cur.fetchone()[0]

            result["rows_would_delete"] = runs + steps

            result["details"] = {"builder_runs": runs, "builder_steps": steps}

        else:

            result["error"] = f"Unknown target: {target}"

            trace.log("mcp.tool", {"target": target, "scope": scope, "preview": True}, result)

            return result

    except Exception as e:

        result["error"] = str(e)

    trace.log("mcp.tool", {"target": target, "scope": scope, "preview": True}, result)

    return result

# ============================================================================

# Unified Command Tools (Consolidated)

# ============================================================================

@mcp.tool()

@mcp_safe_tool

def ck3_logs(

    source: Literal["error", "game", "debug", "crash"] = "error",

    command: Literal["summary", "list", "search", "detail", "categories", "cascades", "read", "raw"] = "summary",

    # Filters

    priority: int | None = None,

    category: str | None = None,

    mod_filter: str | None = None,

    mod_filter_exact: bool = False,

    exclude_cascade_children: bool = True,

    # Search

    query: str | None = None,

    # Detail (for crash)

    crash_id: str | None = None,

    # Read raw

    lines: int = 100,

    from_end: bool = True,

    # Pagination

    limit: int = 50,

    # FR-2: Export results to WIP

    export_to: str | None = None,

    # FR-3: Skip auto-export (use for debugging, or when you want spill format)

    skip_export: bool = False,

) -> Reply:

    """

    Unified logging tool for error.log, game.log, debug.log, and crash reports.

    Source + Command combinations:

    source=error:

        command=summary     ? Error log summary (counts by priority/category/mod)

        command=list        ? Filtered error list (priority, category, mod_filter)

        command=search      ? Search errors (query required)

        command=cascades    ? Cascading error patterns (fix root causes first)

    source=game:

        command=summary     ? Game log summary with category breakdown

        command=list        ? Game log errors (category filter optional)

        command=search      ? Search game log (query required)

        command=categories  ? Category breakdown with descriptions

    source=debug:

        command=summary     ? System info, DLCs, mod list

    source=crash:

        command=summary     ? Recent crash reports list

        command=detail      ? Full crash report (crash_id required)

    Any source:

        command=read        → Raw log content (lines, from_end, query for search)

        command=raw         → Complete raw log file for backup/archival (no limits)

    Args:

        source: Log source to query

        command: Action to perform

        priority: Max priority 1-5 (error source only)

        category: Filter by category

        mod_filter: Filter by mod name (substring match by default)

        mod_filter_exact: If True, require exact match instead of substring

        exclude_cascade_children: Skip errors caused by cascade patterns

        query: Search query for search/read commands

        crash_id: Crash folder name for detail command

        lines: Lines to return for read command

        from_end: Read from end (tail) vs start (head)

        limit: Max results for list commands

        export_to: Canonical output path for the markdown report.

            Example: "root:ck3raven_data/wip/reports/error_{timestamp}.md"

            Use {timestamp} for auto-substitution.

        skip_export: If True, disable auto-export for analysis commands (list, cascades, search).

            Default False. When False, these commands auto-export to MCP_Tool_Reply/ck3_logs/ and

            return a slim summary with report_path instead of full data.

    Returns:

        Dict with results based on command (plus export_path if export_to was used)

    """

    from ck3lens.unified_tools import ck3_logs_impl

    trace_info = get_current_trace_info()

    rb = ReplyBuilder(trace_info, tool='ck3_logs')

    reply = ck3_logs_impl(

        source=source,

        command=command,

        priority=priority,

        category=category,

        mod_filter=mod_filter,

        mod_filter_exact=mod_filter_exact,

        exclude_cascade_children=exclude_cascade_children,

        query=query,

        crash_id=crash_id,

        lines=lines,

        from_end=from_end,

        limit=limit,

        export_to=export_to,

        skip_export=skip_export,

        session=_get_session(),

        rb=rb,

    )

    # Forward Reply (already built with our rb)

    if reply.reply_type == "I":

        return rb.invalid(reply.code, data=reply.data, message=reply.message)

    if reply.reply_type != "S":

        return rb.error(reply.code, data=reply.data, message=reply.message)

    return rb.success(reply.code, data=reply.data, message=reply.message)

# ============================================================================

# ck3_conflicts - Unified Conflict Detection

# ============================================================================

@mcp.tool()

@mcp_safe_tool

def ck3_conflicts(

    command: Literal["symbols", "files", "summary", "diff", "diff_index"] = "symbols",

    # Filters

    symbol_type: str | None = None,

    symbol_names: list[str] | None = None,

    path: str | None = None,

    # Diff parameters

    relpath: str | None = None,

    old_cvid: str | None = None,

    new_cvid: str | None = None,

    diff_mode: Literal["lines", "ast", "both"] = "both",

    output: Literal["summary", "artifact", "html"] = "summary",

    source: Literal["database", "filesystem"] = "database",

    old_path: str | None = None,

    new_path: str | None = None,

    normalize_indent: bool = True,

    context_lines: int = 4,

    output_dir: str | None = None,

    # Options

    limit: int = 100,

) -> Reply:

    """

    Unified conflict detection for the active playset.

    Commands:

    command=symbols    → Find symbols defined by multiple mods (default)

    command=files      → Find file-level conflicts using original-file-aware detection

    command=summary    → Get conflict statistics

    command=diff       → Diff one file between two CVIDs (line/AST modes)

    command=diff_index → Summarize changed files under path prefix between two CVIDs

    Args:

        command: Operation to perform

        symbol_type: Filter by symbol type (trait, event, decision, on_action, etc.)

        symbol_names: Filter to specific symbols (for detailed analysis)

        path: Filter by CK3 folder (e.g., "common/traits", "events"). For diff_index: path prefix scope.

        relpath: For command=diff (database source), exact relpath to diff between CVIDs

        old_cvid: For diff/diff_index (database source), old content version id

        new_cvid: For diff/diff_index (database source), new content version id

        diff_mode: For diff/diff_index, "lines", "ast", or "both"

        output: For diff, "summary", full "artifact", or disk-written report "html"

        source: Diff source route: "database" or "filesystem"

        old_path: For source=filesystem + command=diff, old canonical path (root:/mod:)

        new_path: For source=filesystem + command=diff, new canonical path (root:/mod:)

        normalize_indent: For line diff, expand tabs before matching

        context_lines: Context lines per hunk for line diff

        output_dir: Optional report directory under root:ck3raven_data (for output="html")

        limit: Max conflicts (or diff_index entries) to return (default 100)

    Returns:

        command=symbols:

            {

                "conflict_count": int,

                "conflicts": [

                    {

                        "name": str,

                        "symbol_type": str,

                        "source_count": int,

                        "policy": str,  # OVERRIDE, FIOS, CONTAINER_MERGE, PER_KEY_OVERRIDE

                        "last_loaded": str,  # mod loaded last for this identity (approx)

                        "sources": [{"mod": str, "file": str, "line": int, "load_order": int, "is_last_loaded": bool}],

                    }

                ]

            }

        command=files:

            {

                "conflicts": [

                    {

                        "directory": str,

                        "original_file": str,

                        "original_relpath": str,

                        "original_source": {"mod": str, "load_order": int,

                                            "origin_kind": "primary"|"secondary",

                                            "patches": str|absent},  # secondary only: filename of patched original

                        "policy": str,

                        "overrides": [

                            {"type": "full_override"|"partial_override", "filename": str, "relpath": str,

                             "mod": str, "load_order": int, "is_winner": bool, "prefix": str}

                        ],

                        "warnings": [

                            {"type": "failed_patch_warning", "filename": str, "relpath": str,

                             "suffix": str, "mod": str, "load_order": int, "reason": str}

                        ]

                    }

                ],

                "summary": {

                    "total_originals_with_conflicts": int,

                    "full_overrides": int,

                    "partial_overrides": int,

                    "failed_patch_warnings": int,

                }

            }

        command=summary:

            {

                "total_symbol_conflicts": int,

                "by_type": {"trait": 5, "event": 3, ...},

                "file_conflict_summary": {

                    "total_originals_with_conflicts": int,

                    "full_overrides": int,

                    "partial_overrides": int,

                    "failed_patch_warnings": int,

                }

            }

    Examples:

        ck3_conflicts()  # All symbol conflicts

        ck3_conflicts(symbol_type="on_action")  # Only on_action conflicts

        ck3_conflicts(symbol_names=["brave", "craven"])  # Specific symbols

        ck3_conflicts(command="files", path="common/traits")  # File conflicts in traits

        ck3_conflicts(command="summary")  # Overview statistics

    """

    from ck3lens.unified_tools import ck3_conflicts_impl

    trace_info = get_current_trace_info()

    rb = ReplyBuilder(trace_info, tool='ck3_conflicts')

    session = _get_session()

    try:

        db = _get_db()

    except Exception:

        db = None

    return ck3_conflicts_impl(

        command=command,

        symbol_type=symbol_type,

        symbol_names=symbol_names,

        path=path,

        relpath=relpath,

        old_cvid=old_cvid,

        new_cvid=new_cvid,

        diff_mode=diff_mode,

        output=output,

        source=source,

        old_path=old_path,

        new_path=new_path,

        normalize_indent=normalize_indent,

        context_lines=context_lines,

        output_dir=output_dir,

        limit=limit,

        session=session,

        db=db,

        trace=_get_trace(),

        rb=rb,

    )

# ============================================================================

# Unified File Operations

# ============================================================================

@mcp.tool()

@mcp_safe_tool

def ck3_file(

    command: Literal["read", "write", "edit", "delete", "rename", "create_patch", "create_patch_batch", "index"],

    # Path identification (v2 canonical addressing)

    path: str | None = None,

    mod_name: str | None = None,  # Destination mod for create_patch / create_patch_batch

    # For read/write

    content: str | dict | list | None = None,

    start_line: int = 1,

    end_line: int | None = None,

    max_bytes: int = 200000,

    # For edit

    old_content: str | dict | list | None = None,

    new_content: str | dict | list | None = None,

    # For rename

    new_path: str | None = None,

    # For write/edit

    validate_syntax: bool = True,

    # For create_patch (ck3lens mode only)

    patch_mode: Literal["partial_patch", "full_replace"] | None = None,

    # For create_patch_batch (ck3lens mode only)

    patches: list[dict] | None = None,  # List of {path, patch_mode} dicts

    # For index command

    parse: bool = False,  # If True, also output full CST JSON

    output_path: str | None = None,  # Optional output directory for index files

) -> Reply:

    """

    Unified file operations tool (v2 canonical addressing).

    All paths use canonical format: root:<key>/path or mod:<name>/path

    Commands:

    command=read              → Read file from filesystem (path required)

    command=write             → Write file (path, content required)

    command=edit              → Search-replace edit (path, old_content, new_content required)

    command=delete            → Delete file (path required)

    command=rename            → Rename/move file (path, new_path required)

    command=create_patch      → Create override patch file (ck3lens mode only, path=source file)

    command=create_patch_batch→ Create multiple patches in one operation (ck3lens mode only)

    command=index             → Index file structure (outputs INDEX JSON, optionally CST JSON)

    Args:

        command: Operation to perform

        path: Canonical file path (root:key/path or mod:Name/path). For create_patch, this is the SOURCE file.

        mod_name: Destination mod name for create_patch / create_patch_batch

        content: File content (for write). Accepts dict/list (auto-serialized to JSON).

        start_line: Start line for read (1-indexed)

        end_line: End line for read (inclusive)

        max_bytes: Max bytes to return

        old_content: Content to find (for edit)

        new_content: Replacement content (for edit)

        new_path: New canonical path (for rename)

        validate_syntax: Validate CK3 syntax before write/edit

        patch_mode: "partial_patch" (zzz_ prefix) or "full_replace" (same name) — for create_patch

        patches: List of {path, patch_mode} dicts — for create_patch_batch

    Returns:

        Reply with results based on command

    """

    from ck3lens.unified_tools import ck3_file_impl

    import json as _json

    trace_info = get_current_trace_info()

    rb = ReplyBuilder(trace_info, tool='ck3_file')

    # ── Auto-serialize structured content to JSON strings ──

    def _ensure_str(val: str | dict | list | None) -> str | None:

        if val is None:

            return None

        if isinstance(val, (dict, list)):

            return _json.dumps(val, indent=2, ensure_ascii=False) + "\n"

        return val

    content = _ensure_str(content)

    old_content = _ensure_str(old_content)

    new_content = _ensure_str(new_content)

    session = _get_session()

    trace = _get_trace()

    result = ck3_file_impl(

        command=command,

        path=path,

        mod_name=mod_name,

        content=content,

        start_line=start_line,

        end_line=end_line,

        max_bytes=max_bytes,

        old_content=old_content,

        new_content=new_content,

        new_path=new_path,

        validate_syntax=validate_syntax,

        patch_mode=patch_mode,

        patches=patches,

        parse=parse,

        output_path=output_path,

        session=session,

        trace=trace,

        rb=rb,

    )

    # Notify daemon about mod file changes for live rebuild.

    # Resolve through WA v2 so the daemon receives only mod_name + relative_path.

    if (isinstance(result, Reply)

            and result.is_success

            and command in ("write", "edit")

            and path and path.startswith("mod:")):

        try:

            from ck3lens.daemon_client import DaemonClient

            wa2 = _get_world_v2()

            daemon = DaemonClient()

            s_reply, s_ref = wa2.resolve(path, require_exists=True, rb=rb)

            if s_ref is not None:

                rel_path = s_reply.data.get("relative_path", "")

                mod_name = s_reply.data.get("mod_name")

                if rel_path and mod_name:

                    daemon.enqueue_files(

                        paths=[rel_path],

                        mod_name=mod_name,

                        priority="high",

                        reason="file_changed",

                    )

        except Exception:

            pass  # Daemon may not be running; watcher will catch it

    # If impl returned a Reply directly, pass it through

    if isinstance(result, Reply):

        return result

    if result.get("error") or result.get("reply_type") == "D":

        err = str(result.get("error", "File operation failed"))

        err_lower = err.lower()

        policy_decision = result.get("policy_decision", "")

        visibility = result.get("visibility", "")

        # Pre-enforcement mode denial (mode not initialized)

        if policy_decision == "DENY":

            return rb.denied('EN-WRITE-D-001', data=result, message=err)

        # Invalid reference / not found

        if visibility == "NOT_FOUND" or "not found" in err_lower or "unknown" in err_lower:

            return rb.invalid('WA-RES-I-001', data=result, message=err)

        # System failure requires POSITIVE evidence

        if "failed to" in err_lower or "timeout" in err_lower or "connection" in err_lower or "exception" in err_lower:

            return rb.error('MCP-SYS-E-001', data=result, message=err)

        # Default: Invalid (agent mistake / bad input)

        return rb.invalid('WA-RES-I-001', data=result, message=err)

    # Determine if this was a read or write operation for correct code

    # Write operations pass through enforcement -> EN layer owns success

    if command in ("write", "edit", "delete", "rename"):

        return rb.success('EN-WRITE-S-001', data=result, message=f"File {command} complete.")

    else:

        return rb.success('WA-READ-S-001', data=result, message=f"File {command} complete.")

# ============================================================================

# DELETED: ck3_folder (March 17, 2026)

# ============================================================================

# Removed — ck3_folder list was redundant with ck3_dir list.

# Both resolved canonical addresses via WA2 and listed filesystem directories.

# ck3_dir is the canonical directory navigation tool.

# ============================================================================

# ============================================================================

# Unified Playset Operations

# ============================================================================

@mcp.tool()

@mcp_safe_tool

def ck3_playset(

    command: Literal["get", "list", "switch", "mods", "add_mod", "remove_mod", "reorder", "create", "import", "reload"] = "get",

    # For switch/add_mod/remove_mod/reorder

    playset_name: str | None = None,

    mod_name: str | None = None,

    # For reorder

    new_position: int | None = None,

    # For create

    name: str | None = None,

    description: str | None = None,

    mod_ids: list[str] | None = None,

    # For import

    launcher_playset_name: str | None = None,

    # For mods command

    limit: int | None = None,

) -> Reply:

    """

    Unified playset operations tool.

    Works in both modes:

    - ck3lens mode: Full playset management for mod compatibility work

    - ck3raven-dev mode: Playset context available for parser/ingestion testing

    Commands:

    command=get        ? Get active playset info

    command=list       ? List all playsets

    command=switch     ? Switch to different playset (playset_name required)

    command=mods       ? Get mods in active playset

    command=add_mod    ? Add mod to playset (mod_name required)

    command=remove_mod ? Remove mod from playset (mod_name required)

    command=reorder    ? Change mod load order (mod_name, new_position required)

    command=create     ? Create new playset (name required)

    command=import     ? Import playset from CK3 launcher

    command=reload     ? Reload playset into memory (re-resolves cvids from DB)

    Args:

        command: Operation to perform

        playset_name: Playset name (for switch)

        mod_name: Mod name (for add_mod/remove_mod/reorder)

        new_position: New load order position (for reorder, 0-indexed)

        name: New playset name (for create)

        description: Playset description (for create)

        mod_ids: List of content_version_ids (for create)

        launcher_playset_name: Launcher playset to import (for import)

        limit: Max mods to return for 'mods' command (default: None = all mods)

    Returns:

        Dict with playset info or operation result

    """

    trace_info = get_current_trace_info()

    rb = ReplyBuilder(trace_info, tool='ck3_playset')

    result = _ck3_playset_internal(

        command, playset_name, mod_name, new_position,

        name, description, mod_ids, launcher_playset_name, limit

    )

    if result.get("invalid"):

        return rb.invalid('WA-VIS-I-001', data=result, message=result["invalid"])

    if result.get("error"):

        fatal_error = result.pop("error")
        result.setdefault("errors", []).append({
            "code": "PLAYSET_OPERATION_FAILED",
            "message": fatal_error,
        })
        return rb.error('MCP-SYS-E-001', data=result, message=fatal_error)

    if command == "switch":

        message = f"Switched to playset: {result.get('playset_name')}"

    elif command == "reload":

        message = f"Session reloaded: {result.get('mod_count', 0)} mods, {result.get('cvid_resolved', 0)} cvids resolved"

    elif command == "get":

        name_val = result.get("playset_name") or result.get("name")

        message = f"Active playset: {name_val}"

    else:

        message = f"Playset {command} complete."

    if result.get("exclusion_notice"):
        message += f". {result['exclusion_notice']}"
    if result.get("errors"):
        message += f" Loading reported {len(result['errors'])} error(s); see errors for details."
    return rb.success('WA-VIS-S-001', data=result, message=message)

def _ck3_playset_internal(

    command: str,

    playset_name: str | None,

    mod_name: str | None,

    new_position: int | None,

    name: str | None,

    description: str | None,

    mod_ids: list[str] | None,

    launcher_playset_name: str | None,

    limit: int | None,

) -> dict:

    """Internal implementation returning dict."""

    global _session

    from pathlib import Path

    trace = _get_trace()

    # FILE-BASED IMPLEMENTATION (database is DEPRECATED)

    if command == "list":

        # List all available playsets

        playsets = []

        for f in PLAYSETS_DIR.glob("*.json"):

            if f.name.endswith(".schema.json") or f.name == "playset_manifest.json" or f.name == "sub_agent_templates.json":

                continue

            try:

                data = json.loads(f.read_text(encoding="utf-8-sig"))

                mod_entries = [m for m in data.get("mods", []) if m.get("enabled", True)]

                playsets.append({

                    "filename": f.name,

                    "name": data.get("playset_name", f.stem),

                    "description": data.get("description", ""),

                    "mod_count": len(mod_entries),

                })

            except Exception as e:

                playsets.append({

                    "filename": f.name,

                    "name": f.stem,

                    "error": str(e),

                })

        return {

            "success": True,

            "playsets": playsets,

            "manifest_path": str(PLAYSET_MANIFEST_FILE),

        }

    elif command == "switch":

        # Switch to a different playset by updating manifest

        # Automatically checks build status and starts builder if mods need processing

        if not playset_name:

            return {"success": False, "invalid": "playset_name required for switch"}

        # Find the playset file

        target_file = None

        playset_data = None

        for f in PLAYSETS_DIR.glob("*.json"):

            if f.name.endswith(".schema.json") or f.name == "playset_manifest.json":

                continue

            # Match by filename or playset_name in content

            if f.name == playset_name or f.stem == playset_name:

                target_file = f

                try:

                    playset_data = json.loads(f.read_text(encoding="utf-8-sig"))

                except Exception:

                    pass

                break

            try:

                data = json.loads(f.read_text(encoding="utf-8-sig"))

                if data.get("playset_name") == playset_name:

                    target_file = f

                    playset_data = data

                    break

            except Exception:

                pass

        if not target_file:

            return {"success": False, "invalid": f"Playset '{playset_name}' not found"}

        # Hydrate and inspect the target without replacing the active session.

        from ck3lens.workspace import load_session

        try:

            target_session = load_session(
                playset_path=target_file,
                conn=_get_db().conn,
            )

        except Exception as error:

            return {

                "success": False,

                "error": f"Could not prepare playset '{target_file.name}': {error}",

                "active_playset_unchanged": True,

            }

        session = target_session

        # Check build status for all mods in this playset

        # NOTE: Inlined check logic (January 2026) - avoids qbuilder import hang

        build_status = None

        mods_needing_build = []

        mods_missing_from_disk = []

        db_available = False

        try:

            db = _get_db()

            if db and playset_data:

                db_available = True

                conn = db.conn

                mods = session.mods[1:]

                for mod in mods:

                    mod_name_check = mod.name

                    mod_path_check = str(mod.path)

                    exists_on_disk = mod.path.exists()

                    if not exists_on_disk:

                        mods_missing_from_disk.append(mod_name_check)

                        continue

                    # Check if mod has files in DB using source_path (deterministic)

                    # (matches _ensure_cvid lookup pattern - never rely on name)

                    try:

                        row = conn.execute("""

                            SELECT COUNT(*) as file_count,

                                   (SELECT COUNT(*) FROM build_queue bq 

                                    JOIN files f ON bq.file_id = f.file_id

                                    WHERE f.content_version_id = cv.content_version_id 

                                    AND bq.status = 'pending') as pending_count

                            FROM files f

                            JOIN content_versions cv ON f.content_version_id = cv.content_version_id

                            WHERE cv.source_path = ?

                        """, (mod_path_check,)).fetchone()

                        file_count = row[0] if row else 0

                        pending = row[1] if row else 0

                    except:

                        file_count = 0

                        pending = 0

                    if file_count == 0:

                        mods_needing_build.append({

                            "name": mod_name_check,

                            "status": "not_indexed",

                            "path": mod_path_check

                        })

                    elif pending > 0:

                        mods_needing_build.append({

                            "name": mod_name_check,

                            "status": "pending_build",

                            "path": mod_path_check

                        })

                # Calculate statistics for build_status

                total_mods = len(mods)

                missing_count = len(mods_missing_from_disk)

                pending_count = sum(1 for m in mods_needing_build if m.get("status") == "pending_build")

                not_indexed_count = sum(1 for m in mods_needing_build if m.get("status") == "not_indexed")

                ready_count = total_mods - missing_count - len(mods_needing_build)

                build_status = {

                    "needs_build": len(mods_needing_build) > 0,

                    "mods": mods_needing_build,

                    # Add the keys that the result construction expects:

                    "playset_valid": total_mods > 0 and missing_count < total_mods,

                    "ready_mods": max(0, ready_count),

                    "pending_mods": pending_count + not_indexed_count,

                    "missing_mods": missing_count,

                }

        except Exception as e:

            # DB might not be available yet - need to build everything

            build_status = {"error": str(e), "needs_build": True}

        result = {

            "success": True,

            "message": f"Switched to playset: {target_file.name}",

            "active_playset": target_file.name,

            "playset_name": session.playset_name,

            "mod_count": len(session.mods),

            **session.get_load_report(),

        }

        # Report missing-from-disk mods (these cannot be built)

        if mods_missing_from_disk:

            result["mods_missing_from_disk"] = mods_missing_from_disk

            result["missing_warning"] = (

                f"⚠ {len(mods_missing_from_disk)} mod(s) are in playset but not on disk. "

                f"These mods will be skipped: {mods_missing_from_disk}"

            )

        if mods_needing_build or not db_available:
            result["qbuilder_required"] = True
            result["qbuilder_message"] = "QBuilder is preparing the selected playset"

        # Add build status information

        if build_status and not build_status.get("error"):

            result["build_status"] = {

                "playset_valid": build_status.get("playset_valid", False),

                "ready_mods": build_status.get("ready_mods", 0),

                "pending_mods": build_status.get("pending_mods", 0),

                "missing_mods": build_status.get("missing_mods", 0),

            }

        if not mods_needing_build and not mods_missing_from_disk and db_available and build_status and not build_status.get("error"):

            result["build_status_message"] = "✓ All mods are ready. No build needed."

        scan_result = None
        scan_error = None
        try:

            from ck3lens.daemon_client import daemon as daemon_client

            if not daemon_client.is_available(force_check=True):
                raise RuntimeError("QBuilder availability invariant was not satisfied")
            scan_result = daemon_client.playset_scan(playset_file=target_file.name)

        except Exception as error:

            scan_error = str(error)

        if scan_error:
            return {
                "success": False,
                "error": scan_error,
                "playset_name": session.playset_name,
                "active_playset_unchanged": True,
            }
        if scan_result is None or not scan_result.get("ready", False):
            return {
                "success": False,
                "error": "Target playset contains mods that could not receive CVIDs",
                "playset_name": session.playset_name,
                "missing": scan_result.get("missing", []) if scan_result else [],
                "active_playset_unchanged": True,
            }

        target_cvids = [item["cvid"] for item in scan_result.get("identities", [])]
        ingestion = _get_ingestion_status(target_cvids)

        manifest = {
            "active": target_file.name,
            "last_switched": datetime.now().isoformat(),
            "notes": "Updated by ck3_playset switch command",
        }
        PLAYSET_MANIFEST_FILE.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        _init_session_internal()
        active_session = _get_session()

        result.update({
            "message": (
                f"Switched to playset: {target_file.name}"
                if ingestion["ready"]
                else f"Switched to playset: {target_file.name}; ingestion is running"
            ),
            "playset_name": active_session.playset_name,
            "mod_count": len(active_session.mods),
            "cvids_ready": active_session.is_fully_indexed(),
            "ingestion": ingestion,
            "ingestion_pending": not ingestion["ready"],
            "playset_scan": scan_result,
            **active_session.get_load_report(),
        })
        return result

    elif command == "get":

        # Get current active playset info

        session = _get_session()

        manifest_active = None

        if PLAYSET_MANIFEST_FILE.exists():

            try:

                manifest = json.loads(PLAYSET_MANIFEST_FILE.read_text(encoding='utf-8-sig'))

                manifest_active = manifest.get("active")

            except Exception:

                pass

        # Get vanilla root from session.vanilla (mods[0])

        vanilla_root = None

        if session.vanilla and session.vanilla.path:

            vanilla_root = str(session.vanilla.path)

        return {

            "success": True,

            "active_file": manifest_active,

            "playset_name": session.playset_name,

            "source": "json" if session.playset_name else "none",

            "mod_count": len(session.mods),

            "has_agent_briefing": False,  # TODO: Implement agent briefing in Session

            "vanilla_root": vanilla_root,

            "local_mods_folder": str(session.local_mods_folder) if session.local_mods_folder else None,

            **session.get_load_report(),

        }

    elif command == "mods":

        # Get mods in active playset

        session = _get_session()

        # All mods in session.mods are "enabled" - they're part of the active playset

        # The concept of enabled/disabled doesn't apply here - disabled mods wouldn't be loaded

        mods_list = session.mods

        # Convert ModEntry to dict for JSON serialization

        def mod_to_dict(m):

            return {

                "name": m.name,

                "path": str(m.path) if m.path else None,

                "load_order": m.load_order,

                "workshop_id": m.workshop_id,

                "cvid": m.cvid,

                "is_indexed": m.is_indexed,

            }

        mods_to_return = mods_list[:limit] if limit else mods_list

        return {

            "success": True,

            "playset_name": session.playset_name,

            "mod_count": len(mods_list),

            "mods": [mod_to_dict(m) for m in mods_to_return],

            **session.get_load_report(),

            "truncated": limit is not None and len(mods_list) > limit,

        }

    elif command == "add_mod":

        # Add a mod to the active playset's local_mods array

        if not mod_name:

            return {"success": False, "invalid": "mod_name required for add_mod"}

        # Get the active playset file

        session = _get_session()

        if not session.playset_name:

            return {"success": False, "invalid": "No active playset. Switch to one first."}

        # Find the active playset file

        if not PLAYSET_MANIFEST_FILE.exists():

            return {"success": False, "invalid": "No playset manifest found"}

        try:

            manifest = json.loads(PLAYSET_MANIFEST_FILE.read_text(encoding="utf-8-sig"))

            active_file = manifest.get("active")

            if not active_file:

                return {"success": False, "invalid": "No active playset in manifest"}

            playset_path = PLAYSETS_DIR / active_file

            if not playset_path.exists():

                return {"success": False, "invalid": f"Active playset file not found: {active_file}"}

            original_playset_content = playset_path.read_text(encoding="utf-8-sig")
            playset_data = json.loads(original_playset_content)

        except Exception as e:

            return {"success": False, "error": f"Failed to read playset: {e}"}

        # Check if mod_name is a path or a name

        mod_path = None

        if Path(mod_name).exists():

            # It's a path - normalize it via canonical utility

            mod_path = str(Path(mod_name).resolve())  # Needed for absolute path

            # Try to get the actual mod name from its descriptor

            try:

                mod_name, _ = _read_mod_descriptor(Path(mod_path))

            except Exception:

                pass

        else:

            # It's a name - try to find the mod path

            # Look in common mod locations

            from ck3lens.paths import ROOT_USER_DOCS

            mod_dirs = [

                ROOT_USER_DOCS / "mod",

            ] if ROOT_USER_DOCS else []

            for mod_dir in mod_dirs:

                if not mod_dir.exists():

                    continue

                # Check each folder for a matching descriptor

                for folder in mod_dir.iterdir():

                    if not folder.is_dir():

                        continue

                    try:

                        name_in_desc, _ = _read_mod_descriptor(folder)

                        if name_in_desc.lower() == mod_name.lower():

                            mod_path = str(folder.resolve())  # Needed for absolute path

                            mod_name = name_in_desc  # Use canonical name

                    except Exception:

                        pass

                    if mod_path:

                        break

                if mod_path:

                    break

        if not mod_path:

            return {

                "success": False, 

                "invalid": f"Could not find mod '{mod_name}'. Provide full path or ensure mod is installed.",

                "hint": "Use full path to mod folder, e.g., 'C:\\...\\mod\\MyModFolder'"

            }

        # Check if already in mods[]

        mods_list = playset_data.get("mods", [])

        for existing in mods_list:

            if existing.get("name", "").lower() == mod_name.lower() or existing.get("path") == mod_path:

                return {

                    "success": False,

                    "invalid": f"Mod '{mod_name}' is already in mods[]",

                    "existing_entry": existing

                }

        # Calculate next load order

        max_load_order = max((m.get("load_order", 0) for m in mods_list), default=-1)

        # Add to mods[] (THE list, not a separate local_mods array)

        new_mod_entry = {

            "name": mod_name,

            "path": mod_path,

            "load_order": max_load_order + 1,

            "enabled": True,

            "is_compatch": False,

            "notes": f"Added by ck3_playset add_mod on {datetime.now().isoformat()}"

        }

        mods_list.append(new_mod_entry)

        playset_data["mods"] = mods_list

        # Write back

        try:

            playset_path.write_text(json.dumps(playset_data, indent=2), encoding="utf-8")

        except Exception as e:

            return {"success": False, "error": f"Failed to write playset: {e}"}

        try:
            from ck3lens.daemon_client import daemon as daemon_client

            if not daemon_client.is_available(force_check=True):
                raise RuntimeError(
                    "QBuilder daemon is not running; start it with "
                    "ck3_qbuilder(command='build')"
                )
            scan_result = daemon_client.playset_scan(playset_file=active_file)
            if not scan_result.get("ready", False):
                raise RuntimeError(
                    f"Could not assign CVIDs: {scan_result.get('missing', [])}"
                )
        except Exception as error:
            playset_path.write_text(original_playset_content, encoding="utf-8")
            return {
                "success": False,
                "error": f"Could not add '{mod_name}': {error}",
                "playset_rolled_back": True,
            }

        target_cvids = [item["cvid"] for item in scan_result.get("identities", [])]
        ingestion = _get_ingestion_status(target_cvids)
        if ingestion["ready"]:
            _init_session_internal()

        # Check if mod needs building - pure SQL, no qbuilder import (January 2026)

        build_needed = False

        try:

            db = _get_db()

            if db:

                # Check if mod has files in DB

                row = db.conn.execute("""

                    SELECT COUNT(*) as file_count

                    FROM files f

                    JOIN content_versions cv ON f.content_version_id = cv.content_version_id

                    WHERE cv.name = ?

                """, (mod_name,)).fetchone()

                file_count = row[0] if row else 0

                build_needed = (file_count == 0)

        except Exception:

            build_needed = True  # Assume needs build if we can't check

        result = {

            "success": True,

            "message": f"Added '{mod_name}' to mods[]",

            "mod_entry": new_mod_entry,

            "mods_count": len(mods_list),

        }

        result["playset_scan"] = scan_result
        result["ingestion"] = ingestion
        result["session_updated"] = ingestion["ready"]

        if not ingestion["ready"]:
            result["message"] = (
                f"Added '{mod_name}' to the playset; ingestion is running and "
                "the live session is unchanged"
            )
            result["next_action"] = (
                "Run ck3_qbuilder(command='await'), then "
                "ck3_playset(command='reload')"
            )

        if build_needed:

            result["build_needed"] = True
            result["build_status"] = "Ingestion is running automatically"

        return result

    elif command == "import":

        # Import playset from CK3 launcher database

        # Uses read-only connection - safe for concurrent launcher access

        from pathlib import Path

        from datetime import date

        from ck3lens.paths import ROOT_USER_DOCS, ROOT_STEAM, ROOT_GAME

        if not ROOT_USER_DOCS or not ROOT_STEAM or not ROOT_GAME:

            return {"success": False, "invalid": "Missing path configuration (ROOT_USER_DOCS, ROOT_STEAM, or ROOT_GAME). Run ck3_paths_doctor() to diagnose."}

        launcher_db = ROOT_USER_DOCS / "launcher-v2.sqlite"

        if not launcher_db.exists():

            return {"success": False, "invalid": f"Launcher database not found: {launcher_db}"}

        # Connect in read-only mode (safe for concurrent access)

        import sqlite3

        try:

            conn = sqlite3.connect(f"file:{launcher_db}?mode=ro", uri=True)

            conn.row_factory = sqlite3.Row

        except Exception as e:

            return {"success": False, "error": f"Failed to connect to launcher DB: {e}"}

        try:

            # If no playset name specified, list available playsets

            if not launcher_playset_name:

                rows = conn.execute("""

                    SELECT id, name, isActive,

                        (SELECT COUNT(*) FROM playsets_mods WHERE playsetId = playsets.id) as mod_count

                    FROM playsets

                    ORDER BY name

                """).fetchall()

                available = []

                for row in rows:

                    available.append({

                        "name": row["name"],

                        "mod_count": row["mod_count"],

                        "is_active": bool(row["isActive"]),

                    })

                return {

                    "success": True,

                    "message": "Use launcher_playset_name parameter to import a specific playset",

                    "available_playsets": available,

                    "hint": "ck3_playset(command='import', launcher_playset_name='YourPlaysetName')"

                }

            # Find the requested playset

            # Try exact match first

            row = conn.execute(

                "SELECT id, name, isActive FROM playsets WHERE name = ?", 

                (launcher_playset_name,)

            ).fetchone()

            if not row:

                # Try case-insensitive partial match

                row = conn.execute(

                    "SELECT id, name, isActive FROM playsets WHERE name LIKE ?", 

                    (f"%{launcher_playset_name}%",)

                ).fetchone()

            if not row:

                return {"success": False, "invalid": f"Playset '{launcher_playset_name}' not found in launcher DB"}

            playset_id = row["id"]

            playset_name_actual = row["name"]

            # Get all mods in this playset

            mods_rows = conn.execute("""

                SELECT pm.position, pm.enabled, m.displayName, m.steamId, m.dirPath, m.source

                FROM playsets_mods pm

                JOIN mods m ON pm.modId = m.id

                WHERE pm.playsetId = ?

                ORDER BY pm.position

            """, (playset_id,)).fetchall()

            # Build mods array - only include ENABLED mods

            mods_list = []

            disabled_mods = []  # Track disabled mods for advisory

            steam_count = 0

            local_count = 0

            local_positions = []

            for mod_row in mods_rows:

                position = mod_row["position"]

                enabled = bool(mod_row["enabled"])

                display_name = mod_row["displayName"]

                steam_id = mod_row["steamId"]

                dir_path = mod_row["dirPath"]

                source = mod_row["source"]

                # Skip disabled mods - they should not be in the playset

                if not enabled:

                    disabled_mods.append(display_name)

                    continue

                # Determine path

                is_local = source in ("local", None) or (dir_path and "mod" in str(dir_path).lower() and "workshop" not in str(dir_path).lower())

                if steam_id and not is_local:

                    # Steam workshop mod

                    mod_path = str(ROOT_STEAM / steam_id)

                    steam_count += 1

                elif dir_path:

                    # Local mod - use dirPath

                    mod_path = str(Path(dir_path).resolve())

                    local_count += 1

                    local_positions.append(position)

                    is_local = True

                else:

                    # Fallback - try to construct path

                    mod_path = str(ROOT_USER_DOCS / "mod" / display_name.replace(" ", ""))

                    local_count += 1

                    local_positions.append(position)

                    is_local = True

                # Detect compatch

                name_lower = display_name.lower()

                is_compatch = any(kw in name_lower for kw in ["compatch", "compatibility", "patch"]) and "unofficial" not in name_lower

                mod_entry = {

                    "name": display_name,

                    "path": mod_path.replace("/", "\\"),

                    "load_order": position,

                    "is_compatch": is_compatch,

                    "notes": "local mod" if is_local else "",

                }

                if steam_id and not is_local:

                    mod_entry["steam_id"] = steam_id

                mods_list.append(mod_entry)

            # Build playset JSON

            today = date.today().isoformat()

            safe_name = playset_name_actual.replace(" ", "_").replace("/", "_")

            output_filename = f"{safe_name}_playset.json"

            output_path = PLAYSETS_DIR / output_filename

            playset_data = {

                "$schema": "./playset.schema.json",

                "playset_name": playset_name_actual,

                "description": f"Imported from CK3 Launcher DB on {today} ({steam_count} Steam, {local_count} local mods)",

                "created": today,

                "last_modified": today,

                "vanilla": {

                    "path": str(ROOT_GAME),

                    "enabled": True

                },

                "mods": mods_list,

                "local_mods_folder": str(ROOT_USER_DOCS / "mod"),

                "agent_briefing": {

                    "context": f"Playset '{playset_name_actual}' with {len(mods_list)} mods",

                    "error_analysis_notes": [

                        "Focus on runtime errors during gameplay",

                        "Loading errors from compatch targets may be expected"

                    ],

                    "conflict_resolution_notes": [

                        "Mods marked is_compatch=true are expected to override others"

                    ],

                    "mod_relationships": [],

                    "priorities": [

                        "1. Crashes and game-breaking errors",

                        "2. Gameplay-affecting bugs during steady play",

                        "3. Minor visual/localization issues"

                    ],

                    "custom_instructions": ""

                },

                "sub_agent_config": {

                    "error_analysis": {

                        "enabled": True,

                        "auto_spawn_threshold": 50,

                        "output_format": "markdown",

                        "include_recommendations": True

                    },

                    "conflict_review": {

                        "enabled": False,

                        "min_risk_score": 70,

                        "require_approval": True

                    }

                }

            }

            # Write the playset file

            output_path.write_text(json.dumps(playset_data, indent=2), encoding="utf-8")

            result = {

                "success": True,

                "message": f"Imported and prepared playset '{playset_name_actual}'",

                "playset_file": str(output_path),

                "total_mods": len(mods_list),

                "steam_mods": steam_count,

                "local_mods": local_count,

                "local_positions": local_positions,

            }

            try:
                from ck3lens.daemon_client import daemon as daemon_client

                if not daemon_client.is_available(force_check=True):
                    raise RuntimeError(
                        "QBuilder daemon is not running; start it with "
                        "ck3_qbuilder(command='build')"
                    )
                preparation = daemon_client.playset_scan(playset_file=output_path.name)
                result["preparation"] = preparation
                result["identity_ready"] = preparation.get("ready", False)
                if result["identity_ready"]:
                    target_cvids = [
                        item["cvid"] for item in preparation.get("identities", [])
                    ]
                    ingestion = _get_ingestion_status(target_cvids)
                    result["ingestion"] = ingestion
                    result["ready_for_switch"] = ingestion["ready"]
                    if result["ready_for_switch"]:
                        result["hint"] = (
                            "Switch with: ck3_playset(command='switch', "
                            f"playset_name='{playset_name_actual}')"
                        )
                    else:
                        result["message"] = (
                            f"Imported playset '{playset_name_actual}'; ingestion is running"
                        )
                        result["next_action"] = (
                            "Run ck3_qbuilder(command='await'), then switch to "
                            f"'{playset_name_actual}'"
                        )
                else:
                    result["success"] = False
                    result["ready_for_switch"] = False
                    result["message"] = (
                        f"Imported playset '{playset_name_actual}', but some mods "
                        "could not receive CVIDs"
                    )
            except Exception as error:
                result["success"] = False
                result["ready_for_switch"] = False
                result["preparation_error"] = str(error)
                result["message"] = (
                    f"Imported playset '{playset_name_actual}', but automatic "
                    "preparation failed"
                )

            # Add advisory about disabled mods if any were skipped

            if disabled_mods:

                result["disabled_mods_omitted"] = len(disabled_mods)

                result["disabled_mod_names"] = disabled_mods

                result["advisory"] = f"{len(disabled_mods)} disabled mod(s) were not imported: {', '.join(disabled_mods[:5])}" + (f" (and {len(disabled_mods) - 5} more)" if len(disabled_mods) > 5 else "")

            return result

        finally:

            conn.close()

    elif command == "reload":

        # Reload playset into memory: re-reads playset JSON, re-resolves cvids.

        # Use when scope_to_session_mods gate fails or after external changes.

        session_info = _init_session_internal()

        return {

            "success": True,

            "message": "Session reloaded from playset",

            "playset_name": session_info.get("playset_name"),

            "mod_count": session_info.get("mod_count", 0),

            "cvid_resolved": session_info.get("mod_count", 0) - len(

                [m for m in _session.mods if m.cvid is None]

            ) if _session else 0,

            "cvid_missing": [m.name for m in _session.mods if m.cvid is None] if _session else [],

            "db_status": session_info.get("db_status"),

            **_get_session().get_load_report(),

        }

    else:
        return {
            "success": False,
            "invalid": f"Command '{command}' not recognized",
            "hint": "Commands: get, list, switch, mods, add_mod, remove_mod, reorder, create, import, reload",
        }

# ============================================================================

# Unified Validation Operations

# ============================================================================

@mcp.tool()

@mcp_safe_tool

def ck3_validate(

    target: Literal["syntax", "python", "references", "bundle", "policy"],

    # For syntax/python

    content: str | None = None,

    file_path: str | None = None,

    # For references

    symbol_name: str | None = None,

    symbol_type: str | None = None,

    # For bundle

    artifact_bundle: dict | None = None,

    # For policy

    mode: str | None = None,

    trace_path: str | None = None,

) -> Reply:

    """

    Unified validation tool.

    Targets:

    target=syntax     ? Validate CK3 script syntax (content required)

    target=python     ? Check Python syntax (content or file_path required)

    target=references ? Validate symbol references exist (symbol_name required)

    target=bundle     ? Validate artifact bundle (artifact_bundle required)

    target=policy     ? Validate against policy rules (mode required)

    Args:

        target: What to validate

        content: Code/script content to validate

        file_path: File path (for python, or context for syntax)

        symbol_name: Symbol to look up (for references)

        symbol_type: Filter by symbol type (for references)

        artifact_bundle: Bundle dict to validate

        mode: Agent mode for policy validation

        trace_path: Path to trace file for policy validation

    Returns:

        Dict with validation results

    """

    from ck3lens.unified_tools import ck3_validate_impl

    trace_info = get_current_trace_info()

    rb = ReplyBuilder(trace_info, tool='ck3_validate')

    session = _get_session()

    trace = _get_trace()

    db = _get_db()

    return ck3_validate_impl(

        target=target,

        content=content,

        file_path=file_path,

        symbol_name=symbol_name,

        symbol_type=symbol_type,

        artifact_bundle=artifact_bundle,

        mode=mode,

        trace_path=trace_path,

        session=session,

        db=db,

        trace=trace,

        rb=rb,

    )

# ============================================================================

# VS Code IPC Operations

# ============================================================================

@mcp.tool()

@mcp_safe_tool

def ck3_vscode(

    command: Literal["ping", "diagnostics", "all_diagnostics", "errors_summary", 

                     "validate_file", "open_files", "active_file", "status"] = "status",

    # For diagnostics/validate_file

    path: str | None = None,

    # For all_diagnostics

    severity: str | None = None,

    source: str | None = None,

    limit: int = 50,

) -> Reply:

    """

    Access VS Code IDE APIs via IPC connection.

    Connects to VS Code extension's diagnostics server to query IDE state,

    get Pylance/language server diagnostics, and more.

    Commands:

    command=status         ? Check if VS Code IPC server is available

    command=ping           ? Test connection to VS Code

    command=diagnostics    ? Get diagnostics for a file (path required)

    command=all_diagnostics ? Get diagnostics for all open files

    command=errors_summary ? Get workspace error summary

    command=validate_file  ? Trigger validation for a file (path required)

    command=open_files     ? List currently open files in VS Code

    command=active_file    ? Get active file info with diagnostics

    Args:

        command: Operation to perform

        path: Absolute file path (for diagnostics/validate_file)

        severity: Filter by severity ('error', 'warning', 'info', 'hint')

        source: Filter by source (e.g., 'Pylance', 'CK3 Lens')

        limit: Max files to return for all_diagnostics

    Returns:

        Dict with results based on command. Returns helpful error if VS Code not available.

    """

    from ck3lens.unified_tools import ck3_vscode_impl

    trace_info = get_current_trace_info()

    rb = ReplyBuilder(trace_info, tool='ck3_vscode')

    result = ck3_vscode_impl(

        command=command,

        path=path,

        severity=severity,

        source=source,

        limit=limit,

        trace=None,  # Deprecated: using ReplyBuilder

    )

    if "error" in result:

        return rb.error(

            'MCP-SYS-E-001',

            data=result,

            message=result["error"],

        )

    return rb.success(

        'WA-IO-S-001',

        data=result,

        message=f"VS Code operation completed: command={command}",

    )

# ============================================================================

# CK3 Repair Tool (Launcher Registry, Cache)

# ============================================================================

@mcp.tool()

@mcp_safe_tool

def ck3_repair(

    command: Literal["query", "diagnose_launcher", "repair_registry", "delete_cache", "backup_launcher"] = "query",

    # For query - get status of repair targets

    target: Literal["all", "launcher", "cache", "dlc_load"] | None = None,

    # For repair_registry / delete_cache

    dry_run: bool = True,

    # For backup

    backup_name: str | None = None,

) -> Reply:

    """

    Repair CK3 launcher registry and cache issues.

    ?? MODE: ck3lens only. Not available in ck3raven-dev mode.

    SCOPE: Launcher domain operations only.

    - ~/.ck3raven/ directory management

    - CK3 launcher registry analysis (read-only by default)

    - Cache cleanup

    Commands:

    command=query             ? Get status of repair targets (launcher registry, cache, etc.)

    command=diagnose_launcher ? Analyze launcher database for issues

    command=repair_registry   ? Fix launcher registry entries (requires dry_run=False)

    command=delete_cache      ? Clear ck3raven cache files (requires dry_run=False)

    command=backup_launcher   ? Create backup of launcher database before repair

    Args:

        command: Action to perform

        target: What to query ("all", "launcher", "cache", "dlc_load")

        dry_run: If True (default), only show what would be done

        backup_name: Optional name for backup file

    Returns:

        Dict with repair status/results

    WARNINGS:

    - repair_registry can modify the CK3 launcher database

    - Always run diagnose_launcher first to understand issues

    - backup_launcher before any repair operations

    - Cache deletion is reversible (ck3raven will rebuild)

    """

    trace_info = get_current_trace_info()

    rb = ReplyBuilder(trace_info, tool='ck3_repair')

    return _ck3_repair_internal(command, target, dry_run, backup_name, rb=rb, trace_info=trace_info)

def _ck3_repair_internal(

    command: str,

    target: str | None,

    dry_run: bool,

    backup_name: str | None,

    *,

    rb: ReplyBuilder,

    trace_info: TraceInfo,

) -> Reply:

    """Internal implementation returning Reply."""

    import shutil

    from pathlib import Path

    from datetime import datetime

    from ck3lens.paths import ROOT_CK3RAVEN_DATA, ROOT_USER_DOCS

    trace = _get_trace()

    session = _get_session()

    ck3raven_dir = ROOT_CK3RAVEN_DATA

    # Configured in ~/.ck3raven/config/workspace.toml — fix root_user_docs there if not found
    launcher_db = ROOT_USER_DOCS / "launcher-v2.sqlite"

    if command == "query":

        # Return status of repair targets

        cache_files = list(ck3raven_dir.glob("*.cache")) if ck3raven_dir.exists() else []

        wip_files = list((ck3raven_dir / "wip").glob("**/*")) if (ck3raven_dir / "wip").exists() else []

        status = {

            "ck3raven_dir": str(ck3raven_dir),

            "ck3raven_exists": ck3raven_dir.exists(),

            "db_path": str(session.db_path) if session.db_path else None,

            "db_exists": session.db_path.exists() if session.db_path else False,

            "launcher_db": str(launcher_db),

            "launcher_db_exists": launcher_db.exists(),

            "cache_files": len(cache_files),

            "wip_files": len(wip_files),

            "repair_targets": {

                "cache": {

                    "files": len(cache_files),

                    "path": str(ck3raven_dir),

                },

                "wip": {

                    "files": len(wip_files),

                    "path": str(ck3raven_dir / "wip"),

                },

                "launcher": {

                    "path": str(launcher_db),

                    "exists": launcher_db.exists(),

                    "requires_backup": True,

                },

            },

        }

        if target == "launcher" or target == "all":

            if launcher_db.exists():

                diag_reply = _diagnose_launcher_db(launcher_db, trace_info=trace_info)

                status["launcher_details"] = diag_reply.data

        trace.log("mcp.repair", {"command": "query", "target": target}, {"success": True})

        return rb.success('MCP-SYS-S-001', data=status, message="Repair query complete.")

    elif command == "diagnose_launcher":

        if not launcher_db.exists():

            return rb.invalid('MCP-SYS-I-001', data={

                "launcher_db": str(launcher_db),

                "fix": "Set root_user_docs to your CK3 documents folder in ~/.ck3raven/config/workspace.toml",

            }, message=f"launcher-v2.sqlite not found at configured path: {launcher_db}")

        # Delegate to _diagnose_launcher_db which returns its own Reply

        return _diagnose_launcher_db(launcher_db, trace_info=trace_info)

    elif command == "backup_launcher":

        if not launcher_db.exists():

            return rb.invalid('MCP-SYS-I-001', data={

                "launcher_db": str(launcher_db),

                "fix": "Set root_user_docs to your CK3 documents folder in ~/.ck3raven/config/workspace.toml",

            }, message=f"launcher-v2.sqlite not found at configured path: {launcher_db}")

        backups_dir = ck3raven_dir / "launcher_backups"

        backups_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        name = backup_name or f"launcher-v2_{timestamp}"

        backup_path = backups_dir / f"{name}.sqlite"

        shutil.copy2(launcher_db, backup_path)

        trace.log("mcp.repair", {"command": "backup_launcher"}, {"backup_path": str(backup_path)})

        return rb.success('MCP-SYS-S-001', data={

            "backup_path": str(backup_path),

            "original_path": str(launcher_db),

            "backup_size": backup_path.stat().st_size,

        }, message="Launcher database backed up.")

    elif command == "repair_registry":

        if not launcher_db.exists():

            return rb.invalid('MCP-SYS-I-001', data={

                "launcher_db": str(launcher_db),

                "fix": "Set root_user_docs to your CK3 documents folder in ~/.ck3raven/config/workspace.toml",

            }, message=f"launcher-v2.sqlite not found at configured path: {launcher_db}")

        if dry_run:

            return rb.success('MCP-SYS-S-002', data={

                "dry_run": True,

                "recommendation": "Run backup_launcher first, then diagnose_launcher to see issues.",

            }, message="Would repair launcher registry. Set dry_run=False to proceed.")

        return rb.error('MCP-SYS-E-001', data={

            "reason": "Launcher database modifications require careful testing to avoid data loss",

            "workaround": "Use the CK3 launcher UI to reset settings, or delete ~/.ck3raven/ck3raven.db to force rebuild",

        }, message="Launcher repair not yet implemented")

    elif command == "delete_cache":

        cache_dir = ck3raven_dir

        wip_dir = ck3raven_dir / "wip"

        if dry_run:

            cache_files = list(cache_dir.glob("*.cache")) if cache_dir.exists() else []

            wip_files = list(wip_dir.rglob("*")) if wip_dir.exists() else []

            return rb.success('MCP-SYS-S-002', data={

                "dry_run": True,

                "would_delete": {

                    "cache_files": [str(f) for f in cache_files[:10]],

                    "cache_count": len(cache_files),

                    "wip_files": [str(f) for f in wip_files[:10]],

                    "wip_count": len(wip_files),

                },

            }, message="Set dry_run=False to delete these files")

        deleted = {"cache": 0, "wip": 0}

        for cache_file in cache_dir.glob("*.cache"):

            try:

                cache_file.unlink()

                deleted["cache"] += 1

            except Exception:

                pass

        if wip_dir.exists():

            for item in wip_dir.rglob("*"):

                if item.is_file():

                    try:

                        item.unlink()

                        deleted["wip"] += 1

                    except Exception:

                        pass

        trace.log("mcp.repair", {"command": "delete_cache", "dry_run": False}, deleted)

        return rb.success('MCP-SYS-S-001', data={

            "deleted": deleted,

        }, message="Cache cleared. ck3raven will rebuild as needed.")

    return rb.invalid('MCP-SYS-I-001', data={}, message=f"Unknown command: {command}")

def _diagnose_launcher_db(launcher_db: Path, *, trace_info: TraceInfo) -> Reply:

    """

    Analyze CK3 launcher database for issues.

    Returns Reply with diagnosis report.

    """

    import sqlite3

    rb = ReplyBuilder(trace_info, tool='_diagnose_launcher_db')

    try:

        conn = sqlite3.connect(f"{launcher_db.as_uri()}?mode=ro", uri=True)

        conn.row_factory = sqlite3.Row

        result = {

            "path": str(launcher_db),

            "size_bytes": launcher_db.stat().st_size,

            "tables": [],

            "mods_registered": 0,

            "playsets": 0,

            "issues": [],

            "issues_count": 0,

        }

        # Get tables

        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()

        result["tables"] = [t[0] for t in tables]

        # Count mods

        try:

            mods = conn.execute("SELECT COUNT(*) FROM mods").fetchone()

            result["mods_registered"] = mods[0] if mods else 0

        except:

            result["issues"].append("Cannot read mods table")

        # Count playsets

        try:

            playsets = conn.execute("SELECT COUNT(*) FROM playsets").fetchone()

            result["playsets"] = playsets[0] if playsets else 0

        except:

            result["issues"].append("Cannot read playsets table")

        # Check for orphaned mod references

        try:

            orphans = conn.execute("""

                SELECT pm.mod_id FROM playsets_mods pm 

                LEFT JOIN mods m ON pm.mod_id = m.id 

                WHERE m.id IS NULL

            """).fetchall()

            if orphans:

                result["issues"].append(f"Found {len(orphans)} orphaned mod references in playsets")

        except:

            pass

        # Check for mods with missing paths
        try:
            missing_paths = conn.execute("""
                SELECT display_name, steam_id FROM mods
                WHERE path IS NULL OR path = ''
            """).fetchall()
            if missing_paths:
                result["issues"].append(f"Found {len(missing_paths)} mods with missing paths")
        except Exception:
            pass

        # Check for mods whose recorded path no longer exists on disk
        try:
            path_mods = conn.execute("""
                SELECT display_name, steam_id, path FROM mods
                WHERE path IS NOT NULL AND path != ''
            """).fetchall()
            from pathlib import Path as _Path
            missing_on_disk = []
            for mod in path_mods:
                if not _Path(mod["path"]).exists():
                    label = mod["display_name"] or mod["steam_id"] or "unknown"
                    missing_on_disk.append(f"{label}: {mod['path']}")
            if missing_on_disk:
                result["issues"].append(f"Found {len(missing_on_disk)} mods with paths that no longer exist on disk")
                result["missing_on_disk"] = missing_on_disk[:20]
        except Exception:
            pass

        result["issues_count"] = len(result["issues"])

        conn.close()

        return rb.success("MCP-SYS-S-001", data=result)

    except Exception as e:

        return rb.error("MCP-SYS-E-001", data={"exception": type(e).__name__}, message=f"Failed to analyze launcher database: {e}")

# ============================================================================

# Work Contract Management V1 (CANONICAL CONTRACT SYSTEM)

# ============================================================================

# Schema: Contract V1 (geographic-only authorization)

# Capability Matrix: policy/capability_matrix.py (standalone truth table)

# Contract Storage: policy/contract_v1.py

# 

# BANNED: canonical_domains, semantic domains (active_local_mods, etc.)

# REQUIRED: root_category (one per contract)

# ============================================================================

@mcp.tool()

@mcp_safe_worker_tool

def ck3_contract(

    command: Literal["open", "close", "cancel", "status", "list", "flush", "archive_legacy", "sign_script", "review_script"] = "status",

    # For open - REQUIRED

    intent: str | None = None,

    root_category: str | None = None,

    # For open - REQUIRED in ck3lens mode

    # For open - OPTIONAL

    operations: list[str] | None = None,

    targets: list[dict] | None = None,

    work_declaration: dict | None = None,

    expires_hours: float = 8.0,

    # For open - script execution

    script_signature: dict | None = None,

    # For sign_script / review_script

    script_path: str | None = None,

    # For close/cancel

    contract_id: str | None = None,

    closure_commit: str | None = None,

    cancel_reason: str | None = None,

    # For list

    status_filter: str | None = None,

    include_archived: bool = False,

) -> Reply:

    """

    Manage work contracts (Contract V1 schema).

    Before EVERY sign_script submission, use the VS Code question tool to ask:
    "Are you at the workstation and ready to review and approve a sign_script request? Y/N"
    Wait for an explicit Y; otherwise do not submit. Approval waits at most 180
    seconds. Do not automatically retry after a client timeout or submit another
    signing request while the first may still be pending.

    Contract V1 uses geographic-only authorization via the capability matrix.

    Each contract has exactly ONE root_category (geographic scope).

    Commands:

    command=open           ? Open new contract (intent, root_category required)

    command=close          ? Close contract after work complete

    command=cancel         ? Cancel contract without completing

    command=status         ? Get current active contract status

    command=list           ? List contracts

    command=flush          ? Archive old contracts from previous days

    command=archive_legacy ? Move pre-v1 contracts to legacy folder

    command=sign_script    ? Open script with CodeLens and poll for human approval (script_path required)

    command=review_script  ? Open script in VS Code for review (script_path required)

    Args:

        command: Action to perform

        intent: Description of work to be done (for open)

        root_category: Geographic scope (ONE of: ROOT_REPO, ROOT_USER_DOCS,

            ROOT_STEAM, ROOT_GAME, ROOT_CK3RAVEN_DATA, ROOT_VSCODE, ROOT_EXTERNAL)

        operations: List of operations (READ, WRITE, DELETE)

        targets: List of target dicts with target_type, path, description

        work_declaration: REQUIRED for mutating operations. JSON schema:

            {

                "work_summary": "Brief description of the work",

                "work_plan": ["Step 1", "Step 2", ...],  // 1-15 items

                "out_of_scope": ["What this does NOT include"],

                "edits": [  // REQUIRED for WRITE/DELETE operations

                    {

                        "file": "relative/path/to/file.py",

                        "edit_kind": "add" | "modify" | "delete" | "rename",

                        "location": "description of where in file",

                        "change_description": "what changes are being made"

                    }

                ]

            }

        expires_hours: Hours until expiry (default 8)

        script_signature: Sigil-signed script approval returned by ck3_contract(command="sign_script").

            MUST be the exact dict from the sign_script response data:

            {"script_path": str, "content_sha256": str, "signature": str, "signed_at": str}

            Do NOT construct this dict manually — pass the sign_script response data as-is.

            Required (with script_path) to open a script execution contract.

        script_path: Canonical path for sign_script/review_script (root:ck3raven_data/wip/...)

        contract_id: Contract ID for close/cancel (uses active if not specified)

        closure_commit: Git commit SHA for close

        cancel_reason: Reason for cancellation

        status_filter: Filter list by status

        include_archived: Include archived in list

    Returns:

        Contract info or operation result

    Examples:

        # Open a work contract (ck3raven-dev mode)

        ck3_contract(

            command="open",

            intent="Fix parser bug",

            root_category="ROOT_REPO",

            work_declaration={

                "work_summary": "Fix off-by-one error in lexer",

                "work_plan": ["Locate bug in lexer.py", "Add test case", "Fix bug"],

                "out_of_scope": ["Parser changes"],

                "edits": [{

                    "file": "src/ck3raven/parser/lexer.py",

                    "edit_kind": "modify",

                    "location": "tokenize() function",

                    "change_description": "Fix boundary check"

                }]

            }

        )

        # Script execution workflow (3 steps):

        # 1. Write script, then sign it:

        #    ck3_file(command="write", path="root:ck3raven_data/wip/my_script.py", content="...")

        #    sign_result = ck3_contract(command="sign_script", script_path="root:ck3raven_data/wip/my_script.py")

        # 2. Open contract with BOTH script_signature AND work_declaration (both required):

        #    script_signature is NOT a substitute for work_declaration — it is an additional requirement.

        #    Omitting work_declaration will produce EN-GATE-D-003 even with a valid script_signature.

        ck3_contract(

            command="open",

            intent="Run analysis script",

            root_category="ROOT_CK3RAVEN_DATA",

            script_signature=sign_result_data,  # pass sign_script response .data dict as-is

            script_path="root:ck3raven_data/wip/my_script.py",

            work_declaration={

                "work_summary": "Run my_script.py to perform analysis",

                "work_plan": ["Execute script", "Review output"],

                "out_of_scope": ["Manual file edits"],

                "edits": []  # empty list is valid when the script handles all writes

            }

        )

        # 3. Execute: ck3_exec("python ~/.ck3raven/wip/my_script.py")

    """

    trace_info = get_current_trace_info()

    rb = ReplyBuilder(trace_info, tool='ck3_contract')

    return _ck3_contract_internal(

        rb, command, intent, root_category, operations, targets, work_declaration,

        expires_hours, contract_id, closure_commit, cancel_reason,

        status_filter, include_archived,

        script_signature=script_signature,

        script_path=script_path,

    )

def _ck3_contract_internal(

    rb: 'ReplyBuilder',

    command: str,

    intent: str | None,

    root_category: str | None,

    operations: list[str] | None,

    targets: list[dict] | None,

    work_declaration: dict | None,

    expires_hours: float,

    contract_id: str | None,

    closure_commit: str | None,

    cancel_reason: str | None,

    status_filter: str | None,

    include_archived: bool,

    script_signature: dict | None = None,

    script_path: str | None = None,

) -> 'Reply':

    """Internal implementation returning Reply objects directly."""

    from ck3lens.policy.contract_v1 import (

        open_contract, close_contract, cancel_contract,

        get_active_contract, list_contracts, archive_legacy_contracts,

        ContractV1, RootCategory, Operation, AgentMode,

    )

    from ck3lens.agent_mode import get_agent_mode

    trace = _get_trace()

    agent_mode = get_agent_mode()

    # Null mode check - agent must initialize mode first

    if agent_mode is None and command == "open":

        return rb.invalid('CT-VAL-I-002', data={

            "guidance": "Call ck3_get_mode_instructions() first to initialize.",

            "modes": {

                "ck3lens": "CK3 modding - search database, edit mods, resolve conflicts",

                "ck3raven-dev": "Infrastructure development - modify ck3raven source code",

            },

        }, message="Agent mode not initialized. Call ck3_get_mode_instructions() first.")

    if command == "open":

        # =====================================================================

        # CONTRACT V1: STRICT VALIDATION

        # =====================================================================

        if not intent:

            return rb.invalid('CT-VAL-I-001', data={

                "is_user_error": True,

                "missing_field": "intent",

            }, message="intent is required for open command")

        if not root_category:

            return rb.invalid('CT-VAL-I-001', data={

                "is_user_error": True,

                "missing_field": "root_category",

                "valid_root_categories": [r.value for r in RootCategory],

            }, message="root_category is required for open command")

        # Validate root_category is a valid enum value

        try:

            root_cat = RootCategory(root_category)

        except ValueError:

            return rb.invalid('CT-VAL-I-001', data={

                "is_user_error": True,

                "invalid_field": "root_category",

                "value": root_category,

                "valid_root_categories": [r.value for r in RootCategory],

            }, message=f"Invalid root_category: {root_category}")

        # Convert agent mode string to enum

        mode_enum = AgentMode.CK3LENS if agent_mode == "ck3lens" else AgentMode.CK3RAVEN_DEV

        # Validate operations are valid enum values

        ops_list = operations or ["READ", "WRITE"]

        try:

            op_enums = [Operation(op) for op in ops_list]

        except ValueError as e:

            return rb.invalid('CT-VAL-I-001', data={

                "is_user_error": True,

                "invalid_field": "operations",

                "valid_operations": [o.value for o in Operation],

            }, message=f"Invalid operation: {e}")

        try:

            import json

            from pathlib import Path

            from datetime import datetime

            import uuid

            # Call open_contract (no debug logging in hot path)

            contract = open_contract(

                mode=mode_enum,

                root_category=root_category,

                intent=intent,

                operations=ops_list,

                targets=targets or [],

                work_declaration=work_declaration or {},

                expires_hours=expires_hours,

                script_signature=script_signature,

                script_path=script_path,

            )

            # Trace log (minimal, no large payloads)

            trace.log("contract.open", {

                "intent": intent[:100] if intent else "",

                "root_category": root_category,

            }, {"contract_id": contract.contract_id})

            # =========================================================

            # MINIMAL RESPONSE - transport-safe, strictly serializable

            # =========================================================

            # Convert expires_at to ISO string if datetime

            if contract.expires_at is None:

                expires_str = ""

            elif isinstance(contract.expires_at, str):

                expires_str = contract.expires_at

            else:

                expires_str = contract.expires_at.isoformat()  # type: ignore[union-attr]

            # Convert enums to strings

            if isinstance(contract.root_category, str):

                root_cat_str = contract.root_category

            else:

                root_cat_str = contract.root_category.value

            ops_list_str = [

                op if isinstance(op, str) else op.value

                for op in contract.operations

            ]

            result_data = {

                "success": True,

                "contract_id": contract.contract_id,

                "expires_at": expires_str,

                "mode": agent_mode,

                "root_category": root_cat_str,

                "operations": ops_list_str,

            }

            # =========================================================

            # TRANSPORT SAFETY GUARDS

            # =========================================================

            # Guard 1: Verify JSON-serializable

            try:

                serialized = json.dumps(result_data)

            except (TypeError, ValueError) as ser_err:

                error_id = f"ser-{uuid.uuid4().hex[:8]}"

                debug_log = ROOT_CK3RAVEN_DATA / "contract_debug.log"

                with open(debug_log, "a") as f:

                    f.write(f"\n=== {datetime.now().isoformat()} ===\n")

                    f.write(f"SERIALIZATION_ERROR: {error_id}\n")

                    f.write(f"  Error: {ser_err}\n")

                    f.write(f"  Result keys: {list(result_data.keys())}\n")

                return rb.error('MCP-SYS-E-002', data={

                    "error_id": error_id,

                }, message=f"Response not JSON-serializable. error_id={error_id}")

            # Guard 2: Response size cap (32KB)

            MAX_RESPONSE_BYTES = 32 * 1024

            if len(serialized) > MAX_RESPONSE_BYTES:

                error_id = f"size-{uuid.uuid4().hex[:8]}"

                debug_log = ROOT_CK3RAVEN_DATA / "contract_debug.log"

                with open(debug_log, "a") as f:

                    f.write(f"\n=== {datetime.now().isoformat()} ===\n")

                    f.write(f"RESPONSE_TOO_LARGE: {error_id}\n")

                    f.write(f"  Size: {len(serialized)} bytes\n")

                return rb.error('MCP-SYS-E-003', data={

                    "error_id": error_id,

                    "size": len(serialized),

                }, message=f"Response exceeds {MAX_RESPONSE_BYTES} bytes. error_id={error_id}")

            return rb.success('CT-OPEN-S-001', data=result_data,

                message=f"Contract opened: {contract.contract_id}")

        except ValueError as ve:

            # Contract validation failures — agent supplied bad/missing input

            from ck3lens.hints import get_hint_engine

            error_msg = str(ve)

            hint_engine = get_hint_engine()

            hints = hint_engine.for_contract_error(error_msg, {

                "command": command,

                "intent": intent,

                "root_category": root_category,

                "operations": operations,

                "work_declaration": work_declaration,

            })

            return rb.invalid('CT-VAL-I-001', data={

                "is_user_error": True,

                "validation_error": error_msg,

                **hints,

            }, message=f"Contract validation failed: {error_msg}")

        except Exception as e:

            import traceback

            import uuid

            from pathlib import Path

            from datetime import datetime

            error_id = f"err-{uuid.uuid4().hex[:8]}"

            debug_log = ROOT_CK3RAVEN_DATA / "contract_debug.log"

            with open(debug_log, "a") as f:

                f.write(f"\n=== {datetime.now().isoformat()} ===\n")

                f.write(f"CONTRACT_OPEN_ERROR: {error_id}\n")

                f.write(f"  Exception: {e}\n")

                f.write(traceback.format_exc())

            return rb.error('CT-OPEN-E-001', data={

                "error_id": error_id,

            }, message=f"Internal error opening contract. Logged as {error_id}")

    elif command == "close":

        target_id = contract_id

        if not target_id:

            active = get_active_contract()

            if not active:

                return rb.invalid('CT-CLOSE-I-001', data={},

                    message="No active contract to close")

            target_id = active.contract_id

        try:

            contract = close_contract(target_id, closure_commit)

            trace.log("contract.close", {

                "contract_id": target_id,

            }, {"closure_commit": closure_commit})

            return rb.success('CT-CLOSE-S-001', data={

                "contract_id": contract.contract_id,

                "closed_at": contract.closed_at if isinstance(contract.closed_at, str) else (contract.closed_at.isoformat() if contract.closed_at else None),

                "status": contract.status if isinstance(contract.status, str) else str(contract.status),

            }, message="Contract closed.")

        except Exception as e:

            return rb.error('CT-CLOSE-E-001', data={

                "detail": str(e),

            }, message=f"Error closing contract: {e}")

    elif command == "cancel":

        target_id = contract_id

        if not target_id:

            active = get_active_contract()

            if not active:

                return rb.invalid('CT-CLOSE-I-001', data={},

                    message="No active contract to cancel")

            target_id = active.contract_id

        try:

            contract = cancel_contract(target_id, cancel_reason or "")

            trace.log("contract.cancel", {

                "contract_id": target_id,

                "reason": cancel_reason,

            }, {})

            return rb.success('CT-CLOSE-S-002', data={

                "contract_id": contract.contract_id,

                "status": "cancelled",

            }, message="Contract cancelled.")

        except Exception as e:

            return rb.error('CT-CLOSE-E-001', data={

                "detail": str(e),

            }, message=f"Error cancelling contract: {e}")

    elif command == "status":

        active = get_active_contract()

        if active:

            # Serialize enums/datetime to transport-safe strings

            root_cat_str = active.root_category if isinstance(active.root_category, str) else active.root_category.value

            ops_list_str = [

                op if isinstance(op, str) else op.value

                for op in active.operations

            ]

            expires_str = active.expires_at if isinstance(active.expires_at, str) else (active.expires_at.isoformat() if active.expires_at else None)

            created_str = active.created_at if isinstance(active.created_at, str) else (active.created_at.isoformat() if active.created_at else None)

            return rb.success('MCP-SYS-S-001', data={

                "has_active_contract": True,

                "contract_id": active.contract_id,

                "intent": active.intent,

                "root_category": root_cat_str,

                "operations": ops_list_str,

                "expires_at": expires_str,

                "created_at": created_str,

                "schema_version": "v1",

            }, message=f"Active contract: {active.contract_id}")

        else:

            return rb.invalid('CT-CLOSE-I-001', data={

                "has_active_contract": False,

            }, message="No active contract. Use command=open to start work.")

    elif command == "list":

        contracts = list_contracts(

            status=status_filter,

            include_archived=include_archived,

        )

        # Serialize each contract to transport-safe types

        def serialize_contract(c):

            root_cat_str = c.root_category if isinstance(c.root_category, str) else c.root_category.value

            status_str = c.status if isinstance(c.status, str) else str(c.status)

            created_str = c.created_at if isinstance(c.created_at, str) else (c.created_at.isoformat() if c.created_at else None)

            closed_str = c.closed_at if isinstance(c.closed_at, str) else (c.closed_at.isoformat() if c.closed_at else None)

            return {

                "contract_id": c.contract_id,

                "intent": c.intent,

                "status": status_str,

                "root_category": root_cat_str,

                "created_at": created_str,

                "closed_at": closed_str,

            }

        return rb.success('MCP-SYS-S-001', data={

            "count": len(contracts),

            "contracts": [serialize_contract(c) for c in contracts],

        }, message=f"{len(contracts)} contract(s) found")

    elif command == "flush":

        # Archive contracts from previous days

        import os

        from pathlib import Path

        from datetime import datetime

        contracts_dir = ROOT_CK3RAVEN_DATA / "contracts"

        if not contracts_dir.exists():

            return rb.success('MCP-SYS-S-001', data={

                "archived": 0,

            }, message="No contracts directory — nothing to flush")

        today = datetime.now().strftime("%Y-%m-%d")

        archive_dir = contracts_dir / "archive" / today

        archive_dir.mkdir(parents=True, exist_ok=True)

        archived = 0

        for f in contracts_dir.glob("wcp-*.json"):

            # Only archive if not from today

            if today not in f.name:

                import shutil

                shutil.move(str(f), str(archive_dir / f.name))

                archived += 1

        trace.log("contract.flush", {}, {"archived": archived})

        return rb.success('MCP-SYS-S-001', data={

            "archived": archived,

        }, message=f"Archived {archived} contracts from previous days")

    elif command == "archive_legacy":

        # Move all pre-v1 contracts to legacy folder

        archived = archive_legacy_contracts()

        trace.log("contract.archive", {}, {"archived": archived})

        return rb.success('MCP-SYS-S-001', data={

            "archived": archived,

        }, message=f"Archived {archived} legacy pre-v1 contracts")

    elif command in ("sign_script", "review_script"):

        return _ck3_contract_script_signing(

            rb=rb,

            command=command,

            script_path=script_path,

            trace=trace,

        )

    return rb.invalid('MCP-SYS-I-002', data={

        "command": command,

    }, message=f"Unknown contract command: {command}")

def _ck3_contract_script_signing(

    *,

    rb: 'ReplyBuilder',

    command: str,

    script_path: str | None,

    trace: object,

) -> 'Reply':

    """

    Handle sign_script and review_script subcommands.

    review_script: Opens the file in VS Code for the user to read. No approval flow.

    sign_script:   Opens the file with CodeLens approval button and polls for 

                   human approval (up to 10 min). Returns the Sigil signature on success.

    NOTE: This is v1 code. Uses direct Path operations instead of WA2 to avoid

    VisibilityRef handling issues. Only allows scripts under the configured WIP root.

    """

    import hashlib

    import time

    from pathlib import Path

    if not script_path:

        return rb.invalid('CT-SIGN-I-005', data={})

    # --- v1 path resolution: direct Path operations ---

    # Strip root: prefix if present (accept both canonical and raw paths)

    raw_path = script_path

    if script_path.startswith("root:ck3raven_data/"):

        raw_path = str(ROOT_CK3RAVEN_DATA / script_path[len("root:ck3raven_data/"):])

    # Resolve to absolute path

    host_abs_path = str(Path(raw_path).expanduser().resolve())

    # Check file exists

    if not Path(host_abs_path).is_file():

        return rb.invalid('CT-SIGN-I-006', data={

            "script_path": script_path,

            "resolved_path": host_abs_path,

            "detail": "Script file does not exist",

        })

    # Must be under wip directory

    wip_root = (ROOT_CK3RAVEN_DATA / "wip").resolve()
    wip_dir = str(wip_root)

    if not Path(host_abs_path).is_relative_to(wip_root):

        return rb.invalid('CT-SIGN-I-002', data={

            "script_path": script_path,

            "resolved_path": host_abs_path,

            "wip_dir": wip_dir,

            "detail": "Script must be under root:ck3raven_data/wip/",

        })

    # Compute SHA-256 of script content

    try:

        content = Path(host_abs_path).read_bytes()

        content_sha256 = hashlib.sha256(content).hexdigest()

    except Exception as e:

        return rb.error('CT-SIGN-E-001', data={

            "detail": str(e),

        })

    from ck3lens.ipc_client import VSCodeIPCClient, VSCodeIPCError

    # --- review_script: just open the file, no CodeLens, return immediately ---

    if command == "review_script":

        try:

            with VSCodeIPCClient() as ipc:

                result = ipc.open_script_for_review(host_abs_path, content_sha256, canonical_path=script_path)

        except VSCodeIPCError as e:

            return rb.error('CT-SIGN-E-002', data={

                "detail": str(e),

            })

        if not result.get("opened"):

            return rb.error('CT-SIGN-E-003', data={

                "detail": "VS Code failed to open script for review",

                "host_path": host_abs_path,

                "ipc_result": result,

            })

        return rb.success('CT-SIGN-S-002', data={

            "script_path": script_path,

            "normalized_path": result.get("normalizedPath"),

        })

    # --- sign_script: open with CodeLens + poll for approval (single persistent IPC connection) ---

    POLL_INTERVAL = 2.0   # seconds between approval checks

    MAX_WAIT = 180.0      # Owner-approved synchronous review limit.

    try:

        with VSCodeIPCClient() as ipc:

            open_result = ipc.open_script_for_review(host_abs_path, content_sha256, canonical_path=script_path)

            if not open_result.get("opened"):

                return rb.error('CT-SIGN-E-003', data={

                    "detail": "VS Code failed to open script for signing",

                    "host_path": host_abs_path,

                    "ipc_result": open_result,

                })

            # Poll using the SAME connection and wall-clock deadline.

            # Previously each poll iteration re-created VSCodeIPCClient(), which

            # risked up to 120s port-discovery delays per iteration when

            # CK3LENS_IPC_PORT env var was not set.

            deadline = time.monotonic() + MAX_WAIT

            while time.monotonic() < deadline:

                time.sleep(POLL_INTERVAL)

                result = ipc.get_script_sign_approval(host_abs_path)

                if result.get("pending"):

                    continue

                if result.get("approved"):

                    # Success — return signature data (canonical paths only)

                    return rb.success('CT-SIGN-S-001', data={

                        "script_path": script_path,

                        "content_sha256": result.get("content_sha256", content_sha256),

                        "signature": result["signature"],

                        "signed_at": result.get("signed_at", ""),

                    })

                # User rejected

                return rb.invalid('CT-SIGN-I-003', data={

                    "script_path": script_path,

                })

            # Timeout — clean up CodeLens (same connection, no new port discovery)

            ipc.cancel_script_sign(host_abs_path)

            return rb.invalid('CT-SIGN-I-004', data={

                "script_path": script_path,

            })

    except VSCodeIPCError as e:

        return rb.error('CT-SIGN-E-002', data={

            "detail": str(e),

        })

# ============================================================================

# Command Execution with Enforcement

# ============================================================================

@mcp.tool()

@mcp_safe_tool

def ck3_exec(

    command: str,

    working_dir: str | None = None,

    target_paths: list[str] | None = None,

    token_id: str | None = None,

    dry_run: bool = False,

    timeout: int = 30,

) -> Reply:

    """

    Execute a shell command with enforcement policy.

    Mode-aware behavior:

    - ck3lens mode: Limited to CK3/mod-related commands within playset scope

    - ck3raven-dev mode: Broader access for infrastructure work (USE THIS instead of run_in_terminal)

    This is the ONLY safe way for agents to run shell commands.

    All commands are evaluated against enforcement.py:

    Command execution remains subject to the existing whitelist and signed-script authorization checks.

    - Blocked commands (rm -rf /) ? Always denied

    Args:

        command: Shell command to execute

        working_dir: Working directory (defaults to ck3raven root)

        target_paths: Files/dirs being affected (helps scope validation)

        token_id: Approval token ID (reserved for future use)

        dry_run: If True, only check policy without executing

        timeout: Max seconds to wait for command (default 30, max 300)

    Returns:

        {

            "allowed": bool,

            "executed": bool,  # False if dry_run or denied

            "output": str,     # Command output (if executed)

            "exit_code": int,  # Exit code (if executed)

            "policy": {

                "decision": "ALLOW" | "DENY",

                "reason": str,

                "category": str,

            }

        }

    Examples:

        ck3_exec("cat file.txt")  # Safe - allowed

    """

    trace_info = get_current_trace_info()

    rb = ReplyBuilder(trace_info, tool='ck3_exec')

    # _ck3_exec_internal always returns Reply (normalized as of Feb 22, 2026)

    return _ck3_exec_internal(command, working_dir, target_paths, token_id, dry_run, timeout, rb=rb)

def _kill_process_tree_windows(pid: int) -> None:

    """

    Kill an entire process tree on Windows using taskkill /T /F.

    subprocess.run(timeout=N) only kills the direct child (powershell.exe)

    but orphans grandchild processes (python.exe, etc.). This uses

    'taskkill /T /F /PID' which recursively terminates the full tree.

    """

    import subprocess as _sp

    try:

        _sp.run(

            ["taskkill", "/T", "/F", "/PID", str(pid)],

            capture_output=True,

            timeout=10,

        )

    except Exception:

        # Fallback: kill just the parent process

        try:

            os.kill(pid, 9)

        except Exception:

            pass

def _first_host_path(host_paths: Path | list[Path] | None) -> Path | None:

    """Return the first resolved host path from WA2 token lookup."""

    if host_paths is None:

        return None

    if isinstance(host_paths, list):

        return host_paths[0] if host_paths else None

    return host_paths

def _parse_python_script_command(command: str) -> tuple[list[str], int, str] | None:

    """Parse a python script invocation and return (parts, script_arg_index, raw_script_arg).

    Recognized forms:
      - python root:ck3raven_data/wip/script.py
      - python relative/path/script.py
      - python "quoted path/script.py"

    Non-script forms return None:
      - python -c "..."
      - python -m module
      - non-python commands
    """

    import shlex

    try:

        parts = shlex.split(command, posix=False)

    except ValueError:

        return None

    if not parts:

        return None

    exe = parts[0].lower().replace("\\", "/").rsplit("/", 1)[-1]

    if not exe.startswith("python"):

        return None

    i = 1

    while i < len(parts):

        arg = parts[i]

        if arg in ("-c", "-m"):

            return None

        if arg.startswith("-"):

            i += 1

            continue

        cleaned = arg.strip('"').strip("'")

        if Path(cleaned).suffix.lower() == ".py":

            return (parts, i, cleaned)

        return None

    return None

def _ck3_exec_internal(

    command: str,

    working_dir: str | None,

    target_paths: list[str] | None,

    token_id: str | None,

    dry_run: bool,

    timeout: int,

    rb: ReplyBuilder,

) -> Reply:

    """Internal implementation. Always returns Reply."""

    from ck3lens.policy.enforcement_v2 import enforce

    from ck3lens.policy.contract_v1 import get_active_contract

    import subprocess

    # Get active contract

    active_contract = get_active_contract()

    has_contract = active_contract is not None

    # ==========================================================================

    # V2 ENFORCEMENT: Uses WorldAdapterV2 + enforcement_v2

    # ck3_exec is an EXECUTE operation on the resolved path.

    # ==========================================================================

    wa2 = _get_world_v2()

    # Resolve working directory or default to wip

    if working_dir:

        resolve_input = working_dir

    elif target_paths:

        resolve_input = target_paths[0]

    else:

        resolve_input = "root:ck3raven_data/wip"

    reply, ref = wa2.resolve(resolve_input, require_exists=True, rb=rb)

    if ref is None:

        return rb.invalid('WA-RES-I-001', data={

            "allowed": False,

            "executed": False,

            "output": None,

            "exit_code": None,

        }, message=f"Could not resolve path: {resolve_input}")

    # Working directory coordinates (used as fallback for enforcement)

    wd_root_key = reply.data.get("root_key", "")

    wd_subdirectory = reply.data.get("subdirectory")

    # ======================================================================

    # Script extraction from exec_command.

    # Script path policy:

    #   - Accept canonical session paths (root:/mod:)

    #   - Accept relative paths (resolved against working_dir; default root:ck3raven_data/wip)

    #   - Reject host absolute paths

    #   - Require script to live under working_dir for signed execution

    # ======================================================================

    content_sha256: str | None = None

    script_path: str | None = None

    script_root_key: str | None = None

    script_subdirectory: str | None = None

    command_for_exec = command

    wd_canonical = reply.data.get("resolved") or ""

    parsed_script = _parse_python_script_command(command)

    if parsed_script is not None:

        import hashlib as _hashlib

        import subprocess as _subprocess

        parts, script_arg_idx, script_arg = parsed_script

        if Path(script_arg).is_absolute():

            return rb.invalid('MCP-SYS-I-003', data={

                "command": command,

                "script_arg": script_arg,

                "working_dir": wd_canonical,

                "hint": (

                    "Use a canonical session path (root:/mod:) or a relative path. "

                    "Relative paths are resolved against working_dir (default root:ck3raven_data/wip)."

                ),

            }, message="Host absolute script paths are not allowed for ck3_exec.")

        if script_arg.startswith("root:") or script_arg.startswith("mod:"):

            canonical_script_input = script_arg

        else:

            rel_script = script_arg.replace("\\", "/")

            canonical_script_input = f"{wd_canonical.rstrip('/')}/{rel_script}"

        s_reply, s_ref = wa2.resolve(canonical_script_input, require_exists=True, rb=rb)

        if s_ref is None:

            return rb.invalid('MCP-SYS-I-003', data={

                "command": command,

                "script_arg": script_arg,

                "script_input": canonical_script_input,

                "working_dir": wd_canonical,

            }, message="Script path could not be resolved. Use canonical session path or relative path under working_dir.")

        resolved_script_path = s_reply.data.get("resolved") or ""

        wd_prefix = f"{wd_canonical.rstrip('/')}/"

        if resolved_script_path != wd_canonical and not resolved_script_path.startswith(wd_prefix):

            return rb.invalid('MCP-SYS-I-003', data={

                "command": command,

                "script_arg": script_arg,

                "resolved_script": resolved_script_path,

                "working_dir": wd_canonical,

            }, message="Script must be located under the resolved working_dir.")

        resolved_host = _first_host_path(wa2.host_path(s_ref))

        if resolved_host is None or not resolved_host.exists() or not resolved_host.is_file():

            return rb.invalid('MCP-SYS-I-003', data={

                "command": command,

                "script_arg": script_arg,

                "resolved_script": resolved_script_path,

                "working_dir": wd_canonical,

            }, message="Resolved script target is not an existing file.")

        script_path = resolved_script_path

        script_root_key = s_reply.data.get("root_key")

        script_subdirectory = s_reply.data.get("subdirectory")

        content_sha256 = _hashlib.sha256(resolved_host.read_bytes()).hexdigest()

        parts[script_arg_idx] = str(resolved_host)

        command_for_exec = _subprocess.list2cmdline(parts)

    # Enforcement coordinates: use script's location if available, else working dir

    if script_path is not None and script_root_key is not None:

        enforce_root_key = script_root_key

        enforce_subdirectory = script_subdirectory

    else:

        enforce_root_key = wd_root_key

        enforce_subdirectory = wd_subdirectory

    # Shell execution is an EXECUTE operation — enforcement walks the matrix

    result = enforce(

        rb,

        mode=wa2.mode,

        tool="ck3_exec",

        command=command,

        root_key=enforce_root_key,

        subdirectory=enforce_subdirectory,

        # Context for exec_gate condition predicate

        exec_command=command,

        exec_subdirectory=enforce_subdirectory,

        has_contract=has_contract,

        script_path=script_path,

        content_sha256=content_sha256,

    )

    # Enforcement denial — pass through

    if result.is_denied:

        return result

    # Enforcement passed — build policy info for response

    policy_info = {"decision": "ALLOW", "reason": f"Execute allowed ({result.code})"}

    # Allowed — dry run

    if dry_run:

        return rb.success('EN-EXEC-S-002', data={

            "allowed": True,

            "executed": False,

            "output": None,

            "exit_code": None,

            "policy": policy_info,

        }, message="Dry run - command would be allowed.")

    # Clamp timeout to max 300 seconds

    actual_timeout = min(max(timeout, 1), 300)

    # ======================================================================

    # Determine actual CWD for execution using WA v2 host_path

    # ======================================================================

    host = _first_host_path(wa2.host_path(ref))

    if host is not None:

        exec_cwd = str(host) if host.is_dir() else str(host.parent)

    else:

        from ck3lens.paths import ROOT_REPO as _EXEC_ROOT_REPO

        exec_cwd = str(_EXEC_ROOT_REPO) if _EXEC_ROOT_REPO else None

    # Actually execute

    try:

        import platform

        # Environment variables to prevent git from hanging

        exec_env = os.environ.copy()

        exec_env["GIT_TERMINAL_PROMPT"] = "0"  # Disable credential prompts

        exec_env["GIT_PAGER"] = "cat"  # Disable pager for git commands

        exec_env["PAGER"] = "cat"  # Disable pager generally

        exec_env["GCM_INTERACTIVE"] = "never"  # Disable Git Credential Manager GUI

        exec_env["GIT_ASKPASS"] = ""  # Disable askpass

        exec_env["SSH_ASKPASS"] = ""  # Disable SSH askpass

        exec_env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes"  # SSH non-interactive

        is_windows = platform.system() == "Windows"

        if is_windows:

            # =============================================================

            # FIX: Use Popen + manual timeout + process tree kill on Windows

            # subprocess.run(timeout=N) kills powershell.exe but orphans

            # child processes (python.exe, etc.) — taskkill /T /F kills the

            # entire process tree.

            # =============================================================

            ps_command = ["powershell", "-NoProfile", "-NonInteractive", "-Command", command_for_exec]

            proc = subprocess.Popen(

                ps_command,

                shell=False,

                cwd=exec_cwd,

                stdout=subprocess.PIPE,

                stderr=subprocess.PIPE,

                text=True,

                stdin=subprocess.DEVNULL,

                env=exec_env,

                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,

            )

            try:

                stdout, stderr = proc.communicate(timeout=actual_timeout)

            except subprocess.TimeoutExpired:

                # Kill entire process tree on Windows

                _kill_process_tree_windows(proc.pid)

                # Drain any remaining output after kill

                try:

                    stdout, stderr = proc.communicate(timeout=5)

                except Exception:

                    stdout, stderr = "", ""

                partial = f"{stdout}{stderr}".strip()

                partial_msg = f"\nPartial output:\n{partial}" if partial else ""

                return rb.error('MCP-SYS-E-001', data={

                    "allowed": True,

                    "executed": True,

                    "output": f"Command timed out after {actual_timeout} seconds (process tree killed).{partial_msg}",

                    "exit_code": -1,

                    "policy": policy_info,

                }, message=f"Command timed out after {actual_timeout}s. Use timeout= parameter to increase (max 300s).")

            return rb.success('EN-EXEC-S-001', data={

                "allowed": True,

                "executed": True,

                "output": stdout + stderr,

                "exit_code": proc.returncode,

                "policy": policy_info,

            }, message=f"Command executed. Exit code: {proc.returncode}.")

        else:

            proc = subprocess.run(

                command_for_exec,

                shell=True,

                cwd=exec_cwd,

                capture_output=True,

                text=True,

                timeout=actual_timeout,

                env=exec_env,

            )

            return rb.success('EN-EXEC-S-001', data={

                "allowed": True,

                "executed": True,

                "output": proc.stdout + proc.stderr,

                "exit_code": proc.returncode,

                "policy": policy_info,

            }, message=f"Command executed. Exit code: {proc.returncode}.")

    except subprocess.TimeoutExpired:

        # Non-Windows timeout (from subprocess.run on Unix)

        return rb.error('MCP-SYS-E-001', data={

            "allowed": True,

            "executed": True,

            "output": f"Command timed out after {actual_timeout} seconds",

            "exit_code": -1,

            "policy": policy_info,

        }, message=f"Command timed out after {actual_timeout}s. Use timeout= parameter to increase (max 300s).")

    except Exception as e:

        return rb.error('MCP-SYS-E-001', data={

            "allowed": True,

            "executed": False,

            "output": None,

            "exit_code": None,

            "policy": policy_info,

            "error": str(e),

        }, message=f"Execution failed: {e}")

@mcp.tool()

@mcp_safe_tool

def ck3_token(

    command: Literal["request", "list", "validate", "revoke"] = "list",

    # For request

    token_type: str | None = None,

    reason: str | None = None,

    path_patterns: list[str] | None = None,

    command_patterns: list[str] | None = None,

    ttl_minutes: int | None = None,

    # For validate/revoke

    token_id: str | None = None,

    capability: str | None = None,

    path: str | None = None,

) -> Reply:

    """

    DEPRECATED: Legacy token system replaced by HAT (Human Authorization Token).

    Only canonical tokens remain in tools/compliance/tokens.py:

    - NST (New Symbol Token): For creating new symbol identities

    - LXE (Lint Exception Token): For lint rule exceptions

    - HAT (Human Authorization Token): For protected file writes and mode init (see docs/PROTECTED_FILES_AND_HAT.md)

    This tool now returns a deprecation notice for all commands.

    """

    trace_info = get_current_trace_info()

    rb = ReplyBuilder(trace_info, tool='ck3_token')

    return rb.invalid(

        'WA-CFG-I-001',

        data={

            "deprecated": True,

            "guidance": {

                "file_deletion": "File deletion requires active contract (same as write)",

                "protected_files": "See docs/PROTECTED_FILES_AND_HAT.md for HAT system",

                "canonical_tokens": "NST, LXE, and HAT tokens are in tools/compliance/tokens.py",

            },

        },

        message="Token system deprecated. See HAT architecture for protected file authorization.",

    )

# ============================================================================

# Protected Files Management

# ============================================================================

@mcp.tool()

@mcp_safe_tool

def ck3_protect(

    command: Literal["list", "verify", "add", "remove"] = "list",

    path: str | None = None,

    entry_type: Literal["file", "folder"] | None = None,

    reason: str | None = None,

) -> Reply:

    '''

    Manage protected files manifest.

    Protected files require HAT (Human Authorization Token) approval to modify.

    The manifest at policy/protected_files.json tracks which files are protected.

    HAT approval is ephemeral: the extension writes a signed approval file,

    which is consumed (verified + deleted) here. No hat_id passes through chat.

    Click the shield icon in the CK3 Lens sidebar to approve pending requests.

    Commands:

    command=list   -> List all protected entries

    command=verify -> Check SHA256 hashes of all protected files

    command=add    -> Add file/folder to manifest (requires HAT approval)

    command=remove -> Remove entry from manifest (requires HAT approval)

    Args:

        command: Operation to perform

        path: Relative path for add/remove (e.g., ".github/copilot-instructions.md")

        entry_type: "file" or "folder" (for add, default "file")

        reason: Why this file is protected (for add)

    Returns:

        Protected files info or operation result

    Examples:

        ck3_protect(command="list")

        ck3_protect(command="verify")

        ck3_protect(command="add", path="docs/IMPORTANT.md", reason="Critical doc")

        ck3_protect(command="remove", path="docs/OLD.md")

    '''

    trace_info = get_current_trace_info()

    rb = ReplyBuilder(trace_info, tool='ck3_protect')

    try:

        from tools.compliance.protected_files import (

            load_manifest, save_manifest, verify_all_hashes,

            compute_file_hash, is_protected, ProtectedEntry,

            _get_repo_root, MANIFEST_REL_PATH,

        )

    except ImportError as e:

        return rb.error('MCP-SYS-E-001', data={}, message=f"Failed to import protected_files module: {e}")

    if command == "list":

        entries = load_manifest()

        return rb.success(

            'MCP-SYS-S-001',

            data={

                "entries": [e.to_dict() for e in entries],

                "count": len(entries),

                "manifest_path": MANIFEST_REL_PATH,

                "hardcoded_protected": [MANIFEST_REL_PATH],

            },

            message=f"{len(entries)} protected entries.",

        )

    elif command == "verify":

        mismatches = verify_all_hashes()

        entries = load_manifest()

        return rb.success(

            'WA-VAL-S-001',

            data={

                "total_entries": len(entries),

                "mismatches": [

                    {

                        "path": p,

                        "expected": exp[:16] + "...",

                        "actual": (act[:16] + "...") if act != "FILE_NOT_FOUND" else act,

                    }

                    for p, exp, act in mismatches

                ],

                "all_match": len(mismatches) == 0,

            },

            message="All hashes match." if not mismatches else f"{len(mismatches)} hash mismatch(es) found.",

        )

    elif command == "add":

        if not path:

            return rb.invalid('MCP-SYS-I-001', data={}, message="path required for add command")

        if not reason:

            return rb.invalid('MCP-SYS-I-001', data={}, message="reason required for add command")

        # Consume ephemeral HAT approval (written by extension, verified + deleted here)

        try:

            from tools.compliance.tokens import consume_hat_approval, write_hat_request

        except ImportError as e:

            return rb.error('MCP-SYS-E-001', data={}, message=f"HAT module import failed: {e}")

        valid, msg = consume_hat_approval(required_paths=[path])

        if not valid:

            write_hat_request(

                intent=f"Add protected file: {path}",

                protected_paths=[path],

                root_category="ROOT_REPO",

            )

            return rb.invalid(

                'MCP-SYS-I-001',

                data={"requires_hat": True},

                message=f"HAT approval required: {msg}. Click the shield icon in CK3 Lens sidebar to approve.",

            )

        # Check if already protected

        if is_protected(path):

            return rb.invalid('MCP-SYS-I-001', data={}, message=f"Path already protected: {path}")

        # Compute hash for files

        resolved_type = entry_type or "file"

        sha256 = ""

        if resolved_type == "file":

            repo_root = _get_repo_root()

            file_path = repo_root / path

            if not file_path.exists():

                return rb.invalid('MCP-SYS-I-001', data={}, message=f"File not found: {path}")

            sha256 = compute_file_hash(file_path)

        from datetime import datetime

        entry = ProtectedEntry(

            path=path.replace("\\", "/"),

            entry_type=resolved_type,

            sha256=sha256,

            added_at=datetime.now().isoformat(),

            reason=reason,

        )

        entries = load_manifest()

        entries.append(entry)

        save_manifest(entries)

        return rb.success(

            'EN-WRITE-S-001',

            data={

                "added": entry.to_dict(),

                "total_entries": len(entries),

            },

            message=f"Protected: {path}",

        )

    elif command == "remove":

        if not path:

            return rb.invalid('MCP-SYS-I-001', data={}, message="path required for remove command")

        # Cannot remove the manifest itself (hardcoded)

        normalized = path.replace("\\", "/")

        if normalized == MANIFEST_REL_PATH:

            return rb.invalid(

                'MCP-SYS-I-001',

                data={},

                message=f"Cannot remove manifest self-protection: {MANIFEST_REL_PATH} is hardcoded.",

            )

        # Consume ephemeral HAT approval

        try:

            from tools.compliance.tokens import consume_hat_approval, write_hat_request

        except ImportError as e:

            return rb.error('MCP-SYS-E-001', data={}, message=f"HAT module import failed: {e}")

        valid, msg = consume_hat_approval(required_paths=[path])

        if not valid:

            write_hat_request(

                intent=f"Remove protected file: {path}",

                protected_paths=[path],

                root_category="ROOT_REPO",

            )

            return rb.invalid(

                'MCP-SYS-I-001',

                data={"requires_hat": True},

                message=f"HAT approval required: {msg}. Click the shield icon in CK3 Lens sidebar to approve.",

            )

        entries = load_manifest()

        before_count = len(entries)

        entries = [e for e in entries if e.path.replace("\\", "/") != normalized]

        after_count = len(entries)

        if before_count == after_count:

            return rb.invalid('MCP-SYS-I-001', data={}, message=f"Path not found in manifest: {path}")

        save_manifest(entries)

        return rb.success(

            'EN-WRITE-S-001',

            data={

                "removed": path,

                "total_entries": len(entries),

            },

            message=f"Removed protection: {path}",

        )

    else:

        return rb.invalid('MCP-SYS-I-002', data={}, message=f"Unknown command: {command}")

# ============================================================================

# Unified Search Tool (canonical addressing)

# ============================================================================

@mcp.tool()

@mcp_safe_tool

def ck3_search(

    command: Literal["auto", "symbols", "content", "paths", "filters", "mods"] = "auto",

    query: str = "",

    path: Optional[str] = None,

    symbol_type: Optional[str] = None,

    is_regex: bool = False,

    include_pattern: Optional[str] = None,

    recursive: bool = True,

    limit: int = 50,

    verbose: bool = False,

    definitions_only: bool = False,

    search_by: Literal["name", "workshop_id", "any"] = "any",

    fuzzy: bool = True,

) -> Reply:

    """

    Unified search across symbols, file content, file paths, and mods.

    6 commands in one tool:

      auto     -> Symbol + file path search (DB). Default.

      symbols  -> Symbol definitions + references from DB.

      content  -> Literal string grep on disk. ONLY use this command to search

                  for a SINGLE specific string that would appear verbatim in a

                  file (e.g. a date "724.1.1", a specific key "c_delhi", a code

                  pattern). Do NOT pass multiple entity names separated by spaces

                  or commas — that string will not appear anywhere in any file.

                  To check multiple entities, call content search once per entity.

      paths    -> Find files by path pattern (DB + filesystem).

      filters  -> Return available search filter vocabulary.

      mods     -> Search mods by name or workshop ID (DB).

    Args:

        command: Search command to execute.

        query: For auto/symbols: space-separated terms = AND logic.

               For content: ONE literal string (or regex if is_regex=True) that

               must appear verbatim in the file. Multi-entity comma/space lists

               are INVALID here — they will never match anything.

        path: Canonical address scope (root:game/common/traits, mod:MyMod/events).

        symbol_type: Filter by symbol type (trait, event, decision, etc.).

        is_regex: Treat query as regex (content command only).

        include_pattern: Glob pattern to filter files (e.g. "*.txt").

        recursive: Search subdirectories (default True).

        limit: Max results per category (default 50).

        verbose: More detail per result.

        definitions_only: symbols command: skip references.

        search_by: mods command: search by name, workshop_id, or any.

        fuzzy: Enable fuzzy matching for near-miss results when exact search fails.

              Works for auto, symbols, and mods commands. Default True.

    """

    from ck3lens.unified_tools import ck3_search_impl

    trace_info = get_current_trace_info()

    rb = ReplyBuilder(trace_info, tool='ck3_search')

    session = _get_session()

    trace = _get_trace()

    try:

        db = _get_db()

    except Exception:

        db = None

    return ck3_search_impl(

        command=command,

        query=query,

        path=path,

        symbol_type=symbol_type,

        is_regex=is_regex,

        include_pattern=include_pattern,

        recursive=recursive,

        limit=limit,

        verbose=verbose,

        definitions_only=definitions_only,

        search_by=search_by,

        fuzzy=fuzzy,

        session=session,

        db=db,

        trace=trace,

        rb=rb,

    )

# ============================================================================

# DELETED March 14, 2026: v1 ck3_search, ck3_grep_raw, ck3_file_search

# ============================================================================

# Replaced by unified ck3_search (above) which routes through EN2 pipeline.

# - ck3_search v1 → ck3_search (auto/symbols/paths/filters commands)

# - ck3_grep_raw → ck3_search(command="content")

# - ck3_file_search → ck3_search(command="paths")

# - ck3_confirm_not_exists → ck3_search with exhaustive mode

# - ck3_qr_conflicts / ck3_get_symbol_conflicts → ck3_conflicts tool

# ============================================================================

# ============================================================================

# DELETED January 3, 2026: ck3_get_scope_info

# 

# This tool was redundant with ck3_playset(command="mods").

# It used deprecated db_visibility() and cvids concepts.

# 

# To get mods in active playset, use: ck3_playset(command="mods")

# To get playset info, use: ck3_playset(command="get")

# ============================================================================

@mcp.tool()

@mcp_safe_tool

def ck3_parse_content(

    content: str,

    filename: str = "inline.txt",

) -> Reply:

    """

    Parse CK3 script content and return AST or errors.

    Uses error-recovering parser that collects ALL errors instead of

    stopping at the first one. Returns partial AST even when errors occur.

    For simple syntax validation, prefer ck3_validate_syntax instead.

    Use this when you need the actual AST for analysis.

    Args:

        content: CK3 script content to parse

        filename: Optional filename for error messages

    Returns:

        Reply with code WA-PARSE-S-001 on success (ast and node_count in data),

        or WA-PARSE-I-001 on syntax errors (errors list in data).

        Returns MCP-SYS-E-001 only on unexpected system failure.

    """

    trace = _get_trace()

    trace_info = get_current_trace_info()

    rb = ReplyBuilder(trace_info, tool="ck3_parse_content")

    # Wrap in try/except to distinguish user syntax errors from system failures

    try:

        result = parse_content(content, filename, recover=True)

    except Exception as e:

        # System failure - unexpected exception in parser

        import traceback

        return rb.error(

            "MCP-SYS-E-001",

            data={

                "error": f"Parser system failure: {e}",

                "error_type": type(e).__name__,

                "filename": filename,

                "traceback": traceback.format_exc(),

            },

            message=f"Parser system failure: {e}",

        )

    trace.log("mcp.tool", {

        "filename": filename,

        "content_length": len(content)

    }, {"success": result["success"], "error_count": len(result["errors"])})

    if result["success"]:

        # Count nodes in AST for the message

        node_count = 0

        if result["ast"]:

            # Simple node counting: count all dict items in AST

            def count_nodes(obj):

                if isinstance(obj, dict):

                    return 1 + sum(count_nodes(v) for v in obj.values())

                elif isinstance(obj, list):

                    return sum(count_nodes(item) for item in obj)

                return 0

            node_count = count_nodes(result["ast"])

        return rb.success(

            "WA-PARSE-S-001",

            data={

                "ast": result["ast"],

                "errors": result["errors"],  # Empty list on success

                "node_count": node_count,

                "filename": filename,

            },

        )

    else:

        # Parse failed due to user syntax errors - this is INVALID input, not system error

        first_error = result["errors"][0] if result["errors"] else {"line": 1, "message": "Unknown parse error"}

        return rb.invalid(

            "WA-PARSE-I-001",

            data={

                "ast": result["ast"],  # Partial AST may still be useful

                "errors": result["errors"],

                "line": first_error.get("line", 1),

                "error": first_error.get("message", "Unknown parse error"),

                "filename": filename,

            },

            message=f"Syntax error at line {first_error.get('line', 1)}: {first_error.get('message', 'Unknown parse error')}",

        )

@mcp.tool()

@mcp_safe_tool

def ck3_report_validation_issue(

    issue_type: Literal["parser_false_positive", "reference_false_positive", "parser_missed_error", "other"],

    code_snippet: str,

    expected_behavior: str,

    actual_behavior: str,

    notes: str | None = None,

) -> Reply:

    """

    Report a validation false positive or missed error.

    Use this when the parser or reference validator produces incorrect results.

    These reports help improve ck3raven's validation accuracy.

    Issue types:

    - parser_false_positive: Parser rejected valid CK3 syntax

    - reference_false_positive: Reference checker flagged a valid symbol

    - parser_missed_error: Parser accepted invalid CK3 syntax

    - other: Other validation issues

    Args:

        issue_type: Category of validation issue

        code_snippet: The CK3 code that was incorrectly validated

        expected_behavior: What should have happened

        actual_behavior: What actually happened

        notes: Optional additional context

    Returns:

        Confirmation with issue ID for tracking

    """

    import json

    import hashlib

    from datetime import datetime

    trace_info = get_current_trace_info()

    rb = ReplyBuilder(trace_info, tool='ck3_report_validation_issue')

    trace = _get_trace()

    # Create issue record

    issue_id = hashlib.sha256(

        f"{issue_type}:{code_snippet[:100]}:{datetime.now().isoformat()}".encode()

    ).hexdigest()[:12]

    issue = {

        "issue_id": issue_id,

        "issue_type": issue_type,

        "code_snippet": code_snippet,

        "expected_behavior": expected_behavior,

        "actual_behavior": actual_behavior,

        "notes": notes,

        "reported_at": datetime.now().isoformat(),

        "status": "open",

    }

    # Write to issues file in ck3raven project folder - requires ROOT_REPO from paths.py

    if ROOT_REPO is None:

        return rb.error(

            "GEN-E-001",

            data={},

            message="ROOT_REPO not configured - cannot write validation issues. Run in ck3raven-dev mode.",

        )

    issues_file = ROOT_REPO / "ck3lens_validation_issues.jsonl"

    with issues_file.open("a", encoding="utf-8") as f:

        f.write(json.dumps(issue, ensure_ascii=False) + "\n")

    trace.log("mcp.tool", {

        "issue_type": issue_type,

        "snippet_length": len(code_snippet)

    }, {"issue_id": issue_id})

    # This is an MCP-owned system operation - writes to ck3raven repo, not governed by EN

    return rb.success(

        'MCP-SYS-S-001',

        data={

            "issue_id": issue_id,

            "issues_file": str(issues_file),

        },

        message=f"Validation issue recorded. ID: {issue_id}. Will be reviewed in ck3raven-dev mode.",

    )

# ============================================================================

# DELETED January 2, 2026: ck3_get_completions, ck3_get_hover, ck3_get_definition

# 

# These tools imported from ck3lens.semantic which does not exist.

# They also referenced _get_playset_id() which is undefined.

# 

# The functionality should be provided by the CK3 Lens Explorer VS Code

# extension, not via MCP tools. Re-add when ck3lens.semantic is implemented.

# ============================================================================

# ============================================================================

# DELETED March 15, 2026: ck3_search_mods

# ============================================================================

# Merged into ck3_search(command="mods") via B1 consolidation.

# The search_mods executor in executors.py handles the DB lookup.

# ============================================================================

# ============================================================================

# Mode & Configuration Tools

# ============================================================================

@mcp.tool()

@mcp_safe_tool

def ck3_get_mode_instructions(

    mode: Literal["ck3lens", "ck3raven-dev"],

    mit_token: str | None = None,

    hat_token: str | None = None,

) -> Reply:

    """

    Get the instruction content for a specific agent mode.

    CRITICAL: This is THE initialization function. Call this FIRST.

    This single call handles:

    1. Database connection initialization

    2. Mode setting (persisted to file)

    3. WIP workspace initialization (mode-specific location)

    4. Playset detection

    5. Returns mode instructions + policy boundaries + session info

    Args:

        mode: The mode to initialize:

            - "ck3lens": CK3 modding with database search and mod editing

            - "ck3raven-dev": Full development mode for infrastructure

        mit_token: Deprecated — use hat_token instead.

        hat_token: Signed authorization token from extension (required).

    Returns:

        Mode instructions, policy boundaries, session context, and database status

    """

    trace_info = get_current_trace_info()

    rb = ReplyBuilder(trace_info, tool='ck3_get_mode_instructions')

    # Accept hat_token, fall back to mit_token for backward compat

    token = hat_token or mit_token

    reply = _ck3_get_mode_instructions_internal(mode, token, rb=rb)

    # Forward Reply (already built with our rb)

    if reply.reply_type == "I":

        return rb.invalid(reply.code, data=reply.data, message=reply.message)

    if reply.reply_type != "S":

        return rb.error(reply.code, data=reply.data, message=reply.message)

    return rb.success(reply.code, data=reply.data, message=reply.message)

def _ck3_get_mode_instructions_internal(mode: str, hat_token: str | None = None, *, rb: ReplyBuilder) -> Reply:

    """Internal implementation returning Reply."""

    from pathlib import Path

    from ck3lens.policy import AgentMode, initialize_workspace

    from ck3lens.agent_mode import set_agent_mode, get_agent_mode, VALID_MODES

    from tools.compliance.sigil import sigil_verify, sigil_available

    # Validate mode before proceeding

    if mode not in VALID_MODES:

        return rb.invalid("MCP-SYS-I-001", data={

            "error": f"Invalid mode: {mode}",

            "valid_modes": list(VALID_MODES),

        })

    # =========================================================================

    # IDEMPOTENCY: If already initialized in this mode, return short success.

    # Agents sometimes re-call this mid-conversation. Without this guard the

    # expired HAT token causes a rejection that poisons the agent's context

    # into thinking it is not initialized.

    # =========================================================================

    current_mode = get_agent_mode()

    if current_mode is not None and current_mode == mode:

        session = _get_session()

        return rb.success("MCP-SYS-S-001", data={

            "mode": current_mode,

            "already_initialized": True,

            "session_note": f"Already initialized in {current_mode} mode. You are ready to work.",

            "session": {

                "playset_name": session.playset_name,

                "db_path": str(session.db_path) if session.db_path else None,

            },

        }, message=f"Already initialized in {current_mode} mode. No action needed.")

    # =========================================================================

    # HUMAN-IN-THE-LOOP GATE: Inline HAT token from the prompt.

    # The extension signs "mode|timestamp|nonce" with Sigil when the user

    # clicks "Initialize Agent". Token format: "payload::signature".

    # Agents cannot forge this because they can't access CK3LENS_SIGIL_SECRET.

    # Fallback: CK3LENS_DEV_TOKEN for standalone testing without extension.

    # =========================================================================

    if hat_token:

        if not sigil_available():

            return rb.invalid("MCP-SYS-I-001", data={

                "error": "Cannot verify HAT token: extension signing secret is unavailable.",

                "requires_hat": True,

                "action": "Initialize through the CK3 Lens sidebar after the MCP connection is restored.",

            })

        parts = hat_token.split("::", 1)

        if len(parts) != 2:

            return rb.invalid("MCP-SYS-I-001", data={

                "error": "Malformed HAT token",

                "requires_hat": True,

                "action": "Click 'Initialize Agent' in the CK3 Lens sidebar.",

            })

        payload, signature = parts

        # Verify signature

        if not sigil_verify(payload, signature):

            return rb.invalid("MCP-SYS-I-001", data={

                "error": "HAT token signature invalid — not signed by extension",

                "requires_hat": True,

                "action": "Click 'Initialize Agent' in the CK3 Lens sidebar.",

            })

        # Verify payload contains the requested mode

        token_fields = payload.split("|")

        if len(token_fields) != 3 or token_fields[0] != mode:

            return rb.invalid("MCP-SYS-I-001", data={

                "error": f"HAT token mode mismatch (token has '{token_fields[0] if token_fields else '?'}', requested '{mode}')",

                "requires_hat": True,

                "action": "Click 'Initialize Agent' again with the correct mode.",

            })

        # Verify timestamp (5-minute window)

        try:

            from datetime import datetime, timezone

            token_time = datetime.fromisoformat(token_fields[1])

            age = (datetime.now(timezone.utc) - token_time).total_seconds()

            if age > 300:

                return rb.invalid("MCP-SYS-I-001", data={

                    "error": f"HAT token expired ({age:.0f}s old, max 300s)",

                    "requires_hat": True,

                    "action": "Click 'Initialize Agent' again.",

                })

        except (ValueError, IndexError):

            return rb.invalid("MCP-SYS-I-001", data={

                "error": "HAT token has invalid timestamp",

                "requires_hat": True,

                "action": "Click 'Initialize Agent' again.",

            })

    elif not hat_token:

        # No token provided — check for dev fallback

        dev_token = os.environ.get("CK3LENS_DEV_TOKEN", "")

        if not dev_token:

            return rb.invalid("MCP-SYS-I-001", data={

                "error": "No HAT token provided. Mode initialization requires human authorization.",

                "requires_hat": True,

                "action": "Click 'Initialize Agent' in the CK3 Lens sidebar, then send the prompt.",

            })

    # =========================================================================

    # STEP 1: Initialize database connection (what ck3_init_session used to do)

    # =========================================================================

    session_info = _init_session_internal()

    # =========================================================================

    # STEP 2: Load mode-specific instructions (mode commits only on success)

    # =========================================================================

    mode_files = {

        "ck3lens": "COPILOT_LENS_COMPATCH.md",

        "ck3raven-dev": "COPILOT_RAVEN_DEV.md",

    }

    if ROOT_REPO is None:

        return rb.error("MCP-SYS-E-001", data={

            "error": "ROOT_REPO not configured - mode instructions unavailable",

            "session": session_info,

        })

    instructions_path = ROOT_REPO / ".github" / mode_files[mode]

    if not instructions_path.exists():

        return rb.error("MCP-SYS-E-001", data={

            "error": f"Instructions file not found: {mode_files[mode]}",

            "expected_path": str(instructions_path),

            "session": session_info,

        })

    try:

        content = instructions_path.read_text(encoding="utf-8")

        # =====================================================================

        # STEP 3: Initialize WIP workspace (mode-specific location)

        # =====================================================================

        wip_info = None

        agent_mode = AgentMode.CK3LENS if mode == "ck3lens" else AgentMode.CK3RAVEN_DEV

        try:

            wip_info = initialize_workspace(

                mode=agent_mode,

                wipe=False  # DO NOT auto-wipe - preserve WIP contents across sessions

            )

        except Exception as e:

            wip_info = {"error": f"WIP init failed: {e}"}

        # Policy context: refer agent to canonical docs

        # (capability_matrix.py is the truth table, mode instructions have summaries)

        policy_context = {

            "mode": mode,

            "reference": "See docs/CANONICAL_ARCHITECTURE.md and tools/ck3lens_mcp/ck3lens/capability_matrix.py",

            "note": "Enforcement decides allow/deny at execution time based on the capability matrix.",

        }

        # =====================================================================

        # STEP 4: Log initialization to trace

        # =====================================================================

        trace = _get_trace()

        trace.log("session.mode", {"mode": mode}, {

            "mode": mode,

            "source_file": str(instructions_path),

            "wip_workspace": str(ROOT_CK3RAVEN_DATA / "wip"),

            "playset_id": session_info.get("playset_id"),

            "playset_name": session_info.get("playset_name"),

        })

        # =====================================================================

        # Build complete response before committing the process-local mode.

        # =====================================================================

        reply = rb.success("MCP-SYS-S-001", data={

            "mode": mode,

            "instructions": content,

            "source_file": str(instructions_path),

            "policy": policy_context,

            "wip_workspace": wip_info,

            "session_note": _get_mode_session_note(mode),

            # Session info (from what ck3_init_session used to return)

            "session": {

                "local_mods_folder": session_info.get("local_mods_folder"),

                "db_path": session_info.get("db_path"),

                "playset_id": session_info.get("playset_id"),

                "playset_name": session_info.get("playset_name"),

                "db_status": session_info.get("db_status", {}),

            },

            **({"db_warning": session_info["warning"]} if session_info.get("warning") else {}),

        })

        set_agent_mode(mode)  # type: ignore[arg-type]

        return reply

    except Exception as e:

        return rb.error("MCP-SYS-E-001", data={

            "error": str(e),

            "session": session_info,

        })

def _get_mode_session_note(mode: str) -> str:

    """Get a brief session note for the mode."""

    if mode == "ck3lens":

        return (

            "CK3 Lens mode active. You can:\n"

            "- Search symbols, files, content via database\n"

            "- Draft Python scripts in WIP workspace (root:ck3raven_data/wip/)\n"

            "- Use ck3_repair for launcher/cache issues\n\n"

            "You CANNOT write to workshop mods, vanilla, or ck3raven source."

        )

    elif mode == "ck3raven-dev":

        return (

            "CK3 Raven Dev mode active. You can:\n"

            "- Read all source code and mods (for parser/ingestion testing)\n"

            "- Write/edit ck3raven infrastructure code\n"

            "- Execute commands via ck3_exec (NOT run_in_terminal)\n"

            "- Write analysis scripts to root:ck3raven_data/wip/\n\n"

            "ABSOLUTE PROHIBITION: You CANNOT write to ANY mod files.\n"

        )

    return ""

@mcp.tool()

@mcp_safe_tool

def ck3_get_detected_mode() -> Reply:
    """Return the live initialized mode of this MCP process.

    Reads the same process-bound state used by enforcement. Historical traces
    and the AgentView mode file are never used to infer initialization.

    detected_mode is null when uninitialized. initialized, source, instance_id,
    and process_id identify the current state and process. Compatibility fields
    confidence, last_activity, and evidence remain: confidence is high because
    the state is read directly, last_activity is null, and evidence is empty.
    This status query does not initialize or change the mode.
    """
    from ck3lens.agent_mode import get_agent_mode

    trace_info = get_current_trace_info()
    rb = ReplyBuilder(trace_info, tool='ck3_get_detected_mode')
    mode = get_agent_mode()

    return rb.success(
        'MCP-SYS-S-001',
        data={
            "detected_mode": mode,
            "initialized": mode is not None,
            "source": "process",
            "instance_id": os.environ.get("CK3LENS_INSTANCE_ID", "default"),
            "process_id": os.getpid(),
            "confidence": "high",
            "last_activity": None,
            "evidence": [],
        },
        message=f"Live process mode: {mode or 'none'} (initialized: {mode is not None}).",
    )

@mcp.tool()

@mcp_safe_tool

def ck3_get_workspace_config() -> Reply:

    """

    Get the workspace configuration including tool sets and MCP settings.

    Use this to understand:

    - Available modes/tool sets and what tools they enable

    - MCP server configuration

    - local_mods_folder (for editable mods)

    - Database path

    Returns:

        Complete workspace configuration

    """

    from pathlib import Path

    import json

    trace_info = get_current_trace_info()

    rb = ReplyBuilder(trace_info, tool='ck3_get_workspace_config')

    session = _get_session()

    # Minimal config - WorldAdapter handles visibility, enforcement handles writes

    db_path_str = str(session.db_path) if session.db_path else None

    db_exists = session.db_path.exists() if session.db_path else False

    result = {

        "database": {

            "path": db_path_str,

            "exists": db_exists,

        },

        "playset_name": session.playset_name,

        "local_mods_folder": str(session.local_mods_folder) if session.local_mods_folder else None,

        "available_modes": [

            {

                "name": "ck3lens",

                "description": "CK3 modding - database search, conflict detection, mod editing",

                "use_case": "Fixing mod errors, compatibility patching, mod development",

            },

            {

                "name": "ck3raven-dev",

                "description": "Full development mode - all tools for infrastructure work",

                "use_case": "Python development, MCP server changes, database schema",

            },

        ],

        # NOTE: mcp.json is BANNED - MCP server is provided dynamically by CK3 Lens Explorer extension

        # NOTE: toolSets.json reading removed - hard-coded paths are not portable

    }

    return rb.success(

        'MCP-SYS-S-001',

        data=result,

        message="Workspace configuration loaded.",

    )

# ============================================================================

# Unified Database Query Tool

# ============================================================================

# Schema definitions for --help output

_DB_QUERY_SCHEMA = {

    "provinces": {

        "table": "province_lookup",

        "columns": ["province_id", "content_version_id", "name", "culture", "religion", "terrain", "holding_type"],

        "filters": {

            "province_id": "int - Exact province ID (e.g., 2333)",

            "content_version_id": "int - Specific mod/vanilla content version",

            "name": "str - LIKE pattern for name (e.g., '%paris%')",

            "culture": "str - Exact culture match",

            "religion": "str - Exact religion match",

            "terrain": "str - Terrain type (plains, hills, etc.)",

        },

        "examples": [

            'table=provinces, filters={"province_id": 2333, "content_version_id": 1}',

            'table=provinces, filters={"culture": "french", "content_version_id": 140}',

        ],

    },

    "characters": {

        "table": "character_lookup",

        "columns": ["character_id", "content_version_id", "name", "dynasty_id", "dynasty_house", "culture", 

                    "religion", "birth_date", "death_date", "father_id", "mother_id"],

        "filters": {

            "character_id": "int - Exact character ID",

            "content_version_id": "int - Specific mod/vanilla content version",

            "name": "str - LIKE pattern for name",

            "dynasty_id": "int - Dynasty ID",

            "culture": "str - Culture",

            "religion": "str - Religion",

            "birth_date_gte": "str - Birth >= date (e.g., '800.1.1')",

            "birth_date_lte": "str - Birth <= date",

        },

        "examples": [

            'table=characters, filters={"name": "%charlemagne%", "content_version_id": 1}',

            'table=characters, filters={"dynasty_id": 699, "content_version_id": 140}',

        ],

    },

    "dynasties": {

        "table": "dynasty_lookup",

        "columns": ["dynasty_id", "content_version_id", "name_key", "prefix", "culture", "motto"],

        "filters": {

            "dynasty_id": "int - Exact dynasty ID",

            "content_version_id": "int - Specific mod/vanilla content version",

            "name_key": "str - LIKE pattern for name key",

            "culture": "str - Culture",

        },

        "examples": [

            'table=dynasties, filters={"name_key": "%karling%", "content_version_id": 1}',

        ],

    },

    "titles": {

        "table": "title_lookup",

        "columns": ["title_key", "content_version_id", "tier", "capital_county", "capital_province_id", 

                    "de_jure_liege", "color_r", "color_g", "color_b", "definite_form", "landless"],

        "filters": {

            "title_key": "str - Title key or LIKE pattern (e.g., 'k_france' or 'd_%')",

            "content_version_id": "int - Specific mod/vanilla content version",

            "tier": "str - Tier: e, k, d, c, b, h",

            "de_jure_liege": "str - De jure parent title",

            "capital_county": "str - Capital county",

            "landless": "bool - Landless flag",

        },

        "examples": [

            'table=titles, filters={"tier": "k", "content_version_id": 1}',

            'table=titles, filters={"de_jure_liege": "k_france", "content_version_id": 140}',

        ],

    },

    "localization": {

        "table": "localization_entries",

        "columns": ["loc_key", "value", "file_id", "line_number", "language"],

        "filters": {

            "loc_key": "str - LIKE pattern for key",

            "value": "str - LIKE pattern for value text",

            "language": "str - Language (default: english)",

        },

        "examples": [

            'table=localization, filters={"loc_key": "%trait_brave%"}',

            'table=localization, filters={"value": "%Brave%", "language": "english"}',

        ],

    },

    "symbols": {

        "table": "symbols",

        "columns": ["symbol_id", "ast_id", "line_number", "column_number", "name", "symbol_type", "scope"],

        "filters": {

            "name": "str - LIKE pattern for symbol name",

            "symbol_type": "str - Type (trait, event, decision, etc.)",

        },

        "examples": [

            'table=symbols, filters={"name": "brave", "symbol_type": "trait"}',

            'table=symbols, filters={"symbol_type": "event"}',

        ],

    },

    "files": {

        "table": "files",

        "columns": ["file_id", "content_version_id", "relpath", "content_hash", "file_type", "deleted", "file_size"],

        "filters": {

            "relpath": "str - LIKE pattern for path",

            "content_version_id": "str - Opaque content version ID",

            "file_type": "str - File type",

            "deleted": "bool - Deleted flag (default: false)",

        },

        "examples": [

            'table=files, filters={"relpath": "%traits%"}',

            'table=files, filters={"file_type": "txt"}',

        ],

    },

    "refs": {

        "table": "refs",

        "columns": ["ref_id", "ast_id", "line_number", "column_number", "name", "ref_type", "resolution_status", "resolved_symbol_id"],

        "filters": {

            "name": "str - LIKE pattern for reference name",

            "ref_type": "str - Reference type",

            "resolution_status": "str - Resolution status (resolved, unresolved)",

        },

        "examples": [

            'table=refs, filters={"name": "brave"}',

            'table=refs, filters={"resolution_status": "unresolved"}',

        ],

    },

}

# NOTE: ck3_db_query() DELETED - March 2026 (B2 consolidation)

# Use ck3_db(command="query", ...) or ck3_db(command="help") instead.

# _ck3_db_query_internal() preserved below for the unified handler.

def _ck3_db_query_internal(

    *,

    rb: ReplyBuilder,

    table: str | None = None,

    filters: dict | None = None,

    columns: list[str] | None = None,

    sql: str | None = None,

    sql_file: str | None = None,

    limit: int = 100,

    unfiltered: bool = False,

) -> Reply:

    """Internal query implementation called by ck3_db(command="query")."""

    db = _get_db()

    # Raw SQL mode

    if sql or sql_file:

        from ck3lens.agent_mode import get_agent_mode

        if get_agent_mode() == "ck3lens" and not unfiltered:

            return rb.invalid(

                'WA-DB-I-001',

                data={

                    "error": "Raw SQL not allowed without unfiltered=True in ck3lens mode",

                    "reason": "Raw SQL bypasses playset visibility filtering. Use structured table queries (which are auto-filtered) or set unfiltered=True to explicitly bypass filtering.",

                    "example": "ck3_db(command='query', table='files', filters={'relpath': '%traits%'}) — auto-filtered to playset",

                },

                message="Raw SQL requires unfiltered=True in ck3lens mode. Use table= for auto-filtered queries.",

            )

        try:

            if sql_file:

                with open(sql_file, 'r', encoding='utf-8') as f:

                    sql = f.read()

            if sql is None:

                return rb.invalid('WA-DB-I-001', data={"parameter": "sql"}, message="No SQL provided")

            sql_upper = sql.strip().upper()

            if not sql_upper.startswith("SELECT") and not sql_upper.startswith("WITH"):

                return rb.denied('EN-DB-D-001', data={"mode": "query"}, message="Only SELECT queries allowed for safety")

            if "LIMIT" not in sql_upper:

                sql = f"{sql.rstrip().rstrip(';')} LIMIT {limit}"

            rows = db.conn.execute(sql).fetchall()

            cursor = db.conn.execute(sql)

            col_names = [d[0] for d in cursor.description] if cursor.description else []

            results = [dict(zip(col_names, row)) for row in rows]

            return rb.success(

                'WA-DB-S-001',

                data={"count": len(results), "results": results, "unfiltered": True},

                message=f"Query returned {len(results)} rows (unfiltered).",

            )

        except sqlite3.OperationalError as e:

            error_msg = str(e).lower()

            if any(pattern in error_msg for pattern in [

                "no such table", "no such column", "syntax error", 

                "near", "unrecognized token", "incomplete input"

            ]):

                return rb.invalid(

                    'WA-DB-I-003',

                    data={"sql": sql[:200] if sql else None, "exception": type(e).__name__},

                    message=f"Invalid SQL query: {e}",

                )

            return rb.error('WA-DB-E-001', data={"exception": type(e).__name__}, message=str(e))

        except Exception as e:

            return rb.error('MCP-SYS-E-001', data={"exception": type(e).__name__}, message=str(e))

    # Table query mode

    if not table:

        return rb.invalid('WA-DB-I-001', data={"hint": "Provide table= or sql=, or use command='help'"}, message="No table or SQL provided")

    if table not in _DB_QUERY_SCHEMA:

        return rb.invalid('WA-DB-I-002', data={"table": table, "available": list(_DB_QUERY_SCHEMA.keys())}, message=f"Unknown table '{table}'. Use command='help' to see options.")

    schema = _DB_QUERY_SCHEMA[table]

    db_table = schema["table"]

    select_cols = columns if columns else schema["columns"]

    select_clause = ", ".join(select_cols)

    conditions = []

    params = []

    if filters:

        for col, val in filters.items():

            if col.endswith("_gte"):

                real_col = col[:-4]

                conditions.append(f"{real_col} >= ?")

                params.append(val)

            elif col.endswith("_lte"):

                real_col = col[:-4]

                conditions.append(f"{real_col} <= ?")

                params.append(val)

            elif isinstance(val, str) and '%' in val:

                conditions.append(f"{col} LIKE ?")

                params.append(val)

            elif isinstance(val, bool):

                conditions.append(f"{col} = ?")

                params.append(1 if val else 0)

            else:

                conditions.append(f"{col} = ?")

                params.append(val)

    where_clause = " AND ".join(conditions) if conditions else "1=1"

    from ck3lens.agent_mode import get_agent_mode

    visibility_applied = False

    if get_agent_mode() == "ck3lens" and not unfiltered:

        tables_with_cvid = {"files"}

        if table in tables_with_cvid:

            session = _get_session()

            cvids = [
                mod.cvid for mod in session.mods if mod.cvid is not None
            ]

            if cvids:

                placeholders = ",".join("?" for _ in cvids)

                where_clause = f"({where_clause}) AND content_version_id IN ({placeholders})"

                params.extend(cvids)

            else:

                where_clause = f"({where_clause}) AND 1=0"

            visibility_applied = True

    params.append(limit)

    try:

        query = f"SELECT {select_clause} FROM {db_table} WHERE {where_clause} LIMIT ?"

        rows = db.conn.execute(query, params).fetchall()

        results = [dict(zip(select_cols, row)) for row in rows]

        data = {"count": len(results), "table": table, "results": results}

        if visibility_applied:

            data["visibility_filter"] = "active_playset"

        return rb.success(

            'WA-DB-S-001',

            data=data,

            message=f"Query returned {len(results)} rows from {table}.",

        )

    except Exception as e:

        return rb.error('MCP-SYS-E-001', data={"exception": type(e).__name__, "query": query if 'query' in dir() else None}, message=str(e))

def _get_table_stats(*, rb: ReplyBuilder) -> Reply:

    """Get row counts for all queryable tables."""

    db = _get_db()

    stats = {}

    for name, schema in _DB_QUERY_SCHEMA.items():

        try:

            count = db.conn.execute(f"SELECT COUNT(*) FROM {schema['table']}").fetchone()[0]

            stats[name] = count

        except:

            stats[name] = "error"

    return rb.success("MCP-SYS-S-001", data=stats)

# ============================================================================

# Paths Doctor - Diagnostic Tool

# ============================================================================

@mcp.tool()

@mcp_safe_tool

def ck3_paths_doctor(

    include_resolution_checks: bool = True,

    verbose: bool = False,

) -> Reply:

    """

    Run Paths Doctor to diagnose path configuration issues.

    A read-only diagnostic utility that validates:

    - Required paths (ROOT_GAME, ROOT_STEAM)

    - Optional paths (ROOT_USER_DOCS, ROOT_UTILITIES, etc.)

    - Computed paths (ROOT_REPO, ROOT_CK3RAVEN_DATA)

    - Data structure (wip/, playsets/, logs/, config/ under ROOT_CK3RAVEN_DATA)

    - Local mods folder configuration

    - Config file health

    - Resolution cross-checks (optional)

    Args:

        include_resolution_checks: Run WorldAdapter resolution cross-checks (default True)

        verbose: Include OK findings in output (default False)

    Returns:

        PathsDoctorReport with findings categorized by severity (ERROR, WARN, OK)

    Note:

        This tool ALWAYS returns Reply(S) even if findings include ERRORs.

        The report's 'ok' field indicates whether configuration is healthy.

        Use this to diagnose path issues before reporting bugs.

    """

    from ck3lens.paths_doctor import run_paths_doctor, PathsDoctorReport

    from dataclasses import asdict

    trace_info = get_current_trace_info()

    rb = ReplyBuilder(trace_info, tool='ck3_paths_doctor')

    try:

        report = run_paths_doctor(include_resolution_checks=include_resolution_checks)

        # Convert to serializable dict

        report_dict = {

            "ok": report.ok,

            "findings": [asdict(f) for f in report.findings],

            "summary": report.summary,

            "config_path": report.config_path,

        }

        # Filter to non-OK findings unless verbose

        if not verbose:

            report_dict["findings"] = [

                f for f in report_dict["findings"]

                if f["severity"] != "OK"

            ]

            report_dict["verbose"] = False

        else:

            report_dict["verbose"] = True

        # Always Reply(S) - the report.ok field indicates health

        status = "HEALTHY" if report.ok else "UNHEALTHY"

        return rb.success(

            'MCP-SYS-S-001',

            data=report_dict,

            message=f"Paths Doctor: {status} ({report.summary['ERROR']} errors, {report.summary['WARN']} warnings, {report.summary['OK']} ok)",

        )

    except Exception as e:

        return rb.error(

            'MCP-SYS-E-001',

            data={"error": str(e)},

            message=f"Paths Doctor failed: {e}",

        )

# ============================================================================

# QBuilder - Build System Tools

# ============================================================================

@mcp.tool()

@mcp_safe_tool

def ck3_qbuilder(

    command: Literal[

        "status", "build", "stop", "restart",

        "discover", "enqueue", "await", "logs", "failed",

    ] = "status",

    # --- build / restart ---

    fresh: bool = False,

    # --- enqueue ---

    paths: Optional[list[str]] = None,

    mod_name: Optional[str] = None,

    priority: Literal["normal", "high"] = "normal",

    # --- await ---

    timeout_ms: int = 30000,

    # --- logs ---

    lines: int = 50,

    # --- failed ---

    retry: bool = False,

) -> Reply:

    """

    Unified QBuilder tool — full daemon lifecycle and build system management.

    ARCHITECTURE: MCP servers are READ-ONLY clients.

    All DB mutations go through the QBuilder daemon via IPC.

    See docs/SINGLE_WRITER_ARCHITECTURE.md for details.

    Commands:

    command=status    Get daemon state, queue counts, lock holder, watcher info

    command=build     Launch daemon subprocess (the single DB writer)

    command=stop      Graceful shutdown via IPC

    command=restart   Stop + wait + build (picks up code changes; accepts fresh=True)

    command=discover  Scan active playset and enqueue discovery tasks via IPC

    command=enqueue   Enqueue specific files for (re)processing via IPC

    command=await     Block until daemon finishes all pending work or timeout

    command=logs      Read tail of today's daemon log file

    command=failed    List or retry failed queue items

    Args:

        command:     Operation to perform

        fresh:       For build/restart — wipe all data and rebuild from scratch

        paths:       For enqueue — list of canonical paths (mod:X/... or root:game/...)

        mod_name:    For enqueue — mod name for IPC routing

        priority:    For enqueue — "normal" or "high"

        timeout_ms:  For await — max wait time in ms (default 30000)

        lines:       For logs — number of log lines to return (default 50)

        retry:       For failed — if True, re-enqueue all failed items

    Examples:

        ck3_qbuilder(command="status")

        ck3_qbuilder(command="build")

        ck3_qbuilder(command="build", fresh=True)

        ck3_qbuilder(command="stop")

        ck3_qbuilder(command="restart")

        ck3_qbuilder(command="restart", fresh=True)

        ck3_qbuilder(command="discover")

        ck3_qbuilder(command="enqueue", paths=["mod:My Mod/common/traits/fix.txt"])

        ck3_qbuilder(command="await", timeout_ms=60000)

        ck3_qbuilder(command="logs", lines=100)

        ck3_qbuilder(command="failed")

        ck3_qbuilder(command="failed", retry=True)

    """

    import subprocess

    import sys

    import time

    from ck3lens.daemon_client import daemon, DaemonNotAvailableError

    trace_info = get_current_trace_info()

    rb = ReplyBuilder(trace_info, tool='ck3_qbuilder')

    repo_root = str(ROOT_REPO)

    python_exe = sys.executable

    db_path = ROOT_CK3RAVEN_DATA / "ck3raven.db"

    log_dir = ROOT_CK3RAVEN_DATA / "logs"

    # ── helpers ──────────────────────────────────────────────────────────

    def _get_lock_info() -> dict:

        """Read writer lock file for status display (no IPC needed)."""

        try:

            from qbuilder.writer_lock import WriterLock

            lock = WriterLock(db_path)

            info = lock.get_holder_info()

            if info:

                alive = lock.is_holder_alive()

                return {

                    "held": True,

                    "holder_pid": info.pid,

                    "acquired_at": info.acquired_at,

                    "holder_alive": alive,

                }

        except Exception:

            pass

        return {"held": False}

    def _launch_daemon(use_fresh: bool = False) -> dict:

        """Launch daemon subprocess and wait for a definite startup outcome."""

        from datetime import datetime

        from ck3lens.daemon_lifecycle import terminate_and_reap

        log_dir.mkdir(parents=True, exist_ok=True)

        log_file = log_dir / f"daemon_{datetime.now().strftime('%Y-%m-%d')}.log"

        cmd = [python_exe, "-u", "-m", "qbuilder.cli", "daemon"]

        if use_fresh:

            cmd.append("--fresh")

        launch_env = os.environ.copy()

        launch_env["PYTHONUNBUFFERED"] = "1"

        launch_env["PYTHONFAULTHANDLER"] = "1"

        with open(log_file, "a", encoding="utf-8") as log_handle:

            log_handle.write(f"\n\n=== Daemon started at {datetime.now().isoformat()} ===\n")

            log_handle.flush()

            proc = subprocess.Popen(

                cmd,

                cwd=repo_root,

                stdout=log_handle,

                stderr=subprocess.STDOUT,

                start_new_session=True,

                stdin=subprocess.DEVNULL,

                close_fds=True,

                env=launch_env,

            )

        startup_timeout = 120.0

        deadline = time.time() + startup_timeout

        contender_exit_code = None

        while time.time() < deadline:

            if daemon.is_available(force_check=True):

                health = daemon.health()

                return {

                    "pid": health.daemon_pid,

                    "ipc_port": health.ipc_port,

                    "log_file": str(log_file),

                }

            if contender_exit_code is None and proc.poll() is not None:

                contender_exit_code = proc.wait()

            time.sleep(0.2)

        if daemon.is_available(force_check=True):

            health = daemon.health()

            return {

                "pid": health.daemon_pid,

                "ipc_port": health.ipc_port,

                "log_file": str(log_file),

            }

        if proc.poll() is None:

            terminate_and_reap(proc, tree_on_windows=True)

        detail = (

            f"contender exited with code {contender_exit_code}"

            if contender_exit_code is not None

            else "child stopped"

        )

        client_state = daemon.diagnostic_state()

        raise RuntimeError(

            f"Daemon did not become reachable within {startup_timeout:.0f} seconds; "

            f"{detail}. Client state: {json.dumps(client_state, sort_keys=True)}. "

            f"See {log_file}"

        )

    def _wait_for_exit(max_wait: float = 10.0) -> bool:

        """Poll until daemon IPC is gone and the writer lock has been released."""

        def _has_exited() -> bool:

            lock_info = _get_lock_info()

            lock_released = (not lock_info.get("held")

                             or not lock_info.get("holder_alive"))

            return not daemon.is_available(force_check=True) and lock_released

        deadline = time.time() + max_wait

        while time.time() < deadline:

            if _has_exited():

                return True

            time.sleep(0.2)

        return _has_exited()

    # ── status ───────────────────────────────────────────────────────────

    if command == "status":

        lock_info = _get_lock_info()

        from qbuilder.logging import get_log_file

        log_path = get_log_file()

        try:

            if daemon.is_available():

                health = daemon.health()

                status = daemon.get_queue_status()

                status["daemon_available"] = True

                status["lock"] = {

                    "held": True,

                    "holder_pid": health.daemon_pid,

                    "holder_alive": True,

                    "source": "daemon_ipc",

                }

                if log_path:

                    status["log_file"] = str(log_path)

                queue = status.get("queue", {})

                activity = status.get("recent_activity", {})

                activity_state = activity.get("state", "unknown") if activity else "unknown"

                return rb.success(

                    'MCP-QB-S-001',

                    data=status,

                    message=f"Daemon running ({activity_state}). Pending: {queue.get('pending', 0)}, Leased: {queue.get('leased', 0)}.",

                )

            else:

                data: dict = {

                    "daemon_available": False,

                    "lock": lock_info,

                    "hint": "Use ck3_qbuilder(command='build') to start.",

                }

                if log_path:

                    data["log_file"] = str(log_path)

                return rb.success(

                    'MCP-QB-S-001',

                    data=data,

                    message="Daemon not running.",

                )

        except Exception as e:

            return rb.error('MCP-QB-E-002', data={"exception": type(e).__name__, "lock": lock_info}, message=f"Status check failed: {e}")

    # ── build ────────────────────────────────────────────────────────────

    elif command == "build":

        # Check if already running via IPC

        if daemon.is_available():

            health = daemon.health()

            return rb.invalid(

                'MCP-QB-I-002',

                data={

                    "daemon_pid": health.daemon_pid,

                    "state": health.state,

                    "queue_pending": health.queue_pending,

                },

                message=f"Daemon already running (PID {health.daemon_pid}, state: {health.state}).",

            )

        # Check writer lock for race condition

        lock_info = _get_lock_info()

        if lock_info.get("held") and lock_info.get("holder_alive"):

            # Lock held by a live process but IPC not responding — wait

            for _ in range(240):

                time.sleep(0.5)

                if daemon.is_available(force_check=True):

                    health = daemon.health()

                    return rb.invalid(

                        'MCP-QB-I-002',

                        data={

                            "daemon_pid": health.daemon_pid,

                            "state": health.state,

                            "queue_pending": health.queue_pending,

                        },

                        message=f"Daemon already running (PID {health.daemon_pid}, connected after lock wait).",

                    )

            return rb.invalid(

                'MCP-QB-I-007',

                data={

                    "holder_pid": lock_info.get("holder_pid"),

                    "hint": "Kill the stale process or use ck3_qbuilder(command='stop')",

                },

                message=f"Writer lock held by PID {lock_info.get('holder_pid')} but IPC not responding.",

            )

        try:

            result = _launch_daemon(use_fresh=fresh)

            msg = f"Daemon started (PID {result['pid']})"

            if fresh:

                msg += " with --fresh (full rebuild)"

            return rb.success('MCP-QB-S-002', data=result, message=f"{msg}.")

        except Exception as e:

            return rb.error('MCP-QB-E-001', data={"exception": type(e).__name__}, message=f"Failed to start daemon: {e}")

    # ── stop ─────────────────────────────────────────────────────────────

    elif command == "stop":

        try:

            if not daemon.is_available():

                return rb.success(

                    'MCP-QB-S-003',

                    data={"was_running": False},

                    message="Daemon was not running.",

                )

            result = daemon.shutdown(graceful=True)

            if not result.get("acknowledged", False):

                return rb.invalid(

                    'MCP-QB-I-002',

                    data={

                        "was_running": True,

                        "acknowledged": False,

                        "denied_reason": result.get("denied_reason"),

                        "active_clients": result.get("active_clients", []),

                    },

                    message="Shutdown denied because other VS Code windows are still attached to the daemon.",

                )

            return rb.success(

                'MCP-QB-S-003',

                data={

                    "was_running": True,

                    "acknowledged": True,

                },

                message="Shutdown signal sent. Daemon will exit after completing current work.",

            )

        except DaemonNotAvailableError:

            return rb.success(

                'MCP-QB-S-003',

                data={"was_running": False},

                message="Daemon was not running.",

            )

        except Exception as e:

            return rb.error('MCP-QB-E-002', data={"exception": type(e).__name__}, message=f"Stop failed: {e}")

    # ── restart ──────────────────────────────────────────────────────────

    elif command == "restart":

        # Phase 1: Stop if running

        was_running = False

        try:

            if daemon.is_available():

                was_running = True

                shutdown_result = daemon.shutdown(graceful=True)

                if not shutdown_result.get("acknowledged", False):

                    return rb.invalid(

                        'MCP-QB-I-002',

                        data={

                            "phase": "stop",

                            "denied_reason": shutdown_result.get("denied_reason"),

                            "active_clients": shutdown_result.get("active_clients", []),

                        },

                        message="Restart denied because other VS Code windows are still attached to the daemon.",

                    )

                if not _wait_for_exit(max_wait=15.0):

                    return rb.error(

                        'MCP-QB-E-003',

                        data={"phase": "stop", "hint": "Daemon did not exit in time"},

                        message="Restart failed — daemon did not stop within 15 seconds.",

                    )

        except DaemonNotAvailableError:

            pass

        except Exception as e:

            return rb.error('MCP-QB-E-003', data={"phase": "stop", "exception": type(e).__name__}, message=f"Restart failed during stop: {e}")

        # Phase 2: Start fresh process

        try:

            result = _launch_daemon(use_fresh=fresh)

            result["was_running"] = was_running

            msg = "Daemon restarted"

            if fresh:

                msg += " with --fresh (full rebuild)"

            return rb.success('MCP-QB-S-004', data=result, message=f"{msg} (PID {result['pid']}).")

        except Exception as e:

            return rb.error('MCP-QB-E-003', data={"phase": "start", "exception": type(e).__name__}, message=f"Restart failed during start: {e}")

    # ── discover ─────────────────────────────────────────────────────────

    elif command == "discover":

        try:

            if not daemon.is_available():

                return rb.invalid(

                    'MCP-QB-I-001',

                    data={"hint": "Start daemon first with ck3_qbuilder(command='build')"},

                    message="Daemon not running — cannot enqueue discovery.",

                )

            result = daemon.playset_scan()

            return rb.success(
                'MCP-QB-S-005',
                data=result,
                message="Active playset identities prepared and discovery completed.",
            )

        except DaemonNotAvailableError:

            return rb.invalid(

                'MCP-QB-I-001',

                data={"hint": "Start daemon first with ck3_qbuilder(command='build')"},

                message="Daemon not running — cannot enqueue discovery.",

            )

        except DaemonError as e:

            return rb.error(

                'MCP-QB-E-002',

                data={"daemon_code": e.code},

                message=f"Discovery failed: {e.message}",

            )

        except Exception as e:

            return rb.error('MCP-QB-E-002', data={"exception": type(e).__name__}, message=f"Discovery failed: {e}")

    # ── enqueue ──────────────────────────────────────────────────────────

    elif command == "enqueue":

        if not paths:

            return rb.invalid(

                'MCP-QB-I-004',

                data={"hint": "Provide paths=['mod:Mod Name/common/traits/file.txt']"},

                message="No paths provided for enqueue.",

            )

        if not daemon.is_available():

            return rb.invalid(

                'MCP-QB-I-001',

                data={"hint": "Start daemon first with ck3_qbuilder(command='build')"},

                message="Daemon not running — cannot enqueue files.",

            )

        try:

            wa2 = _get_world_v2()

            session = _get_session()

            session_mod_names = {m.name for m in session.mods}

            grouped_paths: dict[str, list[str]] = {}

            failures: list[dict[str, str]] = []

            for raw_path in paths:

                path_str = str(raw_path)

                resolved_mod = mod_name

                rel_path = path_str.replace("\\", "/").strip("/")

                if path_str.startswith("mod:") or path_str.startswith("root:"):

                    s_reply, s_ref = wa2.resolve(path_str, require_exists=True, rb=rb)

                    if s_ref is None:

                        failures.append({

                            "path": path_str,

                            "reason": s_reply.message,

                        })

                        continue

                    resolved_mod = s_reply.data.get("mod_name")

                    rel_path = s_reply.data.get("relative_path", "")

                    if not resolved_mod:

                        failures.append({

                            "path": path_str,

                            "reason": "ck3_qbuilder enqueue requires mod: paths or an explicit mod_name-bound relative path",

                        })

                        continue

                if not resolved_mod:

                    failures.append({

                        "path": path_str,

                        "reason": "relative enqueue paths require mod_name from the active session",

                    })

                    continue

                if resolved_mod not in session_mod_names:

                    failures.append({

                        "path": path_str,

                        "reason": f"mod not in active session.mods: {resolved_mod}",

                    })

                    continue

                if not rel_path:

                    failures.append({"path": path_str, "reason": "empty relative path"})

                    continue

                grouped_paths.setdefault(resolved_mod, []).append(rel_path)

            enqueued = 0

            deduped = 0

            for resolved_mod, rel_paths in grouped_paths.items():

                result = daemon.enqueue_files(

                    paths=rel_paths,

                    mod_name=resolved_mod,

                    priority=priority,

                    reason="mcp_enqueue",

                )

                enqueued += result.get("enqueued", 0)

                deduped += result.get("deduped", 0)

                failures.extend(result.get("failures", []))

            total = enqueued + deduped

            return rb.success(

                'MCP-QB-S-006',

                data={

                    "enqueued": enqueued,

                    "deduped": deduped,

                    "failures": failures,

                },

                message=f"Enqueued {enqueued} files ({deduped} deduped) from {total} accepted requests.",

            )

        except DaemonNotAvailableError:

            return rb.invalid(

                'MCP-QB-I-001',

                data={"hint": "Daemon disconnected during enqueue"},

                message="Daemon not running — enqueue failed.",

            )

        except Exception as e:

            return rb.error('MCP-QB-E-002', data={"exception": type(e).__name__}, message=f"Enqueue failed: {e}")

    # ── await ────────────────────────────────────────────────────────────

    elif command == "await":

        if not daemon.is_available():

            return rb.invalid(

                'MCP-QB-I-001',

                data={"hint": "Start daemon first with ck3_qbuilder(command='build')"},

                message="Daemon not running — cannot await.",

            )

        try:

            result = daemon.await_idle(timeout_ms=timeout_ms)

            if result.get("idle"):

                return rb.success(

                    'MCP-QB-S-007',

                    data=result,

                    message="Daemon is idle — all pending work complete.",

                )

            else:

                return rb.invalid(

                    'MCP-QB-I-003',

                    data=result,

                    message=f"Timed out after {timeout_ms}ms. Queue still has {result.get('queue_pending', '?')} pending items.",

                )

        except DaemonNotAvailableError:

            return rb.invalid(

                'MCP-QB-I-001',

                data={"hint": "Daemon disconnected while waiting"},

                message="Daemon not running — await failed.",

            )

        except Exception as e:

            return rb.error('MCP-QB-E-002', data={"exception": type(e).__name__}, message=f"Await failed: {e}")

    # ── logs ─────────────────────────────────────────────────────────────

    elif command == "logs":

        from qbuilder.logging import get_log_file

        log_path = get_log_file()

        if not log_path.exists():

            return rb.invalid(

                'MCP-QB-I-005',

                data={"log_dir": str(log_dir)},

                message="No daemon log file found for today.",

            )

        try:

            with open(log_path, "r", encoding="utf-8", errors="replace") as f:

                all_lines = f.readlines()

            tail = all_lines[-lines:] if len(all_lines) > lines else all_lines

            return rb.success(

                'MCP-QB-S-008',

                data={

                    "log_file": str(log_path),

                    "total_lines": len(all_lines),

                    "returned_lines": len(tail),

                    "output": "".join(tail),

                },

                message=f"Last {len(tail)} lines from {log_path.name}.",

            )

        except Exception as e:

            return rb.error('MCP-QB-E-004', data={"log_file": str(log_path)}, message=f"Error reading log: {e}")

    # ── failed ───────────────────────────────────────────────────────────

    elif command == "failed":

        from ck3lens.db_api import db as db_api_instance

        if not retry:

            # List failed items via read-only DB

            result = db_api_instance.execute_safe(

                "SELECT bq.build_id, bq.file_id, bq.envelope, bq.error_message, bq.error_step, "

                "bq.created_at, bq.completed_at, f.relpath, cv.name AS mod_name "

                "FROM build_queue bq "

                "LEFT JOIN files f ON bq.file_id = f.file_id "

                "LEFT JOIN content_versions cv ON f.content_version_id = cv.content_version_id "

                "WHERE bq.status = 'error' "

                "ORDER BY bq.completed_at DESC "

                "LIMIT 100",

            )

            if "error" in result:

                return rb.error('MCP-QB-E-002', data=result, message=f"Failed to query failed items: {result['error']}")

            rows = result.get("rows", [])

            if not rows:

                return rb.invalid(

                    'MCP-QB-I-006',

                    data={"queue_status": "no_failures"},

                    message="No failed items in queue.",

                )

            items = []

            for row in rows:

                items.append({

                    "build_id": row[0],

                    "file_id": row[1],

                    "envelope": row[2],

                    "error_message": row[3],

                    "error_step": row[4],

                    "rel_path": row[7],

                    "mod_name": row[8],

                })

            return rb.success(

                'MCP-QB-S-009',

                data={"failed_count": len(items), "items": items},

                message=f"{len(items)} failed items in queue.",

            )

        else:

            # Retry failed queue rows in-place inside the daemon.

            if not daemon.is_available():

                return rb.invalid(

                    'MCP-QB-I-001',

                    data={"hint": "Start daemon first to retry failed items"},

                    message="Daemon not running — cannot retry.",

                )

            try:

                result = daemon.retry_failed()

                retried = result.get("retried", 0)

                if retried == 0:

                    return rb.invalid(

                        'MCP-QB-I-006',

                        data={"queue_status": "no_failures"},

                        message="No failed items to retry.",

                    )

                return rb.success(

                    'MCP-QB-S-010',

                    data=result,

                    message=f"Reset {retried} failed queue items to pending for retry.",

                )

            except DaemonNotAvailableError:

                return rb.invalid(

                    'MCP-QB-I-001',

                    data={"hint": "Daemon disconnected during retry_failed"},

                    message="Daemon not running — cannot retry failed items.",

                )

            except Exception as e:

                return rb.error('MCP-QB-E-002', data={"exception": type(e).__name__}, message=f"Retry failed items failed: {e}")

    # ── unknown ──────────────────────────────────────────────────────────

    else:

        return rb.invalid(

            'MCP-SYS-I-002',

            data={

                "command": command,

                "valid_commands": [

                    "status", "build", "stop", "restart",

                    "discover", "enqueue", "await", "logs", "failed",

                ],

            },

            message=f"Unknown command: {command}",

        )

# ============================================================================

# World Adapter v2 — Canonical Addressing (Sprint 0)

# ============================================================================

def _get_world_v2():

    """

    Create WorldAdapterV2 with current session.

    No caching — creation is ~microseconds (just builds a 6-key dict from

    path constants). Caching caused stale session refs after playset switch.

    """

    from ck3lens.world_adapter_v2 import WorldAdapterV2

    return WorldAdapterV2.create(session=_get_session())

@mcp.tool()

@mcp_safe_tool

def ck3_dir(

    command: Literal["pwd", "cd", "list", "tree"],

    path: str | None = None,

    depth: int = 3,

) -> Reply:

    """

    Directory navigation using canonical addressing v2.

    This is the Sprint 0 pilot tool for the canonical addressing refactor.

    All paths are expressed as session-absolute addresses — no host paths

    are ever exposed to the agent.

    Commands:

        pwd  — Show current session home root

        cd   — Change session home root (e.g. 'root:repo', 'root:game')

        list — List directory contents (files and folders)

        tree — Show directory tree (folders only, depth-limited)

    Canonical address syntax:

        root:repo/src/server.py          (root-category addressing)

        root:ck3raven_data/wip/          (data folder)

        mod:SomeMod/common/traits        (mod addressing)

    If no path is given, commands operate on the current home root.

    Bare relative paths are resolved against the current home root.

    Args:

        command: One of pwd, cd, list, tree

        path: Canonical address or relative path (optional for pwd)

        depth: Tree depth limit (default 3, only used by tree command)

    """

    from ck3lens.impl.dir_ops import ck3_dir_impl

    from ck3lens.leak_detector import HostPathLeakError

    trace_info = get_current_trace_info()

    rb = ReplyBuilder(trace_info, tool="ck3_dir")

    wa2 = _get_world_v2()

    try:

        data = ck3_dir_impl(command=command, path=path, depth=depth, wa2=wa2, rb=rb)

    except HostPathLeakError as e:

        return rb.error(

            "WA-DIR-E-001",

            data={"error": str(e), "command": command},

            message=f"Internal error: host path leaked in output",

        )

    except ValueError as e:

        return rb.invalid(

            "WA-DIR-I-001",

            data={"error": str(e), "command": command, "path": path},

            message=str(e),

        )

    return rb.success(

        "WA-DIR-S-001",

        data=data,

        message=f"ck3_dir {command} complete",

    )

# ============================================================================

# Main Entry Point

# ============================================================================

import signal

import atexit

# Structured logging (canonical per docs/CANONICAL_LOGS.md)

from ck3lens.logging import info as log_info, error as log_error, bootstrap as log_bootstrap, set_instance_id

from ck3lens.log_rotation import rotate_logs

_pid = os.getpid()
_lifecycle_ipc_client: Any | None = None

def _log_exit():

    """Log exit for debugging - called via atexit."""

    global _instance_id, _pid, _lifecycle_ipc_client

    if _lifecycle_ipc_client is not None:

        lifecycle_client = _lifecycle_ipc_client

        _lifecycle_ipc_client = None

        lifecycle_client.close()

    try:

        from ck3lens.daemon_client import daemon as daemon_client

        unregister_result = daemon_client.unregister_client()

        if unregister_result.get("remaining_clients") == 0:

            daemon_client.shutdown(graceful=True)

    except Exception:

        pass

    # Dual output: structured log file + stderr for VS Code capture

    log_info("mcp.dispose", "MCP server exit (atexit)", instance_id=_instance_id, pid=_pid)

    print(f"MCP server exit: instanceId={_instance_id} pid={_pid}", file=sys.stderr)

def _monitor_lifecycle_disconnect(lifecycle_client: Any) -> None:

    """Exit immediately when the owning extension closes the lifecycle lease."""

    global _lifecycle_ipc_client

    try:

        lifecycle_client.wait_for_disconnect()

    except Exception as e:

        log_info("mcp.dispose", "IPC lifecycle monitor ended with error",

                 instance_id=_instance_id, pid=_pid, error=str(e))

    if _lifecycle_ipc_client is not lifecycle_client:

        return

    log_info("mcp.dispose", "Owning extension lifecycle channel closed; exiting",

             instance_id=_instance_id, pid=_pid)

    try:

        sys.stderr.flush()

    finally:

        os._exit(0)


def _announce_subprocess_start() -> bool:

    """Open and monitor the process-lifetime IPC channel."""

    global _lifecycle_ipc_client

    try:

        from ck3lens.ipc_client import announce_subprocess_start

        _lifecycle_ipc_client = announce_subprocess_start(_instance_id, _pid)

        if _lifecycle_ipc_client is None:

            log_info("mcp.init", "IPC startup hello failed",

                     instance_id=_instance_id, pid=_pid)

            return False

        lifecycle_monitor = threading.Thread(

            target=_monitor_lifecycle_disconnect,

            args=(_lifecycle_ipc_client,),

            daemon=True,

            name="mcp-lifecycle-lease",

        )

        lifecycle_monitor.start()

        log_info("mcp.init", "IPC lifecycle channel opened",

                 instance_id=_instance_id, pid=_pid)

        return True

    except Exception as e:

        _lifecycle_ipc_client = None

        log_info("mcp.init", "IPC startup hello failed",

                 instance_id=_instance_id, pid=_pid, error=str(e))

        return False

def _setup_signal_handlers() -> None:

    """Setup signal handlers for clean shutdown."""

    def handle_signal(signum, frame):

        global _instance_id, _pid

        log_info("mcp.dispose", f"Signal {signum} received, shutting down", 

                 instance_id=_instance_id, pid=_pid, signal=signum)

        print(f"MCP server: signal {signum} received, shutting down instanceId={_instance_id} pid={_pid}", file=sys.stderr)

        sys.exit(0)

    # Handle common termination signals

    signal.signal(signal.SIGTERM, handle_signal)

    signal.signal(signal.SIGINT, handle_signal)

    if hasattr(signal, 'SIGBREAK'):  # Windows

        signal.signal(signal.SIGBREAK, handle_signal)


async def _run_mcp_stdio() -> None:

    def log_response_warning(event: str, data: dict[str, object]) -> None:
        warn("mcp.request", event, instance_id=_instance_id, pid=_pid, **data)

    guard_completed_responses(mcp._mcp_server, log_response_warning)

    def log_transport_event(event: str, data: dict[str, object]) -> None:

        log_info(

            "mcp.transport",

            event,

            instance_id=_instance_id,

            pid=_pid,

            **data,

        )

    async with diagnostic_stdio_server(log_event=log_transport_event) as (read_stream, write_stream):

        await mcp._mcp_server.run(

            read_stream,

            write_stream,

            mcp._mcp_server.create_initialization_options(),

        )


if __name__ == "__main__":

    import argparse

    _startup_parser = argparse.ArgumentParser()
    _startup_parser.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    _transport = _startup_parser.parse_args().transport
    if _transport == "streamable-http":
        if not os.environ.get("CK3LENS_HTTP_TOKEN") or not os.environ.get("CK3LENS_IPC_PORT"):
            raise RuntimeError("Owned HTTP startup requires a transport token and extension lifecycle channel")

        from ck3lens.http_transport import start_owner_monitor

        start_owner_monitor()

    # Enable faulthandler FIRST — prints traceback on segfault/abort to stderr,

    # which VS Code captures from the MCP process output

    import faulthandler

    faulthandler.enable()

    # Bootstrap structured logging

    log_bootstrap(_instance_id)

    # Set instance ID for structured logging

    set_instance_id(_instance_id)

    # Initialize per-window trace file for audit trail

    trace_path = initialize_window_trace(_instance_id)

    # Rotate logs at startup (daily rotation)

    rotated = rotate_logs()

    # Log startup with structured logging

    log_info("mcp.init", "MCP server starting", 

             instance_id=_instance_id, pid=_pid, log_rotated=rotated,

             trace_file=str(trace_path))

    # Also log to stderr for VS Code capture

    print(

        f"MCP server start: instanceId={_instance_id} pid={_pid}",

        file=sys.stderr

    )

    # Establish the owner-window lifecycle lease before serving MCP requests.

    lifecycle_connected = _announce_subprocess_start()

    if os.environ.get("CK3LENS_IPC_PORT") and not lifecycle_connected:

        log_error("mcp.init", "Required IPC lifecycle lease unavailable; refusing unowned startup",

                  instance_id=_instance_id, pid=_pid)

        print(

            f"MCP server: required lifecycle lease unavailable "

            f"instanceId={_instance_id} pid={_pid}",

            file=sys.stderr,

        )

        sys.exit(1)

    # Every fresh MCP generation starts uninitialized.
    try:
        from ck3lens.agent_mode import clear_agent_mode as _clear_agent_mode
        _clear_agent_mode()
        log_info("mcp.init", "Mode file blanked on subprocess startup",
                 instance_id=_instance_id, pid=_pid)
    except Exception as _mode_clear_err:
        log_info("mcp.init", "Failed to blank mode file on startup (non-fatal)",
                 instance_id=_instance_id, pid=_pid, error=str(_mode_clear_err))

    # Register exit handler for logging

    atexit.register(_log_exit)

    # Setup signal handlers

    _setup_signal_handlers()

    # Start heartbeat daemon thread for crash forensics.

    # If the process is killed between heartbeats, the gap in timestamps

    # in the structured log reveals exactly when the kill occurred.

    import threading as _hb_threading

    import time as _hb_time

    def _heartbeat_loop():

        while True:

            _hb_time.sleep(60)

            try:

                log_info("mcp.heartbeat", "alive",

                         instance_id=_instance_id, pid=_pid)

            except Exception:

                pass  # Don't let heartbeat logging crash the server

    _hb_thread = _hb_threading.Thread(

        target=_heartbeat_loop, daemon=True, name="mcp-heartbeat"

    )

    _hb_thread.start()

    try:

        if _transport == "streamable-http":
            from ck3lens.http_transport import run_http_server

            def _log_http_response_warning(event: str, data: dict[str, object]) -> None:
                warn("mcp.request", event, instance_id=_instance_id, pid=_pid, **data)

            guard_completed_responses(mcp._mcp_server, _log_http_response_warning)
            anyio.run(run_http_server, mcp, os.environ["CK3LENS_HTTP_TOKEN"], _instance_id)
        else:
            anyio.run(_run_mcp_stdio)

    except EOFError:

        # Explicit EOF handling - CRITICAL for zombie prevention

        log_info("mcp.dispose", "EOF on stdin, shutting down", 

                 reason="EOF on stdin", instance_id=_instance_id, pid=_pid)

        print(

            f"MCP server: stdin EOF detected, shutting down "

            f"instanceId={_instance_id} pid={_pid}",

            file=sys.stderr

        )

    except BaseException as e:

        # Use BaseException to catch ExceptionGroup/BaseExceptionGroup (Python 3.11+)

        # These occur when asyncio TaskGroups have unhandled sub-task exceptions

        error_details: dict[str, Any] = {"error": str(e), "error_type": type(e).__name__}

        # Unpack ExceptionGroup sub-exceptions for better diagnostics

        if hasattr(e, 'exceptions'):

            sub_exceptions = []

            for i, sub_exc in enumerate(e.exceptions): # type: ignore[attr-defined]

                import traceback

                sub_tb = ''.join(traceback.format_exception(type(sub_exc), sub_exc, sub_exc.__traceback__))

                sub_exceptions.append({

                    "index": i,

                    "type": type(sub_exc).__name__,

                    "message": str(sub_exc),

                    "traceback": sub_tb

                })

            error_details["sub_exceptions"] = sub_exceptions

            error_details["sub_count"] = len(e.exceptions) # type: ignore[attr-defined]

        if _is_stdio_disconnect(e):

            log_info("mcp.dispose", "MCP stdio transport disconnected",

                     instance_id=_instance_id, pid=_pid, **error_details)

            print(

                f"MCP server: stdio transport disconnected "

                f"instanceId={_instance_id} pid={_pid}",

                file=sys.stderr

            )

        else:

            log_error("mcp.dispose", f"Error during run: {e}",

                      instance_id=_instance_id, pid=_pid, **error_details)

            print(

                f"MCP server: error during run - {type(e).__name__}: {e} "

                f"instanceId={_instance_id} pid={_pid}",

                file=sys.stderr

            )

            # Print full traceback to stderr for unexpected failures.

            import traceback

            traceback.print_exc(file=sys.stderr)

    finally:

        # Ensure we exit

        log_info("mcp.dispose", "Main loop ended, exiting", 

                 instance_id=_instance_id, pid=_pid)

        print(

            f"MCP server: main loop ended, exiting "

            f"instanceId={_instance_id} pid={_pid}",

            file=sys.stderr

        )
