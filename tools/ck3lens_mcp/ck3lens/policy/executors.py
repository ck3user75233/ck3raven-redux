"""
Executors — operation execution functions dispatched by enforcement_v2.execute().

Each executor receives:
  - rb: ReplyBuilder
  - ref: VisibilityRef — opaque token for host path recovery
  - wa2: WorldAdapterV2 — for token→path resolution
  - **params: operation-specific parameters

Each executor returns:
  - Reply (S/I/E) — always terminal

Canonical Architecture: Executors run INSIDE enforcement boundary.
After EN2 policy check passes, EN2.execute() dispatches to these functions.
Executors extract host paths from VisibilityRef via wa2.host_path(ref).
"""
from __future__ import annotations

import codecs
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ck3raven.core.reply import Reply
    from ck3lens.world_adapter_v2 import VisibilityRef, WorldAdapterV2


# CK3 file types that should default to UTF-8 with BOM when writing new files.
NEEDS_BOM_EXTS = {".txt", ".yml", ".mod", ".gui", ".gfx", ".asset", ".shader"}


def _resolve_write_encoding(
    file_path: Path,
    content: str,
    existing_has_bom: bool | None = None,
) -> tuple[str, str]:
    """Resolve output encoding and normalize incoming leading BOM char.

    Rules:
    - Existing file: preserve current BOM state exactly.
    - New file: default to BOM for CK3 text-like extensions, utf-8 otherwise.
    - Incoming content: strip one leading U+FEFF to prevent double-BOM writes.
    """
    normalized_content = content[1:] if content.startswith("\ufeff") else content

    if existing_has_bom is None:
        if file_path.exists():
            existing_has_bom = file_path.read_bytes().startswith(codecs.BOM_UTF8)
        else:
            existing_has_bom = False

    if file_path.exists():
        return ("utf-8-sig" if existing_has_bom else "utf-8"), normalized_content

    needs_bom = file_path.suffix.lower() in NEEDS_BOM_EXTS
    return ("utf-8-sig" if needs_bom else "utf-8"), normalized_content


def file_edit(
    rb: Any,
    ref: "VisibilityRef",
    wa2: "WorldAdapterV2",
    *,
    old_content: str,
    new_content: str,
    validate_syntax: bool = False,
    trace: Any = None,
) -> "Reply":
    """
    Execute file edit operation.
    
    Terminal replies:
      - EN-WRITE-S-001: Edit applied successfully
      - EN-WRITE-S-002: old_content not found in file
      - EN-PARSE-I-001: File not written because syntax validation failed
    
    Uncaught exceptions bubble to safety.py catch-all → MCP-SYS-E-001.
    """
    from ck3lens.validate import parse_content

    host_paths = wa2.host_path(ref)
    file_path = host_paths[0].resolve()

    raw_bytes = file_path.read_bytes()
    existing_has_bom = raw_bytes.startswith(codecs.BOM_UTF8)
    read_encoding = "utf-8-sig" if existing_has_bom else "utf-8"
    current_content = raw_bytes.decode(read_encoding)

    normalized_old = old_content[1:] if old_content.startswith("\ufeff") else old_content
    normalized_new = new_content[1:] if new_content.startswith("\ufeff") else new_content

    # Normalize both to LF for matching (handles CRLF files where old_content uses LF only)
    _old_lf = normalized_old.replace("\r\n", "\n")
    _cur_lf = current_content.replace("\r\n", "\n")

    # Check old_content exists
    if _old_lf not in _cur_lf:
        preview = _old_lf[:120].replace("\n", "\\n") if _old_lf else ""
        return rb.invalid("EN-WRITE-S-002", {
            "matched": False,
            "reason": "old_content not found in file — NO CHANGES WERE MADE",
            "hint": "Ensure old_content matches exactly (including whitespace and line endings). Read the file first to confirm the exact text.",
            "old_content_preview": preview,
            "file": str(file_path),
        }, message=f"Edit failed: old_content not found in {file_path.name}. NO CHANGES WERE MADE. Read the file to confirm the exact text to match.")

    # Apply edit on LF-normalised content, then restore dominant line ending
    _new_lf = normalized_new.replace("\r\n", "\n")
    _updated_lf = _cur_lf.replace(_old_lf, _new_lf, 1)

    _crlf_count = current_content.count("\r\n")
    _lf_only_count = current_content.count("\n") - _crlf_count
    if _crlf_count > _lf_only_count:
        updated_content = _updated_lf.replace("\n", "\r\n")
    else:
        updated_content = _updated_lf

    # Validate syntax if requested
    if validate_syntax and file_path.suffix.lower() == ".txt":
        parse_result = parse_content(updated_content, str(file_path))
        if not parse_result["success"]:
            return rb.invalid("EN-PARSE-I-001", {
                "path": str(file_path),
                "parse_errors": parse_result["errors"],
                "bytes_blocked": len(updated_content.encode("utf-8")),
                "write_performed": False,
            })

    # Write the file while preserving BOM state
    write_encoding, normalized_content = _resolve_write_encoding(
        file_path,
        updated_content,
        existing_has_bom=existing_has_bom,
    )
    file_path.write_text(normalized_content, encoding=write_encoding, newline="")

    # Track core source change (ck3raven-dev mode)
    from ck3lens.agent_mode import get_agent_mode
    if get_agent_mode() == "ck3raven-dev":
        from ck3lens.policy.wip_workspace import record_core_source_change
        from ck3lens.policy.contract_v1 import get_active_contract
        contract = get_active_contract()
        if contract:
            path_str = str(file_path).replace("\\", "/").lower()
            if ".wip/" not in path_str:
                record_core_source_change(contract.contract_id)

    if trace:
        trace.log("mcp.tool", {"path": str(file_path)}, {"success": True})

    return rb.success("EN-WRITE-S-001", {
        "path": str(file_path),
        "bytes_written": len(updated_content.encode("utf-8")),
    })


