"""
CK3 Lens Indexing Module

Provides file indexing with two output modes:
- INDEX: Compact JSON navigation map (~5KB) with names, types, signatures, line ranges
- CST: Full JSON tree (~50KB) preserving complete structure for editing/analysis

Supports:
- Python (via libcst) - INDEX + CST
- Markdown (via regex heading extraction) - INDEX only
- CK3 script (via ck3raven parser) - INDEX only
- TypeScript (via tree-sitter or regex fallback) - INDEX only
- Unknown (fallback line-segment) - INDEX only

Cache layer (cst_cache):
- mtime-based invalidation
- SHA256 cache keys for deterministic filenames
- LRU eviction at 1000 entries
- Atomic writes via tmp + os.replace
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

FileType = Literal["python", "typescript", "markdown", "ck3", "unknown"]


def detect_file_type(path: Path) -> FileType:
    """
    Detect file type from extension.
    
    Args:
        path: Path to file (host path or just filename)
        
    Returns:
        FileType literal indicating the detected type
    """
    suffix = path.suffix.lower()
    
    if suffix == ".py":
        return "python"
    elif suffix in (".ts", ".tsx"):
        return "typescript"
    elif suffix in (".md", ".markdown"):
        return "markdown"
    elif suffix in (".txt", ".yml", ".gui", ".gfx", ".sfx", ".asset", ".info"):
        # CK3 script files - .txt is most common
        return "ck3"
    else:
        return "unknown"


def index_file(
    host_path: Path,
    canonical_path: str,
    file_type: FileType,
    *,
    include_cst: bool = False,
    use_cache: bool = True,
) -> tuple[dict, dict | None]:
    """
    Index a file and optionally produce full CST.
    
    Dispatches to the appropriate indexer based on file type.
    Provenance (parser, version, timestamp) is embedded in the index dict.
    
    Args:
        host_path: Absolute path to file on disk
        canonical_path: Canonical address (e.g., "root:repo/tools/...")
        file_type: Pre-validated file type (caller must check before calling)
        include_cst: If True, also generate full CST JSON tree (Python only)
        use_cache: If True, check cache before indexing (default True)
        
    Returns:
        Tuple of (index_dict, cst_dict or None)
        - For Python: both INDEX and CST (if include_cst=True)
        - For other types: INDEX only, CST is always None
        
    Raises:
        FileNotFoundError: If file does not exist
    """
    if not host_path.exists():
        raise FileNotFoundError(f"File not found: {host_path}")
    
    # Check cache first (INDEX only, CST is always regenerated)
    if use_cache and not include_cst:
        from .cst_cache import get_cached_index
        cached = get_cached_index(host_path, canonical_path)
        if cached is not None:
            return cached, None
    
    # Generate fresh index
    if file_type == "python":
        from .python_indexer import index_python_file
        index, cst = index_python_file(host_path, canonical_path, include_cst=include_cst)
    
    elif file_type == "typescript":
        from .typescript_indexer import index_typescript
        index = index_typescript(host_path)
        index["file_path"] = canonical_path  # Use canonical path
        cst = None
    
    elif file_type == "markdown":
        from .markdown_indexer import index_markdown
        index = index_markdown(host_path)
        index["file_path"] = canonical_path  # Use canonical path
        cst = None
    
    elif file_type == "ck3":
        from .ck3_indexer import index_ck3
        index = index_ck3(host_path)
        index["file_path"] = canonical_path  # Use canonical path
        cst = None
    
    elif file_type == "unknown":
        from .fallback_indexer import index_fallback
        index = index_fallback(host_path)
        index["file_path"] = canonical_path  # Use canonical path
        cst = None
    
    else:
        # Unreachable if caller pre-validated, but fail explicitly
        raise NotImplementedError(f"No indexer for file_type={file_type!r}")
    
    # Write to cache (INDEX only)
    if use_cache:
        from .cst_cache import write_index_atomic
        write_index_atomic(host_path, canonical_path, index, prefix="INDEX")
        if cst is not None:
            write_index_atomic(host_path, canonical_path, cst, prefix="CST")
    
    return index, cst


# Re-export cache functions for direct use
from .cst_cache import (
    get_cached_index,
    write_index_atomic,
    invalidate,
    invalidate_all,
    get_cache_stats,
)

__all__ = [
    "FileType",
    "detect_file_type",
    "index_file",
    # Cache functions
    "get_cached_index",
    "write_index_atomic",
    "invalidate",
    "invalidate_all",
    "get_cache_stats",
]
