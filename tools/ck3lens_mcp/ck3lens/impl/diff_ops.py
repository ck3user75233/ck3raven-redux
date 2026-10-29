"""
Diff Operations — machine-useful line/AST diff artifacts.

This module is pure implementation logic used by policy executors.
It does not emit reply codes; callers own terminal reply decisions.
"""
from __future__ import annotations

import difflib
import json
from dataclasses import dataclass
from typing import Any

from ck3lens.validate import parse_content


def _load_ast_from_db(conn: Any, content_hash: str) -> dict[str, Any] | None:
    """Try to load a stored AST from the asts table by content_hash.

    Returns a parse-result-shaped dict (keys: success, ast, errors, source="db")
    or None if no row found or deserialization fails.
    """
    try:
        row = conn.execute(
            """
            SELECT ast_blob, ast_format, parse_ok, diagnostics_json
            FROM asts
            WHERE content_hash = ?
            ORDER BY ast_id DESC
            LIMIT 1
            """,
            (content_hash,),
        ).fetchone()
    except Exception:
        return None

    if row is None:
        return None

    ast_blob = row[0]
    ast_format = row[1]
    parse_ok = row[2]
    diagnostics_json = row[3]

    try:
        if ast_format == "json":
            raw = ast_blob if isinstance(ast_blob, str) else ast_blob.decode("utf-8")
            ast = json.loads(raw)
        elif ast_format == "msgpack":
            import msgpack  # type: ignore[import]
            ast = msgpack.unpackb(ast_blob, raw=False)
        else:
            return None
    except Exception:
        return None

    errors: list[Any] = []
    if diagnostics_json:
        try:
            errors = json.loads(diagnostics_json)
        except Exception:
            pass

    return {
        "success": bool(parse_ok),
        "ast": ast,
        "errors": errors,
        "source": "db",
    }


@dataclass
class DiffConfig:
    normalize_indent: bool = True
    tab_width: int = 4
    context_lines: int = 4


def _norm_lines(lines: list[str], cfg: DiffConfig) -> list[str]:
    if not cfg.normalize_indent:
        return lines
    return [line.expandtabs(cfg.tab_width) for line in lines]


def build_line_diff_artifact(
    relpath: str,
    old_text: str,
    new_text: str,
    *,
    cfg: DiffConfig,
    old_cvid: str,
    new_cvid: str,
    old_hash: str,
    new_hash: str,
) -> dict[str, Any]:
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()

    old_norm = _norm_lines(old_lines, cfg)
    new_norm = _norm_lines(new_lines, cfg)

    exact_equal = old_text == new_text
    indent_equal = old_norm == new_norm

    matcher = difflib.SequenceMatcher(None, old_norm, new_norm, autojunk=False)
    groups = list(matcher.get_grouped_opcodes(cfg.context_lines))

    added = 0
    deleted = 0
    hunks: list[dict[str, Any]] = []

    for group in groups:
        first = next((op for op in group if op[0] != "equal"), None)
        old_start = first[1] + 1 if first else 1
        new_start = first[3] + 1 if first else 1

        hunk_lines: list[dict[str, Any]] = []
        old_min = None
        old_max = None
        new_min = None
        new_max = None

        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                for i, j in zip(range(i1, i2), range(j1, j2)):
                    hunk_lines.append({
                        "op": "ctx",
                        "old_line": i + 1,
                        "new_line": j + 1,
                        "old_text": old_lines[i],
                        "new_text": new_lines[j],
                    })
                    old_min = i + 1 if old_min is None else min(old_min, i + 1)
                    old_max = i + 1 if old_max is None else max(old_max, i + 1)
                    new_min = j + 1 if new_min is None else min(new_min, j + 1)
                    new_max = j + 1 if new_max is None else max(new_max, j + 1)
            elif tag == "delete":
                for i in range(i1, i2):
                    deleted += 1
                    hunk_lines.append({
                        "op": "del",
                        "old_line": i + 1,
                        "new_line": None,
                        "old_text": old_lines[i],
                        "new_text": "",
                    })
                    old_min = i + 1 if old_min is None else min(old_min, i + 1)
                    old_max = i + 1 if old_max is None else max(old_max, i + 1)
            elif tag == "insert":
                for j in range(j1, j2):
                    added += 1
                    hunk_lines.append({
                        "op": "ins",
                        "old_line": None,
                        "new_line": j + 1,
                        "old_text": "",
                        "new_text": new_lines[j],
                    })
                    new_min = j + 1 if new_min is None else min(new_min, j + 1)
                    new_max = j + 1 if new_max is None else max(new_max, j + 1)
            elif tag == "replace":
                ob = old_lines[i1:i2]
                nb = new_lines[j1:j2]
                deleted += len(ob)
                added += len(nb)

                pair_count = min(len(ob), len(nb))
                for k in range(pair_count):
                    oi = i1 + k
                    nj = j1 + k
                    hunk_lines.append({
                        "op": "chg",
                        "old_line": oi + 1,
                        "new_line": nj + 1,
                        "old_text": old_lines[oi],
                        "new_text": new_lines[nj],
                    })
                    old_min = oi + 1 if old_min is None else min(old_min, oi + 1)
                    old_max = oi + 1 if old_max is None else max(old_max, oi + 1)
                    new_min = nj + 1 if new_min is None else min(new_min, nj + 1)
                    new_max = nj + 1 if new_max is None else max(new_max, nj + 1)

                for k in range(pair_count, len(ob)):
                    oi = i1 + k
                    hunk_lines.append({
                        "op": "del",
                        "old_line": oi + 1,
                        "new_line": None,
                        "old_text": old_lines[oi],
                        "new_text": "",
                    })
                    old_min = oi + 1 if old_min is None else min(old_min, oi + 1)
                    old_max = oi + 1 if old_max is None else max(old_max, oi + 1)

                for k in range(pair_count, len(nb)):
                    nj = j1 + k
                    hunk_lines.append({
                        "op": "ins",
                        "old_line": None,
                        "new_line": nj + 1,
                        "old_text": "",
                        "new_text": new_lines[nj],
                    })
                    new_min = nj + 1 if new_min is None else min(new_min, nj + 1)
                    new_max = nj + 1 if new_max is None else max(new_max, nj + 1)

        hunks.append({
            "header": f"@@ ~{old_start} (old) / ~{new_start} (new) @@",
            "old_range": {
                "start": old_min,
                "end": old_max,
            },
            "new_range": {
                "start": new_min,
                "end": new_max,
            },
            "lines": hunk_lines,
        })

    return {
        "diff_version": "v1",
        "relpath": relpath,
        "old": {
            "cvid": old_cvid,
            "hash": old_hash,
            "line_count": len(old_lines),
            "byte_count": len(old_text.encode("utf-8", errors="replace")),
        },
        "new": {
            "cvid": new_cvid,
            "hash": new_hash,
            "line_count": len(new_lines),
            "byte_count": len(new_text.encode("utf-8", errors="replace")),
        },
        "normalization": {
            "indent_expanded": cfg.normalize_indent,
            "tab_width": cfg.tab_width,
        },
        "equivalence": {
            "exact_equal": exact_equal,
            "indent_normalized_equal": indent_equal,
            "formatting_only": (not exact_equal) and indent_equal,
        },
        "stats": {
            "hunk_count": len(hunks),
            "added_lines": added,
            "deleted_lines": deleted,
        },
        "hunks": hunks,
    }