def file_delete(
    rb: Any,
    ref: "VisibilityRef",
    wa2: "WorldAdapterV2",
    *,
    trace: Any = None,
) -> "Reply":
    """
    Execute file delete operation.
    
    Terminal replies:
      - EN-WRITE-S-001: Success (delete is a write operation)
    
    Uncaught exceptions bubble to safety.py catch-all → MCP-SYS-E-001.
    """
    host_paths = wa2.host_path(ref)
    file_path = host_paths[0].resolve()

    file_path.unlink()

    if trace:
        trace.log("mcp.tool", {"path": str(file_path)}, {"deleted": True})

    return rb.success("EN-WRITE-S-001", {
        "path": str(file_path),
        "deleted": True,
    })


def file_write(
    rb: Any,
    ref: "VisibilityRef",
    wa2: "WorldAdapterV2",
    *,
    content: str,
    validate_syntax: bool = False,
    trace: Any = None,
) -> "Reply":
    """
    Execute file write operation.
    
    Terminal replies:
      - EN-WRITE-S-001: Success
      - EN-PARSE-I-001: File not written because syntax validation failed
    
    Uncaught exceptions bubble to safety.py catch-all → MCP-SYS-E-001.
    """
    host_paths = wa2.host_path(ref)
    file_path = host_paths[0].resolve()

    file_path.parent.mkdir(parents=True, exist_ok=True)
    write_encoding, normalized_content = _resolve_write_encoding(file_path, content)

    # Validate syntax if requested
    if validate_syntax and file_path.suffix.lower() == ".txt":
        from ck3lens.validate import parse_content
        parse_result = parse_content(normalized_content, str(file_path))
        if not parse_result["success"]:
            return rb.invalid("EN-PARSE-I-001", {
                "path": str(file_path),
                "parse_errors": parse_result["errors"],
                "bytes_blocked": len(normalized_content.encode("utf-8")),
                "write_performed": False,
            })

    file_path.write_text(normalized_content, encoding=write_encoding, newline="")

    # Track core source change (ck3raven-dev mode)
    from ck3lens.agent_mode import get_agent_mode
    if get_agent_mode() == "ck3raven-dev":
        from ck3lens.policy.wip_workspace import record_core_source_change
        from ck3lens.policy.contract_v1 import get_active_contract
        contract = get_active_contract()
        if contract:
            path_str = str(file_path).replace("\\", "/").lower()
            if ".wip/" not in path_str:
                record_core_source_change(contract.contract_id)

    if trace:
        trace.log("mcp.tool", {"path": str(file_path)}, {"success": True})

    return rb.success("EN-WRITE-S-001", {
        "path": str(file_path),
        "bytes_written": len(normalized_content.encode("utf-8")),
    })


def file_read(
    rb: Any,
    ref: "VisibilityRef",
    wa2: "WorldAdapterV2",
    *,
    start_line: int = 1,
    end_line: int | None = None,
    trace: Any = None,
) -> "Reply":
    """
    Execute file read operation.
    
    Terminal replies:
      - EN-READ-S-001: Success
      - EN-READ-S-002: No file at path (directory, etc.)
    
    Uncaught exceptions bubble to safety.py catch-all → MCP-SYS-E-001.
    """
    host_paths = wa2.host_path(ref)
    file_path = host_paths[0].resolve()

    if not file_path.is_file():
        return rb.success("EN-READ-S-002", {
            "content": "",
            "lines_read": 0,
            "total_lines": 0,
        })

    content = file_path.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines(keepends=True)

    start_idx = max(0, start_line - 1)
    end_idx = end_line if end_line else len(lines)
    selected = lines[start_idx:end_idx]

    if trace:
        trace.log("mcp.tool", {"path": str(file_path)}, {"lines_read": len(selected)})

    data = {
        "content": "".join(selected),
        "lines_read": len(selected),
        "total_lines": len(lines),
        "start_line": start_line,
        "end_line": end_idx,
    }

    # Context-budget warning for large reads
    content_bytes = len(data["content"].encode("utf-8"))
    if content_bytes > 16384:
        data["context_warning"] = (
            f"This read returned ~{content_bytes // 1024}KB. "
            "Consider using start_line/end_line to read narrower "
            "ranges and keep your context budget healthy."
        )

    return rb.success("EN-READ-S-001", data)


def file_rename(
    rb: Any,
    ref: "VisibilityRef",
    wa2: "WorldAdapterV2",
    *,
    new_host_path: Path,
    trace: Any = None,
) -> "Reply":
    """
    Execute file rename/move operation.
    
    Receives opaque VisibilityRef (source), extracts host path at runtime.
    new_host_path is dest (TODO: multi-ref support for dest too).
    
    Terminal replies:
      - EN-WRITE-S-001: Success
      - EN-WRITE-S-002: Destination already exists
    
    Uncaught exceptions bubble to safety.py catch-all → MCP-SYS-E-001.
    """
    host_paths = wa2.host_path(ref)
    file_path = host_paths[0].resolve()
    old_file = file_path
    new_file = new_host_path.resolve()

    # Pre-condition: destination must not exist
    if new_file.exists():
        return rb.success("EN-WRITE-S-002", {
            "matched": False,
            "reason": "Destination already exists",
        })

    new_file.parent.mkdir(parents=True, exist_ok=True)
    old_file.rename(new_file)

    if trace:
        trace.log("mcp.tool", {"old_path": str(old_file), "new_path": str(new_file)}, {"renamed": True})

    return rb.success("EN-WRITE-S-001", {
        "renamed": True,
    })



# ============================================================================
# Search executors — dispatched by execute_db() (DB) or execute() (geographic)
# ============================================================================



def _parse_search_terms(query: str) -> list[str]:
    """Parse query into terms. Quoted strings = exact phrase, spaces = AND."""
    import re
    terms: list[str] = []
    for match in re.finditer(r'"([^"]+)"', query):
        terms.append(match.group(1))
    remaining = re.sub(r'"[^"]*"', "", query)
    for word in remaining.split():
        word = word.strip()
        if word:
            terms.append(word)
    return terms if terms else [query]


