"""
TypeScript Indexer

Uses tree-sitter + tree-sitter-typescript to extract exports, classes, functions.
Falls back to regex-based extraction if tree-sitter is not available.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

INDEX_SCHEMA_VERSION = "1.0.0"
INDEXER_VERSION = "1.0.0"

# Regex patterns for fallback extraction (not as accurate as tree-sitter)
_CLASS_PATTERN = re.compile(
    r'^(?:export\s+)?(?:abstract\s+)?class\s+(\w+)',
    re.MULTILINE
)
_FUNCTION_PATTERN = re.compile(
    r'^(?:export\s+)?(?:async\s+)?function\s+(\w+)',
    re.MULTILINE
)
_ARROW_EXPORT_PATTERN = re.compile(
    r'^export\s+(?:const|let)\s+(\w+)\s*=',
    re.MULTILINE
)
_INTERFACE_PATTERN = re.compile(
    r'^(?:export\s+)?interface\s+(\w+)',
    re.MULTILINE
)
_TYPE_ALIAS_PATTERN = re.compile(
    r'^(?:export\s+)?type\s+(\w+)\s*=',
    re.MULTILINE
)
_ENUM_PATTERN = re.compile(
    r'^(?:export\s+)?(?:const\s+)?enum\s+(\w+)',
    re.MULTILINE
)


def _try_tree_sitter_index(file_path: Path, content: str) -> dict[str, Any] | None:
    """
    Try to index using tree-sitter. Returns None if tree-sitter is not available.
    """
    try:
        import tree_sitter_typescript as ts_typescript
        from tree_sitter import Language, Parser
    except ImportError:
        return None
    
    try:
        # Initialize TypeScript parser
        TS_LANGUAGE = Language(ts_typescript.language())
        parser = Parser(TS_LANGUAGE)
        
        source_bytes = content.encode("utf-8")
        tree = parser.parse(source_bytes)
        root = tree.root_node
        
        elements: list[dict[str, Any]] = []
        
        def extract_elements(node, depth=0):
            """Recursively extract elements from tree-sitter AST."""
            nonlocal elements
            
            # Skip too deep (for performance)
            if depth > 3:
                return
            
            kind = None
            name = None
            
            # Detect node types
            if node.type in ("class_declaration", "abstract_class_declaration"):
                kind = "class"
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = content[name_node.start_byte:name_node.end_byte]
                    
            elif node.type == "function_declaration":
                kind = "function"
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = content[name_node.start_byte:name_node.end_byte]
                    
            elif node.type == "method_definition":
                kind = "method"
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = content[name_node.start_byte:name_node.end_byte]
                    
            elif node.type == "interface_declaration":
                kind = "interface"
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = content[name_node.start_byte:name_node.end_byte]
                    
            elif node.type == "type_alias_declaration":
                kind = "type"
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = content[name_node.start_byte:name_node.end_byte]
                    
            elif node.type == "enum_declaration":
                kind = "enum"
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = content[name_node.start_byte:name_node.end_byte]
                    
            elif node.type in ("lexical_declaration", "variable_declaration"):
                # Check for exported const
                if node.parent and node.parent.type == "export_statement":
                    for child in node.children:
                        if child.type == "variable_declarator":
                            name_child = child.child_by_field_name("name")
                            if name_child:
                                kind = "export"
                                name = content[name_child.start_byte:name_child.end_byte]
                                break
            
            if kind and name:
                line_start = node.start_point[0] + 1  # tree-sitter is 0-indexed
                line_end = node.end_point[0] + 1
                
                element = {
                    "kind": kind,
                    "name": name,
                    "signature": None,
                    "line_start": line_start,
                    "line_end": line_end,
                    "depth": depth,
                    "decorators": [],
                    "docstring": None,
                    "children": [],
                }
                elements.append(element)
                
                # Extract children (methods in classes)
                if kind == "class":
                    for child in node.children:
                        if child.type == "class_body":
                            for member in child.children:
                                if member.type == "method_definition":
                                    method_name_node = member.child_by_field_name("name")
                                    if method_name_node:
                                        method_name = content[method_name_node.start_byte:method_name_node.end_byte]
                                        element["children"].append({
                                            "kind": "method",
                                            "name": method_name,
                                            "signature": None,
                                            "line_start": member.start_point[0] + 1,
                                            "line_end": member.end_point[0] + 1,
                                            "depth": depth + 1,
                                            "decorators": [],
                                            "docstring": None,
                                            "children": [],
                                        })
                return
            
            # Recurse into children
            for child in node.children:
                extract_elements(child, depth)
        
        extract_elements(root)
        
        lines = content.splitlines()
        file_bytes = len(content.encode("utf-8"))
        
        return {
            "schema_version": INDEX_SCHEMA_VERSION,
            "file_path": str(file_path),
            "file_bytes": file_bytes,
            "file_lines": len(lines),
            "file_type": "typescript",
            "parser": "tree-sitter",
            "parser_version": "0.21+",
            "indexer_version": INDEXER_VERSION,
            "indexed_at": datetime.now(timezone.utc).isoformat(),
            "elements": elements,
        }
        
    except Exception as e:
        # tree-sitter failed, return None to trigger fallback
        return None


def _regex_fallback_index(file_path: Path, content: str) -> dict[str, Any]:
    """
    Fallback regex-based extraction when tree-sitter is not available.
    Less accurate but works without dependencies.
    """
    lines = content.splitlines()
    file_bytes = len(content.encode("utf-8"))
    
    elements: list[dict[str, Any]] = []
    
    # Extract classes
    for match in _CLASS_PATTERN.finditer(content):
        line_number = content[:match.start()].count('\n') + 1
        elements.append({
            "kind": "class",
            "name": match.group(1),
            "signature": match.group(0).strip(),
            "line_start": line_number,
            "line_end": line_number,  # Can't determine end without parsing
            "depth": 0,
            "decorators": [],
            "docstring": None,
            "children": [],
        })
    
    # Extract functions
    for match in _FUNCTION_PATTERN.finditer(content):
        line_number = content[:match.start()].count('\n') + 1
        elements.append({
            "kind": "function",
            "name": match.group(1),
            "signature": match.group(0).strip(),
            "line_start": line_number,
            "line_end": line_number,
            "depth": 0,
            "decorators": [],
            "docstring": None,
            "children": [],
        })
    
    # Extract exported consts/arrows
    for match in _ARROW_EXPORT_PATTERN.finditer(content):
        line_number = content[:match.start()].count('\n') + 1
        elements.append({
            "kind": "export",
            "name": match.group(1),
            "signature": match.group(0).strip(),
            "line_start": line_number,
            "line_end": line_number,
            "depth": 0,
            "decorators": [],
            "docstring": None,
            "children": [],
        })
    
    # Extract interfaces
    for match in _INTERFACE_PATTERN.finditer(content):
        line_number = content[:match.start()].count('\n') + 1
        elements.append({
            "kind": "interface",
            "name": match.group(1),
            "signature": match.group(0).strip(),
            "line_start": line_number,
            "line_end": line_number,
            "depth": 0,
            "decorators": [],
            "docstring": None,
            "children": [],
        })
    
    # Extract type aliases
    for match in _TYPE_ALIAS_PATTERN.finditer(content):
        line_number = content[:match.start()].count('\n') + 1
        elements.append({
            "kind": "type",
            "name": match.group(1),
            "signature": match.group(0).strip(),
            "line_start": line_number,
            "line_end": line_number,
            "depth": 0,
            "decorators": [],
            "docstring": None,
            "children": [],
        })
    
    # Extract enums
    for match in _ENUM_PATTERN.finditer(content):
        line_number = content[:match.start()].count('\n') + 1
        elements.append({
            "kind": "enum",
            "name": match.group(1),
            "signature": match.group(0).strip(),
            "line_start": line_number,
            "line_end": line_number,
            "depth": 0,
            "decorators": [],
            "docstring": None,
            "children": [],
        })
    
    # Sort by line number
    elements.sort(key=lambda e: e["line_start"])
    
    return {
        "schema_version": INDEX_SCHEMA_VERSION,
        "file_path": str(file_path),
        "file_bytes": file_bytes,
        "file_lines": len(lines),
        "file_type": "typescript",
        "parser": "regex",
        "parser_version": "1.0.0",
        "indexer_version": INDEXER_VERSION,
        "indexed_at": datetime.now(timezone.utc).isoformat(),
        "elements": elements,
    }


def index_typescript(file_path: Path) -> dict[str, Any]:
    """
    Index a TypeScript file by extracting exports, classes, functions.
    
    Tries tree-sitter first, falls back to regex if not available.
    """
    content = file_path.read_text(encoding="utf-8", errors="replace")
    
    # Try tree-sitter first
    result = _try_tree_sitter_index(file_path, content)
    if result is not None:
        return result
    
    # Fall back to regex
    return _regex_fallback_index(file_path, content)


def index_typescript_from_content(content: str, file_path: str = "<string>") -> dict[str, Any]:
    """
    Index TypeScript content directly (for testing or in-memory sources).
    """
    # Try tree-sitter first
    result = _try_tree_sitter_index(Path(file_path), content)
    if result is not None:
        result["file_path"] = file_path
        return result
    
    # Fall back to regex
    result = _regex_fallback_index(Path(file_path), content)
    result["file_path"] = file_path
    return result
