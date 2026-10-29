# File Conflict Detection Redesign

> **Status:** DESIGN BRIEF (v2)  
> **Date:** February 28, 2026  
> **Scope:** `ck3_conflicts(command="files")` and related infrastructure  
> **Supersedes:** File-level conflict logic in `server.py` lines 1618–1675 and `conflict_ops.get_file_conflicts()`

---

## 1. Problem Statement

The current `ck3_conflicts(command="files")` implementation has fundamental defects that make its output misleading for compatibility patching work.

### 1.1 Current Approach (Broken)

The code groups files by **exact `relpath` match** across all mods, then flags any relpath appearing in 2+ mods as a "conflict." It appends a naive `has_zzz_prefix: bool` that only checks `fname.startswith("zzz_")`.

**What this catches:** Only full overwrites — cases where two mods provide the exact same `relpath` (e.g., both have `common/traits/00_traits.txt`).

**What this misses entirely:**

1. **Partial overrides** — A mod adds `common/traits/zzz_00_traits.txt` to override specific symbols in vanilla's `common/traits/00_traits.txt`. The current code sees these as two completely unrelated files because their relpaths don't match.

2. **Non-zzz prefixes** — Mods use arbitrary prefixes (`fff_`, `aaa_`, `mod_name_`, etc.) for partial overrides. Only `zzz_` is detected.

3. **Failed patch warnings** — A mod creates `common/traits/00_traits_fix.txt` intending to patch `00_traits.txt`, but the `_fix` suffix makes CK3 treat it as an entirely new file (not an override of `00_traits.txt`). This is a common modder mistake that the tool should detect.

4. **No concept of "original file"** — The system treats all files equally. It has no notion that vanilla's `00_traits.txt` is the *target* being modified by later mods. Without establishing originals, you can't classify later files as overrides vs. new content.

5. **`has_zzz_prefix` is cosmetic** — It decorates the output but doesn't connect the prefixed file to the original it targets. Knowing a file starts with `zzz_` is useless without knowing *which original file* it's overriding.

### 1.2 Additional Issues (Full Tool Review)

**A. Spill exemption missing** — `ck3_conflicts` is NOT in `_SPILL_EXEMPT` (`safety.py` line 67). The 10KB global spill threshold causes conflict results to be silently spilled to disk, defeating the purpose of the tool. Conflict data IS the point of the call — same rationale as `ck3_file`.

**B. `conflict_ops.py` uses CVID ordering as proxy for load order** — `get_file_conflicts()` sorts by `contentVersionId` descending, assuming higher CVID = later in load order. This is wrong. CVIDs are assigned at ingest time, not playset ordering. The server.py handler correctly uses `load_order_map` from `session.mods[]`, but `conflict_ops.py` does not.

**C. `conflict_ops.py` is dead code** — The `server.py` handler for `command="files"` has its own inline implementation and does NOT call `conflict_ops.get_file_conflicts()`. The `conflict_ops` module is only used indirectly via `get_conflict_summary()`, which itself has bugs.

**D. `include_compatch` / `COMPATCH_PATTERNS` must be removed** — The tool currently tries to detect "compatch mods" by pattern-matching mod names against strings like "patch", "compatibility", "fix". This produces false positives (e.g., "Unofficial Patch", "Fix Diplomacy") and embeds interpretation logic that does not belong in the conflict detection tool. Whether a mod is a compatch is context the *user* provides (via playset JSON annotations), not something the tool guesses. The agent reading the conflict report should learn which mods are compatches from the user's playset context, which explains *why* those mods have many conflicts (they are intentional). This filtering must be expunged — no branching on compatch status anywhere in `ck3_conflicts`.

**E. No merge policy on file conflicts** — `command="symbols"` correctly attaches `policy` (OVERRIDE/FIOS/CONTAINER_MERGE/PER_KEY_OVERRIDE) to each conflict. `command="files"` does not. File conflicts are just as policy-dependent — a `gui/` file conflict under FIOS has opposite resolution semantics to a `common/traits/` conflict under OVERRIDE.

**F. `symbol_names` filter applied post-query** — The symbols command fetches `limit * 2` rows then filters by name in Python. This means if you ask for specific symbols and they're not in the top `limit * 2` by `source_count DESC`, you won't find them. The filter should be pushed into the SQL WHERE clause.

---

## 2. Core Concepts

### 2.1 Original File

An **original file** is the first instance of a filename encountered in load order within a given directory. The concept is per-directory — `00_traits.txt` in `common/traits/` is independent from `00_traits.txt` in any other directory.