def search_symbols(
    rb: Any,
    *,
    policy_ctx: dict,
    query: str = "",
    path: str | None = None,
    symbol_type: str | None = None,
    limit: int = 50,
    verbose: bool = False,
    definitions_only: bool = False,
    fuzzy: bool = True,
    session: Any = None,
    db: Any = None,
    **_: Any,
) -> "Reply":
    """
    Symbol search via DB (Golden Join: symbols -> asts -> files).

    When fuzzy=True (default), if exact search returns no results,
    performs fuzzy matching to find near-miss symbols (typos, spelling variants).

    Terminal replies:
      - WA-READ-S-001: Success
    """
    import re

    if not query:
        return rb.invalid("MCP-SYS-I-001", {"parameter": "query"},
                          message="'query' required for symbols search")
    if not db:
        return rb.invalid("MCP-SYS-I-001", {"parameter": "db"},
                          message="Database not available")

    cvids = frozenset(
        mod.cvid for mod in session.mods if mod.cvid is not None
    ) or None

    # Convert canonical path to SQL LIKE file_pattern
    file_pattern: str | None = None
    if path:
        # Extract relative path from canonical address for SQL filtering
        # e.g. "root:game/common/traits" -> "common/traits/%"
        parts = path.split("/", 1)
        if len(parts) > 1:
            file_pattern = f"{parts[1]}/%"

    include_references = not definitions_only

    result = db._unified_search_internal(
        query=query,
        file_pattern=file_pattern,
        symbol_type=symbol_type,
        adjacency="auto",
        limit=limit,
        matches_per_file=5 if not verbose else 50,
        include_references=include_references,
        verbose=verbose,
        cvids=cvids,
    )

    refs_by_mod = result.get("symbols", {}).get("references_by_mod", {})
    total_refs = sum(len(v) for v in refs_by_mod.values())
    sym_count = result.get("symbols", {}).get("count", 0)
    content_count = result.get("content", {}).get("count", 0)
    truncated = total_refs >= limit or sym_count >= limit or content_count >= limit

    # Fuzzy matching when no exact results
    near_matches: list = []
    if fuzzy and sym_count == 0 and total_refs == 0:
        from ck3lens.fuzzy import fuzzy_search_symbols, build_search_summary
        near_matches = fuzzy_search_symbols(
            db=db,
            query=query,
            cvids=cvids,
            threshold=0.8,
            max_results=5,
        )
        summary = build_search_summary(query, sym_count, near_matches)
    else:
        summary = (
            f"Symbols: {sym_count} definitions, {total_refs} references, "
            f"{content_count} content files."
        )

    data = {
        "symbols": result.get("symbols", {}),
        "content": result.get("content", {}),
        "files": result.get("files", {}),
        "truncated": truncated,
    }
    if near_matches:
        data["near_matches"] = near_matches

    return rb.success("WA-READ-S-001", data=data, message=summary)


def search_content(
    rb: Any,
    ref: "VisibilityRef",
    wa2: "WorldAdapterV2",
    *,
    query: str = "",
    is_regex: bool = False,
    include_pattern: str | None = None,
    recursive: bool = True,
    limit: int = 50,
    **_: Any,
) -> "Reply":
    """
    Grep file contents on disk. Replaces ck3_grep_raw.

    Terminal replies:
      - WA-READ-S-001: Success
    """
    import re
    import time

    search_paths = wa2.host_path(ref)  # Extract host paths from VisibilityRef

    if not query:
        return rb.invalid("MCP-SYS-I-001", {"parameter": "query"},
                          message="'query' required for content search")

    start_time = time.monotonic()
    glob_pattern = include_pattern or "*"

    # Fast path: use ripgrep when available.
    import shutil
    import subprocess

    rg_path = shutil.which("rg")
    if rg_path:
        # Build search targets as absolute paths for ripgrep
        rg_targets = [str(p) for p in search_paths if p.exists()]
        if not rg_targets:
            return rb.success("WA-READ-S-001", data={
                "matches": [], "files_scanned": 0, "match_count": 0,
                "truncated": False, "elapsed_seconds": 0.0, "backend": "rg",
            }, message="Content search: 0 matches (no valid search paths).")

        command = [
            rg_path,
            "--line-number",
            "--no-heading",
            "--color",
            "never",
            "--no-messages",
            "--path-separator",
            "/",
        ]
        if not is_regex:
            command.append("--fixed-strings")
        command.append("--ignore-case")
        if include_pattern:
            command.extend(["--glob", include_pattern])
        if not recursive:
            command.extend(["--max-depth", "1"])
        command.extend([query] + rg_targets)

        try:
            rg_result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=90,  # Allow up to 90s for large content searches
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            rg_result = None
        else:
            if rg_result.returncode in (0, 1):
                matches: list[dict] = []
                matched_files: set[str] = set()
                truncated = False
                for raw_line in rg_result.stdout.splitlines():
                    parts = raw_line.split(":", 2)
                    if len(parts) != 3:
                        continue
                    rel_path, line_num, content = parts
                    rel_path = rel_path.removeprefix("./").replace("\\", "/")
                    matched_files.add(rel_path)
                    matches.append({
                        "path": rel_path,
                        "line": int(line_num),
                        "content": content[:200],
                    })
                    if len(matches) >= limit:
                        truncated = True
                        break

                elapsed = round(time.monotonic() - start_time, 2)
                return rb.success("WA-READ-S-001", data={
                    "matches": matches,
                    "files_scanned": len(matched_files),
                    "match_count": len(matches),
                    "truncated": truncated,
                    "elapsed_seconds": elapsed,
                    "backend": "rg",
                }, message=f"Content search: {len(matches)} matches in {len(matched_files)} files ({elapsed}s).")

    # Fallback: bounded Python scan when rg is unavailable.
    if is_regex:
        try:
            compiled = re.compile(query, re.IGNORECASE)
        except re.error as e:
            return rb.invalid("MCP-SYS-I-001",
                              {"query": query, "regex_error": str(e)},
                              message=f"Invalid regex: {e}")

        def _match(line: str) -> bool:
            return compiled.search(line) is not None
    else:
        terms_lower = [t.lower() for t in _parse_search_terms(query)]

        def _match(line: str) -> bool:  # type: ignore[misc]
            ll = line.lower()
            return all(t in ll for t in terms_lower)

    # Iterate all search_paths unconditionally
    def _all_files():
        for sp in search_paths:
            if sp.is_file():
                yield sp
            elif sp.is_dir():
                if recursive:
                    yield from sp.rglob(glob_pattern)
                else:
                    yield from sp.glob(glob_pattern)

    matches = []
    files_scanned = 0
    time_budget = 90.0  # Allow up to 90s for large content searches
    timed_out = False

    for file_path in _all_files():
        if not file_path.is_file():
            continue

        files_scanned += 1
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                for line_num, line in enumerate(f, 1):
                    if _match(line):
                        rel = str(file_path).replace("\\", "/")
                        matches.append({
                            "path": rel,
                            "line": line_num,
                            "content": line.rstrip()[:200],
                        })
                        if len(matches) >= limit:
                            break
        except (OSError, UnicodeDecodeError):
            continue

        if len(matches) >= limit:
            break
        if files_scanned % 200 == 0 and (time.monotonic() - start_time) >= time_budget:
            timed_out = True
            break

    elapsed = round(time.monotonic() - start_time, 2)

    return rb.success("WA-READ-S-001", data={
        "matches": matches,
        "files_scanned": files_scanned,
        "match_count": len(matches),
        "truncated": len(matches) >= limit or timed_out,
        "elapsed_seconds": elapsed,
        "backend": "python",
    }, message=f"Content search: {len(matches)} matches in {files_scanned} files ({elapsed}s).")


