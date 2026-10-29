"""
CST Index Cache Layer

Provides mtime-based caching for file indexes. Cache entries are invalidated when:
1. Source file mtime changes
2. Parser/indexer version changes  
3. Manual invalidation via invalidate()

Cache is stored in ~/.ck3raven/wip/indexes/ with deterministic SHA256-based filenames.
LRU eviction when cache exceeds MAX_ENTRIES (1000).
All writes use atomic tmp + os.replace pattern for crash-safety.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

# Cache location
_CACHE_DIR: Path | None = None

# Max entries before LRU eviction
MAX_ENTRIES = 1000


def _get_cache_dir() -> Path:
    """Get or create cache directory."""
    global _CACHE_DIR
    if _CACHE_DIR is None:
        # Import here to avoid circular dependency
        from ..paths import ROOT_CK3RAVEN_DATA
        _CACHE_DIR = ROOT_CK3RAVEN_DATA / "wip" / "indexes"
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR


def _cache_key(canonical_path: str) -> str:
    """Generate deterministic cache key from canonical path."""
    return hashlib.sha256(canonical_path.encode("utf-8")).hexdigest()


def _cache_filename(canonical_path: str, prefix: str = "INDEX") -> str:
    """Generate cache filename with prefix and hash."""
    key = _cache_key(canonical_path)[:16]  # First 16 chars of SHA256
    # Extract filename from canonical path for readability
    name = canonical_path.rsplit("/", 1)[-1] if "/" in canonical_path else canonical_path
    # Sanitize name (remove problematic chars)
    safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in name)[:50]
    return f"{prefix}-{safe_name}-{key}.json"


def _read_cache_meta(cache_path: Path) -> dict[str, Any] | None:
    """Read just the metadata fields from a cached index file.
    
    Returns None if file doesn't exist or is invalid JSON.
    """
    if not cache_path.exists():
        return None
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {
            "source_mtime": data.get("source_mtime"),
            "parser": data.get("parser"),
            "parser_version": data.get("parser_version"),
            "indexer_version": data.get("indexer_version"),
        }
    except (json.JSONDecodeError, OSError):
        return None


def get_cached_index(
    host_path: Path,
    canonical_path: str,
    *,
    parser: str | None = None,
    parser_version: str | None = None,
    indexer_version: str = "1.0.0",
) -> dict[str, Any] | None:
    """Return cached index if valid, else None.
    
    Cache hit requires:
    1. Cache file exists
    2. Source file mtime matches cached mtime
    3. Parser name matches (if specified)
    4. Parser version matches (if specified)
    5. Indexer version matches
    
    Args:
        host_path: Absolute path to source file
        canonical_path: Canonical address (e.g., "root:repo/...")
        parser: Expected parser name (e.g., "libcst")
        parser_version: Expected parser version
        indexer_version: Expected indexer version
        
    Returns:
        Cached index dict if valid, None if cache miss
    """
    cache_dir = _get_cache_dir()
    cache_filename = _cache_filename(canonical_path, "INDEX")
    cache_path = cache_dir / cache_filename
    
    meta = _read_cache_meta(cache_path)
    if meta is None:
        return None
    
    # Check mtime
    try:
        current_mtime = host_path.stat().st_mtime
    except OSError:
        return None
    
    if meta.get("source_mtime") != current_mtime:
        return None
    
    # Check parser (if specified)
    if parser is not None and meta.get("parser") != parser:
        return None
    
    # Check parser version (if specified)
    if parser_version is not None and meta.get("parser_version") != parser_version:
        return None
    
    # Check indexer version
    if meta.get("indexer_version") != indexer_version:
        return None
    
    # All checks passed — read full cache file
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def write_index_atomic(
    host_path: Path,
    canonical_path: str,
    index_dict: dict[str, Any],
    *,
    prefix: str = "INDEX",
) -> Path:
    """Write index to cache with atomic tmp + os.replace pattern.
    
    Embeds source_mtime in the index dict for cache validation.
    Performs LRU eviction if cache exceeds MAX_ENTRIES.
    
    Args:
        host_path: Absolute path to source file (for mtime)
        canonical_path: Canonical address
        index_dict: The index to cache (will be modified with mtime)
        prefix: Filename prefix ("INDEX" or "CST")
        
    Returns:
        Path to the written cache file
    """
    cache_dir = _get_cache_dir()
    cache_filename_str = _cache_filename(canonical_path, prefix)
    cache_path = cache_dir / cache_filename_str
    
    # Embed mtime for validation
    try:
        index_dict["source_mtime"] = host_path.stat().st_mtime
    except OSError:
        index_dict["source_mtime"] = None
    
    # Serialize to JSON
    json_content = json.dumps(index_dict, indent=2, ensure_ascii=False)
    
    # Atomic write: tmp file then os.replace
    tmp_path = cache_path.with_suffix(".tmp")
    try:
        tmp_path.write_text(json_content, encoding="utf-8")
        os.replace(tmp_path, cache_path)
    except OSError:
        # If replace fails, try to clean up tmp
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    
    # LRU eviction if needed
    _maybe_evict(cache_dir)
    
    return cache_path


def invalidate(canonical_path: str, prefix: str = "INDEX") -> bool:
    """Remove a cached index file.
    
    Args:
        canonical_path: Canonical address of the source file
        prefix: Filename prefix ("INDEX" or "CST")
        
    Returns:
        True if file was deleted, False if it didn't exist
    """
    cache_dir = _get_cache_dir()
    cache_filename_str = _cache_filename(canonical_path, prefix)
    cache_path = cache_dir / cache_filename_str
    
    try:
        cache_path.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def invalidate_all(canonical_path: str) -> int:
    """Remove all cached files (INDEX + CST) for a source file.
    
    Returns:
        Number of files deleted
    """
    count = 0
    for prefix in ("INDEX", "CST"):
        if invalidate(canonical_path, prefix):
            count += 1
    return count


def _maybe_evict(cache_dir: Path) -> int:
    """Evict oldest entries if cache exceeds MAX_ENTRIES.
    
    Uses file mtime (cache write time) for LRU ordering.
    
    Returns:
        Number of files evicted
    """
    try:
        cache_files = list(cache_dir.glob("*.json"))
    except OSError:
        return 0
    
    if len(cache_files) <= MAX_ENTRIES:
        return 0
    
    # Sort by mtime (oldest first)
    try:
        files_with_mtime = [(f, f.stat().st_mtime) for f in cache_files]
    except OSError:
        return 0
    
    files_with_mtime.sort(key=lambda x: x[1])
    
    # Evict oldest entries to get below threshold
    to_evict = len(cache_files) - MAX_ENTRIES + 100  # Remove 100 extra for headroom
    evicted = 0
    
    for cache_file, _ in files_with_mtime[:to_evict]:
        try:
            cache_file.unlink()
            evicted += 1
        except OSError:
            pass
    
    return evicted


def get_cache_stats() -> dict[str, Any]:
    """Get cache statistics.
    
    Returns:
        Dict with entry_count, total_bytes, oldest_entry, newest_entry
    """
    cache_dir = _get_cache_dir()
    
    try:
        cache_files = list(cache_dir.glob("*.json"))
    except OSError:
        return {"entry_count": 0, "total_bytes": 0, "error": "Cannot read cache dir"}
    
    if not cache_files:
        return {"entry_count": 0, "total_bytes": 0}
    
    total_bytes = 0
    oldest_mtime = float("inf")
    newest_mtime = 0.0
    
    for f in cache_files:
        try:
            stat = f.stat()
            total_bytes += stat.st_size
            oldest_mtime = min(oldest_mtime, stat.st_mtime)
            newest_mtime = max(newest_mtime, stat.st_mtime)
        except OSError:
            pass
    
    return {
        "entry_count": len(cache_files),
        "total_bytes": total_bytes,
        "oldest_entry": oldest_mtime if oldest_mtime != float("inf") else None,
        "newest_entry": newest_mtime if newest_mtime > 0 else None,
    }


__all__ = [
    "get_cached_index",
    "write_index_atomic",
    "invalidate",
    "invalidate_all",
    "get_cache_stats",
    "MAX_ENTRIES",
]