- Vanilla files (load_order=0) are almost always original files
- A mod can introduce a new filename that has never existed — that too is an original file
- Original files are established per-directory as you walk the load order

### 2.2 Primary File Conflicts

These are relationships where a later file explicitly targets an original file:

| Type | Pattern | Example | Meaning |
|------|---------|---------|---------|
| **Full Override** | `[mod_fname]` == `[original_fname]` | vanilla: `00_traits.txt` → mod: `00_traits.txt` | Mod completely replaces the original file |
| **Partial Override** | `[prefix]` + `[original_fname]` == `[mod_fname]` | vanilla: `00_traits.txt` → mod: `zzz_00_traits.txt` | Mod overrides specific symbols within the original file's domain |
| **Failed Patch Warning** | `[original_fname]` stem is a prefix but has suffix | vanilla: `00_traits.txt` → mod: `00_traits_fix.txt` | Modder likely intended a patch but the suffix makes CK3 treat this as a new, independent original file |

### 2.3 Secondary File Conflicts

A secondary file conflict occurs when a file that is itself an override of an original also gets fully overwritten by another mod providing the exact same filename.

**Example:**

```
base game (load_order=0): example-file-name.txt          ← ORIGINAL
mod 1     (load_order=5): zzz_example-file-name.txt      ← PARTIAL OVERRIDE of original
mod 2     (load_order=8): fff_example-file-name.txt      ← PARTIAL OVERRIDE of original
mod 3     (load_order=10): zzz_example-file-name.txt     ← PARTIAL OVERRIDE of original (PRIMARY)
                                                             FULL OVERRIDE of mod 1's file (SECONDARY)
```

Mod 3's `zzz_example-file-name.txt` has TWO conflict relationships:
- **Primary:** It is a partial override of the original `example-file-name.txt` (prefix `zzz_`)
- **Secondary:** It fully overwrites mod 1's `zzz_example-file-name.txt` (exact filename match, mod 3 loads later)

The primary conflict tells you what original content is being targeted. The secondary conflict tells you that another mod's patch is being replaced. Both are important for understanding the full picture.

**Detection:** Secondary conflicts fall out naturally. When mod 3's `zzz_example-file-name.txt` is processed, `zzz_example-file-name.txt` already exists in the originals registry (introduced by mod 1). So it registers as a full override — that's the secondary conflict. It also matches the partial override check against the base `example-file-name.txt` — that's the primary conflict. Both are recorded.

### 2.4 What Is NOT a Conflict

- A mod introducing an entirely new filename with no substring match to any original — this is new content, not a conflict

---

## 3. Algorithm: Primary and Secondary File Conflict Detection

### 3.1 Input

- `session.mods[]` — mod list with `cvid` and `load_order` per mod
- `files` table — all files indexed, with `relpath` and `content_version_id`
- Optional `game_folder` filter (e.g., `"common/traits"`)

### 3.2 Processing Steps

```
1. Query all files from visible CVIDs:
   - Extract directory = relpath minus filename (e.g., "common/traits")
   - Extract filename = last path component
   - Attach load_order and mod_name from session.mods[] via CVID

2. For each directory:
   a. Sort all (filename, mod, load_order, cvid) tuples by load_order ASC
   b. Maintain a dict: original_files = {}
      Key: filename (e.g., "00_traits.txt")
      Value: {mod, load_order, cvid}
   
   c. For each file entry in load_order:
      matched = False
      
      i. EXACT MATCH: If filename exists in original_files:
         → This is a FULL OVERRIDE of original_files[filename]
         Record: {type: "full_override", target: filename,
                  target_source: original_files[filename],
                  overrider: current_entry}
         matched = True
         
         # Check if the target itself was a partial override of something
         # If so, this is ALSO a secondary conflict
         # (handled automatically — the override target was registered as
         #  an original, but it may also appear in the overrides list of
         #  another original. The data is complete.)
      
      ii. SUBSTRING CHECK (only if not exact match):
          For each original_fname in original_files:
            - Check if current filename ENDS WITH original_fname
              (filename[-len(original_fname):] == original_fname)
            - If yes, and prefix = filename[:-len(original_fname)] is non-empty:
              → PARTIAL OVERRIDE
              Record: {type: "partial_override", target: original_fname,
                       prefix: prefix,
                       target_source: original_files[original_fname],
                       overrider: current_entry}
              matched = True
              (do NOT break — a file could theoretically match multiple
               originals, though this is rare)
          
          Also check for suffix matches (failed patches):
          For each original_fname in original_files:
            - original_stem = original_fname without extension
            - original_ext = extension of original_fname
            - If: filename starts with original_stem
              AND filename != original_fname
              AND filename ends with original_ext
              AND filename[len(original_stem)] == '_'
              → FAILED PATCH WARNING
              suffix = filename[len(original_stem):-len(original_ext)]
              Record: {type: "failed_patch_warning", target: original_fname,
                       suffix: suffix, source: current_entry,
                       reason: "Suffix after original stem makes this a new 
                                file, not a patch"}
              # This file is still added as a new original (step iii)
              # because CK3 treats it as one
      
      iii. Register as original:
           If filename is NOT already in original_files:
             original_files[filename] = current_entry
           # Note: Even files that ARE overrides get registered as originals.
           # A partial override file (zzz_00_traits.txt) is itself a new
           # original that other mods can full-override.
           # A full override replaces the original_files entry.
           If filename IS already in original_files and this is a full override:
             # Update the entry — the new mod is now "the" provider
             # (but we already recorded the conflict above)
             original_files[filename] = current_entry

3. Collect all records across all directories → output
```