def search_paths(
    rb: Any,
    *,
    policy_ctx: dict,
    query: str = "",
    include_pattern: str | None = None,
    limit: int = 50,
    session: Any = None,
    db: Any = None,
    **_: Any,
) -> "Reply":
    """
    Find files by path pattern in DB.

    DB executor — receives policy_ctx with cvids from execute_db().

    Terminal replies:
      - WA-READ-S-001: Success
    """
    import re

    if not db:
        return rb.invalid("MCP-SYS-I-001", {"parameter": "db"},
                          message="Database not available")

    cvids = frozenset(
        mod.cvid for mod in session.mods if mod.cvid is not None
    ) or None

    if not query:
        return rb.invalid("MCP-SYS-I-001", {"parameter": "query"},
                          message="'query' required for paths search")

    normalized = re.sub(r"[-_.:]+", "%", query)
    file_pattern = f"%{normalized}%"

    db_files = db._search_files_internal(
        file_pattern,
        cvids=cvids,
        limit=limit,
    )

    results = [{
        "path": f["relpath"],
        "mod": f.get("source_name"),
        "size": f.get("size"),
        "source": "db",
    } for f in db_files]

    return rb.success("WA-READ-S-001", data={
        "files": results,
        "count": len(results),
        "truncated": len(results) >= limit,
    }, message=f"Found {len(results)} files from DB.")


def search_filters(
    rb: Any,
    *,
    policy_ctx: dict,
    session: Any = None,
    db: Any = None,
    **_: Any,
) -> "Reply":
    """
    Return available search filter vocabulary.

    DB executor — receives policy_ctx with cvids from execute_db().

    Terminal replies:
      - WA-READ-S-001: Success
    """
    if not db:
        return rb.invalid("MCP-SYS-I-001", {"parameter": "db"},
                          message="Database not available")

    cvids = frozenset(
        mod.cvid for mod in session.mods if mod.cvid is not None
    ) or None

    cv_filter = ""
    cv_params: list[str] = []
    if cvids:
        cv_params = sorted(cvids)
        placeholders = ",".join("?" for _ in cv_params)
        cv_filter = f" AND f.content_version_id IN ({placeholders})"

    # Symbol types
    sym_rows = db.conn.execute(f"""
        SELECT DISTINCT s.symbol_type, COUNT(*) as cnt
        FROM symbols s
        JOIN asts a ON s.ast_id = a.ast_id
        JOIN files f ON f.content_hash = a.content_hash
        WHERE 1=1 {cv_filter}
        GROUP BY s.symbol_type
        ORDER BY cnt DESC
    """, cv_params).fetchall()

    symbol_types = [{"type": r["symbol_type"], "count": r["cnt"]}
                    for r in sym_rows]

    # Game folders (top-level)
    folder_rows = db.conn.execute(f"""
        SELECT
            CASE
                WHEN INSTR(f.relpath, '/') > 0
                THEN SUBSTR(f.relpath, 1, INSTR(f.relpath, '/') - 1)
                ELSE f.relpath
            END as top_folder,
            COUNT(*) as cnt
        FROM files f
        WHERE f.deleted = 0 {cv_filter}
        GROUP BY top_folder
        HAVING cnt >= 5
        ORDER BY cnt DESC
        LIMIT 50
    """, cv_params).fetchall()

    game_folders = [{"folder": r["top_folder"], "file_count": r["cnt"]}
                    for r in folder_rows]

    # Mods from session
    mods = [{"name": m.name, "cvid": m.cvid, "indexed": m.cvid is not None}
            for m in session.mods] if session else []

    return rb.success("WA-READ-S-001", data={
        "symbol_types": symbol_types,
        "game_folders": game_folders,
        "mods": mods,
    }, message=(
        f"Filters: {len(symbol_types)} symbol types, "
        f"{len(game_folders)} folders, {len(mods)} mods."
    ))


