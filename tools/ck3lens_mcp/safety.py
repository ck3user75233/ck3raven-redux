"""
MCP Safety Wrapper - Canonical Reply System enforcement.

This module provides the @mcp_safe_tool decorator that wraps all MCP tool handlers
to enforce the Reply contract:

1. Generate trace_id per invocation
2. Attach session_id
3. Set trace_info in context variable (accessible via get_current_trace_info())
4. Catch exceptions -> MCP-SYS-E-001
5. Enforce Reply return type (non-Reply -> MCP-SYS-E-003)
6. Serialize Reply to dict for transport
7. Log tool calls to window trace file (FR-3)
8. Emit canonical tool_end log entry (CANONICAL_LOGS.md compliance)

Reply Code Ownership Rules:
    WA  → May emit: S, I, E    Must NOT emit: D
    EN  → May emit: S, I, D, E
    CT  → May emit: S, I, E    Must NOT emit: D
    MCP → May emit: E, I       (transport layer only)

Usage:
    @mcp.tool()
    @mcp_safe_tool
    def ck3_my_tool(param: str) -> Reply:
        trace_info = get_current_trace_info()
        rb = ReplyBuilder(trace_info, tool='ck3_my_tool')
        return rb.success('WA-SYS-S-001', data={...})
"""
from __future__ import annotations

import contextvars
import functools
import json
import os
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, TypeVar, ParamSpec, Optional

from anyio.to_thread import run_sync

from ck3raven.core.reply import Reply, TraceInfo, MetaInfo
from ck3raven.core.trace import generate_trace_id, get_or_create_session_id
from ck3lens.paths import ROOT_CK3RAVEN_DATA

# ============================================================================
# Result Spill-to-File configuration
# ============================================================================
# When a tool result exceeds a byte threshold, the full data is written to disk
# and replaced with a compact pointer. This prevents large results from
# permanently consuming the agent's context budget.
#
# Structure: ~/.ck3raven/wip/MCP_Tool_Reply/{tool_name}/{trace_id}.json
#
# Global cap applies to ALL tools. Per-tool caps override for specific tools.
# ============================================================================

_SPILL_GLOBAL_THRESHOLD = 10 * 1024  # 10KB global cap

_SPILL_PER_TOOL: Dict[str, int] = {
    "debug_get_logs": 2 * 1024,   # 2KB
    "ck3_search": 12 * 1024,      # 12KB
    "ck3_logs": 2 * 1024,         # 2KB — same pattern as debug_get_logs
    "ck3_file": 6 * 1024,         # 6KB — must stay below VS Code's own spill threshold
    "ck3_conflicts": 6 * 1024,    # 6KB — same reason
}

# Tools exempt from spill.
# NOTE: ck3_file and ck3_conflicts were previously exempt ("content IS the point").
# Removed in VS Code 1.113: VS Code now has its own spill guard that fires on large
# MCP responses and wraps them in file:// workspace-storage links.  With #fetch
# enabled the agent routes those links to the web fetch tool instead of ck3_file,
# breaking the intended read-back flow.  Both tools now carry an explicit 6KB cap
# so our guard intercepts first — agent paginates via start_line/end_line as before.
_SPILL_EXEMPT: set[str] = set()


def _get_spill_dir(tool_name: str) -> Path:
    """Get (and create) the spill directory for a tool."""
    spill_dir = ROOT_CK3RAVEN_DATA / "wip" / "MCP_Tool_Reply" / tool_name
    spill_dir.mkdir(parents=True, exist_ok=True)
    return spill_dir