### 3.3 Optimization

Comparisons are scoped to the same directory:

- `common/traits/` files only compare against other `common/traits/` filenames
- `events/` files only compare against other `events/` filenames
- This naturally partitions the work and is already how CK3 resolves files

No global cross-directory comparisons needed.

### 3.4 Handling Multiple Overrides of the Same Original

When multiple mods provide full overrides of the same original:

```
vanilla (load_order=0):  00_traits.txt     ← ORIGINAL
mod A   (load_order=5):  00_traits.txt     ← FULL OVERRIDE (loses)
mod B   (load_order=10): 00_traits.txt     ← FULL OVERRIDE (wins, last loaded)
```

All are recorded. The output includes load_order for each, and `is_winner` marks the last-loaded one.

When multiple mods provide partial overrides of the same original:

```
vanilla (load_order=0):  00_traits.txt     ← ORIGINAL
mod A   (load_order=5):  zzz_00_traits.txt ← PARTIAL OVERRIDE of original
mod B   (load_order=10): aaa_00_traits.txt ← PARTIAL OVERRIDE of original
```

Both partial overrides target the same original but have different filenames — they coexist. Their *symbols* may conflict, but that's symbol-level analysis.

When a partial override is itself fully overwritten (secondary conflict):

```
vanilla (load_order=0):  00_traits.txt         ← ORIGINAL
mod A   (load_order=5):  zzz_00_traits.txt     ← PARTIAL OVERRIDE of original
mod B   (load_order=10): zzz_00_traits.txt     ← FULL OVERRIDE of mod A's file (secondary)
                                                   PARTIAL OVERRIDE of original (primary)
```