def search_auto(
    rb: Any,
    *,
    policy_ctx: dict,
    query: str = "",
    path: str | None = None,
    symbol_type: str | None = None,
    limit: int = 50,
    verbose: bool = False,
    definitions_only: bool = False,
    fuzzy: bool = True,
    session: Any = None,
    db: Any = None,
    **_: Any,
) -> "Reply":
    """
    Auto search: symbols, content, and file paths from unified DB search.

    When fuzzy=True (default), if exact search returns no results,
    performs fuzzy matching to find near-miss symbols (typos, spelling variants).

    DB executor — receives policy_ctx with cvids from execute_db().

    Terminal replies:
      - WA-READ-S-001: Success
    """
    if not query:
        return rb.invalid("MCP-SYS-I-001", {"parameter": "query"},
                          message="'query' required for search")

    cvids = frozenset(
        mod.cvid for mod in session.mods if mod.cvid is not None
    ) or None
    result: dict = {"query": query}

    if db:
        # Convert path to file_pattern for SQL
        file_pattern: str | None = None
        if path:
            parts = path.split("/", 1)
            if len(parts) > 1:
                file_pattern = f"{parts[1]}/%"

        include_references = not definitions_only
        sym_result = db._unified_search_internal(
            query=query,
            file_pattern=file_pattern,
            symbol_type=symbol_type,
            adjacency="auto",
            limit=limit,
            matches_per_file=5 if not verbose else 50,
            include_references=include_references,
            verbose=verbose,
            cvids=cvids,
        )
        result["symbols"] = sym_result.get("symbols", {})
        result["content"] = sym_result.get("content", {})
        result["files"] = sym_result.get("files", {})

    refs_by_mod = result.get("symbols", {}).get("references_by_mod", {})
    total_refs = sum(len(v) for v in refs_by_mod.values())
    sym_count = result.get("symbols", {}).get("count", 0)
    content_count = result.get("content", {}).get("count", 0)
    file_count = result.get("files", {}).get("count", 0)

    result["truncated"] = (
        total_refs >= limit
        or sym_count >= limit
        or content_count >= limit
        or file_count >= limit
    )

    total_results = sym_count + total_refs + content_count + file_count

    # Fuzzy matching when no exact results
    if total_results == 0:
        if fuzzy and db:
            from ck3lens.fuzzy import fuzzy_search_symbols, build_search_summary
            near_matches = fuzzy_search_symbols(
                db=db,
                query=query,
                cvids=cvids,
                threshold=0.8,
                max_results=5,
            )
            if near_matches:
                result["near_matches"] = near_matches
                summary = build_search_summary(query, sym_count, near_matches)
                return rb.success("WA-READ-S-001", data=result, message=summary)

        # Fall back to hint engine if no fuzzy matches
        try:
            from ck3lens.hints import get_hint_engine
            hints = get_hint_engine().for_empty_search(query, search_type="unified")
            result.update(hints)
        except Exception:
            pass

    return rb.success("WA-READ-S-001", data=result,
        message=(
            f"Search: {sym_count} symbols, {total_refs} refs, "
            f"{content_count} content files, {file_count} files."
        ))


def search_mods(
    rb: Any,
    *,
    policy_ctx: dict,
    query: str = "",
    search_by: str = "any",
    fuzzy: bool = True,
    limit: int = 20,
    session: Any = None,
    db: Any = None,
    **_: Any,
) -> "Reply":
    """
    Search for mods by name, workshop ID, or both.

    Supports fuzzy matching for name searches (handles abbreviations
    like "EPE" for "Ethnicities and Portraits Expanded").

    DB executor — dispatched via execute_db().

    Reply result fields per mod:
      - session_path: root:key/rel session-absolute path (infrastructure addressing)
      - mod: mod:<name> canonical mod address (None for non-mod content like vanilla)

    Terminal replies:
      - WA-READ-S-001: Success
    """
    if not query:
        return rb.invalid("MCP-SYS-I-001", {"parameter": "query"},
                          message="'query' required for mods search")
    if not db:
        return rb.invalid("MCP-SYS-I-001", {"parameter": "db"},
                          message="Database not available")

    results: list[dict] = []

    from ck3lens.world_adapter_v2 import WorldAdapterV2
    wa2 = WorldAdapterV2.create(session=session)
    base_source_path = session.mods[0].path if session and session.mods else None

    def is_mod_source(source_path: str | None) -> bool:
        return source_path is None or base_source_path is None or Path(source_path) != base_source_path

    if search_by in ("workshop_id", "any") and query.isdigit():
        rows = db.conn.execute("""
            SELECT cv.content_version_id, cv.name, cv.workshop_id,
                   cv.source_path, cv.file_count
            FROM content_versions cv
            WHERE cv.workshop_id = ?
            ORDER BY cv.ingested_at DESC
        """, (query,)).fetchall()
        for r in rows:
            name = r[1]
            is_mod = is_mod_source(r[3])
            results.append({
                "content_version_id": r[0], "name": name, "workshop_id": r[2],
                "session_path": wa2.host_to_session_abs(r[3]),
                "mod": f"mod:{name}" if is_mod else None,
                "file_count": r[4],
                "match_type": "exact_id",
            })

    if search_by in ("name", "any"):
        seen_ids = {r["content_version_id"] for r in results}

        # Pass 1: exact name match — must be exhausted before partial patterns fire.
        exact_rows = db.conn.execute("""
            SELECT cv.content_version_id, cv.name, cv.workshop_id,
                   cv.source_path, cv.file_count
            FROM content_versions cv
            WHERE LOWER(cv.name) = LOWER(?)
            ORDER BY cv.ingested_at DESC
            LIMIT ?
        """, (query, limit)).fetchall()
        for r in exact_rows:
            if r[0] not in seen_ids:
                seen_ids.add(r[0])
                name = r[1]
                is_mod = is_mod_source(r[3])
                results.append({
                    "content_version_id": r[0], "name": name, "workshop_id": r[2],
                    "session_path": wa2.host_to_session_abs(r[3]),
                    "mod": f"mod:{name}" if is_mod else None,
                    "file_count": r[4],
                    "match_type": "exact_name",
                })

        # Pass 2: partial/near-match patterns — only when exact name produced nothing.
        if not exact_rows:
            patterns = [
                (f"%{query}%", "contains"),
            ]

            if fuzzy:
                if query.isupper() and len(query) <= 5:
                    abbrev_pattern = "%".join(query) + "%"
                    patterns.append((abbrev_pattern, "abbreviation"))

                tokens = query.replace("_", " ").split()
                if len(tokens) > 1:
                    token_pattern = "%".join(tokens)
                    patterns.append((f"%{token_pattern}%", "tokens"))

            for pattern, match_type in patterns:
                rows = db.conn.execute("""
                    SELECT cv.content_version_id, cv.name, cv.workshop_id,
                           cv.source_path, cv.file_count
                    FROM content_versions cv
                    WHERE LOWER(cv.name) LIKE LOWER(?)
                    ORDER BY cv.ingested_at DESC
                    LIMIT ?
                """, (pattern, limit)).fetchall()

                for r in rows:
                    if r[0] not in seen_ids:
                        seen_ids.add(r[0])
                        name = r[1]
                        is_mod = is_mod_source(r[3])
                        results.append({
                            "content_version_id": r[0], "name": name, "workshop_id": r[2],
                            "session_path": wa2.host_to_session_abs(r[3]),
                            "mod": f"mod:{name}" if is_mod else None,
                            "file_count": r[4],
                            "match_type": match_type,
                        })

    trimmed = results[:limit]
    return rb.success("WA-READ-S-001", data={
        "results": trimmed, "query": query, "count": len(trimmed),
    }, message=f"Found {len(trimmed)} mods matching '{query}'.")


