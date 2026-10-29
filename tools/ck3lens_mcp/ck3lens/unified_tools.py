"""
Unified MCP Tool Implementations

Consolidates multiple granular tools into parameterized commands to reduce
tool count while maintaining full functionality.

Consolidated tools:
- ck3_logs: 11 log/error/crash tools -> 1 unified tool
- ck3_conflicts: 7 conflict tools -> 1 unified tool
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from .db.golden_join import GOLDEN_JOIN
# Canonical path constants - use these instead of computing paths from __file__
from .paths import ROOT_REPO, ROOT_USER_DOCS

# Canonical Reply System (Phase C migration)
from ck3raven.core.reply import Reply
from typing import TYPE_CHECKING, Literal, Any

if TYPE_CHECKING:
    from ck3lens_mcp.safety import ReplyBuilder


# ============================================================================
# Helpers
# ============================================================================

def _compute_mod_prefix(mod_name: str) -> str:
    """
    Generate a short prefix from mod name using first letter of each word.
    
    Used for creating override patch files with zzz_ prefix.
    
    Examples:
        "Mini Super Compatch" → "msc"
        "Adoption Options" → "ao"
        "MSC Religion Expanded" → "mre"
        "Unofficial Patch" → "up"
        "More Game Rules" → "mgr"
        "EPE" → "epe" (single word/acronym preserved)
    
    Returns:
        Lowercase prefix string (e.g., "msc", "ao", "up")
    """
    words = mod_name.split()
    if not words:
        return "mod"
    prefix = "".join(word[0].lower() for word in words if word)
    return prefix or "mod" 


# ============================================================================
# ck3_logs - Unified Logging Tool
# ============================================================================

LogSource = Literal["error", "game", "debug", "crash"]
LogCommand = Literal["summary", "list", "search", "detail", "categories", "cascades", "read", "raw"]


def ck3_logs_impl(
    source: LogSource = "error",
    command: LogCommand = "summary",
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
    # FR-3: Skip auto-export for analysis commands
    skip_export: bool = False,
    # Dependencies
    *, session: Any, rb: ReplyBuilder,
) -> Reply:
    """
    Unified logging tool implementation.
    
    Source + Command combinations:
    
    error + summary     -> Error log summary with counts by priority/category/mod
    error + list        -> Filtered list of errors with fix hints  
    error + search      -> Search errors by message/path
    error + cascades    -> Get cascading error patterns (root causes)
    
    game + summary      -> Game log summary with category breakdown
    game + list         -> Filtered list of game log errors
    game + search       -> Search game log by message/path
    game + categories   -> Category breakdown with descriptions
    
    debug + summary     -> System info, DLCs, mod list from debug.log
    
    crash + summary     -> Recent crash reports list
    crash + detail      -> Full crash report (requires crash_id)
    
    Any source + read   -> Raw log content (tail/head with optional search)
    
    FR-2: export_to -> Export results to WIP as markdown
    FR-3: skip_export -> Disable auto-export for analysis commands
    
    Auto-export behavior (FR-3):
        Commands 'list', 'cascades', 'search' auto-export to MCP_Tool_Reply/ck3_logs/
        and return a slim summary instead of full data. Set skip_export=True to disable.
    """
    
    # Commands that produce large results and should auto-export by default
    EXPORTABLE_COMMANDS = {"list", "cascades", "search"}
    
    # Route based on source and command
    if command == "raw":
        result = _read_log_full(source, rb=rb)
    elif command == "read":
        result = _read_log_raw(source, lines, from_end, query, rb=rb)
    elif source == "error":
        result = _error_log_handler(
            command,
            priority,
            category,
            mod_filter,
            mod_filter_exact,
            exclude_cascade_children,
            query,
            limit,
            rb=rb,
        )
    elif source == "game":
        result = _game_log_handler(command, category, query, limit, rb=rb)
    elif source == "debug":
        result = _debug_log_handler(command, rb=rb)
    elif source == "crash":
        result = _crash_handler(command, crash_id, limit, rb=rb)
    else:
        return rb.invalid("MCP-SYS-I-001", data={"source": source}, message=f"Unknown source: {source}")
    
    # FR-2/FR-3: Auto-export for analysis commands (unless skip_export=True)
    should_auto_export = (
        command in EXPORTABLE_COMMANDS
        and not skip_export
        and result.reply_type == "S"
    )
    
    if should_auto_export:
        # Auto-generate export path to MCP_Tool_Reply/ck3_logs/
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        auto_export_path = f"root:ck3raven_data/wip/MCP_Tool_Reply/ck3_logs/{source}_{command}_{timestamp}.md"
        effective_export_to = export_to or auto_export_path
        
        export_reply = _export_logs_result(
            result.data,
            source,
            command,
            effective_export_to,
            session=session,
            rb=rb,
        )
        if export_reply.reply_type == "S":
            report_path = export_reply.data["export_path"]
            # Return slim summary instead of full data
            slim_summary = _build_slim_summary(result.data, command, report_path)
            return rb.success("WA-LOG-S-001", message="Analysis complete. See report_path for details.", data=slim_summary)
        else:
            # Export failed, fall back to full result with error note
            result.data["export_error"] = export_reply.data.get("error", "Export failed")
    
    # FR-2: Export results if explicitly requested (for non-exportable commands)
    elif export_to and result.reply_type == "S":
        export_reply = _export_logs_result(
            result.data,
            source,
            command,
            export_to,
            session=session,
            rb=rb,
        )
        if export_reply.reply_type == "S":
            result.data["export_path"] = export_reply.data["export_path"]
            result.data["export_success"] = True
        else:
            result.data["export_error"] = export_reply.data.get("error", "Export failed")
    
    return result


def _build_slim_summary(full_data: dict, command: str, report_path: str) -> dict[str, Any]:
    """Build a slim summary for auto-exported analysis results."""

    slim: dict[str, Any] = {
        "report_path": report_path,
        "guidance": f"Full details in markdown report: {report_path}",
    }
    
    if command == "cascades":
        cascades = full_data.get("cascades", [])
        slim["summary"] = {
            "cascade_count": full_data.get("cascade_count", len(cascades)),
            "total_errors": full_data.get("total_errors", 0),
        }
        # Top 5 cascades by impact
        slim["top_cascades"] = []
        sorted_cascades = sorted(cascades, key=lambda c: c.get("child_count", 0), reverse=True)[:5]
        for c in sorted_cascades:
            root = c.get("root_error", {})
            slim["top_cascades"].append({
                "file": root.get("file_path", "unknown"),
                "impact": c.get("child_count", 0) + 1,  # +1 for root
                "root_cause": root.get("message", "unknown")[:100],
            })
    
    elif command == "list":
        errors = full_data.get("errors", [])
        slim["summary"] = {
            "count": full_data.get("count", len(errors)),
            "total_in_log": full_data.get("total_in_log", 0),
        }
        # File distribution (top 5)
        file_counts: dict[str, int] = {}
        for e in errors:
            fp = e.get("file_path") or "unknown"
            file_counts[fp] = file_counts.get(fp, 0) + 1
        slim["top_files"] = [
            {"file": f, "count": c}
            for f, c in sorted(file_counts.items(), key=lambda x: -x[1])[:5]
        ]
    
    elif command == "search":
        errors = full_data.get("errors", [])
        slim["summary"] = {
            "query": full_data.get("query", ""),
            "count": full_data.get("count", len(errors)),
        }
        # First 3 matches preview
        slim["first_matches"] = [
            {"file": e.get("file_path", "?"), "message": e.get("message", "")[:80]}
            for e in errors[:3]
        ]
    
    return slim


def _export_logs_result(
    result: dict,
    source: str,
    command: str,
    export_to: str,
    *, session: Any, rb: ReplyBuilder,
) -> Reply:
    """
    Export logs result to markdown file in WIP.
    
    Args:
        result: The result dict from log analysis
        source: Log source (error, game, debug, crash)
        command: Command that was run
        export_to: WIP path for output (supports {timestamp} substitution)
    
    Returns:
        Reply with export_path on success, error on failure
    """
    from datetime import datetime
    
    from ck3lens.world_adapter_v2 import WorldAdapterV2

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    canonical_export_path = export_to.replace("{timestamp}", timestamp)
    wa2 = WorldAdapterV2.create(session=session)
    resolve_reply, export_ref = wa2.resolve(
        canonical_export_path,
        require_exists=False,
        rb=rb,
    )
    if resolve_reply.is_invalid or export_ref is None:
        return resolve_reply

    host_paths = wa2.host_path(export_ref)
    if not host_paths:
        return rb.error(
            "WA-RES-E-001",
            data={"export_to": canonical_export_path},
            message="Resolved export_to has no host path",
        )
    export_path = host_paths[0]
    
    # Ensure parent directories exist
    export_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Generate markdown content
    md_content = _result_to_markdown(result, source, command)
    
    try:
        export_path.write_text(md_content, encoding='utf-8')
        return rb.success("WA-LOG-S-001", data={"export_path": str(export_path)})
    except Exception as e:
        return rb.error("MCP-SYS-E-001", data={"exception": type(e).__name__}, message=f"Failed to write export: {e}")


def _result_to_markdown(result: dict, source: str, command: str) -> str:
    """Convert log result to formatted markdown."""
    from datetime import datetime
    
    lines = [
        f"# CK3 Log Analysis: {source.upper()} / {command}",
        "",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Source:** {source}",
        f"**Command:** {command}",
        "",
        "---",
        "",
    ]
    
    if command == "summary":
        lines.append("## Summary")
        lines.append("")
        for key, value in result.items():
            if isinstance(value, dict):
                lines.append(f"### {key.replace('_', ' ').title()}")
                lines.append("")
                # Format as table if all values are numeric
                if all(isinstance(v, (int, float)) for v in value.values()):
                    lines.append("| Key | Value |")
                    lines.append("|-----|-------|")
                    for k, v in sorted(value.items(), key=lambda x: -x[1] if isinstance(x[1], (int, float)) else 0):
                        lines.append(f"| {k} | {v:,} |" if isinstance(v, int) else f"| {k} | {v} |")
                else:
                    for k, v in value.items():
                        lines.append(f"- **{k}:** {v}")
                lines.append("")
            else:
                lines.append(f"- **{key.replace('_', ' ').title()}:** {value}")
        lines.append("")
    
    elif command == "cascades":
        lines.append("## Cascade Analysis")
        lines.append("")
        lines.append(f"**Total Cascades Detected:** {result.get('cascade_count', 0)}")
        lines.append(f"**Total Errors in Log:** {result.get('total_errors', 0):,}")
        lines.append("")
        if result.get("recommendation"):
            lines.append(f"> **Recommendation:** {result['recommendation']}")
            lines.append("")
        
        # Sort cascades by impact (child_count)
        cascades = result.get("cascades", [])
        sorted_cascades = sorted(cascades, key=lambda c: c.get("child_count", 0), reverse=True)
        
        # Summary table
        if sorted_cascades:
            lines.append("## Top Cascades by Impact")
            lines.append("")
            lines.append("| Rank | File | Impact (Errors) | Root Cause |")
            lines.append("|------|------|-----------------|------------|")
            for i, cascade in enumerate(sorted_cascades[:15], 1):
                root = cascade.get("root_error", {})
                file_path = root.get("file_path", "unknown")
                if file_path and len(file_path) > 40:
                    file_path = "..." + file_path[-37:]
                impact = cascade.get("child_count", 0) + 1  # +1 for root
                msg = root.get("message", "unknown")[:50]
                lines.append(f"| {i} | `{file_path}` | {impact:,} | {msg}... |")
            lines.append("")
        
        # Detailed cascades
        lines.append("## Cascade Details")
        lines.append("")
        for i, cascade in enumerate(sorted_cascades[:20], 1):
            root = cascade.get("root_error", {})
            pattern_type = cascade.get("pattern_type", "unknown")
            child_count = cascade.get("child_count", 0)
            confidence = cascade.get("confidence", 0)
            
            lines.append(f"### Cascade #{i}: {pattern_type}")
            lines.append("")
            lines.append(f"**Impact:** {child_count + 1:,} errors (1 root + {child_count:,} children)")
            lines.append(f"**Confidence:** {confidence:.0%}")
            lines.append("")
            
            # Root error details
            lines.append("**Root Error:**")
            lines.append("```")
            lines.append(f"File: {root.get('file_path', 'unknown')}")
            lines.append(f"Line: {root.get('game_line', '?')}")
            lines.append(f"Category: {root.get('category', 'unknown')}")
            lines.append(f"Priority: {root.get('priority', '?')}")
            lines.append(f"Message: {root.get('message', 'unknown')}")
            lines.append("```")
            lines.append("")
            
            # Child samples
            child_sample = cascade.get("child_sample", [])
            if child_sample:
                lines.append(f"**Child Error Samples ({len(child_sample)} shown):**")
                lines.append("")
                for child in child_sample[:3]:
                    child_msg = child.get("message", "")[:100]
                    child_file = child.get("file_path", "")
                    lines.append(f"- `{child_file}`: {child_msg}")
                lines.append("")
            
            lines.append("---")
            lines.append("")
    
    elif command == "list":
        errors = result.get("errors", [])
        total = result.get("total_in_log", len(errors))
        
        lines.append(f"## Error List")
        lines.append("")
        lines.append(f"**Showing:** {len(errors):,} errors")
        lines.append(f"**Total in Log:** {total:,}")
        lines.append("")
        
        # Group by file for better readability
        by_file: dict[str, list[dict]] = {}
        for error in errors:
            fp = error.get("file_path") or "unknown"
            if fp not in by_file:
                by_file[fp] = []
            by_file[fp].append(error)
        
        # Sort files by error count
        sorted_files = sorted(by_file.items(), key=lambda x: -len(x[1]))
        
        lines.append("## Errors by File")
        lines.append("")
        for file_path, file_errors in sorted_files[:30]:  # Top 30 files
            lines.append(f"### `{file_path}` ({len(file_errors)} errors)")
            lines.append("")
            for error in file_errors[:20]:  # Max 20 per file
                msg = error.get("message", "")
                cat = error.get("category", "unknown")
                game_line = error.get("game_line", "?")
                priority = error.get("priority", "?")
                fix_hint = error.get("fix_hint", "")
                
                lines.append(f"- **Line {game_line}** [{cat}] (P{priority}): {msg[:120]}")
                if fix_hint:
                    lines.append(f"  - *Fix:* {fix_hint}")
            
            if len(file_errors) > 20:
                lines.append(f"  - *... and {len(file_errors) - 20} more errors*")
            lines.append("")
    
    elif command == "search":
        errors = result.get("errors", [])
        query = result.get("query", "")
        
        lines.append(f"## Search Results")
        lines.append("")
        lines.append(f"**Query:** `{query}`")
        lines.append(f"**Matches:** {len(errors):,}")
        lines.append("")
        
        if errors:
            lines.append("## Matching Errors")
            lines.append("")
            lines.append("| # | File | Line | Category | Message |")
            lines.append("|---|------|------|----------|---------|")
            for i, error in enumerate(errors[:100], 1):
                fp = error.get("file_path") or "?"
                if len(fp) > 30:
                    fp = "..." + fp[-27:]
                line = error.get("game_line", "?")
                cat = error.get("category", "?")
                msg = error.get("message", "")[:60]
                lines.append(f"| {i} | `{fp}` | {line} | {cat} | {msg} |")
            
            if len(errors) > 100:
                lines.append("")
                lines.append(f"*... and {len(errors) - 100} more matches*")
        lines.append("")
    
    else:
        # Generic fallback - dump as code block
        import json
        lines.append("## Raw Result")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(result, indent=2, default=str))
        lines.append("```")
    
    return "\n".join(lines)


def _error_log_handler(
    command: str,
    priority: int | None,
    category: str | None,
    mod_filter: str | None,
    mod_filter_exact: bool,
    exclude_cascade_children: bool,
    query: str | None,
    limit: int,
    *, rb: ReplyBuilder,
) -> Reply:
    """Handle error.log commands."""
    from ck3raven.analyzers.error_parser import CK3ErrorParser, ERROR_CATEGORIES
    
    parser = CK3ErrorParser()
    
    try:
        parser.parse_log()
        parser.detect_cascading_errors()
    except FileNotFoundError:
        return rb.invalid("MCP-SYS-I-001", data={
            "source": "error",
            "hint": "Make sure CK3 has been run at least once",
        }, message="error.log not found")
    
    if command == "summary":
        return rb.success("WA-LOG-S-001", data=parser.get_summary())
    
    elif command == "list":
        errors = parser.get_errors(
            category=category,
            priority=priority,
            mod_filter=mod_filter,
            mod_filter_exact=mod_filter_exact,
            exclude_cascade_children=exclude_cascade_children,
            limit=limit,
        )
        
        results = []
        for error in errors:
            cat = next((c for c in ERROR_CATEGORIES if c.name == error.category), None)
            results.append({
                **error.to_dict(),
                "fix_hint": cat.fix_hint if cat else None,
            })
        
        return rb.success("WA-LOG-S-001", data={
            "count": len(results),
            "total_in_log": parser.stats['total_errors'],
            "errors": results,
        })
    
    elif command == "search":
        if not query:
            return rb.invalid("MCP-SYS-I-001", data={"parameter": "query", "command": "search", "source": "error"}, message="query parameter required for search command")
        
        errors = parser.search_errors(query, limit=limit)
        return rb.success("WA-LOG-S-001", data={
            "query": query,
            "count": len(errors),
            "errors": [e.to_dict() for e in errors],
        })
    
    elif command == "cascades":
        cascades = [c.to_dict() for c in parser.cascade_patterns]
        return rb.success("WA-LOG-S-001", data={
            "cascade_count": len(cascades),
            "total_errors": parser.stats['total_errors'],
            "cascades": cascades,
            "recommendation": "Fix root errors first - they can eliminate many child errors",
        })
    
    return rb.invalid("MCP-SYS-I-001", data={"command": command, "source": "error"}, message=f"Unknown command for error source: {command}")


def _game_log_handler(
    command: str,
    category: str | None,
    query: str | None,
    limit: int,
    *, rb: ReplyBuilder,
) -> Reply:
    """Handle game.log commands."""
    from ck3raven.analyzers.log_parser import CK3LogParser, LogType, GAME_LOG_CATEGORIES
    
    parser = CK3LogParser()
    
    try:
        parser.parse_game_log()
    except FileNotFoundError:
        return rb.invalid("MCP-SYS-I-001", data={"source": "game"}, message="game.log not found")
    
    if command == "summary":
        return rb.success("WA-LOG-S-001", data=parser.get_game_log_summary())
    
    elif command == "list":
        entries = parser.entries[LogType.GAME]
        
        if category:
            entries = [e for e in entries if e.category == category]
        
        return rb.success("WA-LOG-S-001", data={
            "total_parsed": len(parser.entries[LogType.GAME]),
            "filtered_count": len(entries[:limit]),
            "summary": parser.get_game_log_summary(),
            "errors": [e.to_dict() for e in entries[:limit]],
        })
    
    elif command == "search":
        if not query:
            return rb.invalid("MCP-SYS-I-001", data={"parameter": "query", "command": "search", "source": "game"}, message="query parameter required for search command")
        
        entries = parser.search_entries(query, log_type=LogType.GAME, limit=limit)
        return rb.success("WA-LOG-S-001", data={
            "query": query,
            "count": len(entries),
            "errors": [e.to_dict() for e in entries],
        })
    
    elif command == "categories":
        stats = parser.stats.get(LogType.GAME, {})
        by_category = dict(stats.get('by_category', {}).most_common())
        
        category_info = {name: desc for name, _, _, desc in GAME_LOG_CATEGORIES}
        
        categories = []
        for cat, count in by_category.items():
            categories.append({
                "category": cat,
                "count": count,
                "description": category_info.get(cat, "Other/uncategorized errors"),
            })
        
        return rb.success("WA-LOG-S-001", data={
            "total_errors": stats.get('total', 0),
            "categories": categories,
        })
    
    return rb.invalid("MCP-SYS-I-001", data={"command": command, "source": "game"}, message=f"Unknown command for game source: {command}")


def _debug_log_handler(command: str, *, rb: ReplyBuilder) -> Reply:
    """Handle debug.log commands."""
    from ck3raven.analyzers.log_parser import CK3LogParser
    
    parser = CK3LogParser()
    
    try:
        parser.parse_debug_log(extract_system_info=True)
    except FileNotFoundError:
        return rb.invalid("MCP-SYS-I-001", data={"source": "debug"}, message="debug.log not found")
    
    if command == "summary":
        return rb.success("WA-LOG-S-001", data=parser.get_debug_info_summary())
    
    return rb.invalid("MCP-SYS-I-001", data={"command": command, "source": "debug"}, message=f"Unknown command for debug source: {command}")


def _crash_handler(command: str, crash_id: str | None, limit: int, *, rb: ReplyBuilder) -> Reply:
    """Handle crash report commands."""
    
    if command == "summary":
        from ck3raven.analyzers.crash_parser import get_recent_crashes
        
        crashes = get_recent_crashes(limit=limit)
        
        if not crashes:
            return rb.success("WA-LOG-S-001", data={
                "count": 0,
                "message": "No crash reports found",
            })
        
        return rb.success("WA-LOG-S-001", data={
            "count": len(crashes),
            "crashes": [c.to_dict() for c in crashes],
        })
    
    elif command == "detail":
        if not crash_id:
            return rb.invalid("MCP-SYS-I-001", data={"parameter": "crash_id", "command": "detail", "source": "crash"}, message="crash_id parameter required for detail command")
        
        from ck3raven.analyzers.crash_parser import parse_crash_folder
        
        crashes_dir = (
            Path.home() / "Documents" / "Paradox Interactive" / 
            "Crusader Kings III" / "crashes"
        )
        
        crash_path = crashes_dir / crash_id
        
        if not crash_path.exists():
            return rb.invalid("MCP-SYS-I-001", data={
                "crash_id": crash_id,
                "hint": "Use source=crash, command=summary to see available crashes",
            }, message=f"Crash folder not found: {crash_id}")
        
        report = parse_crash_folder(crash_path)
        
        if not report:
            return rb.error("MCP-SYS-E-001", data={"crash_id": crash_id}, message="Failed to parse crash folder")
        
        return rb.success("WA-LOG-S-001", data=report.to_dict())
    
    return rb.invalid("MCP-SYS-I-001", data={"command": command, "source": "crash"}, message=f"Unknown command for crash source: {command}")


def _read_log_raw(
    source: str,
    lines: int,
    from_end: bool,
    search: str | None,
    *, rb: ReplyBuilder,
) -> Reply:
    """Read raw log content.
    
    MEMORY-SAFE: 
    - For files >100KB with search: refuses (search requires full scan)
    - For files >100KB without search: uses efficient tail-reading
    - Always caps output to 200 lines max
    """
    MAX_SIZE_FOR_SEARCH = 100 * 1024  # 100KB limit when search is used
    MAX_LINES = 200  # Never return more than 200 lines
    
    log_files = {
        "error": "error.log",
        "game": "game.log",
        "debug": "debug.log",
        "setup": "setup.log",
        "gui_warnings": "gui_warnings.log",
        "database_conflicts": "database_conflicts.log",
    }

    if source not in log_files:
        return rb.invalid("MCP-SYS-I-001", data={
            "source": source,
            "available": list(log_files.keys()),
        }, message=f"Unknown log source: {source}")

    log_path = ROOT_USER_DOCS / "logs" / log_files[source]
    
    if not log_path.exists():
        return rb.invalid("MCP-SYS-I-001", data={
            "log_path": str(log_path),
            "hint": "Check the path or make sure CK3 has been run",
        }, message=f"Log file not found: {log_path}")
    
    try:
        file_size = log_path.stat().st_size
        
        # If search is requested on a large file, refuse
        if search and file_size > MAX_SIZE_FOR_SEARCH:
            size_mb = file_size / 1024 / 1024
            return rb.invalid("MCP-SYS-I-001", data={
                "file_path": str(log_path),
                "file_size": file_size,
                "suggestion": (
                    "For large log files, use structured access instead:\n"
                    "  • ck3_logs(command='summary') - parsed error summary\n"
                    "  • ck3_logs(command='list', mod_filter='...') - filter by mod\n"
                    "  • ck3_logs(command='read', lines=100) - get last N lines without search"
                ),
            }, message=f"File too large ({size_mb:.1f}MB) for search. Search requires loading entire file.")
        
        # Cap lines
        max_lines = min(lines, MAX_LINES)
        
        # For small files (<100KB), just read the whole thing
        if file_size <= MAX_SIZE_FOR_SEARCH:
            content_lines = log_path.read_text(encoding='utf-8', errors='replace').splitlines()
            total_lines = len(content_lines)
        else:
            # Large file, no search - read efficiently from end
            # Read enough to get ~200 lines (assuming ~500 bytes/line avg)
            chunk_size = min(150 * 1024, file_size)  # ~150KB should be plenty for 200 lines
            with open(log_path, 'rb') as f:
                if from_end:
                    f.seek(max(0, file_size - chunk_size))
                chunk = f.read(chunk_size).decode('utf-8', errors='replace')
            content_lines = chunk.splitlines()
            # First line may be partial if we seeked mid-file
            if file_size > chunk_size and content_lines and from_end:
                content_lines = content_lines[1:]  # Drop partial first line
            total_lines = None  # Unknown without full scan
        
        # Apply search filter if provided (only for small files per check above)
        if search:
            search_lower = search.lower()
            content_lines = [l for l in content_lines if search_lower in l.lower()]
        
        # Select lines
        if from_end:
            selected = content_lines[-max_lines:] if len(content_lines) > max_lines else content_lines
        else:
            selected = content_lines[:max_lines]
        
        return rb.success("WA-LOG-S-001", data={
            "log_source": source,
            "file_path": str(log_path),
            "file_size_bytes": file_size,
            "total_lines": total_lines,  # May be None for large files
            "returned_lines": len(selected),
            "lines_requested": lines,
            "lines_capped_at": max_lines if lines > max_lines else None,
            "from_end": from_end,
            "search": search,
            "content": "\n".join(selected),
        })
    except Exception as e:
        return rb.error("MCP-SYS-E-001", data={"exception": type(e).__name__}, message=str(e))


def _read_log_full(source: str, *, rb: ReplyBuilder) -> Reply:
    """Return raw log file content for backup/archival.
    
    MEMORY-SAFE: Refuses files larger than 100KB to prevent OOM.
    For large files, suggests copying to WIP workspace first.
    """
    MAX_SIZE_BYTES = 100 * 1024  # 100KB limit for safety
    
    log_files = {
        "error": "error.log",
        "game": "game.log",
        "debug": "debug.log",
        "setup": "setup.log",
        "gui_warnings": "gui_warnings.log",
        "database_conflicts": "database_conflicts.log",
    }

    if source not in log_files:
        return rb.invalid("MCP-SYS-I-001", data={
            "source": source,
            "available": list(log_files.keys()),
        }, message=f"Unknown log source: {source}")

    log_path = ROOT_USER_DOCS / "logs" / log_files[source]
    
    if not log_path.exists():
        return rb.invalid("MCP-SYS-I-001", data={
            "log_path": str(log_path),
            "hint": "Check the path or make sure CK3 has been run",
        }, message=f"Log file not found: {log_path}")
    
    try:
        file_size = log_path.stat().st_size
        
        # Refuse large files to prevent OOM
        if file_size > MAX_SIZE_BYTES:
            size_mb = file_size / 1024 / 1024
            return rb.invalid("MCP-SYS-I-001", data={
                "file_path": str(log_path),
                "file_size": file_size,
                "suggestion": (
                    "For large log files, use targeted access instead:\n"
                    "  • ck3_logs(command='read', lines=100) - get last N lines\n"
                    "  • ck3_logs(command='summary') - get parsed summary\n"
                    "  • ck3_logs(command='list', limit=50) - get filtered errors\n"
                    "  • ck3_logs(command='search', query='...') - search for specific text"
                ),
            }, message=f"File too large ({size_mb:.1f}MB). command='raw' is limited to 100KB.")
        
        content = log_path.read_text(encoding='utf-8', errors='replace')
        line_count = content.count('\n') + (1 if content and not content.endswith('\n') else 0)
        
        return rb.success("WA-LOG-S-001", data={
            "log_source": source,
            "file_path": str(log_path),
            "file_size": file_size,
            "line_count": line_count,
            "content": content,
        })
    except Exception as e:
        return rb.error("MCP-SYS-E-001", data={"exception": type(e).__name__}, message=str(e))


# ============================================================================
# ck3_conflicts - Unified Conflict Detection (v2)
# ============================================================================
# Replaces: direct db._get_symbol_conflicts_internal() + inline SQL in server.py
# Uses: EN2 execute_db() — all 3 commands are DB reads
#
# Thin entry point:
#   symbols/files/summary -> en2_execute_db() (DATABASE_OPERATIONS_MATRIX)
# ============================================================================

ConflictCommand = Literal["symbols", "files", "summary", "diff", "diff_index"]
DiffMode = Literal["lines", "ast", "both"]
DiffOutput = Literal["summary", "artifact", "html"]
DiffSource = Literal["database", "filesystem"]


def ck3_conflicts_impl(
    command: ConflictCommand = "symbols",
    # Symbol/File filters
    symbol_type: str | None = None,
    symbol_names: list[str] | None = None,
    path: str | None = None,
    # Diff parameters
    relpath: str | None = None,
    old_cvid: str | None = None,
    new_cvid: str | None = None,
    diff_mode: DiffMode = "both",
    output: DiffOutput = "summary",
    source: DiffSource = "database",
    old_path: str | None = None,
    new_path: str | None = None,
    normalize_indent: bool = True,
    context_lines: int = 4,
    output_dir: str | None = None,
    # Options
    limit: int = 100,
    # Dependencies (injected, keyword-only)
    *,
    session=None,
    db=None,
    trace=None,
    rb: "ReplyBuilder",
) -> "Reply":
    """
    Unified conflict detection for the active playset (v2).

    Commands:
        symbols    -> Symbol-level conflicts (multiple mods define same symbol)
        files      -> File-level conflicts (original-file-aware detection)
        summary    -> Conflict statistics overview
        diff       -> Diff one file between two CVIDs (line/AST modes)
        diff_index -> Diff file list under path prefix between two CVIDs

    Routing:
        All commands -> database: en2_execute_db -> conflict executor with cvid scoping
    """
    from ck3lens.agent_mode import get_agent_mode
    from ck3lens.logging import info

    mode = get_agent_mode()
    if not mode:
        return rb.invalid("WA-CFG-I-001", {
            "guidance": "Call ck3_get_mode_instructions(mode='ck3lens' or 'ck3raven-dev') to initialize",
        }, message="Agent mode not initialized")

    if not session:
        return rb.invalid("MCP-SYS-I-001", {
            "parameter": "session",
        }, message="No session available")

    info("mcp.tool", "ck3_conflicts called",
         command=command, mode=mode)

    if command == "diff" and output == "html" and diff_mode == "ast":
        return rb.invalid(
            "MCP-SYS-I-001",
            {"parameter": "output", "value": output, "reason": "html output requires line diff content"},
        )

    # Filesystem diff route (single-file only): reuse WA2 resolve + EN execute
    # through existing ck3_file/read capability, then run diff logic in-memory.
    if command == "diff" and source == "filesystem":
        if not old_path:
            return rb.invalid("MCP-SYS-I-001", {"parameter": "old_path"})
        if not new_path:
            return rb.invalid("MCP-SYS-I-001", {"parameter": "new_path"})

        from ck3lens.world_adapter_v2 import WorldAdapterV2
        from ck3lens.policy.enforcement_v2 import execute as en2_execute
        from ck3lens.impl.diff_formatters import render_line_diff_html
        from ck3lens.impl.diff_ops import DiffConfig, build_ast_diff, build_line_diff_artifact
        from ck3lens.impl.diff_reports import write_diff_report_bundle

        wa2 = WorldAdapterV2.create(session=session)

        old_resolve, old_ref = wa2.resolve(old_path, require_exists=True, rb=rb)
        if old_resolve.is_invalid or old_ref is None:
            return old_resolve

        new_resolve, new_ref = wa2.resolve(new_path, require_exists=True, rb=rb)
        if new_resolve.is_invalid or new_ref is None:
            return new_resolve

        old_read = en2_execute(
            rb,
            mode=mode,
            tool="ck3_file",
            command="read",
            root_key=old_resolve.data.get("root_key", ""),
            subdirectory=old_resolve.data.get("subdirectory"),
            ref=old_ref,
            wa2=wa2,
            params={"start_line": 1, "end_line": None, "trace": trace},
        )
        if old_read.reply_type != "S":
            return old_read

        new_read = en2_execute(
            rb,
            mode=mode,
            tool="ck3_file",
            command="read",
            root_key=new_resolve.data.get("root_key", ""),
            subdirectory=new_resolve.data.get("subdirectory"),
            ref=new_ref,
            wa2=wa2,
            params={"start_line": 1, "end_line": None, "trace": trace},
        )
        if new_read.reply_type != "S":
            return new_read

        old_content = (old_read.data or {}).get("content", "")
        new_content = (new_read.data or {}).get("content", "")

        import hashlib
        old_hash = hashlib.sha256(old_content.encode("utf-8", errors="replace")).hexdigest()
        new_hash = hashlib.sha256(new_content.encode("utf-8", errors="replace")).hexdigest()

        artifact: dict[str, Any] = {
            "source": "filesystem",
            "old_path": old_path,
            "new_path": new_path,
            "diff_mode": diff_mode,
        }

        line_artifact = None
        if diff_mode in {"lines", "both"}:
            line_artifact = build_line_diff_artifact(
                relpath or f"{old_path} -> {new_path}",
                old_content,
                new_content,
                cfg=DiffConfig(
                    normalize_indent=normalize_indent,
                    tab_width=4,
                    context_lines=context_lines,
                ),
                old_cvid=old_cvid or "",
                new_cvid=new_cvid or "",
                old_hash=old_hash,
                new_hash=new_hash,
            )
            artifact["line_diff"] = line_artifact

        ast_artifact = None
        if diff_mode in {"ast", "both"}:
            ast_artifact = build_ast_diff(
                relpath or f"{old_path} -> {new_path}",
                old_content,
                new_content,
                old_cvid=old_cvid or "",
                new_cvid=new_cvid or "",
            )
            artifact["ast_diff"] = ast_artifact

        if output == "artifact":
            return rb.success("EN-READ-S-001", data=artifact)
        if output == "html":
            html_report = render_line_diff_html(artifact)
            report_data = write_diff_report_bundle(
                artifact=artifact,
                html=html_report,
                output_dir=output_dir,
            )
            return rb.success("EN-READ-S-001", data=report_data)

        summary: dict[str, Any] = {
            "source": "filesystem",
            "old_path": old_path,
            "new_path": new_path,
            "diff_mode": diff_mode,
        }
        if line_artifact is not None:
            summary["line_stats"] = line_artifact.get("stats", {})
            summary["equivalence"] = line_artifact.get("equivalence", {})
        if ast_artifact is not None:
            summary["ast_change_count"] = ast_artifact.get("change_count", 0)
            summary["ast_parse_ok"] = ast_artifact.get("parse_ok", False)
            summary["warnings"] = ast_artifact.get("warnings", [])
        return rb.success("EN-READ-S-001", data=summary)

    if command == "diff_index" and source == "filesystem":
        return rb.invalid("MCP-SYS-I-001", {
            "parameter": "source",
            "value": source,
            "reason": "filesystem route for diff_index is not implemented yet",
        })

    from ck3lens.policy.enforcement_v2 import execute_db as en2_execute_db

    return en2_execute_db(
        rb, mode=mode, tool="ck3_conflicts", command=command,
        params={
            "symbol_type": symbol_type,
            "symbol_names": symbol_names,
            "path": path,
            "relpath": relpath,
            "old_cvid": old_cvid,
            "new_cvid": new_cvid,
            "diff_mode": diff_mode,
            "output": output,
            "source": source,
            "old_path": old_path,
            "new_path": new_path,
            "normalize_indent": normalize_indent,
            "context_lines": context_lines,
            "output_dir": output_dir,
            "limit": limit,
            "session": session,
            "db": db,
        },
        session=session,
    )


# ============================================================================
# ck3_file - Unified File Operations
# ============================================================================

FileCommand = Literal["read", "write", "edit", "delete", "rename", "create_patch", "create_patch_batch", "index"]


def ck3_file_impl(
    command: FileCommand,
    # Path identification (v2 ONLY - canonical addresses)
    path: str | None = None,
    mod_name: str | None = None,  # Destination mod for create_patch / create_patch_batch
    # For read/write
    content: str | None = None,
    start_line: int = 1,
    end_line: int | None = None,
    max_bytes: int = 200000,
    # For edit
    old_content: str | None = None,
    new_content: str | None = None,
    # For rename
    new_path: str | None = None,
    # For write/edit
    validate_syntax: bool = True,
    # For create_patch (ck3lens mode only)
    patch_mode: str | None = None,  # "partial_patch" or "full_replace"
    # For create_patch_batch (ck3lens mode only)
    patches: list[dict] | None = None,  # List of {path, patch_mode} dicts
    # For index command
    parse: bool = False,  # If True, also output full CST JSON
    output_path: str | None = None,  # Optional output directory for index files
    # Dependencies (injected, keyword-only)
    *,
    session=None,
    trace=None,
    # Reply system
    rb: ReplyBuilder,
) -> Reply:
    """
    Unified file operations tool (v2 canonical addressing ONLY).
    
    Commands:
    
    command=read         -> Read file from filesystem (path required, canonical address)
    command=write        -> Write file (path, content required)
    command=edit         -> Search-replace (path, old_content, new_content required)
    command=delete       -> Delete file (path required)
    command=rename       -> Rename/move file (path, new_path required)
    command=create_patch -> Create override patch file (ck3lens only)
    command=index        -> Index file for navigation
    
    Path format (canonical addresses):
    - root:repo/... — ck3raven source
    - root:game/... — vanilla CK3
    - root:ck3raven_data/... — ~/.ck3raven/
    - mod:ModName/... — mod in active playset
    """
    from ck3lens.agent_mode import get_agent_mode
    
    mode = get_agent_mode()
    
    from ck3lens.world_adapter_v2 import WorldAdapterV2
    from ck3lens.policy.enforcement_v2 import execute as en2_execute
    # ==========================================================================
    # MODE GATE: All operations require mode initialization
    # ==========================================================================
    if not mode:
        return rb.invalid("WA-CFG-I-001", {
            "guidance": "Call ck3_get_mode_instructions(mode='ck3lens' or 'ck3raven-dev') to initialize",
        }, message="Agent mode not initialized")
    
    # ==========================================================================
    # ROUTE TO IMPLEMENTATION (WA2 + EN2 — v2 only)
    # ==========================================================================
    
    if command == "read":
        if not path:
            return rb.invalid("MCP-SYS-I-001", {
                "parameter": "path",
                "command": "read",
                "example": "path='root:repo/tools/ck3lens_mcp/server.py' or path='mod:MyMod/common/traits/00_traits.txt'",
            }, message="'path' required for read command")
        
        from ck3lens.world_adapter_v2 import WorldAdapterV2
        from ck3lens.policy.enforcement_v2 import execute as en2_execute

        wa2 = WorldAdapterV2.create(session=session)
        resolve_reply, ref = wa2.resolve(path, require_exists=True, rb=rb)

        if resolve_reply.is_invalid or ref is None:
            return resolve_reply

        root_key = resolve_reply.data.get("root_key", "")
        subdirectory = resolve_reply.data.get("subdirectory")

        return en2_execute(
            rb,
            mode=mode,
            tool="ck3_file",
            command="read",
            root_key=root_key,
            subdirectory=subdirectory,
            ref=ref, wa2=wa2,
            params={
                "start_line": start_line,
                "end_line": end_line,
                "trace": trace,
            },
        )
    
    elif command == "write":
        if not path:
            return rb.invalid("MCP-SYS-I-001", {
                "parameter": "path", "command": "write",
            }, message="'path' required for write command")
        if content is None:
            return rb.invalid("MCP-SYS-I-001", {
                "parameter": "content", "command": "write",
            }, message="'content' required for write command")
        
        from ck3lens.world_adapter_v2 import WorldAdapterV2
        from ck3lens.policy.enforcement_v2 import execute as en2_execute

        wa2 = WorldAdapterV2.create(session=session)
        resolve_reply, ref = wa2.resolve(path, require_exists=False, rb=rb)

        if resolve_reply.is_invalid or ref is None:
            return resolve_reply

        root_key = resolve_reply.data.get("root_key", "")
        subdirectory = resolve_reply.data.get("subdirectory")

        return en2_execute(
            rb,
            mode=mode,
            tool="ck3_file",
            command="write",
            root_key=root_key,
            subdirectory=subdirectory,
            ref=ref, wa2=wa2,
            params={
                "content": content,
                "validate_syntax": validate_syntax,
                "trace": trace,
            },
            session=session,
        )
    
    elif command == "edit":
        if not path:
            return rb.invalid("MCP-SYS-I-001", {"parameter": "path", "command": "edit"}, message="path required")
        if old_content is None or new_content is None:
            return rb.invalid("MCP-SYS-I-001", {
                "parameter": "old_content/new_content", "command": "edit",
            }, message="old_content and new_content required for edit")
        
        wa2 = WorldAdapterV2.create(session=session)
        resolve_reply, ref = wa2.resolve(path, require_exists=True, rb=rb)

        if resolve_reply.is_invalid or ref is None:
            return resolve_reply

        root_key = resolve_reply.data.get("root_key", "")
        subdirectory = resolve_reply.data.get("subdirectory")

        return en2_execute(
            rb,
            mode=mode,
            tool="ck3_file",
            command="edit",
            root_key=root_key,
            subdirectory=subdirectory,
            ref=ref, wa2=wa2,
            params={
                "old_content": old_content,
                "new_content": new_content,
                "validate_syntax": validate_syntax,
                "trace": trace,
            },
            session=session,
        )
    
    elif command == "delete":
        if not path:
            return rb.invalid("MCP-SYS-I-001", {"parameter": "path", "command": "delete"}, message="path required")
        
        wa2 = WorldAdapterV2.create(session=session)
        resolve_reply, ref = wa2.resolve(path, require_exists=True, rb=rb)

        if resolve_reply.is_invalid or ref is None:
            return resolve_reply

        root_key = resolve_reply.data.get("root_key", "")
        subdirectory = resolve_reply.data.get("subdirectory")

        return en2_execute(
            rb,
            mode=mode,
            tool="ck3_file",
            command="delete",
            root_key=root_key,
            subdirectory=subdirectory,
            ref=ref, wa2=wa2,
            params={
                "trace": trace,
            },
            session=session,
        )
    
    elif command == "rename":
        if not path:
            return rb.invalid("MCP-SYS-I-001", {"parameter": "path", "command": "rename"}, message="path required")
        if not new_path:
            return rb.invalid("MCP-SYS-I-001", {
                "parameter": "new_path", "command": "rename",
            }, message="new_path required for rename")
        
        wa2 = WorldAdapterV2.create(session=session)
        
        # Resolve source path
        resolve_reply, ref = wa2.resolve(path, require_exists=True, rb=rb)
        if resolve_reply.is_invalid or ref is None:
            return resolve_reply

        # Resolve destination path
        new_resolve_reply, new_ref = wa2.resolve(new_path, require_exists=False, rb=rb)
        if new_resolve_reply.is_invalid or new_ref is None:
            return new_resolve_reply

        # Rename has TWO paths — extract dest path for params
        # (ideal: support multi-ref, but rename is special case)
        new_host_paths = wa2.host_path(new_ref)
        if not new_host_paths:
            return rb.error("WA-RES-E-001", {"input_path": new_path}, message="Token expired or invalid for new_path")
        new_host_path = new_host_paths[0]

        root_key = resolve_reply.data.get("root_key", "")
        subdirectory = resolve_reply.data.get("subdirectory")

        return en2_execute(
            rb,
            mode=mode,
            tool="ck3_file",
            command="rename",
            root_key=root_key,
            subdirectory=subdirectory,
            ref=ref, wa2=wa2,
            params={
                "new_host_path": new_host_path,
                "trace": trace,
            },
            session=session,
        )
    
    elif command == "create_patch":
        # ck3lens mode only - creates override patch file
        # path = canonical address of the SOURCE file being patched
        # mod_name = DESTINATION mod where patch file is written
        return _file_create_patch(
            path=path,
            mod_name=mod_name,
            patch_mode=patch_mode,
            initial_content=content,
            validate_syntax=validate_syntax,
            session=session,
            trace=trace,
            mode=mode,
            rb=rb,
        )
    
    elif command == "create_patch_batch":
        # ck3lens mode only - creates multiple override patch files in one operation
        # patches = list of {path, patch_mode} dicts
        # mod_name = DESTINATION mod where all patch files are written
        return _file_create_patch_batch(
            patches=patches,
            mod_name=mod_name,
            validate_syntax=validate_syntax,
            session=session,
            trace=trace,
            mode=mode,
            rb=rb,
        )
    
    elif command == "index":
        # Sprint 2.1: Index file and optionally produce full CST
        return _file_index(
            path=path,
            parse=parse,
            output_path=output_path,
            trace=trace,
            session=session,
            rb=rb,
        )
    
    return rb.invalid("MCP-SYS-I-002", {"command": command}, message=f"Unknown command: {command}")


def _file_create_patch(path, mod_name, patch_mode, initial_content, validate_syntax, session, trace, mode, rb: ReplyBuilder):
    """
    Create an override patch file in a mod.
    
    MODE: ck3lens only. Not available in ck3raven-dev mode.
    
    Parameters:
    - path: Canonical address of the SOURCE file being patched
            (e.g., "mod:Gambo's LSC/common/bookmarks/groups/00_bookmark_groups.txt")
    - mod_name: DESTINATION mod where the patch file is written
    - patch_mode: "partial_patch" (zzz_ prefix) or "full_replace" (same name)
    - initial_content: Content for the patch (if None, reads from source file)
    
    Resolution flow:
    1. Resolve source via wa2.resolve(path, require_exists=True)
       → extracts relative_path, source mod_name from reply.data
    2. Compute destination filename (zzz_ prefix or same name)
    3. Build destination canonical: mod:{mod_name}/{dest_rel_path}
    4. Resolve destination via wa2.resolve(dest_canonical, require_exists=False)
    5. EN2 executes write (executor does mkdir -p automatically)
    
    Uses v2 WA2 + EN2 pipeline.
    """
    from pathlib import PurePosixPath
    from datetime import datetime
    from ck3lens.world_adapter_v2 import WorldAdapterV2
    from ck3lens.policy.enforcement_v2 import execute as en2_execute
    
    # Mode check: ck3lens only
    if mode == "ck3raven-dev":
        return rb.invalid("WA-CFG-I-001", {
            "guidance": "This tool creates override patches in CK3 mods, which is not relevant to ck3raven development",
        }, message="create_patch command is only available in ck3lens mode")
    
    # Validate required parameters
    if not path:
        return rb.invalid("MCP-SYS-I-001", {
            "parameter": "path",
            "command": "create_patch",
            "example": "path='mod:SourceMod/common/traits/00_traits.txt'",
        }, message="path required for create_patch (canonical address of the source file being patched)")
    if not mod_name:
        return rb.invalid("MCP-SYS-I-001", {
            "parameter": "mod_name",
            "command": "create_patch",
        }, message="mod_name required for create_patch (destination mod)")
    if not patch_mode:
        return rb.invalid("MCP-SYS-I-001", {
            "parameter": "patch_mode",
            "command": "create_patch",
        }, message="patch_mode required: 'partial_patch' or 'full_replace'")
    if patch_mode not in ("partial_patch", "full_replace"):
        return rb.invalid("MCP-SYS-I-001", {
            "parameter": "patch_mode",
            "value": patch_mode,
            "valid_values": ["partial_patch", "full_replace"],
        }, message=f"Invalid patch_mode: {patch_mode}. Use 'partial_patch' or 'full_replace'")
    
    wa2 = WorldAdapterV2.create(session=session)
    
    # ── Step 1: Resolve SOURCE file (must exist) ──
    source_resolve, source_ref = wa2.resolve(path, require_exists=True, rb=rb)
    if source_resolve.is_invalid:
        return source_resolve
    if source_ref is None:
        return source_resolve
    
    source_host = wa2.host_path(source_ref)[0]
    source_rel_path = source_resolve.data.get("relative_path", "")
    source_mod_name = source_resolve.data.get("mod_name", "")

    if initial_content is None:
        if not source_host.exists():
            return rb.invalid("WA-RES-I-001", {"input_path": path}, message="Source file not found on disk")
        try:
            initial_content = source_host.read_text(encoding='utf-8-sig')
            header = (
                f"# Override patch for: {source_mod_name}/{source_rel_path}\n"
                f"# Created: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
                f"# Patch mode: {patch_mode}\n"
                f"# Source mod: {source_mod_name}\n"
                f"# Target mod: {mod_name}\n"
                f"# This file was copied from the source mod. Edit as needed.\n\n"
            )
            initial_content = header + initial_content
        except Exception as e:
            return rb.error("WA-RES-E-001", {"input_path": path, "exception": type(e).__name__}, message=f"Failed to read source file: {e}")

    # ── Step 3: Compute destination filename and relpath ──
    source_posix = PurePosixPath(source_rel_path)
    
    if patch_mode == "partial_patch":
        mod_prefix = _compute_mod_prefix(mod_name)
        dest_filename = f"zzz_{mod_prefix}_{source_posix.name}"
    else:  # full_replace
        dest_filename = source_posix.name
    
    dest_rel_path = str(source_posix.parent / dest_filename)
    
    # ── Step 4: Resolve DESTINATION path (may not exist yet) ──
    dest_canonical = f"mod:{mod_name}/{dest_rel_path}"
    
    dest_resolve, dest_ref = wa2.resolve(dest_canonical, require_exists=False, rb=rb)
    if dest_resolve.is_invalid or dest_ref is None:
        return dest_resolve
    
    root_key = dest_resolve.data.get("root_key", "")
    subdirectory = dest_resolve.data.get("subdirectory")
    
    # ── Step 5: EN2 write (executor does mkdir -p) ──
    write_result = en2_execute(
        rb,
        mode=mode,
        tool="ck3_file",
        command="write",
        root_key=root_key,
        subdirectory=subdirectory,
        ref=dest_ref, wa2=wa2,
        params={
            "content": initial_content,
            "validate_syntax": validate_syntax,
            "trace": trace,
        },
        session=session,
    )
    
    if write_result.is_success:
        patch_data = {**write_result.data, "patch_info": {
            "source_mod": source_mod_name,
            "source_path": source_rel_path,
            "patch_mode": patch_mode,
            "created_path": dest_rel_path,
        }, "message": f"Created {patch_mode} patch: {dest_rel_path}"}
        
        if trace:
            trace.log("mcp.tool", {
                "mod_name": mod_name,
                "source_mod": source_mod_name,
                "source_path": source_rel_path,
                "patch_mode": patch_mode,
            }, {"success": True, "created_path": dest_rel_path})
        
        return rb.success("EN-WRITE-S-001", patch_data)
    
    return write_result


def _file_create_patch_batch(patches, mod_name, validate_syntax, session, trace, mode, rb: ReplyBuilder):
    """
    Create multiple override patch files in a single operation.
    
    MODE: ck3lens only. Not available in ck3raven-dev mode.
    
    Parameters:
    - patches: List of dicts, each with:
        - path: Canonical address of SOURCE file (required)
        - patch_mode: "partial_patch" or "full_replace" (required)
    - mod_name: DESTINATION mod where ALL patch files are written
    - validate_syntax: Whether to validate CK3 syntax for each file
    
    Benefits over single create_patch:
    - One contract covers all patches
    - Atomic success/failure reporting
    - Reduced round-trips
    
    Returns:
        Success with list of created patches, or
        Partial success with created + failed lists, or
        Invalid if parameters are wrong
    """
    from pathlib import PurePosixPath
    from datetime import datetime
    from ck3lens.world_adapter_v2 import WorldAdapterV2
    from ck3lens.policy.enforcement_v2 import execute as en2_execute
    
    # Mode check: ck3lens only
    if mode == "ck3raven-dev":
        return rb.invalid("WA-CFG-I-001", {
            "guidance": "This tool creates override patches in CK3 mods, which is not relevant to ck3raven development",
        }, message="create_patch_batch command is only available in ck3lens mode")
    
    # Validate required parameters
    if not patches:
        return rb.invalid("MCP-SYS-I-001", {
            "parameter": "patches",
            "command": "create_patch_batch",
            "example": "patches=[{\"path\": \"mod:SourceMod/common/traits/00_traits.txt\", \"patch_mode\": \"partial_patch\"}]",
        }, message="patches required for create_patch_batch (list of {path, patch_mode} dicts)")
    
    if not isinstance(patches, list):
        return rb.invalid("MCP-SYS-I-001", {
            "parameter": "patches",
            "command": "create_patch_batch",
            "received_type": type(patches).__name__,
        }, message="patches must be a list of {path, patch_mode} dicts")
    
    if not mod_name:
        return rb.invalid("MCP-SYS-I-001", {
            "parameter": "mod_name",
            "command": "create_patch_batch",
        }, message="mod_name required for create_patch_batch (destination mod for all patches)")
    
    # Validate each patch entry
    for i, patch in enumerate(patches):
        if not isinstance(patch, dict):
            return rb.invalid("MCP-SYS-I-001", {
                "parameter": f"patches[{i}]",
                "command": "create_patch_batch",
                "received_type": type(patch).__name__,
            }, message=f"patches[{i}] must be a dict with 'path' and 'patch_mode' keys")
        
        if "path" not in patch:
            return rb.invalid("MCP-SYS-I-001", {
                "parameter": f"patches[{i}].path",
                "command": "create_patch_batch",
            }, message=f"patches[{i}] missing required 'path' key")
        
        if "patch_mode" not in patch:
            return rb.invalid("MCP-SYS-I-001", {
                "parameter": f"patches[{i}].patch_mode",
                "command": "create_patch_batch",
            }, message=f"patches[{i}] missing required 'patch_mode' key")
        
        if patch["patch_mode"] not in ("partial_patch", "full_replace"):
            return rb.invalid("MCP-SYS-I-001", {
                "parameter": f"patches[{i}].patch_mode",
                "value": patch["patch_mode"],
                "valid_values": ["partial_patch", "full_replace"],
            }, message=f"patches[{i}].patch_mode invalid: use 'partial_patch' or 'full_replace'")
    
    wa2 = WorldAdapterV2.create(session=session)
    mod_prefix = _compute_mod_prefix(mod_name)
    
    results = {
        "created": [],
        "failed": [],
        "total": len(patches),
    }
    
    for i, patch in enumerate(patches):
        path = patch["path"]
        patch_mode = patch["patch_mode"]
        
        # ── Step 1: Resolve SOURCE file (must exist) ──
        source_resolve, source_ref = wa2.resolve(path, require_exists=True, rb=rb)
        if source_resolve.is_invalid or source_ref is None:
            results["failed"].append({
                "index": i,
                "path": path,
                "error": source_resolve.message if source_resolve.is_invalid else "Resolution failed",
                "code": source_resolve.code,
            })
            continue
        
        source_host = wa2.host_path(source_ref)[0]
        
        source_rel_path = source_resolve.data.get("relative_path", "")
        source_mod_name = source_resolve.data.get("mod_name", "")
        
        if not source_rel_path:
            results["failed"].append({
                "index": i,
                "path": path,
                "error": "Could not extract relative path from source address",
                "code": "WA-RES-I-001",
            })
            continue
        
        # ── Step 2: Read source content ──
        if not source_host.exists():
            results["failed"].append({
                "index": i,
                "path": path,
                "error": "Source file not found on disk",
                "code": "WA-RES-I-001",
            })
            continue
        
        try:
            content = source_host.read_text(encoding='utf-8-sig')
            header = f"""# Override patch for: {source_mod_name}/{source_rel_path}