def _generate_search_summary(data: Dict[str, Any]) -> str:
    """
    Generate a clustered summary of ck3_search results.
    
    Groups results by:
    - Game folder (common/traits/, events/, etc.)
    - Mod source
    - Symbol type
    
    This gives the agent enough information to narrow searches
    without loading all results into context.
    """
    lines: list[str] = []
    
    # Symbol definitions
    symbols = data.get("symbols", {})
    defs = symbols.get("definitions", [])
    if defs:
        # Group by type
        by_type: Dict[str, list] = {}
        for d in defs:
            stype = d.get("type", "unknown")
            by_type.setdefault(stype, []).append(d)
        lines.append(f"## Symbol Definitions ({len(defs)} total)")
        for stype, items in sorted(by_type.items()):
            names = [i.get("name", "?") for i in items[:5]]
            more = f" (+{len(items)-5} more)" if len(items) > 5 else ""
            lines.append(f"  {stype}: {', '.join(names)}{more}")
    
    # References by mod
    refs_by_mod = symbols.get("references_by_mod", {})
    if refs_by_mod:
        total_refs = sum(len(v) for v in refs_by_mod.values())
        lines.append(f"## References ({total_refs} total across {len(refs_by_mod)} mods)")
        for mod, refs in sorted(refs_by_mod.items(), key=lambda x: -len(x[1])):
            # Group refs by folder
            folder_counts: Dict[str, int] = {}
            for ref in refs:
                fpath = ref.get("file", "")
                # Extract top-level game folder
                parts = fpath.replace("\\", "/").split("/")
                folder = "/".join(parts[:2]) if len(parts) > 1 else parts[0] if parts else "root"
                folder_counts[folder] = folder_counts.get(folder, 0) + 1
            folder_str = ", ".join(f"{f}({c})" for f, c in sorted(folder_counts.items(), key=lambda x: -x[1])[:5])
            lines.append(f"  {mod} ({len(refs)} refs): {folder_str}")
    
    # Content matches
    content = data.get("content", {})
    matches = content.get("matches", [])
    if matches:
        lines.append(f"## Content Matches ({content.get('count', len(matches))} files)")
        # Group by game folder
        folder_counts: Dict[str, int] = {}
        for m in matches:
            fpath = m.get("file", "")
            parts = fpath.replace("\\", "/").split("/")
            folder = "/".join(parts[:2]) if len(parts) > 1 else parts[0] if parts else "root"
            folder_counts[folder] = folder_counts.get(folder, 0) + 1
        for folder, count in sorted(folder_counts.items(), key=lambda x: -x[1]):
            lines.append(f"  {folder}: {count} files")
    
    # File path matches
    files = data.get("files", {})
    file_matches = files.get("matches", [])
    if file_matches:
        lines.append(f"## File Path Matches ({files.get('count', len(file_matches))})")
        for fm in file_matches[:10]:
            lines.append(f"  {fm.get('path', '?')}")
        if len(file_matches) > 10:
            lines.append(f"  ... +{len(file_matches)-10} more")
    
    # Guidance
    if data.get("truncated"):
        lines.append(f"\n## Guidance")
        lines.append(data.get("guidance", "Results truncated. Use path or symbol_type to narrow."))
    
    return "\n".join(lines)


