from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ck3lens.paths import ROOT_CK3RAVEN_DATA


_CANONICAL_PREFIX = "root:ck3raven_data"
_DEFAULT_SUBDIR = Path("wip") / "diff_reports" / "ck3_conflicts"


def _canonical_from_host(path: Path) -> str:
    rel = path.resolve().relative_to(ROOT_CK3RAVEN_DATA.resolve())
    rel_posix = rel.as_posix()
    if rel_posix:
        return f"{_CANONICAL_PREFIX}/{rel_posix}"
    return _CANONICAL_PREFIX


def _resolve_output_dir(output_dir: str | None) -> Path:
    if not output_dir:
        target = ROOT_CK3RAVEN_DATA / _DEFAULT_SUBDIR
        target.mkdir(parents=True, exist_ok=True)
        return target

    normalized = output_dir.strip()
    if normalized == _CANONICAL_PREFIX:
        target = ROOT_CK3RAVEN_DATA
    elif normalized.startswith(f"{_CANONICAL_PREFIX}/"):
        rel = normalized[len(_CANONICAL_PREFIX) + 1 :]
        target = ROOT_CK3RAVEN_DATA / Path(rel)
    else:
        raise ValueError("output_dir must be under root:ck3raven_data")

    target.mkdir(parents=True, exist_ok=True)
    return target


def _strip_canonical_prefix(path: str) -> str:
    """Strip root:key/ or mod:name/ prefix, returning only the relative path portion."""
    m = re.match(r"^(?:root|mod):[^/]+/(.+)$", path)
    return m.group(1) if m else path


def _slug_from_artifact(artifact: dict[str, Any]) -> str:
    relpath = artifact.get("relpath")
    if relpath and not relpath.startswith(("root:", "mod:")) and " -> " not in relpath:
        # Database diff: relpath is already a clean game-relative path
        basis = relpath
    else:
        # Filesystem diff: strip canonical prefix from new_path or old_path
        raw = (
            artifact.get("new_path")
            or artifact.get("old_path")
            or relpath
            or "diff_report"
        )
        basis = _strip_canonical_prefix(str(raw))

    text = str(basis)
    text = text.replace("\\", "/")
    text = text.replace("/", "__")
    text = text.replace(".", "_")
    text = text.replace(":", "_")
    text = re.sub(r"[^a-zA-Z0-9_\-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return f"diff__{text}" if text else "diff__report"


def write_diff_report_bundle(
    *,
    artifact: dict[str, Any],
    html: str,
    output_dir: str | None = None,
    stem: str | None = None,
) -> dict[str, Any]:
    out_dir = _resolve_output_dir(output_dir)
    name_stem = stem or _slug_from_artifact(artifact)

    json_path = out_dir / f"{name_stem}.json"
    html_path = out_dir / f"{name_stem}.html"

    line_stats = (artifact.get("line_diff") or {}).get("stats", {})
    equivalence = (artifact.get("line_diff") or {}).get("equivalence", {})

    payload = {
        "format": "ck3_conflicts_diff_report_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "artifact": artifact,
        "line_stats": line_stats,
        "equivalence": equivalence,
        "html_report": _canonical_from_host(html_path),
    }

    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    html_path.write_text(html, encoding="utf-8")

    return {
        "format": "report_files",
        "report_type": "disk",
        "report_dir": _canonical_from_host(out_dir),
        "json_report": _canonical_from_host(json_path),
        "html_report": _canonical_from_host(html_path),
        "line_stats": line_stats,
        "equivalence": equivalence,
    }