# ============================================================================
# Validation executors — dispatched by execute_db()
# ============================================================================


def validate_references(
    rb: Any,
    *,
    policy_ctx: dict,
    symbol_name: str,
    symbol_type: str | None = None,
    session: Any = None,
    db: Any = None,
    **_: Any,
) -> "Reply":
    """
    Validate that a symbol exists within the active DB scope.

    DB executor — receives policy_ctx with mode-aware scoping from execute_db().
    Reply ownership stays with the MCP tool contract. DATABASE_OPERATIONS_MATRIX
    only governs whether this lookup is allowed and how it is scoped.

    Terminal replies:
      - MCP-VAL-S-001: Symbol found
      - MCP-VAL-S-002: Symbol not found
      - MCP-VAL-E-001: Database lookup failed unexpectedly
    """
    if not db:
        return rb.invalid("MCP-SYS-I-001", {"parameter": "db"})
    if not symbol_name:
        return rb.invalid("MCP-SYS-I-001", {"parameter": "symbol_name"})

    cvids = frozenset(
        mod.cvid for mod in session.mods if mod.cvid is not None
    ) or None
    cv_filter, cv_params = db._cv_filter_sql(cvids, "f.content_version_id")

    conditions = ["s.name = ?"]
    params: list[Any] = [symbol_name]
    if symbol_type:
        conditions.append("s.symbol_type = ?")
        params.append(symbol_type)
    params.extend(cv_params)

    try:
        rows = db.conn.execute(f"""
            SELECT s.symbol_id, s.name, s.symbol_type, s.line_number,
                   f.relpath, cv.name as mod_name
            FROM symbols s
            JOIN asts a ON s.ast_id = a.ast_id
            JOIN files f ON f.content_hash = a.content_hash
            JOIN content_versions cv ON f.content_version_id = cv.content_version_id
            WHERE {" AND ".join(conditions)}
            {cv_filter}
            ORDER BY cv.name, f.relpath, s.line_number
        """, params).fetchall()
    except Exception as exc:
        return rb.error("MCP-VAL-E-001", {
            "symbol_name": symbol_name,
            "symbol_type": symbol_type,
            "error": str(exc),
        })

    definitions = [{
        "file": row["relpath"],
        "line": row["line_number"],
        "type": row["symbol_type"],
        "mod": row["mod_name"],
    } for row in rows]

    data = {
        "valid": bool(definitions),
        "symbol_name": symbol_name,
        "symbol_type": symbol_type,
        "definitions": definitions,
        "match_count": len(definitions),
    }

    if not definitions:
        return rb.success("MCP-VAL-S-002", data)

    return rb.success("MCP-VAL-S-001", data)


# Conflict executors — dispatched by execute_db()
# ============================================================================


