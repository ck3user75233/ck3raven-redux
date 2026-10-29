"""
Fuzzy Matching Utilities for Symbol Search

Provides Levenshtein-based similarity matching to help agents find
symbols/files when exact search fails.

No external dependencies - pure Python implementation.
"""
from __future__ import annotations
from typing import TypedDict


class NearMatch(TypedDict):
    """Structure for a fuzzy match result."""
    query: str
    found: str
    similarity: float
    mod: str | None
    symbol_type: str | None
    analysis: str | None


def levenshtein_distance(s1: str, s2: str) -> int:
    """
    Compute Levenshtein edit distance between two strings.
    
    Uses Wagner-Fischer dynamic programming algorithm.
    O(len(s1) * len(s2)) time and O(min(len(s1), len(s2))) space.
    """
    if len(s1) < len(s2):
        s1, s2 = s2, s1
    
    if len(s2) == 0:
        return len(s1)
    
    prev_row = list(range(len(s2) + 1))
    curr_row = [0] * (len(s2) + 1)
    
    for i, c1 in enumerate(s1):
        curr_row[0] = i + 1
        for j, c2 in enumerate(s2):
            cost = 0 if c1 == c2 else 1
            curr_row[j + 1] = min(
                prev_row[j + 1] + 1,      # deletion
                curr_row[j] + 1,           # insertion
                prev_row[j] + cost,        # substitution
            )
        prev_row, curr_row = curr_row, prev_row
    
    return prev_row[len(s2)]


def similarity_ratio(s1: str, s2: str) -> float:
    """
    Compute similarity ratio between two strings.
    
    Returns a value between 0.0 (completely different) and 1.0 (identical).
    Based on Levenshtein distance normalized by max string length.
    """
    if s1 == s2:
        return 1.0
    if not s1 or not s2:
        return 0.0
    
    distance = levenshtein_distance(s1.lower(), s2.lower())
    max_len = max(len(s1), len(s2))
    return 1.0 - (distance / max_len)


def analyze_difference(query: str, found: str) -> str | None:
    """
    Analyze the difference between query and found string.
    
    Returns human-readable analysis like "likely typo" or "underscore variation".
    """
    q = query.lower()
    f = found.lower()
    
    # Check for single character difference
    distance = levenshtein_distance(q, f)
    if distance == 1:
        # Identify the type of single-char difference
        if len(q) == len(f):
            # Substitution - find the differing character
            for i, (qc, fc) in enumerate(zip(q, f)):
                if qc != fc:
                    # Check for common typos (adjacent keys, similar letters)
                    similar_chars = {
                        ('e', 'i'), ('i', 'e'), ('a', 'o'), ('o', 'a'),
                        ('k', 'c'), ('c', 'k'), ('s', 'z'), ('z', 's'),
                        ('n', 'm'), ('m', 'n'), ('u', 'v'), ('v', 'u'),
                    }
                    if (qc, fc) in similar_chars or (fc, qc) in similar_chars:
                        return f"likely typo ({qc}→{fc})"
                    return "single character substitution"
        elif len(q) < len(f):
            return "missing character"
        else:
            return "extra character"
    
    # Check for underscore vs no underscore
    if q.replace("_", "") == f.replace("_", ""):
        return "underscore variation"
    
    # Check for plural/singular
    if q.rstrip("s") == f or q == f.rstrip("s"):
        return "plural/singular"
    
    # Check for prefix difference (e.g., b_ vs c_)
    if len(q) > 2 and len(f) > 2:
        if q[2:] == f[2:] and q[1] == "_" and f[1] == "_":
            return f"prefix difference ({q[:2]} vs {f[:2]})"
    
    # Check for numbered suffix (e.g., _1, _2)
    import re
    q_base = re.sub(r'_\d+$', '', q)
    f_base = re.sub(r'_\d+$', '', f)
    if q_base == f_base:
        return "numbered variant"
    
    # Generic similar
    ratio = similarity_ratio(query, found)
    if ratio >= 0.9:
        return "very similar"
    elif ratio >= 0.8:
        return "similar"
    
    return None


def find_near_matches(
    query: str,
    candidates: list[tuple[str, str | None, str | None]],  # (name, mod, symbol_type)
    threshold: float = 0.8,
    max_results: int = 5,
) -> list[NearMatch]:
    """
    Find near-matches for a query among candidate strings.
    
    Args:
        query: The search query that found no exact matches
        candidates: List of (symbol_name, mod_name, symbol_type) tuples
        threshold: Minimum similarity ratio (0.0-1.0) to include
        max_results: Maximum number of results to return
    
    Returns:
        List of NearMatch dicts sorted by similarity (highest first)
    """
    matches: list[tuple[float, NearMatch]] = []
    query_lower = query.lower()
    
    for name, mod, symbol_type in candidates:
        ratio = similarity_ratio(query_lower, name.lower())
        if ratio >= threshold:
            analysis = analyze_difference(query, name)
            matches.append((ratio, {
                "query": query,
                "found": name,
                "similarity": round(ratio, 3),
                "mod": mod,
                "symbol_type": symbol_type,
                "analysis": analysis,
            }))
    
    # Sort by similarity descending
    matches.sort(key=lambda x: x[0], reverse=True)
    
    return [m[1] for m in matches[:max_results]]


