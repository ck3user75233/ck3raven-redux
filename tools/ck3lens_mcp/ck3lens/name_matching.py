from __future__ import annotations

from difflib import SequenceMatcher
import re
from typing import Iterable

_NEAR_MATCH_SPLIT_RE = re.compile(r"[^0-9a-z]+")


def _normalize_near_match_key(value: str) -> str:
    """Normalize strings for near-match scoring."""
    return "".join(_NEAR_MATCH_SPLIT_RE.split(value.casefold()))


def _tokenize_near_match_key(value: str) -> tuple[str, ...]:
    """Tokenize a string for overlap-based near matching."""
    return tuple(token for token in _NEAR_MATCH_SPLIT_RE.split(value.casefold()) if token)


def find_near_matches(
    query: str,
    candidates: Iterable[str],
    *,
    limit: int = 5,
    min_score: float = 0.55,
) -> list[str]:
    """Return ranked near matches for a user-supplied name."""
    query = query.strip()
    if not query or limit <= 0:
        return []

    query_casefold = query.casefold()
    query_normalized = _normalize_near_match_key(query)
    query_tokens = set(_tokenize_near_match_key(query))

    scored: list[tuple[float, str]] = []
    seen: set[str] = set()
    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)

        candidate_casefold = candidate.casefold()
        if candidate_casefold == query_casefold:
            continue

        candidate_normalized = _normalize_near_match_key(candidate)
        candidate_tokens = set(_tokenize_near_match_key(candidate))

        if query_normalized and candidate_normalized and query_normalized == candidate_normalized:
            score = 1.0
        else:
            raw_ratio = SequenceMatcher(None, query_casefold, candidate_casefold).ratio()
            normalized_ratio = 0.0
            if query_normalized and candidate_normalized:
                normalized_ratio = SequenceMatcher(None, query_normalized, candidate_normalized).ratio()

            token_overlap = 0.0
            if query_tokens and candidate_tokens:
                token_overlap = len(query_tokens & candidate_tokens) / max(len(query_tokens), len(candidate_tokens))

            contains_ratio = 0.0
            if query_normalized and candidate_normalized:
                if query_normalized in candidate_normalized or candidate_normalized in query_normalized:
                    contains_ratio = min(len(query_normalized), len(candidate_normalized)) / max(
                        len(query_normalized), len(candidate_normalized)
                    )

            score = max(raw_ratio, normalized_ratio, token_overlap, contains_ratio)

        if score >= min_score:
            scored.append((score, candidate))

    scored.sort(key=lambda item: (-item[0], item[1].casefold()))
    return [candidate for _, candidate in scored[:limit]]
