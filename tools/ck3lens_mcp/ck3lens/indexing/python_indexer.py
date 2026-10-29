"""
Python Indexer using libcst.

Extracts a compact INDEX (navigation map) and optionally full CST JSON tree.
"""

from __future__ import annotations

import importlib.metadata
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import libcst as cst
from libcst.metadata import PositionProvider, MetadataWrapper, CodeRange


# Schema version for index output
INDEX_SCHEMA_VERSION = "1.0.0"


@dataclass
class IndexElement:
    """A single element in the index."""
    
    kind: str                         # function, class, method, assignment, import_group
    name: str                         # identifier name
    signature: str | None             # function signature or assignment value
    line_start: int                   # 1-based start line
    line_end: int                     # 1-based end line
    depth: int                        # nesting depth (0 = top-level)
    decorators: list[str] = field(default_factory=list)
    docstring: str | None = None
    children: list["IndexElement"] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        """Convert to JSON-serializable dict."""
        return {
            "kind": self.kind,
            "name": self.name,
            "signature": self.signature,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "depth": self.depth,
            "decorators": self.decorators,
            "docstring": self.docstring,
            "children": [c.to_dict() for c in self.children],
        }


class IndexExtractor(cst.CSTVisitor):
    """
    CST visitor that extracts index elements from Python source.
    
    Collects:
    - Functions (with signatures, decorators, docstrings)
    - Classes (with methods as children)
    - Top-level assignments (module constants)
    """
    
    METADATA_DEPENDENCIES = (PositionProvider,)
    
    def __init__(self) -> None:
        super().__init__()
        self.elements: list[IndexElement] = []
        self._class_stack: list[IndexElement] = []  # Track nested classes
        self._depth = 0
    
    def _get_position(self, node: cst.CSTNode) -> tuple[int, int]:
        """Get start and end line numbers for a node."""
        pos = self.get_metadata(PositionProvider, node)
        if not isinstance(pos, CodeRange):
            raise RuntimeError(f"Position metadata unavailable for node: {type(node).__name__}")
        return pos.start.line, pos.end.line
    
    def _extract_signature(self, node: cst.FunctionDef) -> str:
        """Extract function signature as string."""
        params = []
        for param in node.params.params:
            p = param.name.value
            if param.annotation:
                p += f": {_node_to_source(param.annotation.annotation)}"
            if param.default:
                p += f" = {_node_to_source(param.default)}"
            params.append(p)
        
        # Add *args
        if node.params.star_arg and isinstance(node.params.star_arg, cst.Param):
            p = f"*{node.params.star_arg.name.value}"
            if node.params.star_arg.annotation:
                p += f": {_node_to_source(node.params.star_arg.annotation.annotation)}"
            params.append(p)
        
        # Add **kwargs
        if node.params.star_kwarg:
            p = f"**{node.params.star_kwarg.name.value}"
            if node.params.star_kwarg.annotation:
                p += f": {_node_to_source(node.params.star_kwarg.annotation.annotation)}"
            params.append(p)
        
        sig = f"({', '.join(params)})"
        
        if node.returns:
            sig += f" -> {_node_to_source(node.returns.annotation)}"
        
        return sig
    
    def _extract_docstring(self, body: cst.BaseSuite) -> str | None:
        """Extract docstring from function/class body if present."""
        if isinstance(body, cst.IndentedBlock) and body.body:
            first_stmt = body.body[0]
            if isinstance(first_stmt, cst.SimpleStatementLine) and first_stmt.body:
                expr = first_stmt.body[0]
                if isinstance(expr, cst.Expr) and isinstance(expr.value, (cst.SimpleString, cst.ConcatenatedString)):
                    docstring = _node_to_source(expr.value)
                    # Strip quotes and clean up
                    if docstring.startswith(('"""', "'''")):
                        docstring = docstring[3:-3]
                    elif docstring.startswith(('"', "'")):
                        docstring = docstring[1:-1]
                    # Truncate long docstrings
                    if len(docstring) > 100:
                        docstring = docstring[:100] + "..."
                    return docstring.strip()
        return None
    
    def _extract_decorators(self, node: cst.FunctionDef | cst.ClassDef) -> list[str]:
        """Extract decorator names."""
        decorators = []
        for dec in node.decorators:
            if isinstance(dec.decorator, cst.Name):
                decorators.append(dec.decorator.value)
            elif isinstance(dec.decorator, cst.Attribute):
                decorators.append(_node_to_source(dec.decorator))
            elif isinstance(dec.decorator, cst.Call):
                if isinstance(dec.decorator.func, cst.Name):
                    decorators.append(dec.decorator.func.value)
                elif isinstance(dec.decorator.func, cst.Attribute):
                    decorators.append(_node_to_source(dec.decorator.func))
        return decorators
    
    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
        """Visit function definition."""
        line_start, line_end = self._get_position(node)
        
        kind = "method" if self._class_stack else "function"
        
        element = IndexElement(
            kind=kind,
            name=node.name.value,
            signature=self._extract_signature(node),
            line_start=line_start,
            line_end=line_end,
            depth=self._depth,
            decorators=self._extract_decorators(node),
            docstring=self._extract_docstring(node.body),
        )
        
        if self._class_stack:
            # Add as child of current class
            self._class_stack[-1].children.append(element)
        else:
            self.elements.append(element)
        
        # Don't recurse into nested functions for now
        return False
    
    def visit_ClassDef(self, node: cst.ClassDef) -> bool:
        """Visit class definition."""
        line_start, line_end = self._get_position(node)
        
        element = IndexElement(
            kind="class",
            name=node.name.value,
            signature=None,
            line_start=line_start,
            line_end=line_end,
            depth=self._depth,
            decorators=self._extract_decorators(node),
            docstring=self._extract_docstring(node.body),
        )
        
        if self._class_stack:
            # Nested class
            self._class_stack[-1].children.append(element)
        else:
            self.elements.append(element)
        
        # Push onto stack and continue to collect methods
        self._class_stack.append(element)
        self._depth += 1
        return True
    
    def leave_ClassDef(self, node: cst.ClassDef) -> None:
        """Leave class definition."""
        self._class_stack.pop()
        self._depth -= 1
    
    def visit_Assign(self, node: cst.Assign) -> bool:
        """Visit assignment (module-level constants)."""
        # Only capture top-level assignments
        if self._depth > 0:
            return False
        
        # Only capture simple name targets
        for target in node.targets:
            if isinstance(target.target, cst.Name):
                name = target.target.value
                # Skip private variables
                if name.startswith("_") and not name.startswith("__"):
                    continue
                
                line_start, line_end = self._get_position(node)
                value_str = _node_to_source(node.value)
                if len(value_str) > 50:
                    value_str = value_str[:50] + "..."
                
                element = IndexElement(
                    kind="assignment",
                    name=name,
                    signature=value_str,
                    line_start=line_start,
                    line_end=line_end,
                    depth=0,
                )
                self.elements.append(element)
        
        return False
    
    def visit_AnnAssign(self, node: cst.AnnAssign) -> bool:
        """Visit annotated assignment (type hints)."""
        if self._depth > 0:
            return False
        
        if isinstance(node.target, cst.Name):
            name = node.target.value
            if name.startswith("_") and not name.startswith("__"):
                return False
            
            line_start, line_end = self._get_position(node)
            
            # Build signature with type annotation
            sig_parts = [_node_to_source(node.annotation.annotation)]
            if node.value:
                val = _node_to_source(node.value)
                if len(val) > 30:
                    val = val[:30] + "..."
                sig_parts.append(f" = {val}")
            
            element = IndexElement(
                kind="assignment",
                name=name,
                signature="".join(sig_parts),
                line_start=line_start,
                line_end=line_end,
                depth=0,
            )
            self.elements.append(element)
        
        return False