def fuzzy_search_symbols(
    db,
    query: str,
    cvids: list[str] | None,
    threshold: float = 0.8,
    max_candidates: int = 1000,
    max_results: int = 5,
) -> list[NearMatch]:
    """
    Search for near-matching symbols in the database.
    
    Fetches candidate symbols and runs fuzzy matching against them.
    Scoped to session CVIDs for playset filtering.
    
    Uses a two-pass strategy:
    1. First pass: LIKE prefix match (first 3+ chars) for targeted results
    2. Second pass: If no matches found, broader length-based random sample
    
    Args:
        db: Database connection with conn attribute
        query: The search query that found no exact matches
        cvids: Content version IDs to scope search (None = all)
        threshold: Minimum similarity ratio
        max_candidates: Max symbols to fetch for comparison
        max_results: Max near-matches to return
    
    Returns:
        List of NearMatch results
    """
    if not db or not db.conn:
        return []
    
    # Calculate length bounds for candidate filtering
    query_len = len(query)
    length_margin = max(5, int(query_len * 0.3))
    min_len = max(1, query_len - length_margin)
    max_len = query_len + length_margin
    
    # Determine prefix for LIKE matching (first 3+ chars, minimum 2)
    prefix_len = min(3, max(2, query_len // 2))
    prefix = query[:prefix_len]
    like_pattern = f"{prefix}%"
    
    candidates: list[tuple[str, str, str]] = []
    
    # === PASS 1: LIKE prefix match (targeted) ===
    if cvids:
        placeholders = ",".join("?" * len(cvids))
        sql_prefix = f"""
            SELECT DISTINCT s.name, s.symbol_type, cv.name as mod_name
            FROM symbols s
            JOIN asts a ON s.ast_id = a.ast_id
            JOIN files f ON a.content_hash = f.content_hash
            JOIN content_versions cv ON f.content_version_id = cv.content_version_id
            WHERE f.content_version_id IN ({placeholders})
              AND LENGTH(s.name) BETWEEN ? AND ?
              AND s.name LIKE ?
            LIMIT ?
        """
        params_prefix = (*cvids, min_len, max_len, like_pattern, max_candidates)
    else:
        sql_prefix = """
            SELECT DISTINCT s.name, s.symbol_type, cv.name as mod_name
            FROM symbols s
            JOIN asts a ON s.ast_id = a.ast_id
            JOIN files f ON a.content_hash = f.content_hash
            JOIN content_versions cv ON f.content_version_id = cv.content_version_id
            WHERE LENGTH(s.name) BETWEEN ? AND ?
              AND s.name LIKE ?
            LIMIT ?
        """
        params_prefix = (min_len, max_len, like_pattern, max_candidates)
    
    try:
        rows = db.conn.execute(sql_prefix, params_prefix).fetchall()
        candidates = [(row[0], row[2], row[1]) for row in rows]  # (name, mod_name, symbol_type)
    except Exception:
        pass
    
    # Run fuzzy matching on prefix candidates
    if candidates:
        matches = find_near_matches(query, candidates, threshold, max_results)
        if matches:
            return matches
    
    # === PASS 2: Length-based random sample (fallback) ===
    if cvids:
        placeholders = ",".join("?" * len(cvids))
        sql_random = f"""
            SELECT DISTINCT s.name, s.symbol_type, cv.name as mod_name
            FROM symbols s
            JOIN asts a ON s.ast_id = a.ast_id
            JOIN files f ON a.content_hash = f.content_hash
            JOIN content_versions cv ON f.content_version_id = cv.content_version_id
            WHERE f.content_version_id IN ({placeholders})
              AND LENGTH(s.name) BETWEEN ? AND ?
            ORDER BY RANDOM()
            LIMIT ?
        """
        params_random = (*cvids, min_len, max_len, max_candidates)
    else:
        sql_random = """
            SELECT DISTINCT s.name, s.symbol_type, cv.name as mod_name
            FROM symbols s
            JOIN asts a ON s.ast_id = a.ast_id
            JOIN files f ON a.content_hash = f.content_hash
            JOIN content_versions cv ON f.content_version_id = cv.content_version_id
            WHERE LENGTH(s.name) BETWEEN ? AND ?
            ORDER BY RANDOM()
            LIMIT ?
        """
        params_random = (min_len, max_len, max_candidates)
    
    try:
        rows = db.conn.execute(sql_random, params_random).fetchall()
        candidates = [(row[0], row[2], row[1]) for row in rows]  # (name, mod_name, symbol_type)
    except Exception:
        return []
    
    return find_near_matches(query, candidates, threshold, max_results)


def build_search_summary(
    query: str,
    exact_count: int,
    near_matches: list[NearMatch],
) -> str:
    """Build human-readable search summary including fuzzy results."""
    if exact_count > 0:
        return f"Found {exact_count} exact matches for '{query}'."
    
    if not near_matches:
        return f"No matches for '{query}'. No similar symbols found."
    
    best = near_matches[0]
    pct = int(best["similarity"] * 100)
    return (
        f"No exact match for '{query}'. "
        f"{len(near_matches)} near-match(es), best: '{best['found']}' at {pct}% similarity."
    )
