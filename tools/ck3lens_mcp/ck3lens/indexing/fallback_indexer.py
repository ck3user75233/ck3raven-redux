"""
Fallback Indexer

For unknown file types. Returns line-count segmentation without parsing.
Provides basic structure: first_third, middle_third, last_third.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

INDEX_SCHEMA_VERSION = "1.0.0"
INDEXER_VERSION = "1.0.0"


def index_fallback(file_path: Path) -> dict[str, Any]:
    """
    Index an unknown file type by line-count segmentation.
    
    Returns INDEX JSON with file_summary and line_ranges for thirds.
    """
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()
        file_bytes = len(content.encode("utf-8"))
        line_count = len(lines)
    except Exception:
        # Binary or unreadable file
        file_bytes = file_path.stat().st_size
        line_count = 0
    
    # Create line range segments (thirds)
    elements: list[dict[str, Any]] = []
    
    if line_count > 0:
        third = max(1, line_count // 3)
        
        elements.append({
            "kind": "file_segment",
            "name": "first_third",
            "signature": f"lines 1-{third}",
            "line_start": 1,
            "line_end": third,
            "depth": 0,
            "decorators": [],
            "docstring": None,
            "children": [],
        })
        
        if line_count > third:
            mid_end = min(third * 2, line_count)
            elements.append({
                "kind": "file_segment",
                "name": "middle_third",
                "signature": f"lines {third + 1}-{mid_end}",
                "line_start": third + 1,
                "line_end": mid_end,
                "depth": 0,
                "decorators": [],
                "docstring": None,
                "children": [],
            })
        
        if line_count > third * 2:
            elements.append({
                "kind": "file_segment",
                "name": "last_third",
                "signature": f"lines {third * 2 + 1}-{line_count}",
                "line_start": third * 2 + 1,
                "line_end": line_count,
                "depth": 0,
                "decorators": [],
                "docstring": None,
                "children": [],
            })
    else:
        # Binary or empty file
        elements.append({
            "kind": "file_summary",
            "name": "unknown_content",
            "signature": f"{file_bytes} bytes",
            "line_start": 1,
            "line_end": 1,
            "depth": 0,
            "decorators": [],
            "docstring": "Binary or empty file - no line structure available",
            "children": [],
        })
    
    return {
        "schema_version": INDEX_SCHEMA_VERSION,
        "file_path": str(file_path),
        "file_bytes": file_bytes,
        "file_lines": line_count,
        "file_type": "unknown",
        "parser": "fallback",
        "parser_version": "1.0.0",
        "indexer_version": INDEXER_VERSION,
        "indexed_at": datetime.now(timezone.utc).isoformat(),
        "elements": elements,
    }


def index_fallback_from_content(content: str, file_path: str = "<string>") -> dict[str, Any]:
    """
    Index content directly (for testing or in-memory sources).
    """
    lines = content.splitlines()
    file_bytes = len(content.encode("utf-8"))
    line_count = len(lines)
    
    elements: list[dict[str, Any]] = []
    
    if line_count > 0:
        third = max(1, line_count // 3)
        
        elements.append({
            "kind": "file_segment",
            "name": "first_third",
            "signature": f"lines 1-{third}",
            "line_start": 1,
            "line_end": third,
            "depth": 0,
            "decorators": [],
            "docstring": None,
            "children": [],
        })
        
        if line_count > third:
            mid_end = min(third * 2, line_count)
            elements.append({
                "kind": "file_segment",
                "name": "middle_third",
                "signature": f"lines {third + 1}-{mid_end}",
                "line_start": third + 1,
                "line_end": mid_end,
                "depth": 0,
                "decorators": [],
                "docstring": None,
                "children": [],
            })
        
        if line_count > third * 2:
            elements.append({
                "kind": "file_segment",
                "name": "last_third",
                "signature": f"lines {third * 2 + 1}-{line_count}",
                "line_start": third * 2 + 1,
                "line_end": line_count,
                "depth": 0,
                "decorators": [],
                "docstring": None,
                "children": [],
            })
    
    return {
        "schema_version": INDEX_SCHEMA_VERSION,
        "file_path": file_path,
        "file_bytes": file_bytes,
        "file_lines": line_count,
        "file_type": "unknown",
        "parser": "fallback",
        "parser_version": "1.0.0",
        "indexer_version": INDEXER_VERSION,
        "indexed_at": datetime.now(timezone.utc).isoformat(),
        "elements": elements,
    }