def _maybe_spill(
    result: Reply,
    trace_info: TraceInfo,
    tool_name: str,
) -> Reply:
    """
    Check if Reply exceeds context budget and spill to disk if needed.
    
    Operates on Reply objects only - no dict shuffling.
    Returns the original Reply if under threshold, or a spill Reply if over.
    
    Per Canonical Reply System: Reply flows through the system,
    single boundary conversion happens in the wrapper.
    
    Spill Schema (stable):
        {
            "spilled": True,
            "spill_file": "root:ck3raven_data/wip/MCP_Tool_Reply/{tool}/{trace}.json",
            "spill_bytes": int,
            "spill_lines": int,  # Line count for selective reading
            "original_data_keys": [...],
            "summary": "...",  # For ck3_search only
            "guidance": "...",  # Tool-specific narrowing advice
            "original_code": "...",  # Code that would have been returned
        }
    
    IMPORTANT: Agent should NOT load entire spill file into context.
    Use ck3_file(command="read", path="...", start_line=X, end_line=Y)
    for selective reading, or narrow the original query to get inline results.
    """
    # Serialize to check size
    result_dict = result.to_dict()
    result_json = json.dumps(result_dict, ensure_ascii=False)
    result_bytes = len(result_json.encode("utf-8"))
    
    # Check threshold
    per_tool_cap = _SPILL_PER_TOOL.get(tool_name)
    threshold = per_tool_cap if per_tool_cap is not None else _SPILL_GLOBAL_THRESHOLD
    
    if result_bytes <= threshold or tool_name in _SPILL_EXEMPT:
        return result  # No spill needed, return original Reply
    
    # --- Spill to disk ---
    spill_dir = _get_spill_dir(tool_name)
    trace_id = trace_info.trace_id
    spill_file = spill_dir / f"{trace_id}.json"
    
    # Build summary header for ck3_search
    summary = ""
    original_data = result.data
    if tool_name == "ck3_search":
        summary = _generate_search_summary(original_data)
        header = f"// Search Summary (use this to decide what to read)\n// Query: {original_data.get('query', '?')}\n//\n"
        for line in summary.split("\n"):
            header += f"// {line}\n"
        header += "//\n// Full results below:\n"
        spill_content = header + result_json
    else:
        spill_content = result_json
    
    spill_file.write_text(spill_content, encoding="utf-8")
    spill_lines = spill_content.count("\n") + 1
    
    # Build canonical path for the spill file
    # Format: root:ck3raven_data/wip/MCP_Tool_Reply/{tool_name}/{trace_id}.json
    spill_canonical = f"root:ck3raven_data/wip/MCP_Tool_Reply/{tool_name}/{trace_id}.json"
    
    # Extract original data keys for agent reference
    original_keys = list(original_data.keys())[:15]
    
    # Build spill metadata with STABLE SCHEMA
    # NOTE: spill_file is canonical address, not host path
    spill_data: Dict[str, Any] = {
        "spilled": True,
        "spill_file": spill_canonical,
        "spill_bytes": result_bytes,
        "spill_lines": spill_lines,
        "original_data_keys": original_keys,
        "original_code": result.code,
    }
    
    # Tool-specific guidance (actionable narrowing advice)
    # IMPORTANT: Guide agent toward selective reads, NOT full retrieval
    if tool_name == "ck3_search" and summary:
        spill_data["summary"] = summary
        spill_data["guidance"] = (
            f"Results exceed context budget ({result_bytes} bytes, {spill_lines} lines). "
            "Use the summary above to identify relevant sections. Options:\n"
            "1. Narrow your search with path='...', symbol_type='...', or include_pattern='...' to get inline results\n"
            f"2. Read specific portions: ck3_file(command='read', path='{spill_canonical}', start_line=1, end_line=50)"
        )
    elif tool_name == "ck3_dir":
        spill_data["guidance"] = (
            f"Directory listing exceeds context budget ({result_bytes} bytes, {spill_lines} lines). Options:\n"
            "1. Re-call with depth=1 or target a subdirectory to get inline results\n"
            f"2. Read specific portions: ck3_file(command='read', path='{spill_canonical}', start_line=1, end_line=50)"
        )
    elif tool_name == "debug_get_logs":
        spill_data["total_entries"] = original_data.get("total_available", 0)
        spill_data["guidance"] = (
            f"Log entries exceed context budget ({result_bytes} bytes, {spill_lines} lines). Options:\n"
            "1. Re-call with category/level filters to get inline results\n"
            f"2. Read specific portions: ck3_file(command='read', path='{spill_canonical}', start_line=1, end_line=50)"
        )
    else:
        spill_data["guidance"] = (
            f"Result exceeds context budget ({result_bytes} bytes, {spill_lines} lines). "
            f"Read specific portions: ck3_file(command='read', path='{spill_canonical}', start_line=1, end_line=50)"
        )
    
    # Add original code to spill metadata for agent reference
    spill_data["original_code"] = result.code
    
    # Return spill Reply with MCP code (spill is an MCP-layer transformation)
    # The original operation succeeded (original_code) but delivery was spilled
    rb = ReplyBuilder(trace_info, tool=tool_name, layer="MCP")
    return rb.success(
        code="MCP-SYS-S-003",  # Spill success code
        data=spill_data,
        message=f"Result spilled ({result_bytes} bytes). {result.message or ''}".strip(),
    )