# Created: {datetime.now().strftime("%Y-%m-%d %H:%M")}
# Patch mode: {patch_mode}
# Source mod: {source_mod_name}
# Target mod: {mod_name}
# 
# This file was copied from the source mod. Edit as needed.

"""
            content = header + content
        except Exception as e:
            results["failed"].append({
                "index": i,
                "path": path,
                "error": f"Failed to read source file: {e}",
                "code": "WA-RES-E-001",
            })
            continue
        
        # ── Step 3: Compute destination filename and relpath ──
        source_posix = PurePosixPath(source_rel_path)
        
        if patch_mode == "partial_patch":
            dest_filename = f"zzz_{mod_prefix}_{source_posix.name}"
        else:  # full_replace
            dest_filename = source_posix.name
        
        dest_rel_path = str(source_posix.parent / dest_filename)
        
        # ── Step 4: Resolve DESTINATION path (may not exist yet) ──
        dest_canonical = f"mod:{mod_name}/{dest_rel_path}"
        
        dest_resolve, dest_ref = wa2.resolve(dest_canonical, require_exists=False, rb=rb)
        if dest_resolve.is_invalid or dest_ref is None:
            results["failed"].append({
                "index": i,
                "path": path,
                "error": dest_resolve.message if dest_resolve.is_invalid else "Destination resolution failed",
                "code": dest_resolve.code,
            })
            continue
        
        root_key = dest_resolve.data.get("root_key", "")
        subdirectory = dest_resolve.data.get("subdirectory")
        
        # ── Step 5: EN2 write (executor does mkdir -p) ──
        write_result = en2_execute(
            rb,
            mode=mode,
            tool="ck3_file",
            command="write",
            root_key=root_key,
            subdirectory=subdirectory,
            ref=dest_ref, wa2=wa2,
            params={
                "content": content,
                "validate_syntax": validate_syntax,
                "trace": trace,
            },
            session=session,
        )
        
        if write_result.is_success:
            results["created"].append({
                "index": i,
                "source_path": path,
                "source_mod": source_mod_name,
                "created_path": dest_rel_path,
                "patch_mode": patch_mode,
            })
        else:
            results["failed"].append({
                "index": i,
                "path": path,
                "error": write_result.message,
                "code": write_result.code,
            })
    
    # Build summary
    created_count = len(results["created"])
    failed_count = len(results["failed"])
    
    if trace:
        trace.log("mcp.tool", {
            "mod_name": mod_name,
            "total_patches": len(patches),
            "created": created_count,
            "failed": failed_count,
        }, {"success": failed_count == 0})
    
    if failed_count == 0:
        # All succeeded
        return rb.success("EN-WRITE-S-001", {
            **results,
            "message": f"Created {created_count} patch files in {mod_name}",
        })
    elif created_count == 0:
        # All failed
        return rb.invalid("EN-WRITE-I-001", {
            **results,
            "message": f"All {failed_count} patches failed",
        })
    else:
        # Partial success — return success with warnings
        return rb.success("EN-WRITE-S-001", {
            **results,
            "message": f"Created {created_count}/{len(patches)} patches ({failed_count} failed)",
            "partial_success": True,
        })


def _file_index(
    path: str | None,
    parse: bool,
    output_path: str | None,
    trace,
    session,
    rb: "ReplyBuilder",
) -> "Reply":
    """
    Index a file and optionally produce full CST.
    
    Uses WA2 for canonical address resolution.
    NO ENFORCEMENT NEEDED — output goes to files (wip/indexes/), not context.
    
    Produces two JSON outputs:
    - INDEX: Compact navigation map (~5KB) with names, types, signatures, line ranges
    - CST (optional): Full JSON tree (~50KB) preserving complete structure
    
    Args:
        path: Canonical path (e.g., "root:repo/tools/...", "mod:Name/...")
        parse: If True, also output full CST JSON
        output_path: Optional output directory (defaults to ~/.ck3raven/wip/indexes/)
        trace: Trace object
        session: Session object
        rb: ReplyBuilder
        
    Returns:
        Reply with index_path, cst_path (if parse=True), and metadata
    """
    from pathlib import Path as P
    import hashlib
    import json
    
    from ck3lens.indexing import index_file, detect_file_type
    from ck3lens.paths import ROOT_CK3RAVEN_DATA
    from ck3lens.world_adapter_v2 import WorldAdapterV2
    
    # Validate path input
    if not path:
        return rb.invalid("WA-RES-I-001", {
            "detail": "path required for index command",
            "invalid_field": "path",
        })
    
    # Validate canonical format
    if not (path.startswith("root:") or path.startswith("mod:")):
        return rb.invalid("WA-RES-I-001", {
            "detail": "index command requires canonical path format (root: or mod:)",
            "invalid_field": "path",
            "provided": path,
            "example": "path='root:repo/tools/ck3lens_mcp/server.py'",
        })
    
    # Resolve via WA2
    wa2 = WorldAdapterV2.create(session=session)
    resolve_reply, ref = wa2.resolve(path, require_exists=True, rb=rb)
    
    if resolve_reply.is_invalid:
        return resolve_reply
    
    if ref is None:
        return rb.invalid("WA-RES-I-001", {"input_path": path}, message="WA2 resolve failed")
    
    # Get host path
    host_path = wa2.host_path(ref)[0]
    
    # Check file type is supported
    file_type = detect_file_type(host_path)
    # All file types now supported - Sprint 2.2 indexers complete
    
    # Run indexer - returns (index_dict, cst_dict or None)
    try:
        index_dict, cst_dict = index_file(host_path, path, file_type, include_cst=parse)
    except Exception as e:
        return rb.error("WA-READ-E-001", {"file_path": path, "exception": type(e).__name__}, message=f"Indexing failed: {e}")
    
    # Determine output directory
    if output_path:
        # Resolve output_path via WA2
        out_reply, out_ref = wa2.resolve(output_path, require_exists=False, rb=rb)
        if out_reply.is_invalid or out_ref is None:
            return out_reply
        out_dir = wa2.host_path(out_ref)[0]
    else:
        # Default to ~/.ck3raven/wip/indexes/
        out_dir = ROOT_CK3RAVEN_DATA / "wip" / "indexes"
    
    # Ensure output directory exists
    out_dir.mkdir(parents=True, exist_ok=True)
    
    # Compute filename with hash suffix (disambiguate same-name files from different paths)
    filename = host_path.name
    path_hash = hashlib.sha256(path.encode()).hexdigest()[:8]
    
    # Write INDEX JSON
    index_filename = f"INDEX-{filename}-{path_hash}.json"
    index_path = out_dir / index_filename
    index_path.write_text(json.dumps(index_dict, indent=2, ensure_ascii=False), encoding="utf-8")
    
    # Write CST JSON if requested
    cst_path = None
    cst_bytes = None
    if parse and cst_dict:
        cst_filename = f"CST-{filename}-{path_hash}.json"
        cst_path = out_dir / cst_filename
        cst_json_str = json.dumps(cst_dict, indent=2, ensure_ascii=False)
        cst_path.write_text(cst_json_str, encoding="utf-8")
        cst_bytes = len(cst_json_str.encode("utf-8"))
    
    # Log
    if trace:
        trace.log("mcp.tool", {
            "source_path": path,
            "parse": parse,
        }, {
            "index_path": str(index_path),
            "element_count": len(index_dict.get("elements", [])),
        })
    
    # Build response (file_type is inside index_dict)
    data = {
        "source_path": path,
        "index_path": str(index_path),
        "file_type": index_dict.get("file_type", file_type),
        "element_count": len(index_dict.get("elements", [])),
        "index_bytes": index_path.stat().st_size,
    }
    
    if cst_path:
        data["cst_path"] = str(cst_path)
        data["cst_bytes"] = cst_bytes
    
    return rb.success("WA-READ-S-001", data)


# ============================================================================
# DELETED: ck3_folder (March 17, 2026)
# ============================================================================
# Removed — ck3_folder list was redundant with ck3_dir list.
# Both resolved canonical addresses via WA2 and listed filesystem directories.
# ck3_dir is the canonical directory navigation tool.
# ============================================================================


# ============================================================================
# DELETED: ck3_playset - Database-Based Playset Operations (January 2, 2026)
# ============================================================================
# The following functions were EXPUNGED as BANNED CONCEPTS:
# - ck3_playset_impl() - used playset_id parameter
# - _playset_get() - JOINed on playset_mods table
# - _playset_list() - JOINed on playset_mods table  
# - _playset_switch() - queried playsets table
# - _playset_mods() - JOINed on playset_mods table
# - _playset_add_mod() - INSERTed to playset_mods table
# - _playset_remove_mod() - DELETEd from playset_mods table
# - _playset_reorder() - UPDATEd playset_mods table
# - _playset_create() - INSERTed to playsets table
# - _playset_import() - placeholder
#
# REASON: playset_mods and playsets are BANNED TABLES that duplicate
# the canonical mods[] array from playset JSON files.
# 
# CANONICAL APPROACH: Playset operations are now FILE-BASED in server.py.
# See ck3_playset() in server.py which reads/writes playset/*.json files.
# ============================================================================


# ============================================================================
# ck3_git - DELETED (March 17, 2026)
# Migrated to v2 pipeline: server.py handler → WA2 resolve → EN2 execute → executors.py
# GitCommand, _run_git_in_path, _git_ops_for_path, ck3_git_impl all removed.
# Git executor functions now live in ck3lens/policy/executors.py.
# ============================================================================


# ============================================================================
# ck3_validate - Unified Validation Operations
# ============================================================================

ValidateTarget = Literal["syntax", "python", "references", "bundle", "policy"]


def ck3_validate_impl(
    target: ValidateTarget,
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
    # Dependencies
    session=None,
    db=None,
    trace=None,
    rb: "ReplyBuilder | None" = None,
) -> "Reply":
    """
    Unified validation tool.

    target=references is DB-routed through EN2 for mode-aware scoping.
    Other targets are direct WA validation replies.
    """
    from ck3lens.agent_mode import get_agent_mode
    from ck3lens.policy.enforcement_v2 import execute_db as en2_execute_db

    if rb is None:
        raise ValueError("ReplyBuilder required")

    current_mode = get_agent_mode()
    if not current_mode:
        return rb.invalid("WA-CFG-I-001", {
            "guidance": "Call ck3_get_mode_instructions(mode='ck3lens' or 'ck3raven-dev') to initialize",
        }, message="Agent mode not initialized")

    if target == "references":
        return en2_execute_db(
            rb,
            mode=current_mode,
            tool="ck3_validate",
            command="references",
            params={
                "symbol_name": symbol_name,
                "symbol_type": symbol_type,
                "session": session,
                "db": db,
            },
            session=session,
        )

    if target == "syntax":
        if not content:
            return rb.invalid("MCP-SYS-I-001", {"parameter": "content", "target": target},
                              message="content required for syntax validation")
        return _validation_reply(rb, _validate_syntax(content, file_path or "inline.txt", trace), target)

    if target == "python":
        validation_path = file_path
        if file_path and content is None:
            from ck3lens.world_adapter_v2 import WorldAdapterV2

            wa2 = WorldAdapterV2.create(session=session)
            resolve_reply, ref = wa2.resolve(file_path, require_exists=True, rb=rb)
            if ref is None:
                return resolve_reply

            host_paths = wa2.host_path(ref)
            if len(host_paths) != 1:
                return rb.error(
                    "WA-RES-E-001",
                    {"input_path": file_path, "resolved_path_count": len(host_paths)},
                    message="Canonical path did not resolve to exactly one host path",
                )
            validation_path = str(host_paths[0])

        return _validation_reply(
            rb,
            _validate_python(content, validation_path, trace, display_path=file_path),
            target,
        )

    if target == "bundle":
        if not artifact_bundle:
            return rb.invalid("MCP-SYS-I-001", {"parameter": "artifact_bundle", "target": target},
                              message="artifact_bundle required for bundle validation")
        return _validation_reply(rb, _validate_bundle(artifact_bundle, trace), target)

    if target == "policy":
        if not mode:
            return rb.invalid("MCP-SYS-I-001", {"parameter": "mode", "target": target},
                              message="mode required for policy validation")
        return _validation_reply(rb, _validate_policy(mode, trace_path, trace), target)

    return rb.invalid("MCP-SYS-I-002", {"target": target}, message=f"Unknown target: {target}")


def _validation_reply(rb: "ReplyBuilder", result: dict, target: str) -> "Reply":
    if result.get("error"):
        return rb.error("WA-VAL-E-001", result, message=str(result["error"]))
    if result.get("valid") is False:
        return rb.invalid("WA-VAL-I-001", result, message=f"Validation failed: target={target}")
    return rb.success("WA-VAL-S-001", result, message=f"Validation passed: target={target}")


def _validate_syntax(content, filename, trace):
    """Validate CK3 script syntax."""
    from ck3lens.validate import parse_content
    
    result = parse_content(content, filename)
    
    if trace:
        trace.log("mcp.tool", {"filename": filename},
                  {"valid": result.get("success", False)})
    
    return {
        "valid": result.get("success", False),
        "errors": result.get("errors", []),
        "node_count": result.get("node_count", 0),
    }


def _validate_python(content, file_path, trace, display_path=None):
    """Validate Python syntax.
    
    NOTE: For .txt files (CK3 script files), this automatically routes to
    CK3 syntax validation using our Paradox script parser instead of Python's ast.
    This prevents false positives when validating CK3 mod files.
    """
    import ast
    from pathlib import Path as P
    
    # Keep canonical identity in diagnostics while reading via the WA2 host path.
    filename = display_path or file_path or "<string>"
    
    # CK3 script files (.txt) should use CK3 syntax validation, not Python
    if filename.lower().endswith('.txt'):
        # Route to CK3 syntax validation
        if content:
            source = content
        elif file_path:
            path = P(file_path)
            if not path.exists():
                return {"valid": False, "error": f"File not found: {file_path}"}
            source = path.read_text(encoding='utf-8')
        else:
            return {"error": "Either content or file_path required"}
        
        # Use CK3 parser for .txt files
        return _validate_syntax(source, filename, trace)
    
    # Python files - use ast.parse
    if content:
        source = content
    elif file_path:
        path = P(file_path)
        if not path.exists():
            return {"valid": False, "error": f"File not found: {file_path}"}
        source = path.read_text(encoding='utf-8')
    else:
        return {"error": "Either content or file_path required"}
    
    try:
        ast.parse(source, filename)
        
        if trace:
            trace.log("mcp.tool", {"filename": filename}, {"valid": True})
        
        return {"valid": True}
    except SyntaxError as e:
        if trace:
            trace.log("mcp.tool", {"filename": filename}, {"valid": False})
        
        return {
            "valid": False,
            "error": str(e),
            "line": e.lineno,
            "column": e.offset,
        }


def _validate_bundle(artifact_bundle, trace):
    """Validate artifact bundle."""
    from ck3lens.validate import validate_artifact_bundle
    from ck3lens.contracts import ArtifactBundle
    
    try:
        bundle = ArtifactBundle.model_validate(artifact_bundle)
        result = validate_artifact_bundle(bundle)
        
        if trace:
            trace.log("mcp.tool", {}, {"valid": result.ok})
        
        return result.model_dump()
    except Exception as e:
        return {"valid": False, "error": str(e)}


def _validate_policy(mode, trace_path, trace_obj):
    """Validate against policy rules.
    
    NOTE: validate_for_mode was deleted in the canonical architecture cleanup.
    This function now returns a placeholder - policy validation has been
    superseded by the enforcement.py single-gate approach.
    """
    # DEPRECATED: The old policy validation system was removed.
    # See docs/CANONICAL_ARCHITECTURE.md - enforcement.py is now THE gate.
    return {
        "error": "Policy validation target is deprecated. Use enforcement.py directly.",
        "hint": "Policy is now enforced at operation time via ck3lens.policy.enforce()",
    }


# =============================================================================
# ck3_vscode - VS Code IPC operations
# =============================================================================

VSCodeCommand = Literal[
    "ping",
    "diagnostics",
    "all_diagnostics",
    "errors_summary",
    "validate_file",
    "open_files",
    "active_file",
    "status"
]


def ck3_vscode_impl(
    command: VSCodeCommand = "status",
    # For diagnostics/validate_file
    path: str | None = None,
    # For all_diagnostics
    severity: str | None = None,
    source: str | None = None,
    limit: int = 50,
    # Dependencies
    trace=None,
) -> dict:
    """
    Unified VS Code IPC operations tool.
    
    Connects to VS Code extension's diagnostics server to access IDE APIs.
    Requires VS Code to be running with CK3 Lens extension active.
    
    Commands:
    
    command=ping           -> Test connection to VS Code
    command=diagnostics    -> Get diagnostics for a file (path required)
    command=all_diagnostics -> Get diagnostics for all files
    command=errors_summary -> Get workspace error summary
    command=validate_file  -> Trigger validation for a file (path required)
    command=open_files     -> List currently open files
    command=active_file    -> Get active file info with diagnostics
    command=status         -> Check IPC server status
    
    Args:
        command: Operation to perform
        path: File path (for diagnostics/validate_file)
        severity: Filter by severity ('error', 'warning', 'info', 'hint')
        source: Filter by source (e.g., 'Pylance', 'CK3 Lens')
        limit: Max files to return for all_diagnostics
    
    Returns:
        Dict with results based on command
    """
    from ck3lens.ipc_client import VSCodeIPCClient, VSCodeIPCError, is_vscode_available
    
    if command == "status":
        available = is_vscode_available()
        return {
            "available": available,
            "message": "VS Code IPC server is running" if available else "VS Code IPC server not available"
        }
    
    try:
        with VSCodeIPCClient() as client:
            if command == "ping":
                result = client.ping()
                if trace:
                    trace.log("mcp.tool", {}, {"ok": True})
                return result
            
            elif command == "diagnostics":
                if not path:
                    return {"error": "path required for diagnostics command"}
                result = client.get_diagnostics(path)
                if trace:
                    trace.log("mcp.tool", {"path": path},
                              {"count": len(result.get("diagnostics", []))})
                return result
            
            elif command == "all_diagnostics":
                result = client.get_all_diagnostics(
                    severity=severity,
                    source=source,
                    limit=limit
                )
                if trace:
                    trace.log("mcp.tool",
                              {"severity": severity, "source": source},
                              {"files": result.get("fileCount", 0)})
                return result
            
            elif command == "errors_summary":
                result = client.get_workspace_errors()
                if trace:
                    trace.log("mcp.tool", {},
                              {"errors": result.get("summary", {}).get("errors", 0)})
                return result
            
            elif command == "validate_file":
                if not path:
                    return {"error": "path required for validate_file command"}
                result = client.validate_file(path)
                if trace:
                    trace.log("mcp.tool", {"path": path},
                              {"count": len(result.get("diagnostics", []))})
                return result
            
            elif command == "open_files":
                result = client.get_open_files()
                if trace:
                    trace.log("mcp.tool", {},
                              {"count": result.get("count", 0)})
                return result
            
            elif command == "active_file":
                result = client.get_active_file()
                if trace:
                    trace.log("mcp.tool", {},
                              {"active": result.get("active", False)})
                return result
            
            return {"error": f"Unknown command: {command}"}
            
    except VSCodeIPCError as e:
        return {
            "error": True,
            "message": str(e),
            "suggestion": "Ensure VS Code is running with CK3 Lens extension active",
            "help": "The VS Code IPC server starts automatically when CK3 Lens extension activates"
        }


# ============================================================================
# ck3_search - Unified Search Tool (v2)
# ============================================================================
# Replaces: ck3_search (old), ck3_grep_raw, ck3_file_search
# Uses: WA2, EN2, CM2 — canonical addressing throughout
#
# Thin entry point:
#   content  -> WA2 resolve -> en2_execute() (geographic, OPERATIONS_MATRIX)
#   symbols/paths/filters/auto/mods -> en2_execute_db() (DATABASE_OPERATIONS_MATRIX)
# ============================================================================

SearchCommand = Literal["auto", "symbols", "content", "paths", "filters", "mods"]


def ck3_search_impl(
    command: SearchCommand = "auto",
    query: str = "",
    # Scope
    path: str | None = None,
    symbol_type: str | None = None,
    # Content search options
    is_regex: bool = False,
    include_pattern: str | None = None,
    # Shared options
    recursive: bool = True,
    limit: int = 50,
    verbose: bool = False,
    definitions_only: bool = False,
    # Mods search options
    search_by: str = "any",
    fuzzy: bool = True,
    # Dependencies (injected, keyword-only)
    *,
    session=None,
    db=None,
    trace=None,
    rb: "ReplyBuilder",
) -> "Reply":
    """
    Unified search across symbols, file content, and file paths (v2).

    Commands:
        auto     -> Symbol search (DB) + file path search (DB). Default.
        symbols  -> Symbol definitions + references from DB.
        content  -> Grep file contents on disk (replaces ck3_grep_raw).
        paths    -> Find files by path pattern (DB).
        filters  -> Return available search filter vocabulary.
        mods     -> Search mods by name or workshop ID (DB).

    Routing:
        content  -> geographic: WA2 resolve -> en2_execute -> content executor
        all else -> database:   en2_execute_db -> DB executor with cvid scoping
    """
    from ck3lens.agent_mode import get_agent_mode
    from ck3lens.logging import info

    mode = get_agent_mode()
    if not mode:
        return rb.invalid("WA-CFG-I-001", {
            "guidance": "Call ck3_get_mode_instructions(mode='ck3lens' or 'ck3raven-dev') to initialize",
        }, message="Agent mode not initialized")

    if not session:
        return rb.invalid("MCP-SYS-I-001", {
            "parameter": "session",
        }, message="No session available")

    info("mcp.tool", "ck3_search called",
         command=command, query=query, path=path, mode=mode)

    if command == "content":
        return _ck3_search_content(
            query=query, path=path, is_regex=is_regex,
            include_pattern=include_pattern, recursive=recursive,
            limit=limit, session=session, mode=mode, rb=rb,
        )
    elif command in ("symbols", "paths", "filters", "auto"):
        return _ck3_search_db(
            command=command, query=query, path=path,
            symbol_type=symbol_type, include_pattern=include_pattern,
            limit=limit, verbose=verbose, definitions_only=definitions_only,
            session=session, db=db, mode=mode, rb=rb,
            fuzzy=fuzzy,
        )
    elif command == "mods":
        return _ck3_search_db(
            command="mods", query=query, path=None,
            symbol_type=None, include_pattern=None,
            limit=limit, verbose=False, definitions_only=False,
            session=session, db=db, mode=mode, rb=rb,
            search_by=search_by, fuzzy=fuzzy,
        )
    else:
        return rb.invalid("MCP-SYS-I-002", {"command": command},
                          message=f"Unknown search command: {command}")


def _ck3_search_content(
    *,
    query: str,
    path: str | None,
    is_regex: bool,
    include_pattern: str | None,
    recursive: bool,
    limit: int,
    session,
    mode: str,
    rb: "ReplyBuilder",
) -> "Reply":
    """Geographic content search: WA2 resolve -> en2_execute -> content executor."""
    from ck3lens.world_adapter_v2 import WorldAdapterV2
    from ck3lens.policy.enforcement_v2 import execute as en2_execute

    if not path:
        return rb.invalid("MCP-SYS-I-001", {"parameter": "path"},
                          message="'path' required for content search (canonical address)")

    # Detect multi-entity list queries — a comma or 3+ space-separated tokens
    # strongly suggests the caller is treating content search like an AND keyword
    # search, which it is not. Emit a query_hint warning so the agent self-corrects.
    import re as _re
    _query_hint: str | None = None
    if "," in query:
        _query_hint = (
            "CONTENT SEARCH QUERY ERROR: query contains commas — this command does "
            "a literal string grep. A comma-separated list will NEVER match anything. "
            "Search for ONE entity at a time."
        )
    else:
        _tokens = [t for t in _re.split(r"\s+", query.strip()) if t]
        _id_like = [t for t in _tokens if "_" in t]
        if len(_tokens) >= 3 or len(_id_like) >= 2:
            _query_hint = (
                "CONTENT SEARCH QUERY ERROR: query looks like a space-separated list "
                f"of {len(_tokens)} tokens — this command does a literal string grep. "
                "A multi-token list will NEVER match anything in a file. "
                "Search for ONE entity at a time, e.g. ck3_search(command='content', query='c_delhi', path=...)."
            )

    if _query_hint:
        return rb.invalid("MCP-SYS-I-001", {
            "parameter": "query",
            "query": query,
            "query_hint": _query_hint,
        }, message=_query_hint)

    wa2 = WorldAdapterV2.create(session=session)
    resolve_reply, ref = wa2.resolve(path, require_exists=True, rb=rb)
    if resolve_reply.is_invalid or ref is None:
        return resolve_reply

    root_key = resolve_reply.data.get("root_key", "")
    subdirectory = resolve_reply.data.get("subdirectory")

    return en2_execute(
        rb, mode=mode, tool="ck3_search", command="content",
        root_key=root_key, subdirectory=subdirectory,
        ref=ref, wa2=wa2,
        params={
            "query": query,
            "is_regex": is_regex,
            "include_pattern": include_pattern,
            "recursive": recursive,
            "limit": limit,
            "session": session,
        },
        session=session,
    )


def _ck3_search_db(
    *,
    command: str,
    query: str,
    path: str | None,
    symbol_type: str | None,
    include_pattern: str | None,
    limit: int,
    verbose: bool,
    definitions_only: bool,
    session,
    db,
    mode: str,
    rb: "ReplyBuilder",
    search_by: str = "any",
    fuzzy: bool = True,
) -> "Reply":
    """Database search: en2_execute_db -> DB executor with session.cvids scoping."""
    from ck3lens.policy.enforcement_v2 import execute_db as en2_execute_db

    return en2_execute_db(
        rb, mode=mode, tool="ck3_search", command=command,
        params={
            "query": query,
            "path": path,
            "symbol_type": symbol_type,
            "include_pattern": include_pattern,
            "limit": limit,
            "verbose": verbose,
            "definitions_only": definitions_only,
            "search_by": search_by,
            "fuzzy": fuzzy,
            "session": session,
            "db": db,
        },
        session=session,
    )
