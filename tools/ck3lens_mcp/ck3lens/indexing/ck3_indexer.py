"""
CK3 Indexer

Wraps the existing CK3 parser to extract top-level blocks.
Falls back to fallback_indexer if parser fails.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

INDEX_SCHEMA_VERSION = "1.0.0"
INDEXER_VERSION = "1.0.0"


def _calculate_end_line(content: str, end_offset: int) -> int:
    """Calculate line number from byte offset."""
    return content[:end_offset].count('\n') + 1


def index_ck3(file_path: Path) -> dict[str, Any]:
    """
    Index a CK3 script file by extracting top-level blocks.
    
    Returns INDEX JSON with blocks as elements:
    - kind: "block"
    - name: block name (e.g., "trait_brave", "on_game_start")
    - line_start/line_end: line numbers (1-indexed)
    """
    try:
        from ck3raven.parser import parse_file, ParseError, BlockNode, AssignmentNode
    except ImportError as e:
        # Parser not available - use fallback
        from ck3lens.indexing.fallback_indexer import index_fallback
        result = index_fallback(file_path)
        result["file_type"] = "ck3"
        result["parser"] = "fallback"
        result["parser_error"] = f"ck3raven.parser import failed: {e}"
        return result
    
    content = file_path.read_text(encoding="utf-8-sig", errors="replace")
    lines = content.splitlines()
    file_bytes = len(content.encode("utf-8"))
    
    try:
        ast = parse_file(str(file_path))
    except (ParseError, Exception) as e:
        # Parser failed - use fallback
        from ck3lens.indexing.fallback_indexer import index_fallback
        result = index_fallback(file_path)
        result["file_type"] = "ck3"
        result["parser"] = "fallback"
        result["parser_error"] = str(e)
        return result
    
    elements: list[dict[str, Any]] = []
    
    for child in ast.children:
        if isinstance(child, BlockNode):
            # Calculate end line from end_offset if available
            end_line = child.line
            if child.end_offset > 0:
                end_line = _calculate_end_line(content, child.end_offset)
            
            elements.append({
                "kind": "block",
                "name": child.name,
                "signature": f"{child.name} = {{ ... }}",
                "line_start": child.line,
                "line_end": end_line,
                "depth": 0,
                "decorators": [],
                "docstring": None,
                "children": _extract_child_blocks(child, content, depth=1),
            })
        elif isinstance(child, AssignmentNode):
            elements.append({
                "kind": "assignment",
                "name": child.key,
                "signature": f"{child.key} = ...",
                "line_start": child.line,
                "line_end": child.line,
                "depth": 0,
                "decorators": [],
                "docstring": None,
                "children": [],
            })
    
    return {
        "schema_version": INDEX_SCHEMA_VERSION,
        "file_path": str(file_path),
        "file_bytes": file_bytes,
        "file_lines": len(lines),
        "file_type": "ck3",
        "parser": "ck3raven",
        "parser_version": "1.0.0",
        "indexer_version": INDEXER_VERSION,
        "indexed_at": datetime.now(timezone.utc).isoformat(),
        "elements": elements,
    }


def _extract_child_blocks(block, content: str, depth: int, max_depth: int = 3) -> list[dict[str, Any]]:
    """Extract nested blocks up to max_depth."""
    if depth > max_depth:
        return []
    
    try:
        from ck3raven.parser import BlockNode
    except ImportError:
        return []
    
    children: list[dict[str, Any]] = []
    
    for child in getattr(block, 'children', []):
        if isinstance(child, BlockNode):
            end_line = child.line
            if child.end_offset > 0:
                end_line = _calculate_end_line(content, child.end_offset)
            
            children.append({
                "kind": "block",
                "name": child.name,
                "signature": f"{child.name} = {{ ... }}",
                "line_start": child.line,
                "line_end": end_line,
                "depth": depth,
                "decorators": [],
                "docstring": None,
                "children": _extract_child_blocks(child, content, depth + 1, max_depth),
            })
    
    return children


def index_ck3_from_content(content: str, file_path: str = "<string>") -> dict[str, Any]:
    """
    Index CK3 content directly (for testing or in-memory sources).
    """
    try:
        from ck3raven.parser import parse_source, ParseError, BlockNode, AssignmentNode
    except ImportError as e:
        from ck3lens.indexing.fallback_indexer import index_fallback_from_content
        result = index_fallback_from_content(content, file_path)
        result["file_type"] = "ck3"
        result["parser"] = "fallback"
        result["parser_error"] = f"ck3raven.parser import failed: {e}"
        return result
    
    lines = content.splitlines()
    file_bytes = len(content.encode("utf-8"))
    
    try:
        ast = parse_source(content, file_path)
    except (ParseError, Exception) as e:
        from ck3lens.indexing.fallback_indexer import index_fallback_from_content
        result = index_fallback_from_content(content, file_path)
        result["file_type"] = "ck3"
        result["parser"] = "fallback"
        result["parser_error"] = str(e)
        return result
    
    elements: list[dict[str, Any]] = []
    
    for child in ast.children:
        if isinstance(child, BlockNode):
            end_line = child.line
            if child.end_offset > 0:
                end_line = _calculate_end_line(content, child.end_offset)
            
            elements.append({
                "kind": "block",
                "name": child.name,
                "signature": f"{child.name} = {{ ... }}",
                "line_start": child.line,
                "line_end": end_line,
                "depth": 0,
                "decorators": [],
                "docstring": None,
                "children": _extract_child_blocks(child, content, depth=1),
            })
        elif isinstance(child, AssignmentNode):
            elements.append({
                "kind": "assignment",
                "name": child.key,
                "signature": f"{child.key} = ...",
                "line_start": child.line,
                "line_end": child.line,
                "depth": 0,
                "decorators": [],
                "docstring": None,
                "children": [],
            })
    
    return {
        "schema_version": INDEX_SCHEMA_VERSION,
        "file_path": file_path,
        "file_bytes": file_bytes,
        "file_lines": len(lines),
        "file_type": "ck3",
        "parser": "ck3raven",
        "parser_version": "1.0.0",
        "indexer_version": INDEXER_VERSION,
        "indexed_at": datetime.now(timezone.utc).isoformat(),
        "elements": elements,
    }