def _node_to_source(node: cst.CSTNode) -> str:
    """Convert a CST node back to source code string."""
    return cst.parse_module("").code_for_node(node)


def index_python_file(
    host_path: Path,
    canonical_path: str,
    *,
    include_cst: bool = False,
) -> tuple[dict, dict | None]:
    """
    Parse Python file with libcst, extract index and optional full CST.
    
    Args:
        host_path: Absolute path to Python file
        canonical_path: Canonical address for the file
        include_cst: If True, also produce full CST JSON tree
        
    Returns:
        Tuple of (index_dict, cst_dict or None).
        Provenance (parser, version, timestamp) is embedded in the index dict.
    """
    source = host_path.read_text(encoding="utf-8")
    
    # Parse with libcst
    module = cst.parse_module(source)
    
    # Wrap with metadata to get line numbers
    wrapper = MetadataWrapper(module)
    
    # Extract index
    extractor = IndexExtractor()
    wrapper.visit(extractor)
    
    # Build compact index with provenance header
    file_stat = host_path.stat()
    index = {
        "schema_version": INDEX_SCHEMA_VERSION,
        "file_path": canonical_path,
        "file_bytes": file_stat.st_size,
        "file_lines": source.count("\n") + 1,
        "file_type": "python",
        "parser": "libcst",
        "parser_version": importlib.metadata.version("libcst"),
        "indexer_version": "1.0.0",
        "indexed_at": datetime.now(timezone.utc).isoformat(),
        "elements": [e.to_dict() for e in extractor.elements],
    }
    
    # Build CST JSON if requested
    cst_json = None
    if include_cst:
        from .cst_serializer import cst_to_json
        cst_json = cst_to_json(module, canonical_path)
    
    return (index, cst_json)


__all__ = [
    "IndexElement",
    "IndexExtractor",
    "index_python_file",
]