Mod B gets two conflict entries: one primary (targeting the original), one secondary (replacing mod A's version of the same prefixed file).

### 3.5 Edge Case: Mod-introduced originals

```
mod A (load_order=5):  my_custom_traits.txt  ← ORIGINAL (mod-introduced)
mod B (load_order=10): my_custom_traits.txt  ← FULL OVERRIDE of mod A's file
```

Correct — mod A introduced the original, mod B overrides it. This is how compatches work: they override another mod's file, not vanilla's.

### 3.6 Edge Case: Prefix-only filename

A filename like `zzz_.txt` (prefix + nothing before the extension) should NOT match as a partial override of any file. The match requires that `original_fname` is a real filename already in the registry.

---

## 4. Output Schema

### 4.1 `command="files"` — New Output

```json
{
  "conflicts": [
    {
      "directory": "common/traits",
      "original_file": "00_traits.txt",
      "original_relpath": "common/traits/00_traits.txt",
      "original_source": {"mod": "CK3 Game Files", "load_order": 0},
      "policy": "OVERRIDE",
      "overrides": [
        {
          "type": "full_override",
          "filename": "00_traits.txt",
          "relpath": "common/traits/00_traits.txt",
          "mod": "Some Trait Mod",
          "load_order": 15,
          "is_winner": true
        },
        {
          "type": "partial_override",
          "filename": "zzz_00_traits.txt",
          "relpath": "common/traits/zzz_00_traits.txt",
          "prefix": "zzz_",
          "mod": "Trait Tweak Mod",
          "load_order": 20
        }
      ],
      "warnings": [
        {
          "type": "failed_patch_warning",
          "filename": "00_traits_fix.txt",
          "relpath": "common/traits/00_traits_fix.txt",
          "suffix": "_fix",
          "mod": "Buggy Mod",
          "load_order": 8,
          "reason": "Suffix '_fix' after original stem makes this a new file, not a patch of 00_traits.txt"
        }
      ]
    }
  ],
  "summary": {
    "total_originals_with_conflicts": 42,
    "full_overrides": 38,
    "partial_overrides": 67,
    "failed_patch_warnings": 3
  }
}
```

### 4.2 Key Differences from Current Output

| Field | Old | New |
|-------|-----|-----|
| Grouping | By exact relpath | By original file per directory |
| Override type | Not classified | `full_override`, `partial_override`, `failed_patch_warning` |
| Original file tracking | None | Explicit `original_file` + `original_source` |
| Prefix detection | `has_zzz_prefix` boolean | Actual extracted prefix string |
| Suffix detection | None | `failed_patch_warning` with suffix |
| Merge policy | Not included | `policy` from folder path |
| Secondary conflicts | Not detected | Full overrides of override files are captured |
| Compatch filtering | `include_compatch` + pattern matching | **Removed** — not the tool's job |

---

## 5. Implementation Plan

### 5.1 Files to Modify

| File | Change |
|------|--------|
| `tools/ck3lens_mcp/safety.py` | Add `"ck3_conflicts"` to `_SPILL_EXEMPT` |
| `tools/ck3lens_mcp/ck3lens/impl/conflict_ops.py` | New `detect_primary_file_conflicts()` function; remove dead `get_file_conflicts()` |
| `tools/ck3lens_mcp/server.py` | Replace `command="files"` handler; update docstring; update `command="summary"` |
| `tools/ck3lens_mcp/ck3lens/db_queries.py` | Remove `COMPATCH_PATTERNS`, `_is_compatch_mod()`, `include_compatch` branching from `_get_symbol_conflicts_internal()` |
| `docs/CONFLICT_ANALYSIS_ARCHITECTURE.md` | Update to reflect new file conflict model |

### 5.2 Removals

| What | Where | Why |
|------|-------|-----|
| `COMPATCH_PATTERNS` list | `db_queries.py` ~line 1163 | False positive heuristic; not the tool's job |
| `_is_compatch_mod()` method | `db_queries.py` ~line 1172 | Called only by compatch branching |
| `include_compatch` parameter | `ck3_conflicts()` in server.py | No branching on compatch status |
| `compatch_conflicts_hidden` output field | `_get_symbol_conflicts_internal()` | No longer tracked |
| `is_compatch_conflict` field | `_get_symbol_conflicts_internal()` | No longer tracked |
| `get_file_conflicts()` | `conflict_ops.py` | Dead code, never called by server.py |

### 5.3 Implementation Order

1. **Add spill exemption** — One-line change in `safety.py`
2. **Remove compatch logic** from `db_queries.py` and `server.py`
3. **Implement `detect_primary_file_conflicts()`** in `conflict_ops.py`
4. **Replace server.py `command="files"` handler** to call new function
5. **Update `command="summary"`** to use new conflict structure
6. **Update docstring/output schema** in server.py
7. **Validate with Pylance** and test against live playset

### 5.4 What NOT to Change (Yet)

- `command="symbols"` — Working correctly (after compatch removal), has proper load_order and policy
- on_actions CONTAINER_MERGE sub-block analysis — Separate project
- Content diffing for full overrides — Part of future AST-based modding tools suite

---

## 6. Other Recommended Improvements

### 6.1 Near-Term (Separate Tasks)

| Issue | Description |
|-------|-------------|
| `symbol_names` filter post-query | Push into SQL WHERE clause so specific symbol lookups don't miss rows outside top N |

### 6.2 Future Phase

| Feature | Description |
|---------|-------------|
| Content diffing for full overrides | AST-level comparison of override vs. original to show what changed (part of future AST-based modding tools suite) |
| on_actions sub-block analysis | Detect when multiple mods add single-slot blocks to the same on_action (separate project) |

---

## 7. Open Questions

1. **Should partial overrides be called "conflicts"?** — They're valid by design. A partial override of an original is working as intended. The real conflict is when *multiple* full overrides target the same file, or when secondary conflicts occur (one mod's partial override replaces another's). Consider whether the output should distinguish "conflict" from "relationship."

2. **Performance at scale** — 74 mods × ~91K files. The per-directory comparison loop is O(files_in_dir × originals_in_dir). For most directories this is tiny (<100 files). The largest might be `gfx/` or `localization/` — may need profiling.

3. **Multiple partial override matches** — A filename could theoretically match multiple originals (e.g., `zzz_00_traits.txt` could match both `00_traits.txt` and `traits.txt` if both exist). The algorithm records all matches. In practice this is unlikely because CK3 file naming conventions make ambiguous matches rare.