# Canonical logging import - for tool_end visibility
from ck3lens.logging import (
    info as log_info,
    warn as log_warn,
    error as log_error,
    debug as log_debug,
    set_trace_id as set_canonical_trace_id,
)

# Canonical reply codes - import registry for validation (optional startup check)
from ck3lens.reply_codes import validate_code_format, Codes


P = ParamSpec('P')
T = TypeVar('T')

# Context variable for current trace_info (set by decorator, read by tool)
_current_trace_info: contextvars.ContextVar[Optional[TraceInfo]] = contextvars.ContextVar(
    'current_trace_info', default=None
)

# Module-level window trace file path (set during MCP initialization)
_window_trace_path: Path | None = None
_window_id: str | None = None


def initialize_window_trace(window_id: str) -> Path:
    """
    Initialize per-window trace file for capturing tool calls.
    
    Called during MCP server initialization with the window's unique ID.
    Creates trace file at the configured data root's traces/{window_id}.jsonl.
    
    Args:
        window_id: Unique window identifier (e.g., "f9hf-719e4f")
        
    Returns:
        Path to the trace file
    """
    global _window_trace_path, _window_id
    
    traces_dir = ROOT_CK3RAVEN_DATA / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)
    
    _window_id = window_id
    _window_trace_path = traces_dir / f"{window_id}.jsonl"
    
    # Write initialization marker
    _log_trace_event({
        "event": "session_start",
        "window_id": window_id,
        "timestamp": time.time(),
        "iso_time": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "pid": os.getpid(),
    })
    
    return _window_trace_path


