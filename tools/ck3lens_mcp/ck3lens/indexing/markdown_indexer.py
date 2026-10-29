"""
Markdown Indexer

Extracts heading structure from Markdown files using regex.
Produces INDEX JSON with heading levels as depth.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

INDEX_SCHEMA_VERSION = "1.0.0"
INDEXER_VERSION = "1.0.0"

# Regex for ATX headings: # Heading, ## Heading, etc.
HEADING_PATTERN = re.compile(r'^(#{1,6})\s+(.+)$', re.MULTILINE)


def index_markdown(file_path: Path) -> dict[str, Any]:
    """
    Index a Markdown file by extracting its heading structure.
    
    Returns INDEX JSON with headings as elements:
    - kind: "heading"
    - name: heading text (stripped)
    - depth: heading level (1-6, where # = 0 in depth terms)
    - line_start/line_end: line number (1-indexed)
    """
    content = file_path.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines()
    file_bytes = len(content.encode("utf-8"))
    
    elements: list[dict[str, Any]] = []
    
    for match in HEADING_PATTERN.finditer(content):
        hashes = match.group(1)
        heading_text = match.group(2).strip()
        level = len(hashes)  # 1-6
        
        # Find line number (1-indexed)
        line_number = content[:match.start()].count('\n') + 1
        
        elements.append({
            "kind": "heading",
            "name": heading_text,
            "signature": f"{'#' * level} {heading_text}",
            "line_start": line_number,
            "line_end": line_number,
            "depth": level - 1,  # depth 0 = h1, depth 1 = h2, etc.
            "decorators": [],
            "docstring": None,
            "children": [],
        })
    
    return {
        "schema_version": INDEX_SCHEMA_VERSION,
        "file_path": str(file_path),  # Will be converted to canonical in caller
        "file_bytes": file_bytes,
        "file_lines": len(lines),
        "file_type": "markdown",
        "parser": "regex",
        "parser_version": "1.0.0",
        "indexer_version": INDEXER_VERSION,
        "indexed_at": datetime.now(timezone.utc).isoformat(),
        "elements": elements,
    }


def index_markdown_from_content(content: str, file_path: str = "<string>") -> dict[str, Any]:
    """
    Index Markdown content directly (for testing or in-memory sources).
    """
    lines = content.splitlines()
    file_bytes = len(content.encode("utf-8"))
    
    elements: list[dict[str, Any]] = []
    
    for match in HEADING_PATTERN.finditer(content):
        hashes = match.group(1)
        heading_text = match.group(2).strip()
        level = len(hashes)
        line_number = content[:match.start()].count('\n') + 1
        
        elements.append({
            "kind": "heading",
            "name": heading_text,
            "signature": f"{'#' * level} {heading_text}",
            "line_start": line_number,
            "line_end": line_number,
            "depth": level - 1,
            "decorators": [],
            "docstring": None,
            "children": [],
        })
    
    return {
        "schema_version": INDEX_SCHEMA_VERSION,
        "file_path": file_path,
        "file_bytes": file_bytes,
        "file_lines": len(lines),
        "file_type": "markdown",
        "parser": "regex",
        "parser_version": "1.0.0",
        "indexer_version": INDEXER_VERSION,
        "indexed_at": datetime.now(timezone.utc).isoformat(),
        "elements": elements,
    }