def _load_cvid_file_record(conn: Any, cvid: str, relpath: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT f.relpath, f.content_hash,
               fc.content_text, fc.content_blob
        FROM files f
        JOIN file_contents fc ON f.content_hash = fc.content_hash
        WHERE f.content_version_id = ? AND f.relpath = ?
        LIMIT 1
        """,
        (cvid, relpath),
    ).fetchone()
    if not row:
        return None

    text = row["content_text"]
    if text is None:
        blob = row["content_blob"]
        if blob is None:
            text = ""
        else:
            try:
                text = blob.decode("utf-8")
            except UnicodeDecodeError:
                text = blob.decode("latin-1", errors="replace")

    return {
        "relpath": row["relpath"],
        "content_hash": row["content_hash"],
        "content": text,
    }


def _cvid_exists(conn: Any, cvid: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM content_versions WHERE content_version_id = ? LIMIT 1",
        (cvid,),
    ).fetchone()
    return row is not None


def conflict_diff(
    rb: Any,
    *,
    policy_ctx: dict,
    relpath: str | None = None,
    old_cvid: str | None = None,
    new_cvid: str | None = None,
    diff_mode: str = "both",
    output: str = "summary",
    normalize_indent: bool = True,
    context_lines: int = 4,
    output_dir: str | None = None,
    db: Any = None,
    **_: Any,
) -> "Reply":
    """Diff one file between two CVIDs (line and/or AST output)."""
    if not db:
        return rb.invalid("MCP-SYS-I-001", {"parameter": "db"})
    if not relpath:
        return rb.invalid("MCP-SYS-I-001", {"parameter": "relpath"})
    if old_cvid is None:
        return rb.invalid("MCP-SYS-I-001", {"parameter": "old_cvid"})
    if new_cvid is None:
        return rb.invalid("MCP-SYS-I-001", {"parameter": "new_cvid"})
    if diff_mode not in {"lines", "ast", "both"}:
        return rb.invalid("MCP-SYS-I-001", {"parameter": "diff_mode", "allowed": ["lines", "ast", "both"]})
    if output not in {"summary", "artifact", "html"}:
        return rb.invalid("MCP-SYS-I-001", {"parameter": "output", "allowed": ["summary", "artifact", "html"]})

    if not _cvid_exists(db.conn, old_cvid):
        return rb.invalid("MCP-SYS-I-001", {"parameter": "old_cvid", "value": old_cvid, "reason": "content_version_id not found"})
    if not _cvid_exists(db.conn, new_cvid):
        return rb.invalid("MCP-SYS-I-001", {"parameter": "new_cvid", "value": new_cvid, "reason": "content_version_id not found"})

    old_rec = _load_cvid_file_record(db.conn, old_cvid, relpath)
    new_rec = _load_cvid_file_record(db.conn, new_cvid, relpath)
    if old_rec is None or new_rec is None:
        return rb.invalid("WA-READ-I-001", {
            "relpath": relpath,
            "old_found": old_rec is not None,
            "new_found": new_rec is not None,
            "old_cvid": old_cvid,
            "new_cvid": new_cvid,
        })

    from ck3lens.impl.diff_formatters import render_line_diff_html
    from ck3lens.impl.diff_ops import DiffConfig, build_ast_diff, build_line_diff_artifact
    from ck3lens.impl.diff_reports import write_diff_report_bundle

    artifact: dict[str, Any] = {
        "relpath": relpath,
        "old_cvid": old_cvid,
        "new_cvid": new_cvid,
        "diff_mode": diff_mode,
    }

    line_artifact = None
    if diff_mode in {"lines", "both"}:
        line_artifact = build_line_diff_artifact(
            relpath,
            old_rec["content"],
            new_rec["content"],
            cfg=DiffConfig(
                normalize_indent=normalize_indent,
                tab_width=4,
                context_lines=context_lines,
            ),
            old_cvid=old_cvid,
            new_cvid=new_cvid,
            old_hash=old_rec["content_hash"],
            new_hash=new_rec["content_hash"],
        )
        artifact["line_diff"] = line_artifact

    ast_artifact = None
    if diff_mode in {"ast", "both"}:
        ast_artifact = build_ast_diff(
            relpath,
            old_rec["content"],
            new_rec["content"],
            old_cvid=old_cvid,
            new_cvid=new_cvid,
            conn=db.conn,
            old_hash=old_rec["content_hash"],
            new_hash=new_rec["content_hash"],
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

    summary = {
        "relpath": relpath,
        "old_cvid": old_cvid,
        "new_cvid": new_cvid,
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


def conflict_diff_index(
    rb: Any,
    *,
    policy_ctx: dict,
    path: str | None = None,
    old_cvid: str | None = None,
    new_cvid: str | None = None,
    diff_mode: str = "lines",
    normalize_indent: bool = True,
    context_lines: int = 4,
    limit: int = 100,
    db: Any = None,
    **_: Any,
) -> "Reply":
    """Build a file-level diff index for a path prefix between two CVIDs."""
    if not db:
        return rb.invalid("MCP-SYS-I-001", {"parameter": "db"})
    if old_cvid is None:
        return rb.invalid("MCP-SYS-I-001", {"parameter": "old_cvid"})
    if new_cvid is None:
        return rb.invalid("MCP-SYS-I-001", {"parameter": "new_cvid"})
    if diff_mode not in {"lines", "ast", "both"}:
        return rb.invalid("MCP-SYS-I-001", {"parameter": "diff_mode", "allowed": ["lines", "ast", "both"]})

    prefix = (path or "").rstrip("/")
    like_clause = f"{prefix}/%" if prefix else "%"

    old_rows = db.conn.execute(
        "SELECT relpath, content_hash FROM files WHERE content_version_id = ? AND relpath LIKE ?",
        (old_cvid, like_clause),
    ).fetchall()
    new_rows = db.conn.execute(
        "SELECT relpath, content_hash FROM files WHERE content_version_id = ? AND relpath LIKE ?",
        (new_cvid, like_clause),
    ).fetchall()

    old_map = {r["relpath"]: r["content_hash"] for r in old_rows}
    new_map = {r["relpath"]: r["content_hash"] for r in new_rows}

    relpaths = sorted(set(old_map.keys()) | set(new_map.keys()))
    changed = [p for p in relpaths if old_map.get(p) != new_map.get(p)]

    from ck3lens.impl.diff_ops import DiffConfig, build_ast_diff, build_line_diff_artifact

    entries: list[dict[str, Any]] = []
    for relpath_item in changed[: max(1, limit)]:
        old_hash = old_map.get(relpath_item)
        new_hash = new_map.get(relpath_item)

        row_old = _load_cvid_file_record(db.conn, old_cvid, relpath_item) if old_hash else None
        row_new = _load_cvid_file_record(db.conn, new_cvid, relpath_item) if new_hash else None

        if row_old is None or row_new is None:
            entries.append({
                "relpath": relpath_item,
                "old_present": row_old is not None,
                "new_present": row_new is not None,
            })
            continue

        item: dict[str, Any] = {"relpath": relpath_item}

        if diff_mode in {"lines", "both"}:
            line_art = build_line_diff_artifact(
                relpath_item,
                row_old["content"],
                row_new["content"],
                cfg=DiffConfig(
                    normalize_indent=normalize_indent,
                    tab_width=4,
                    context_lines=context_lines,
                ),
                old_cvid=old_cvid,
                new_cvid=new_cvid,
                old_hash=row_old["content_hash"],
                new_hash=row_new["content_hash"],
            )
            item["line_stats"] = line_art.get("stats", {})
            item["equivalence"] = line_art.get("equivalence", {})

        if diff_mode in {"ast", "both"}:
            ast_art = build_ast_diff(
                relpath_item,
                row_old["content"],
                row_new["content"],
                old_cvid=old_cvid,
                new_cvid=new_cvid,
            )
            item["ast_change_count"] = ast_art.get("change_count", 0)
            item["ast_parse_ok"] = ast_art.get("parse_ok", False)
            if ast_art.get("warnings"):
                item["warnings"] = ast_art.get("warnings")

        entries.append(item)

    data = {
        "path_prefix": prefix,
        "old_cvid": old_cvid,
        "new_cvid": new_cvid,
        "diff_mode": diff_mode,
        "total_changed": len(changed),
        "returned": len(entries),
        "entries": entries,
    }
    return rb.success("EN-READ-S-001", data=data)


def conflict_symbols(
    rb: Any,
    *,
    policy_ctx: dict,
    symbol_type: str | None = None,
    symbol_names: list[str] | None = None,
    path: str | None = None,
    limit: int = 100,
    session: Any = None,
    db: Any = None,
    **_: Any,
) -> "Reply":
    """
    Symbol-level conflict detection via DB (Golden Join).

    Finds symbols defined in multiple mods within the active playset.

    DB executor — receives policy_ctx from execute_db().

    Terminal replies:
      - WA-READ-S-001: Success
    """
    if not db:
        return rb.invalid("MCP-SYS-I-001", {"parameter": "db"},
                          message="Database not available")

    if not session or not getattr(session, 'mods', None):
        return rb.invalid("MCP-SYS-I-001", {"parameter": "session"},
                          message="No indexed mods in session")

    result = db._get_symbol_conflicts_internal(
        cvids=frozenset(
            mod.cvid for mod in session.mods if mod.cvid is not None
        ),
        symbol_type=symbol_type,
        game_folder=path,
        limit=limit,
        load_order_map={
            m.cvid: m.load_order
            for m in session.mods
            if m.cvid is not None
        },
    )

    # Apply symbol_names filter if provided
    if symbol_names and result.get("conflicts"):
        names_lower = {n.lower() for n in symbol_names}
        result["conflicts"] = [
            c for c in result["conflicts"]
            if c["name"].lower() in names_lower
        ]
        result["conflict_count"] = len(result["conflicts"])

    return rb.success("WA-READ-S-001", data=result,
                      message=f"Found {result.get('conflict_count', 0)} symbol conflicts.")


def conflict_files(
    rb: Any,
    *,
    policy_ctx: dict,
    path: str | None = None,
    limit: int = 100,
    session: Any = None,
    db: Any = None,
    **_: Any,
) -> "Reply":
    """
    File-level conflict detection using original-file-aware algorithm.

    DB executor — receives policy_ctx from execute_db().

    Terminal replies:
      - WA-READ-S-001: Success
    """
    if not db:
        return rb.invalid("MCP-SYS-I-001", {"parameter": "db"},
                          message="Database not available")

    if not session or not getattr(session, 'mods', None):
        return rb.invalid("MCP-SYS-I-001", {"parameter": "session"},
                          message="No indexed mods in session")

    from ck3lens.impl.conflict_ops import detect_primary_file_conflicts

    result = detect_primary_file_conflicts(
        conn=db.conn,
        cvids=frozenset(
            mod.cvid for mod in session.mods if mod.cvid is not None
        ),
        load_order_map={
            m.cvid: m.load_order
            for m in session.mods
            if m.cvid is not None
        },
        mod_name_map={
            m.cvid: m.name
            for m in session.mods
            if m.cvid is not None
        },
        game_folder=path,
        limit=limit,
    )

    summary = result.get("summary", {})
    total = summary.get("total_originals_with_conflicts", 0)
    return rb.success(
        "WA-READ-S-001", data=result,
        message=(
            f"Found {total} original files with conflicts "
            f"({summary.get('full_overrides', 0)} full, "
            f"{summary.get('partial_overrides', 0)} partial, "
            f"{summary.get('failed_patch_warnings', 0)} warnings)."
        ),
    )


def conflict_summary(
    rb: Any,
    *,
    policy_ctx: dict,
    path: str | None = None,
    session: Any = None,
    db: Any = None,
    **_: Any,
) -> "Reply":
    """
    Conflict summary statistics: symbol conflicts by type + file conflict counts.

    DB executor — receives policy_ctx from execute_db().

    Terminal replies:
      - WA-READ-S-001: Success
    """
    if not db:
        return rb.invalid("MCP-SYS-I-001", {"parameter": "db"},
                          message="Database not available")

    if not session or not getattr(session, 'mods', None):
        return rb.invalid("MCP-SYS-I-001", {"parameter": "session"},
                          message="No indexed mods in session")

    # Symbol conflict summary by type (Golden Join)
    session_cvids = [
        mod.cvid for mod in session.mods if mod.cvid is not None
    ]
    type_rows = []
    if session_cvids:
        placeholders = ",".join("?" for _ in session_cvids)
        type_rows = db.conn.execute(f"""
            SELECT
                sub.symbol_type,
                COUNT(DISTINCT sub.name) as conflict_count
            FROM (
                SELECT s.symbol_type, s.name
                FROM symbols s
                JOIN asts a ON s.ast_id = a.ast_id
                JOIN files f ON a.content_hash = f.content_hash
                JOIN content_versions cv ON f.content_version_id = cv.content_version_id
                WHERE cv.content_version_id IN ({placeholders})
                GROUP BY s.symbol_type, s.name
                HAVING COUNT(DISTINCT cv.content_version_id) > 1
            ) sub
            GROUP BY sub.symbol_type
        """, session_cvids).fetchall()
    by_type = {row["symbol_type"]: row["conflict_count"] for row in type_rows}
    total_symbols = sum(by_type.values())

    # File conflict summary
    from ck3lens.impl.conflict_ops import detect_primary_file_conflicts

    file_result = detect_primary_file_conflicts(
        conn=db.conn,
        cvids=frozenset(session_cvids),
        load_order_map={
            m.cvid: m.load_order
            for m in session.mods
            if m.cvid is not None
        },
        mod_name_map={
            m.cvid: m.name
            for m in session.mods
            if m.cvid is not None
        },
        game_folder=path,
        limit=5000,
    )
    file_summary = file_result.get("summary", {})

    return rb.success(
        "WA-READ-S-001",
        data={
            "total_symbol_conflicts": total_symbols,
            "by_type": by_type,
            "file_conflict_summary": file_summary,
        },
        message=(
            f"Summary: {total_symbols} symbol conflicts, "
            f"{file_summary.get('total_originals_with_conflicts', 0)} file conflicts."
        ),
    )


# Registry: (tool, command) -> executor function
EXECUTORS: dict[tuple[str, str], Any] = {
    ("ck3_file", "edit"): file_edit,
    ("ck3_file", "delete"): file_delete,
    ("ck3_file", "write"): file_write,
    ("ck3_file", "read"): file_read,
    ("ck3_file", "rename"): file_rename,
    # Search executors
    ("ck3_search", "symbols"): search_symbols,
    ("ck3_search", "content"): search_content,
    ("ck3_search", "paths"): search_paths,
    ("ck3_search", "filters"): search_filters,
    ("ck3_search", "auto"): search_auto,
    ("ck3_search", "mods"): search_mods,
    ("ck3_validate", "references"): validate_references,
    # Conflict executors
    ("ck3_conflicts", "symbols"): conflict_symbols,
    ("ck3_conflicts", "files"): conflict_files,
    ("ck3_conflicts", "summary"): conflict_summary,
    ("ck3_conflicts", "diff"): conflict_diff,
    ("ck3_conflicts", "diff_index"): conflict_diff_index,
    
}