def build_ast_diff(
    relpath: str,
    old_text: str,
    new_text: str,
    *,
    old_cvid: str,
    new_cvid: str,
    conn: Any = None,
    old_hash: str | None = None,
    new_hash: str | None = None,
) -> dict[str, Any]:
    from deepdiff import DeepDiff  # type: ignore[import]

    # Try DB-stored AST first; fall back to live parse if unavailable.
    old_parse: dict[str, Any] | None = None
    old_ast_source = "parsed"
    if conn is not None and old_hash is not None:
        old_parse = _load_ast_from_db(conn, old_hash)
        if old_parse is not None:
            old_ast_source = "db"
    if old_parse is None:
        old_parse = parse_content(old_text, f"{relpath}@{old_cvid}")

    new_parse: dict[str, Any] | None = None
    new_ast_source = "parsed"
    if conn is not None and new_hash is not None:
        new_parse = _load_ast_from_db(conn, new_hash)
        if new_parse is not None:
            new_ast_source = "db"
    if new_parse is None:
        new_parse = parse_content(new_text, f"{relpath}@{new_cvid}")

    warnings: list[dict[str, Any]] = []
    if not old_parse.get("success"):
        warnings.append({
            "side": "old",
            "code": "WA-PARSE-I-001",
            "errors": old_parse.get("errors", []),
        })
    if not new_parse.get("success"):
        warnings.append({
            "side": "new",
            "code": "WA-PARSE-I-001",
            "errors": new_parse.get("errors", []),
        })

    old_ast = old_parse.get("ast") or {}
    new_ast = new_parse.get("ast") or {}

    diff = DeepDiff(
        old_ast,
        new_ast,
        ignore_order=True,
        report_repetition=True,
        verbose_level=2,
    )

    # Serialize: DeepDiff returns a custom dict-like object; convert to plain dict
    # with string keys for JSON serialization.
    changes: dict[str, Any] = {}
    for change_type, detail in diff.items():
        try:
            changes[change_type] = _serialize_deepdiff_detail(detail)
        except Exception as exc:
            changes[change_type] = {"_serialization_error": str(exc)}

    return {
        "mode": "ast",
        "ast_source": {"old": old_ast_source, "new": new_ast_source},
        "parse_ok": old_parse.get("success", False) and new_parse.get("success", False),
        "warnings": warnings,
        "change_count": sum(
            len(v) if isinstance(v, (dict, list)) else 1
            for v in changes.values()
        ),
        "changes": changes,
    }


def _serialize_deepdiff_detail(detail: Any) -> Any:
    """Recursively convert DeepDiff output to plain JSON-serializable structures."""
    if isinstance(detail, dict):
        return {str(k): _serialize_deepdiff_detail(v) for k, v in detail.items()}
    if isinstance(detail, (list, tuple, set, frozenset)):
        return [_serialize_deepdiff_detail(i) for i in detail]
    if hasattr(detail, "__dict__"):
        # DeepDiff PrettyOrderedSet, DiffLevel, etc.
        try:
            return _serialize_deepdiff_detail(detail.__dict__)
        except Exception:
            return str(detail)
    # Scalars — ensure JSON-safe
    if isinstance(detail, (str, int, float, bool)) or detail is None:
        return detail
    return str(detail)
