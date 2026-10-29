"""
Conflict Operations — File Conflict Detection

Implements original-file-aware conflict detection for CK3 mod playsets.
See docs/FILE_CONFLICT_DETECTION_REDESIGN.md for the full design.

Architecture:
- Takes database connection, CVID set, load_order_map, and optional filters
- Returns structured conflict data grouped by original file per directory
- Detects full overrides, partial overrides, and failed patch warnings
- Tracks secondary conflicts (override files that are themselves overridden)

Origin Kinds:
- original (primary):   Genuinely new filename with no precedent.
                        Standalone content — readable in isolation.
- original (secondary): First appearance of a filename that is itself a
                        partial override of another original. NOT standalone —
                        must be read together with the file it patches.
"""
from __future__ import annotations

import os
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from ck3raven.resolver.policies import MergePolicy, get_policy_for_folder as _get_policy_for_path


def _get_policy_for_folder(relpath: str) -> MergePolicy:
    """Extract folder from relpath and determine merge policy."""
    parts = relpath.replace("\\", "/").rsplit("/", 1)
    folder = parts[0] if len(parts) > 1 else ""
    return _get_policy_for_path(folder)


@dataclass
class FileEntry:
    """A file from the database with its mod context."""
    filename: str
    directory: str
    relpath: str
    mod_name: str
    load_order: int
    cvid: str


@dataclass
class OriginalInfo:
    """Metadata about an original file in the per-directory registry.
    
    origin_kind:
        "primary"   — genuinely new filename, standalone content
        "secondary" — first appearance but IS a partial override of another file;
                      must be read together with patches_original to understand contribution
    
    patches_original:
        For secondary originals, the filename of the primary original this file patches.
        None for primary originals.
    """
    entry: FileEntry
    origin_kind: str  # "primary" | "secondary"
    patches_original: str | None = None  # filename of the patched original (secondary only)


@dataclass
class OverrideRecord:
    """A detected override relationship."""
    override_type: str  # "full_override", "partial_override", "failed_patch_warning"
    filename: str
    relpath: str
    mod: str
    load_order: int
    # For full_override: is this the last-loaded (winner)?
    is_winner: bool = False
    # For partial_override: the prefix used
    prefix: str = ""
    # For failed_patch_warning: the suffix and reason
    suffix: str = ""
    reason: str = ""


