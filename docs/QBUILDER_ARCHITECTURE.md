# QBuilder Architecture: Clean-Sheet Redesign

> **Status:** Design Document (Pre-Implementation)  
> **Created:** March 18, 2026  
> **Branch:** `agent/fix-ipc-port-isolation`
> **Core Philosophy:** "The database stores all mods independently per `content_version_id`. Conflicts are session-level views, not storage-level facts."

---

## Table of Contents

1. [Current State Audit](#1-current-state-audit)
2. [Design Mandates](#2-design-mandates)
3. [Mandate 1: Single Routing Mechanism](#3-mandate-1-single-routing-mechanism)
4. [Mandate 2: Reconcile Dual Routing Systems](#4-mandate-2-reconcile-dual-routing-systems)
5. [Mandate 3: build_queue Carries cvid](#5-mandate-3-build_queue-carries-cvid)
6. [Mandate 4: Eliminate BuildContext and process_item](#6-mandate-4-eliminate-buildcontext-and-process_item)
7. [Mandate 5: history/titles/ Uses Lookup Extraction](#7-mandate-5-historytitles-uses-lookup-extraction)
8. [Mandate 6: title_history_lookup Schema Review](#8-mandate-6-title_history_lookup-schema-review)
9. [Mandate 7: Title and Title History Extractors](#9-mandate-7-title-and-title-history-extractors)
10. [Mandate 8: Core DB Philosophy](#10-mandate-8-core-db-philosophy)
11. [Migration Plan](#11-migration-plan)
12. [File-Level Change Matrix](#12-file-level-change-matrix)

---

## 1. Current State Audit

### Pipeline Overview

The qbuilder is a two-phase, queue-based build system:

```
Phase 1: Discovery    Phase 2: Build
┌────────────────┐    ┌──────────────────┐
│  playset_scan  │    │  claim_work()    │
│     ↓          │    │     ↓            │
│  enumerate     │    │  process_item()  │
│  files         │    │     ↓            │
│     ↓          │    │  EnvelopeExec    │
│  _commit_batch │    │  ┌────────────┐  │
│  ┌──────────┐  │    │  │ parse      │  │
│  │ files    │  │    │  │ symbols    │  │
│  │ build_q  │  │    │  │ refs       │  │
│  └──────────┘  │    │  │ lookups    │  │
└────────────────┘    │  └────────────┘  │
                      └──────────────────┘
```

### Files and Responsibilities

| File | Lines | Purpose | Issues |
|------|-------|---------|--------|
| `routing_table.json` | ~210 | Routing rules and envelope definitions | Contains **two incompatible schemas** (v1 legacy + v2 stub) |
| `routing.py` | ~160 | v2 `Router` class | **Dead code** — expects v2 JSON keys that don't exist |
| `discovery.py` | ~490 | File enumeration, routing, queue insertion | Uses v1 routing (`get_envelope_for_file`) which works |
| `enqueue.py` | ~140 | Alternative queue path using v2 Router | **Dead code** — Router crashes on current JSON |
| `worker.py` | ~750 | Build queue worker + `EnvelopeExecutor` | `BuildContext` is dict→dataclass ceremony; `process_item` is thin wrapper |
| `api.py` | ~400 | Public API for flash enqueue + status | Uses v1 routing (imports from discovery.py) |
| `cli.py` | ~550 | CLI commands: daemon, discover, build, run | FK workaround applied; otherwise sound |
| `schema.py` | ~200 | Queue table DDL (discovery_queue, build_queue) | build_queue lacks cvid (by design, but see Mandate 3) |
| `lookup_extractors.py` | ~500 | Regex-based data extraction for characters, provinces, etc. | Missing title and title_history extractors |

### The Dual Routing Problem

**routing_table.json** contains a v1 schema with these top-level keys:
- `path_rules` — ordered list of `{match, envelope}` objects
- `extension_to_type` — extension → file_type map
- `type_to_envelope` — file_type → envelope map
- `skip_extensions` — list of extensions to skip
- `envelope_steps` — envelope → steps map

**routing.py** defines a `Router` class expecting v2 keys:
- `file_types` — file type definitions with `match` and `envelope`
- `match_order` — ordered list of file type names
- `envelopes` — envelope definitions with `steps`
- `steps` — step definitions with `order`

**These v2 keys do not exist in routing_table.json.** The `Router` class will crash with `KeyError` if instantiated. The `enqueue.py` module imports `get_router()` from `routing.py` and is therefore **dead code**.

### What Actually Routes Files Today

Only the v1 functions in `discovery.py` work:

```python
# discovery.py — THE ACTUAL ROUTING
def get_envelope_for_file(relpath, routing_table):
    # 1. Check path_rules (substring match, first hit wins)
    # 2. Check skip_extensions
    # 3. Fall through to extension_to_type → type_to_envelope
```

This is used by:
- `discovery.py` `_commit_batch()` — during discovery phase
- `api.py` `enqueue_file()` — during flash enqueue

### Data Flow Problems

1. **build_queue lacks cvid** — `claim_work()` must JOIN through `files → content_versions` to get `cvid` and `source_path`. This JOIN runs on every single claim. If `cvid` were on `build_queue`, the worker could resolve paths without the JOIN.

2. **Three representations of same data** — When the worker claims work, it:
   - Gets a raw dict from `claim_work()` 
   - Converts it to `BuildContext` dataclass in `process_item()`
   - Passes `BuildContext` to `EnvelopeExecutor.execute()`
   
   `BuildContext` adds zero logic. `process_item()` is a try/except wrapper that constructs a `BuildContext` from the dict and calls `executor.execute()`. This ceremony adds no value.

3. **history/titles/ goes to AST parsing** — `history/titles/*.txt` files are currently NOT in `path_rules`, so they fall through to `extension_to_type[".txt"] = "script"` → `type_to_envelope["script"] = "E_SCRIPT"`. This means every title history file gets full AST parsing + symbol extraction + ref extraction. Title history files are date-keyed data tables (`1066.9.15 = { holder = 12345 }`). They should use lightweight lookup extraction like characters and provinces.

---

## 2. Design Mandates

| # | Mandate | Impact |
|---|---------|--------|
| 1 | **Single routing mechanism** — one ordered list of match rules | Delete v1 alias chains (`skip_extensions`, `extension_to_type`, `type_to_envelope`) |
| 2 | **Reconcile dual routing systems** — one Router, one JSON schema | Delete dead `routing.py` v2 class; delete dead `enqueue.py` |
| 3 | **build_queue carries cvid** — denormalized for performance | Add `content_version_id` column to `build_queue` |
| 4 | **Eliminate BuildContext and process_item** — flatten ceremony | Merge into `claim_work()` return + direct `execute()` call |
| 5 | **history/titles/ uses lookup extraction** — not AST parsing | Add `path_rules` entry routing to `E_LOOKUP_TITLES` |
| 6 | **title_history_lookup schema review** — fix autoincrement PK | Change to natural composite key `(title_key, effective_date, content_version_id)` |
| 7 | **Title and title_history extractors** — populate empty tables | Write `extract_titles()` and `extract_title_history()` in `lookup_extractors.py` |
| 8 | **Core DB philosophy** — mods independent, conflicts are session views | No leaking of playset/load-order into storage tables |

---

## 3. Mandate 1: Single Routing Mechanism

### Problem

The v1 JSON has **four separate lookup paths** that a file can traverse:

```
path_rules[] → substring match → envelope        # path-based
skip_extensions[] → extension check → E_SKIP      # extension blacklist
extension_to_type{} → ext → file_type             # ext → type alias
type_to_envelope{} → file_type → envelope         # type → envelope alias
```

This creates a 3-step alias chain for the common case (`.txt` → `"script"` → `"E_SCRIPT"`). The `skip_extensions` array is redundant with `path_rules` entries that return `E_SKIP`. The `extension_to_type` → `type_to_envelope` indirection serves no purpose — no code ever needs `file_type` independent of `envelope`.

### Solution

Replace all four lookups with a **single ordered match_rules array**:

```json
{
  "version": 2,
  "match_rules": [
    {"name": "GIT_INTERNALS",     "match": {"path_contains": ".git/"},               "envelope": "SKIP"},
    {"name": "TITLE_HISTORY",     "match": {"path_prefix": "history/titles/"},        "envelope": "LOOKUP_TITLE_HISTORY"},
    {"name": "CHARACTERS",        "match": {"path_prefix": "history/characters/"},    "envelope": "LOOKUP_CHARACTERS"},
    {"name": "PROVINCES",         "match": {"path_prefix": "history/provinces/"},     "envelope": "LOOKUP_PROVINCES"},
    {"name": "DYNASTIES",         "match": {"path_prefix": "common/dynasties/"},      "envelope": "LOOKUP_DYNASTIES"},
    {"name": "HOLY_SITES",        "match": {"path_prefix": "common/religion/holy_sites/"}, "envelope": "LOOKUP_HOLY_SITES"},
    {"name": "NAME_LISTS",        "match": {"path_prefix": "common/culture/name_lists/"}, "envelope": "LOOKUP_NAMES"},
    {"name": "GUI_FILES",         "match": {"path_prefix": "gui/"},                   "envelope": "SKIP"},
    {"name": "GFX_FILES",         "match": {"path_prefix": "gfx/"},                   "envelope": "SKIP"},
    {"name": "MAP_DATA",          "match": {"path_prefix": "map_data/"},              "envelope": "SKIP"},
    {"name": "BINARY_MEDIA",      "match": {"extensions": [".dds",".png",".jpg",".wav",".mp3",".ogg",".mesh",".anim",".skeleton"]}, "envelope": "SKIP"},
    {"name": "LOCALIZATION",      "match": {"extensions": [".yml"]},                  "envelope": "LOC"},
    {"name": "PDX_SCRIPT",        "match": {"extensions": [".txt"]},                  "envelope": "SCRIPT"},
    {"name": "UNKNOWN",           "match": {},                                        "envelope": "SKIP", "notes": "Fallback for unrecognized files"}
  ],
  "envelopes": {
    "SCRIPT":               {"steps": ["parse", "extract_symbols", "extract_refs"]},
    "LOC":                  {"steps": ["parse_loc", "extract_loc_entries"]},
    "LOOKUP_CHARACTERS":    {"steps": ["extract_characters"]},
    "LOOKUP_PROVINCES":     {"steps": ["extract_provinces"]},
    "LOOKUP_NAMES":         {"steps": ["extract_names"]},
    "LOOKUP_HOLY_SITES":    {"steps": ["extract_holy_sites"]},
    "LOOKUP_DYNASTIES":     {"steps": ["extract_dynasties"]},
    "LOOKUP_TITLE_HISTORY": {"steps": ["extract_title_history"]},
    "SKIP":                 {"steps": []}
  }
}
```

### Match Semantics

Each rule can have one of three match modes:

| Mode | Key | Behavior |
|------|-----|----------|
| Path prefix | `path_prefix` | `relpath.lower().startswith(prefix)` |
| Path contains | `path_contains` | `substring in relpath.lower()` |
| Extension | `extensions` | `path.suffix.lower() in extensions` |
| Fallback | `{}` (empty) | Matches everything — must be last |

Rules are evaluated **in order**. First match wins. This is identical to how `path_rules` works today, but absorbs `skip_extensions` and `extension_to_type/type_to_envelope` into the same list.

### What Gets Deleted

- `routing_table.json`: Keys `path_rules`, `skip_extensions`, `extension_to_type`, `type_to_envelope`, `envelope_steps` — all replaced by `match_rules` and `envelopes`
- `discovery.py`: Functions `get_routing_table()`, `get_envelope_for_file()`, `get_file_type()` — replaced by router
- `api.py`: Import of `get_envelope_for_file`, `get_routing_table` from discovery — use router instead

---

## 4. Mandate 2: Reconcile Dual Routing Systems

### Problem

Two routing systems exist:
- **v1** (`discovery.py`): `get_envelope_for_file()` — functional, used in production
- **v2** (`routing.py`): `Router` class — dead code, v2 JSON keys don't exist

`enqueue.py` imports from `routing.py` and is therefore dead code too.

### Solution

Keep `routing.py` as THE routing module. Rewrite the `Router` class to consume the new v2 JSON (from Mandate 1). Delete the v1 functions from `discovery.py`. Delete `enqueue.py` entirely — its `enqueue_all_files()` function is a separate code path that nothing calls.

**New routing.py interface:**

```python
# routing.py — THE SOLE ROUTING AUTHORITY

@dataclass(frozen=True)
class RouteResult:
    rule_name: str        # e.g., "PDX_SCRIPT"
    envelope: str         # e.g., "SCRIPT"
    steps: tuple[str, ...] # e.g., ("parse", "extract_symbols", "extract_refs")

class Router:
    def __init__(self, table: dict): ...
    def route(self, relpath: str) -> RouteResult: ...

def get_router() -> Router:
    """Module-level singleton, lazy-loaded."""
```

**Callers after migration:**

| Caller | Before | After |
|--------|--------|-------|
| `discovery.py` `_commit_batch()` | `get_envelope_for_file(relpath, self.routing_table)` | `get_router().route(relpath).envelope` |
| `api.py` `enqueue_file()` | `get_envelope_for_file(rel_path, routing_table)` | `get_router().route(rel_path).envelope` |
| `worker.py` `EnvelopeExecutor._load_envelope_steps()` | `routing.get('envelope_steps', {})` | `{e: env["steps"] for e, env in table["envelopes"].items()}` |

### Files Deleted

| File | Reason |
|------|--------|
| `enqueue.py` | Dead code — `Router` crashes, nothing calls `enqueue_all_files()` |

### Files Modified

| File | Change |
|------|--------|
| `routing.py` | Rewrite `Router` to consume v2 JSON with `match_rules` |
| `routing_table.json` | Replace v1 schema with v2 schema |
| `discovery.py` | Delete `get_routing_table()`, `get_envelope_for_file()`, `get_file_type()`. Import `get_router` from routing |
| `api.py` | Replace `discovery.get_envelope_for_file` import with `routing.get_router` |
| `worker.py` | Replace `routing_path / routing_table.json` loading with `get_router()` |

---

## 5. Mandate 3: build_queue Carries cvid

### Problem

The `build_queue` stores only `file_id`. When the worker claims an item, it must JOIN through `files → content_versions` to get `cvid` and `source_path`:

```sql
-- worker.py claim_work(), executed on EVERY claim
SELECT f.content_version_id, f.relpath, cv.source_path
FROM files f
JOIN content_versions cv ON f.content_version_id = cv.content_version_id
WHERE f.file_id = ?
```

This works, but:
- The JOIN is unnecessary overhead on the hot path
- The worker already needs `cvid` for every lookup extractor call (`extract_characters(content, file_id, cvid, conn)`)
- Discovery already knows `cvid` when it inserts into `build_queue` — it's discarding information

### Solution

Add `content_version_id` to `build_queue` as a denormalized column. Populate it at enqueue time (discovery and flash enqueue both know cvid). The worker reads it directly from the claimed row.

**Schema change:**

```sql
CREATE TABLE IF NOT EXISTS build_queue (
    build_id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL,
    content_version_id INTEGER NOT NULL,     -- NEW: denormalized from files
    envelope TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    -- ... rest unchanged
);
```

**Discovery insertion change:**

```python
# discovery.py _commit_batch() — cvid is already available
self.conn.execute("""
    INSERT INTO build_queue 
        (file_id, content_version_id, envelope, priority, ...)
    VALUES (?, ?, ?, 0, ...)
""", (file_id, cvid, envelope, ...))
```

**Worker claim change:**

```python
# worker.py claim_work() — cvid comes from build_queue directly
cursor = self.conn.execute("""
    UPDATE build_queue
    SET status = 'processing', ...
    WHERE build_id = (...)
    RETURNING build_id, file_id, content_version_id, envelope, priority, ...
""", ...)

# Then only need files JOIN for relpath + source_path (still needed for abspath)
# But cvid is already resolved — no content_versions JOIN needed
```

**Impact:** The `files → content_versions` JOIN is still needed (for `source_path` to construct `abspath`), but `cvid` no longer leaks through the JOIN. If `source_path` were cached or resolved via a different mechanism (e.g., in-memory map from cvid → source_path populated once at startup), the JOIN could be eliminated entirely.

### Migration

```sql
-- Add column
ALTER TABLE build_queue ADD COLUMN content_version_id INTEGER;

-- Backfill from files table
UPDATE build_queue SET content_version_id = (
    SELECT f.content_version_id FROM files f WHERE f.file_id = build_queue.file_id
);
```

---

## 6. Mandate 4: Eliminate BuildContext and process_item

### Problem

Three representations of the same data:

```python
# 1. Raw dict from claim_work()
item = {
    'build_id': 42, 'file_id': 100, 'cvid': 5,
    'relpath': 'common/traits/00_traits.txt',
    'envelope': 'E_SCRIPT', 'abspath': Path('...'),
    'work_mtime': 1234.5, 'work_size': 8192, 'work_hash': 'abc...'
}

# 2. BuildContext dataclass (in process_item)
ctx = BuildContext(
    build_id=item['build_id'],
    file_id=item['file_id'],
    cvid=item['cvid'],
    relpath=item['relpath'],
    envelope=item['envelope'],
    abspath=item['abspath'],
    work_mtime=item['work_mtime'],
    work_size=item['work_size'],
    work_hash=item['work_hash'],
)

# 3. Arguments to executor.execute(ctx)
# (which just reads ctx.envelope, ctx.abspath, ctx.work_hash, etc.)
```

`process_item()` does exactly three things:
1. Construct `BuildContext` from dict
2. Call `self.executor.execute(ctx)` in a try/except
3. Update `build_queue` status on success/error

### Solution

Return a `BuildContext` (or equivalent `NamedTuple`) directly from `claim_work()` instead of a raw dict. Move the try/except status-update logic into `run_build_worker()` directly. Delete `process_item()`.

**New flow:**

```python
# worker.py

@dataclass(frozen=True)
class WorkItem:
    """Claimed build queue item with all context resolved."""
    build_id: int
    file_id: int
    cvid: int
    relpath: str
    envelope: str
    abspath: Path
    work_mtime: float
    work_size: int
    work_hash: str | None

class BuildWorker:
    def claim_work(self) -> WorkItem | None:
        """Claim and return fully-resolved work item."""
        # ... existing claim SQL ...
        # Return WorkItem instead of dict
        return WorkItem(build_id=..., file_id=..., ...)

# In run_build_worker():
item = worker.claim_work()
if not item:
    continue

try:
    completed_steps = worker.executor.execute(item)
    worker._mark_completed(item.build_id)
except Exception as e:
    worker._mark_error(item.build_id, str(e), ...)
```

### What Gets Deleted

- `BuildContext` dataclass → replaced by `WorkItem` 
- `process_item()` method → logic moves to `run_build_worker()` loop
- Dict-to-dataclass conversion ceremony → `claim_work()` returns structured data directly

---

## 7. Mandate 5: history/titles/ Uses Lookup Extraction

### Problem

`history/titles/*.txt` files contain date-keyed data tables:

```pdx
k_france = {
    867.1.1 = {
        holder = 6392
        liege = e_francia
        government = feudal_government
    }
    1066.9.15 = {
        holder = 217
    }
}
```

Currently these files are NOT in `path_rules`, so they fall through to:
```
extension_to_type[".txt"] = "script" → type_to_envelope["script"] = "E_SCRIPT"
```

This triggers full AST parsing + symbol extraction + ref extraction. These files:
- Contain no script logic (no effects, triggers, modifiers)
- Have no symbols worth indexing (date keys are not reusable identifiers)
- Have no cross-references worth tracking
- Are >2000 files in vanilla CK3 alone (significant parse time)

### Solution

Add routing rule for `history/titles/` that sends files to a new `E_LOOKUP_TITLE_HISTORY` envelope:

```json
{"name": "TITLE_HISTORY", "match": {"path_prefix": "history/titles/"}, "envelope": "LOOKUP_TITLE_HISTORY"}
```

This rule must come BEFORE the generic `.txt → SCRIPT` rule in `match_rules` ordering.

The `LOOKUP_TITLE_HISTORY` envelope runs a single step: `extract_title_history`, which uses regex-based extraction like the character and province extractors.

### Performance Impact

Vanilla CK3 has 2,093 files under `history/titles/`. Each currently spawns a subprocess for AST parsing. Switching to regex extraction eliminates ~2,000 subprocess spawns and produces more useful structured data.

---

## 8. Mandate 6: title_history_lookup Schema Review

### Problem

`title_history_lookup` uses `INTEGER PRIMARY KEY AUTOINCREMENT`:

```sql
CREATE TABLE IF NOT EXISTS title_history_lookup (
    id INTEGER PRIMARY KEY AUTOINCREMENT,   -- ← Synthetic PK
    title_key TEXT NOT NULL,
    effective_date TEXT NOT NULL,
    holder_id INTEGER,
    liege_title TEXT,
    government TEXT,
    succession_laws_json TEXT,
    content_version_id INTEGER,
    ...
);
```

This creates problems:
- No natural deduplication — re-extracting the same file inserts duplicate rows
- No `ON CONFLICT` upsert possible without a unique constraint on the natural key
- The autoincrement PK carries no semantic meaning

Compare with `title_lookup` which correctly uses a composite PK:

```sql
CREATE TABLE IF NOT EXISTS title_lookup (
    title_key TEXT NOT NULL,
    content_version_id INTEGER NOT NULL,
    ...
    PRIMARY KEY (title_key, content_version_id)  -- ← Natural composite key
);
```

### Solution

Replace the autoincrement PK with a natural composite key:

```sql
CREATE TABLE IF NOT EXISTS title_history_lookup (
    title_key TEXT NOT NULL,
    effective_date TEXT NOT NULL,
    content_version_id INTEGER NOT NULL,
    holder_id INTEGER,
    liege_title TEXT,
    government TEXT,
    succession_laws_json TEXT,
    PRIMARY KEY (title_key, effective_date, content_version_id),
    FOREIGN KEY (content_version_id) REFERENCES content_versions(content_version_id)
);
```

This enables idempotent `INSERT OR REPLACE` during extraction, consistent with all other lookup tables.

### Migration

Since the table is currently empty (no extractor populates it), this is a clean schema change — no data migration needed. Drop and recreate the table.

---

## 9. Mandate 7: Title and Title History Extractors

### Problem

The schema defines `title_lookup` and `title_history_lookup` tables, but:
- No extractors populate them
- No routing rules send files to extractors
- The archived `landed_titles.py` extractor exists in `archive/old_builder/extractors/lookups/` but is not wired into qbuilder

### Solution

#### 9a. Title History Extractor (`history/titles/`)

Add `extract_title_history()` to `lookup_extractors.py`:

```python
def extract_title_history(content: str, file_id: int, cvid: int, conn: sqlite3.Connection) -> int:
    """
    Extract title history entries from history/titles/ files.
    
    Format:
        k_france = {
            867.1.1 = {
                holder = 6392
                liege = e_francia
                government = feudal_government
                succession_laws = { feudal_elective_succession_law }
            }
            1066.9.15 = {
                holder = 217
            }
        }
    
    Returns count of entries extracted.
    """
```

Key behaviors:
- Parse the top-level title key (e.g., `k_france`)
- Parse each date block within it
- Extract `holder`, `liege`, `government`, `succession_laws`
- Use `INSERT OR REPLACE` for idempotent re-extraction

#### 9b. Title Extractor (`common/landed_titles/`)

**This is more complex.** Landed titles files use deeply nested hierarchical structure:

```pdx
e_britannia = {
    color = { 0.8 0 0 }
    capital = k_england
    k_england = {
        capital = d_york
        d_york = {
            c_york = {
                b_york = { province = 123 }
            }
        }
    }
}
```

The nesting defines the de_jure hierarchy (empires contain kingdoms contain duchies, etc.). This **does** require AST parsing because:
- The nesting is recursive and arbitrarily deep
- Title keys are legitimate symbols (used elsewhere as references)
- Scripted blocks (`can_create`, `ai_primary_priority`) are intermixed

**Decision:** Keep `common/landed_titles/` on the SCRIPT envelope. Title symbols are already extracted by the symbol extractor. If a dedicated `title_lookup` extractor is needed later, it should run as a **post-parse step** on the existing AST (not a separate routing envelope).

Wire into `LOOKUP_EXECUTORS` in `lookup_extractors.py`:

```python
LOOKUP_EXECUTORS = {
    'extract_characters': extract_characters,
    'extract_provinces': extract_provinces,
    'extract_names': extract_names,
    'extract_holy_sites': extract_holy_sites,
    'extract_dynasties': extract_dynasties,
    'extract_title_history': extract_title_history,  # NEW
}
```

Wire into `worker.py` `EnvelopeExecutor`:

```python
def _step_extract_title_history(self, ctx: BuildContext) -> None:
    """Extract title history to title_history_lookup table."""
    content = _read_ck3_text(ctx.abspath)
    LOOKUP_EXECUTORS['extract_title_history'](content, ctx.file_id, ctx.cvid, self.conn)
    self.conn.commit()
```

---

## 10. Mandate 8: Core DB Philosophy

### Statement

> "The database stores all mods independently per `content_version_id`. Conflicts are session-level views, not storage-level facts."

### What This Means

1. **Every lookup table is keyed by `(identity, content_version_id)`** — never by `(identity)` alone. Two mods defining the same character ID (e.g., `character_id=6392`) get separate rows. The database never "merges" them.

2. **No playset/load-order data in storage tables** — the `content_versions`, `files`, `asts`, `symbols`, `refs`, and all `*_lookup` tables contain raw data per mod. They know nothing about which mods are active or their relative order.

3. **Conflict detection is a session-level query** — when a user asks "what conflicts exist in my playset?", the MCP server runs a query that JOINs across the active playset's CVIDs and finds duplicate keys. This is a VIEW operation, not a stored fact.

4. **Rebuilding a single mod never touches other mods' data** — if mod X gets re-indexed, only rows with `content_version_id = X` are affected. No cascading updates to other mods.

### Audit Against Current Code

| Table | Keyed by cvid? | Status |
|-------|----------------|--------|
| `files` | `(content_version_id, relpath)` UNIQUE | ✅ Correct |
| `asts` | `(content_hash, parser_version_id)` — content-addressed | ✅ Correct (cvid is irrelevant for content-identical files) |
| `symbols` | `(ast_id)` — content-keyed via Golden Join | ✅ Correct |
| `refs` | `(ast_id)` — content-keyed via Golden Join | ✅ Correct |
| `character_lookup` | `(character_id, content_version_id)` PK | ✅ Correct |
| `province_lookup` | `(province_id, content_version_id)` PK | ✅ Correct |
| `dynasty_lookup` | `(dynasty_id, content_version_id)` PK | ✅ Correct |
| `title_lookup` | `(title_key, content_version_id)` PK | ✅ Correct |
| `title_history_lookup` | **autoincrement PK, no cvid in PK** | ❌ Fix in Mandate 6 |
| `holy_site_lookup` | `(holy_site_key, content_version_id)` PK | ✅ Correct |
| `name_lookup` | autoincrement PK, but `UNIQUE(name_list_id, name, gender, content_version_id)` | ✅ Correct (unique constraint enforces it) |
| `localization_entries` | `UNIQUE(file_id, loc_key, version)` | ⚠️ Missing cvid in unique key — file_id implicitly carries it but makes cross-cvid queries harder |

---

## 11. Migration Plan

### Phase 1: Routing Consolidation (No behavior change)

1. Rewrite `routing_table.json` to v2 format with `match_rules` and `envelopes`
2. Rewrite `Router` class in `routing.py` to consume v2 JSON
3. Add `history/titles/` routing rule → `LOOKUP_TITLE_HISTORY`
4. Update `discovery.py` to import `get_router` from `routing.py` instead of using inline functions
5. Update `api.py` to import `get_router` from `routing.py`
6. Update `worker.py` `EnvelopeExecutor` to use router for envelope step lookup
7. Delete `discovery.py` functions: `get_routing_table()`, `get_envelope_for_file()`, `get_file_type()`
8. Delete `enqueue.py` entirely

**Test:** Run discovery + build on a fresh DB. Verify same files get same envelopes.

### Phase 2: Schema + Worker Cleanup

1. Fix `title_history_lookup` schema: drop autoincrement PK, add composite PK
2. Add `content_version_id` column to `build_queue` (with backfill SQL)
3. Merge `BuildContext` into `WorkItem` returned by `claim_work()`
4. Delete `process_item()`, move error handling to `run_build_worker()`
5. Remove stale FK from `files.content_hash → file_contents.content_hash` in `schema.py`
6. Remove `PRAGMA foreign_keys = OFF` workaround from `cli.py`

**Test:** Run full pipeline. Verify all queue operations still work.

### Phase 3: Title History Extractor

1. Write `extract_title_history()` in `lookup_extractors.py`
2. Register in `LOOKUP_EXECUTORS`
3. Add `_step_extract_title_history()` to `EnvelopeExecutor`
4. Run fresh build and verify `title_history_lookup` is populated

**Test:** Query `title_history_lookup` for known vanilla titles (k_france, e_britannia).

### Phase 4: Stale FK Removal (Option B from FK crash fix)

1. Remove `FOREIGN KEY (content_hash) REFERENCES file_contents(content_hash)` from `files` table DDL in `schema.py`
2. Remove `PRAGMA foreign_keys = OFF` workaround from `cli.py` (already listed in Phase 2)
3. Test with `PRAGMA foreign_keys = ON` to verify no other FK violations exist

---

## 12. File-Level Change Matrix

| File | Phase | Action | Details |
|------|-------|--------|---------|
| `routing_table.json` | 1 | **Rewrite** | v1 → v2 format |
| `routing.py` | 1 | **Rewrite** | New Router consuming v2 JSON |
| `discovery.py` | 1 | **Edit** | Replace 3 v1 functions with router import |
| `api.py` | 1 | **Edit** | Replace routing imports |
| `worker.py` | 1+2+3 | **Edit** | P1: router import; P2: delete BuildContext/process_item; P3: add title_history step |
| `enqueue.py` | 1 | **Delete** | Dead code |
| `schema.py` (qbuilder) | 2 | **Edit** | Add cvid to build_queue DDL |
| `schema.py` (db) | 2+4 | **Edit** | P2: fix title_history_lookup PK; P4: remove files FK |
| `lookup_extractors.py` | 3 | **Edit** | Add extract_title_history |
| `cli.py` | 4 | **Edit** | Remove FK workaround |