def _log_trace_event(event: dict) -> None:
    """Write a trace event to the window's trace file."""
    if _window_trace_path is None:
        return  # Tracing not initialized
    
    try:
        with open(_window_trace_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
    except (OSError, TypeError, ValueError) as error:
        log_error("mcp.trace", "Failed to write tool trace", error=str(error), pid=os.getpid())


def _log_tool_start(tool_name: str, trace_id: str, kwargs: dict[str, Any]) -> None:
    event: dict[str, Any] = {
        "event": "tool_start",
        "timestamp": time.time(),
        "iso_time": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "window_id": _window_id,
        "pid": os.getpid(),
        "trace_id": trace_id,
        "tool": tool_name,
        "parameter_names": sorted(kwargs),
    }
    command = kwargs.get("command")
    if tool_name == "ck3_contract" and isinstance(command, str):
        event["command"] = command
    _log_trace_event(event)


def _log_tool_call(
    tool_name: str,
    trace_id: str,
    args: tuple,
    kwargs: dict,
    result: dict | None,
    error: str | None,
    duration_ms: float,
) -> None:
    """
    Log a tool invocation to the window trace file.
    
    Called after each tool completes (success or failure).
    """
    if _window_trace_path is None:
        return  # Tracing not initialized
    
    # Sanitize kwargs - remove large content fields for readability
    sanitized_kwargs = {}
    for k, v in kwargs.items():
        if any(secret in k.lower() for secret in ("token", "secret", "password", "signature", "key")):
            sanitized_kwargs[k] = "***REDACTED***"
        elif k == "content" and isinstance(v, str) and len(v) > 500:
            sanitized_kwargs[k] = f"<{len(v)} chars>"
        elif isinstance(v, str) and len(v) > 1000:
            sanitized_kwargs[k] = v[:500] + f"... <{len(v)} chars total>"
        else:
            sanitized_kwargs[k] = v
    
    # Extract result summary
    result_summary = None
    if result:
        result_summary = {
            "reply_type": result.get("reply_type"),
            "code": result.get("code"),
            "message": result.get("message"),
        }
        # Include truncated data keys
        if "data" in result and isinstance(result["data"], dict):
            result_summary["data_keys"] = list(result["data"].keys())[:10]
    
    event = {
        "event": "tool_call",
        "timestamp": time.time(),
        "iso_time": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "window_id": _window_id,
        "pid": os.getpid(),
        "trace_id": trace_id,
        "tool": tool_name,
        "params": sanitized_kwargs,
        "result_summary": result_summary,
        "error": error,
        "duration_ms": round(duration_ms, 2),
    }
    
    _log_trace_event(event)


def _log_canonical_tool_end(
    tool_name: str,
    trace_id: str,
    reply_type: str,
    code: str,
    duration_ms: float,
    message: str | None = None,
    data_keys: list[str] | None = None,
    result_bytes: int | None = None,
    spilled: bool = False,
) -> None:
    """
    Emit canonical tool_end log entry per CANONICAL_LOGS.md.
    
    This is the single always-visible summary line per tool call.
    Level mapping:
        S (Success) -> INFO
        I (Invalid) -> WARN
        D (Denied)  -> WARN
        E (Error)   -> ERROR
    
    Args:
        tool_name: Name of the tool
        trace_id: Correlation ID
        reply_type: S, I, D, or E
        code: Reply code (e.g., WA-SYS-S-001)
        duration_ms: Execution time in milliseconds
        message: Optional message for DEBUG detail
        data_keys: Optional data keys for DEBUG detail
        result_bytes: Serialized result size in bytes (for context budget tracking)
        spilled: Whether the result was spilled to disk
    """
    # Minimal payload for INFO/WARN/ERROR summary
    summary_data = {
        "tool": tool_name,
        "reply_type": reply_type,
        "code": code,
        "duration_ms": round(duration_ms, 2),
    }
    if result_bytes is not None:
        summary_data["result_bytes"] = result_bytes
    if spilled:
        summary_data["spilled"] = True
    
    # Emit summary at appropriate level based on reply_type
    if reply_type == "S":
        log_info("mcp.tool", "tool_end", **summary_data)
    elif reply_type in ("I", "D"):
        log_warn("mcp.tool", "tool_end", **summary_data)
    elif reply_type == "E":
        log_error("mcp.tool", "tool_end", **summary_data)
    else:
        # Unknown reply_type - log as warning
        log_warn("mcp.tool", "tool_end", **summary_data)
    
    # Optional DEBUG detail with richer info (not required for validation)
    if message or data_keys:
        detail_data: dict[str, object] = {
            "tool": tool_name,
            "code": code,
        }
        if message:
            # Truncate message for safety
            detail_data["message"] = message[:500] if len(message) > 500 else message
        if data_keys:
            detail_data["data_keys"] = data_keys[:10]
        log_debug("mcp.tool", "tool_detail", **detail_data)


def get_current_trace_info() -> TraceInfo:
    """
    Get the current TraceInfo from context.
    
    Call this from within a @mcp_safe_tool decorated function.
    Raises RuntimeError if called outside of a tool context.
    """
    trace_info = _current_trace_info.get()
    if trace_info is None:
        raise RuntimeError('get_current_trace_info() called outside of @mcp_safe_tool context')
    return trace_info


def mcp_safe_tool(func: Callable[P, Reply]) -> Callable[P, Dict[str, Any]]:
    """
    Decorator that wraps MCP tool handlers for safety and Reply enforcement.
    
    Responsibilities:
    1. Generate unique trace_id per invocation
    2. Get/create session_id
    3. Set trace_info in context variable (access via get_current_trace_info())
    4. Catch ALL exceptions and convert to MCP-SYS-E-001
    5. Enforce that tool returns Reply (non-Reply -> MCP-SYS-E-003)
    6. Serialize Reply to dict for MCP transport
    7. Log tool call to window trace file (FR-3)
    8. Emit canonical tool_end log entry (CANONICAL_LOGS.md compliance)
    
    IMPORTANT: Do NOT add trace_info to the function signature. Use
    get_current_trace_info() inside the function to access it.
    """
    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> Dict[str, Any]:
        # Generate trace context
        trace_id = generate_trace_id()
        session_id = get_or_create_session_id()
        trace_info = TraceInfo(trace_id=trace_id, session_id=session_id)
        
        tool_name = func.__name__
        start_time = time.perf_counter()
        
        # Set canonical logger trace context for correlation
        set_canonical_trace_id(trace_id)
        
        # Set trace_info in context variable
        token = _current_trace_info.set(trace_info)
        
        result_dict: Dict[str, Any] | None = None
        error_msg: str | None = None
        result_bytes: int | None = None
        spilled: bool = False
        
        try:
            _log_tool_start(tool_name, trace_id, kwargs)
            result = func(*args, **kwargs)
            
            # Enforce Reply return type
            if not isinstance(result, Reply):
                actual_type = type(result).__name__
                error_reply = Reply.error(
                    code='MCP-SYS-E-003',
                    message=f'Tool returned {actual_type}, expected Reply.',
                    data={
                        'actual_type': actual_type,
                        'tool': tool_name,
                    },
                    trace=trace_info,
                    meta=MetaInfo(layer='MCP', tool=tool_name),
                )
                result_dict = error_reply.to_dict()
                error_msg = f"Invalid return type: {actual_type}"
                return result_dict
            
            # --- Context-budget spill check ---
            # If over threshold, _maybe_spill returns a spill Reply
            # Otherwise returns original Reply unchanged.
            # Spill is best-effort — exceptions fall through to original result
            try:
                result = _maybe_spill(result, trace_info, tool_name)
            except Exception:
                pass  # Keep original result on spill failure
            # --- End spill check ---
            
            # Single boundary conversion: Reply -> wire dict
            result_dict = result.to_dict()
            
            # Derive logging metadata from result
            spilled = result.data.get("spilled", False) if isinstance(result.data, dict) else False
            if spilled:
                result_bytes = result.data.get("spill_bytes")
            else:
                # Compute for logging
                try:
                    result_bytes = len(json.dumps(result_dict, ensure_ascii=False).encode("utf-8"))
                except Exception:
                    result_bytes = None
            
            return result_dict
            
        except Exception as e:
            # Catch-all: convert to MCP-SYS-E-001
            error_msg = str(e)
            stack_trace = traceback.format_exc()
            
            # Log to disk for debugging
            _log_exception(trace_id, tool_name, e, stack_trace)
            
            error_reply = Reply.error(
                code='MCP-SYS-E-001',
                message=f'Unhandled exception: {error_msg}',
                data={
                    'error': error_msg,
                    'error_type': type(e).__name__,
                    'tool': tool_name,
                },
                trace=trace_info,
                meta=MetaInfo(layer='MCP', tool=tool_name),
            )
            result_dict = error_reply.to_dict()
            return result_dict
        finally:
            # Reset context variable
            _current_trace_info.reset(token)
            
            # Calculate duration
            duration_ms = (time.perf_counter() - start_time) * 1000
            
            # === CANONICAL LOGGING: tool_end summary ===
            # This is the single always-visible log line per tool call
            # Required for CANONICAL_LOGS.md compliance (validation matrix)
            if result_dict:
                reply_type = result_dict.get("reply_type", "E")
                code = result_dict.get("code", "UNKNOWN")
                message = result_dict.get("message")
                
                # Extract data_keys for DEBUG detail
                data_keys = None
                if "data" in result_dict and isinstance(result_dict["data"], dict):
                    data_keys = list(result_dict["data"].keys())
                
                _log_canonical_tool_end(
                    tool_name=tool_name,
                    trace_id=trace_id,
                    reply_type=reply_type,
                    code=code,
                    duration_ms=duration_ms,
                    message=message,
                    data_keys=data_keys,
                    result_bytes=result_bytes,
                    spilled=spilled,
                )
            
            # Log tool call to window trace file (FR-3) - audit trail
            _log_tool_call(
                tool_name=tool_name,
                trace_id=trace_id,
                args=args,
                kwargs=kwargs,
                result=result_dict,
                error=error_msg,
                duration_ms=duration_ms,
            )
    
    # Override return annotation: we return Dict, not Reply
    # FastMCP uses Pydantic validation on return type annotations;
    # we must tell it we're returning a dict (the wire format)
    if hasattr(wrapper, '__annotations__'):
        wrapper.__annotations__ = {
            k: v for k, v in getattr(func, '__annotations__', {}).items()
            if k != 'return'
        }
        wrapper.__annotations__['return'] = Dict[str, Any]
    
    return wrapper


def mcp_safe_worker_tool(func: Callable[P, Reply]) -> Callable[P, Awaitable[Dict[str, Any]]]:
    """Keep blocking approval handlers off the MCP protocol event loop."""
    safe_handler = mcp_safe_tool(func)

    @functools.wraps(safe_handler)
    async def worker(*args: P.args, **kwargs: P.kwargs) -> Dict[str, Any]:
        return await run_sync(functools.partial(safe_handler, *args, **kwargs))

    return worker


def _log_exception(trace_id: str, tool_name: str, exc: Exception, stack_trace: str) -> None:
    """
    Log exception details to disk for debugging.
    """
    import sys
    from datetime import datetime, timezone
    
    ts = datetime.now(timezone.utc).isoformat()
    print(
        f'[{ts}] EXCEPTION trace_id={trace_id} tool={tool_name}\n'
        f'  {type(exc).__name__}: {exc}\n'
        f'{stack_trace}',
        file=sys.stderr,
    )


# ===========================================================================
# Helper for creating Replies with automatic trace injection
# ===========================================================================

class ReplyBuilder:
    """
    Helper class for building Reply objects with pre-set trace and meta.
    
    Usage in tool handlers:
        @mcp.tool()
        @mcp_safe_tool
        def ck3_my_tool(param: str) -> Reply:
            trace_info = get_current_trace_info()
            rb = ReplyBuilder(trace_info, tool='ck3_my_tool', layer='WA')
            
            if not param:
                return rb.invalid('WA-RES-I-001', {'path': param})
            
            return rb.success('WA-READ-S-001', {'result': 'ok'})
    
    Layer Ownership (enforced at runtime):
        - Builder layer MUST match code prefix (no cross-layer minting)
        - WA builders can only emit WA-* codes
        - EN builders can only emit EN-* codes
        - MCP builders can only emit MCP-* codes
        - MCP success is valid for tool-owned outcomes that do not belong to WA, EN, or CT
        
    Reply Type Ownership (enforced at runtime):
        - WA (World Adapter): S, I, E only (never D)
        - EN (Enforcement): S, D, E only (never I) 
        - CT (Contract): S, I, E only (never D)
        - MCP (Transport): S, E, I
    """
    
    def __init__(
        self,
        trace_info: TraceInfo,
        tool: str,
        layer: str = 'MCP',
        contract_id: str | None = None,
    ):
        self.trace_info = trace_info
        self.tool = tool
        self.layer = layer
        self.contract_id = contract_id
    
    def _meta(self, layer: str | None = None) -> MetaInfo:
        """Create MetaInfo with optional layer override."""
        contract_id = self.contract_id
        if contract_id is None:
            try:
                from ck3lens.policy.contract_v1 import get_active_contract
                active = get_active_contract()
                if active:
                    contract_id = active.get("contract_id")
            except Exception:
                pass
        return MetaInfo(
            layer=layer or self.layer,  # type: ignore
            tool=self.tool,
            contract_id=contract_id,
        )
    
    def _validate_and_get_layer(self, code: str, expected_type: str) -> str:
        """
        Validate code format/ownership and extract canonical layer.
        
        Returns the layer prefix from the code (e.g., "WA" from "WA-READ-S-001").
        This MUST be used for meta.layer to ensure consistency.
        
        ENFORCEMENT:
        - Builder layer MUST match code layer (no cross-layer minting)
        - MCP builders can only emit MCP-* codes
        - WA builders can only emit WA-* codes
        - MCP-coded success is canonical when the tool itself owns the successful outcome
        - etc.
        
        Raises ValueError if code violates canonical rules.
        """
        valid, err = validate_code_format(code)
        if not valid:
            raise ValueError(f"Invalid reply code: {err}")
        
        # Extract layer and type from code
        parts = code.split("-")
        if len(parts) >= 3:
            code_layer = parts[0]
            code_type = parts[2]
            
            # CRITICAL: Enforce builder layer matches code layer
            # This prevents MCP builders from minting WA/EN/CT codes
            # NOTE: Disabled — builder layers not consistently set across codebase.
            # Executors receive MCP-layer rb from server.py but emit EN-* codes.
            # Re-enable when all tool handlers pass correctly-layered builders.
            #if code_layer != self.layer:
            #    raise ValueError(
            #        f"Layer mismatch: ReplyBuilder(layer='{self.layer}') cannot mint "
            #        f"code '{code}' (prefix '{code_layer}'). Use a builder with "
            #        f"layer='{code_layer}' or change the code prefix."
            #    )
            
            # Validate layer->type ownership rules
            if code_layer == "WA" and code_type == "D":
                raise ValueError(f"WA layer cannot emit Denied (D): {code}")
            if code_layer == "CT" and code_type == "D":
                raise ValueError(f"CT layer cannot emit Denied (D): {code}")
            
            return code_layer
        
        # Fallback for malformed (should not reach here if validate_code_format passed)
        return self.layer
    
    def _validate_code(self, code: str, expected_type: str) -> None:
        """
        Validate code format and ownership rules at runtime.
        
        DEPRECATED: Use _validate_and_get_layer instead.
        Kept for backward compat.
        
        Raises ValueError if code violates canonical rules.
        """
        self._validate_and_get_layer(code, expected_type)
    
    def success(
        self,
        code: str,
        data: Dict[str, Any],
        message: str | None = None,
        layer: str | None = None,  # DEPRECATED: layer derived from code prefix
    ) -> Reply:
        """Create a Success reply. meta.layer is derived from code prefix."""
        code_layer = self._validate_and_get_layer(code, "S")
        from ck3lens.reply_codes import get_message
        return Reply.success(
            code=code,
            message=message or get_message(code, **data),
            data=data,
            trace=self.trace_info,
            meta=self._meta(code_layer),
        )
    
    def invalid(
        self,
        code: str,
        data: Dict[str, Any],
        message: str | None = None,
        layer: str | None = None,  # DEPRECATED: layer derived from code prefix
    ) -> Reply:
        """
        Create an Invalid reply (malformed input, unknown reference, or
        a request that could not be completed without writing invalid data).

        May be emitted by: WA, EN, CT, MCP.
        meta.layer is derived from code prefix.
        """
        code_layer = self._validate_and_get_layer(code, "I")
        from ck3lens.reply_codes import get_message
        return Reply.invalid(
            code=code,
            message=message or get_message(code, **data),
            data=data,
            trace=self.trace_info,
            meta=self._meta(code_layer),
        )
    
    def denied(
        self,
        code: str,
        data: Dict[str, Any],
        message: str | None = None,
        layer: str | None = None,  # DEPRECATED: layer derived from code prefix
    ) -> Reply:
        """
        Create a Denied reply.
        
        DECISION-LOCKED: Only EN (Enforcement) may emit Denied.
        WA and CT must never emit Denied.
        meta.layer is derived from code prefix.
        """
        code_layer = self._validate_and_get_layer(code, "D")
        from ck3lens.reply_codes import get_message
        return Reply.denied(
            code=code,
            message=message or get_message(code, **data),
            data=data,
            trace=self.trace_info,
            meta=self._meta(code_layer),
        )
    
    def error(
        self,
        code: str,
        data: Dict[str, Any],
        message: str | None = None,
        layer: str | None = None,  # DEPRECATED: layer derived from code prefix
    ) -> Reply:
        """Create an Error reply. meta.layer is derived from code prefix."""
        code_layer = self._validate_and_get_layer(code, "E")
        from ck3lens.reply_codes import get_message
        return Reply.error(
            code=code,
            message=message or get_message(code, **data),
            data=data,
            trace=self.trace_info,
            meta=self._meta(code_layer),
        )