def detect_primary_file_conflicts(
    conn: sqlite3.Connection,
    cvids: frozenset[str],
    load_order_map: dict[str, int],
    mod_name_map: dict[str, str],
    game_folder: str | None = None,
    limit: int = 500,
) -> dict:
    """
    Detect file-level conflicts using the original-file-aware algorithm.
    
    For each directory, establishes which files are "originals" (first seen
    in load order), then classifies later files as full overrides, partial
    overrides, or failed patch warnings based on filename matching.
    
    Each original is tagged with an origin_kind:
    - "primary":   Genuinely new filename with no precedent. Standalone.
    - "secondary": First appearance of this filename, but it IS a partial
                   override of another original. Must be read alongside the
                   file it patches.
    
    Args:
        conn: Database connection
        cvids: Set of content_version_ids to include
        load_order_map: CVID → load_order mapping from session.mods[]
        mod_name_map: CVID → mod_name mapping from session.mods[]
        game_folder: Optional filter (e.g., "common/traits")
        limit: Max original-file conflict groups to return
    
    Returns:
        {
            "conflicts": [...],  # conflict entries as dicts
            "summary": {
                "total_originals_with_conflicts": int,
                "full_overrides": int,
                "partial_overrides": int,
                "failed_patch_warnings": int,
            }
        }
    """
    if not cvids:
        return {
            "conflicts": [],
            "summary": {
                "total_originals_with_conflicts": 0,
                "full_overrides": 0,
                "partial_overrides": 0,
                "failed_patch_warnings": 0,
            },
        }

    # ── Step 1: Query all files from visible CVIDs ──────────────────────
    ordered_cvids = sorted(cvids)
    placeholders = ",".join("?" for _ in ordered_cvids)
    sql = f"""
        SELECT
            f.relpath,
            f.content_version_id
        FROM files f
        WHERE f.content_version_id IN ({placeholders})
    """
    params: list = list(ordered_cvids)

    if game_folder:
        sql += " AND f.relpath LIKE ?"
        params.append(f"{game_folder}%")

    sql += " ORDER BY f.relpath"
    rows = conn.execute(sql, params).fetchall()

    # ── Step 2: Parse into FileEntry objects, group by directory ─────────
    by_directory: dict[str, list[FileEntry]] = defaultdict(list)

    for row in rows:
        relpath: str = row["relpath"]
        cvid: str = row["content_version_id"]
        # Normalize separators
        relpath = relpath.replace("\\", "/")
        # Split directory and filename
        if "/" in relpath:
            directory, filename = relpath.rsplit("/", 1)
        else:
            directory = ""
            filename = relpath

        lo = load_order_map.get(cvid, -1)
        mod = mod_name_map.get(cvid, "unknown")

        by_directory[directory].append(FileEntry(
            filename=filename,
            directory=directory,
            relpath=relpath,
            mod_name=mod,
            load_order=lo,
            cvid=cvid,
        ))

    # ── Step 3: Per-directory conflict detection ────────────────────────
    all_conflicts: list[dict] = []
    total_full = 0
    total_partial = 0
    total_warnings = 0

    for directory, entries in by_directory.items():
        # Sort by load_order ascending (first loaded first)
        entries.sort(key=lambda e: e.load_order)

        # original_files: filename → OriginalInfo (first occurrence per filename)
        original_files: dict[str, OriginalInfo] = {}
        # Per-original overrides and warnings
        overrides_for: dict[str, list[dict]] = defaultdict(list)
        warnings_for: dict[str, list[dict]] = defaultdict(list)
        # Track which originals have any conflict
        originals_with_conflicts: set[str] = set()

        for entry in entries:
            fname = entry.filename
            matched_as_partial_override = False
            # Track which original(s) this file is a partial override of
            # (used to determine origin_kind when registering)
            partial_override_targets: list[str] = []

            # ── i. EXACT MATCH: full override ───────────────────────
            if fname in original_files:
                overrides_for[fname].append({
                    "type": "full_override",
                    "filename": fname,
                    "relpath": entry.relpath,
                    "mod": entry.mod_name,
                    "load_order": entry.load_order,
                    "is_winner": False,  # resolved after loop
                })
                originals_with_conflicts.add(fname)
                # Update the original entry — the new mod is now "the" provider
                # Preserve origin_kind from the existing registration
                existing = original_files[fname]
                original_files[fname] = OriginalInfo(
                    entry=entry,
                    origin_kind=existing.origin_kind,
                    patches_original=existing.patches_original,
                )

            # ── ii. SUBSTRING CHECK: partial override or failed patch ─
            else:
                for orig_fname in list(original_files.keys()):
                    # Base-game files (load_order 0) are all separate Paradox-
                    # authored originals — never partial overrides or patches.
                    # e.g. jb_hunt_events.txt is NOT an override of hunt_events.txt.
                    # Non-base-game mods CAN have intra-mod partial overrides
                    # (e.g. aaa_filename.txt patching filename.txt within the same mod).
                    if entry.load_order == 0:
                        break

                    # --- Partial override: fname ends with orig_fname ---
                    if (
                        len(fname) > len(orig_fname)
                        and fname.endswith(orig_fname)
                    ):
                        prefix = fname[: -len(orig_fname)]
                        overrides_for[orig_fname].append({
                            "type": "partial_override",
                            "filename": fname,
                            "relpath": entry.relpath,
                            "prefix": prefix,
                            "mod": entry.mod_name,
                            "load_order": entry.load_order,
                        })
                        originals_with_conflicts.add(orig_fname)
                        matched_as_partial_override = True
                        partial_override_targets.append(orig_fname)
                        # NOTE: don't break — could match multiple originals
                        # (rare but possible)

                    # --- Failed patch warning: stem match with suffix ---
                    else:
                        orig_stem, orig_ext = os.path.splitext(orig_fname)
                        if not orig_stem:
                            continue
                        cur_stem, cur_ext = os.path.splitext(fname)
                        if (
                            cur_ext == orig_ext
                            and cur_stem.startswith(orig_stem)
                            and fname != orig_fname
                            and len(cur_stem) > len(orig_stem)
                            and cur_stem[len(orig_stem)] == "_"
                        ):
                            suffix = cur_stem[len(orig_stem):]
                            warnings_for[orig_fname].append({
                                "type": "failed_patch_warning",
                                "filename": fname,
                                "relpath": entry.relpath,
                                "suffix": suffix,
                                "mod": entry.mod_name,
                                "load_order": entry.load_order,
                                "reason": (
                                    f"Suffix '{suffix}' after original stem "
                                    f"makes this a new file, not a patch of "
                                    f"{orig_fname}"
                                ),
                            })
                            originals_with_conflicts.add(orig_fname)
                            # This file is still registered as a new original
                            # (see step iii below) because CK3 treats it as one

            # ── iii. Register as original if not already present ─────
            if fname not in original_files:
                if matched_as_partial_override and partial_override_targets:
                    # Secondary original: first appearance of this filename,
                    # but it IS a partial override of another file.
                    # Use the first target as the patches reference.
                    original_files[fname] = OriginalInfo(
                        entry=entry,
                        origin_kind="secondary",
                        patches_original=partial_override_targets[0],
                    )
                else:
                    # Primary original: genuinely new filename, standalone.
                    original_files[fname] = OriginalInfo(
                        entry=entry,
                        origin_kind="primary",
                        patches_original=None,
                    )

        # ── Resolve is_winner for full overrides ────────────────────
        for orig_fname in originals_with_conflicts:
            full_overrides = [
                o for o in overrides_for.get(orig_fname, [])
                if o["type"] == "full_override"
            ]
            if full_overrides:
                max_lo = max(o["load_order"] for o in full_overrides)
                for o in full_overrides:
                    o["is_winner"] = (o["load_order"] == max_lo)

        # ── Build conflict entries ──────────────────────────────────
        for orig_fname in sorted(originals_with_conflicts):
            ov_list = overrides_for.get(orig_fname, [])
            warn_list = warnings_for.get(orig_fname, [])

            if not ov_list and not warn_list:
                continue

            orig_info = original_files.get(orig_fname)
            if orig_info is None:
                continue  # shouldn't happen

            # The first_source is the mod that first introduced this filename.
            # For full overrides, original_files[fname] gets updated to the
            # latest provider, so we walk entries to find the true first.
            first_source: FileEntry | None = None
            for e in entries:
                if e.filename == orig_fname:
                    first_source = e
                    break

            if first_source is None:
                continue  # shouldn't happen

            orig_relpath = f"{directory}/{orig_fname}" if directory else orig_fname
            policy = _get_policy_for_folder(orig_relpath)

            total_full += sum(1 for o in ov_list if o["type"] == "full_override")
            total_partial += sum(1 for o in ov_list if o["type"] == "partial_override")
            total_warnings += len(warn_list)

            # Build original_source with origin_kind
            original_source: dict = {
                "mod": first_source.mod_name,
                "load_order": first_source.load_order,
                "origin_kind": orig_info.origin_kind,
            }
            if orig_info.patches_original is not None:
                original_source["patches"] = orig_info.patches_original

            all_conflicts.append({
                "directory": directory,
                "original_file": orig_fname,
                "original_relpath": orig_relpath,
                "original_source": original_source,
                "policy": policy.name,
                "overrides": ov_list,
                "warnings": warn_list,
            })

            if len(all_conflicts) >= limit:
                break

        if len(all_conflicts) >= limit:
            break

    return {
        "conflicts": all_conflicts,
        "summary": {
            "total_originals_with_conflicts": len(all_conflicts),
            "full_overrides": total_full,
            "partial_overrides": total_partial,
            "failed_patch_warnings": total_warnings,
        },
    }
