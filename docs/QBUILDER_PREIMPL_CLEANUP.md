# QBuilder Pre-Implementation Cleanup Plan

> **Status:** Complete (all 3 priorities implemented and committed)  
> **Created:** March 19, 2026  
> **Branch:** `agent/fix-ipc-port-isolation`  
> **Prerequisite for:** QBuilder Architecture mandates (QBUILDER_ARCHITECTURE.md)
> **Commits:** `06983b65` (P1), `da3c0913` (P2), `1ac6f448` (P3)

---

## Overview

Three pre-refactor cleanup priorities must be completed before the main QBuilder
architecture mandates can proceed. These clean up architectural violations and
establish the infrastructure needed for the full refactor.

| Priority | Title | Risk | Scope |
|----------|-------|------|-------|
| **1 (Critical)** | Enforce MCP Read-Only Boundary | Active violation of SINGLE_WRITER | server.py |
| **2 (High)** | Purge V1 Routing Debt | Dead code blocking Mandate 1+2 | qbuilder/*.py + routing_table.json |
| **3 (Medium)** | Realize the DB Watcher | Missing live-edit → rebuild loop | qbuilder/watcher.py (new) + cli.py + daemon_client.py |

---

## Priority 1: Enforce MCP Read-Only Boundary (Critical)

### Problem

`_ck3_db_delete_internal()` in server.py (lines ~962–1235) contains 11 SQL
mutation statements (DELETE FROM, UPDATE). These violate SINGLE_WRITER_ARCHITECTURE.md
Rule A: "MCP processes must connect to SQLite using `mode=ro`."

The mutations are **already broken** — `db_api` opens the connection with
`mode=ro`, so any confirmed delete would throw:
```
sqlite3.OperationalError: attempt to write a readonly database
```

The function is dead code in practice but represents ~270 lines of architectural
violation and technical debt.

### Audit: 11 Mutation Statements

| Line | Statement | Target |
|------|-----------|--------|
| 1070 | `DELETE FROM asts` | AST records |
| 1075 | `DELETE FROM symbols` | Symbol records |
| 1080 | `DELETE FROM refs` | Reference records |
| 1098 | `DELETE FROM files` (cascade) | File records + cascade to asts/symbols/refs |
| 1135 | `DELETE FROM files` (by cvid) | Files by content_version |
| 1139 | `DELETE FROM content_versions` | Content versions |
| 1179 | `DELETE FROM {table}` (generic) | Lookup tables (trait, event, etc.) |
| 1202 | `DELETE FROM build_lock` | Build tracking |
| 1203 | `DELETE FROM builder_steps` | Build tracking |
| 1205 | `DELETE FROM builder_runs` | Build tracking |
| 1790 | `UPDATE manifest` | False positive (comment) |

### Solution

**Phase 1A: Add `delete_data` IPC method to daemon**

The daemon's IPC server (qbuilder/ipc_server.py) currently supports 6 methods:
- `health`, `get_status`, `enqueue_files`, `playset_scan`, `await_idle`, `shutdown`

Add a 7th method: `delete_data(target, scope, ids, content_version_ids, confirm)`

This method runs the same deletion logic but inside the daemon process (which
has a writable DB connection). The daemon already holds the WriterLock.

New files/changes:
- `qbuilder/ipc_server.py`: Add `_handle_delete_data()` handler
- `tools/ck3lens_mcp/ck3lens/daemon_client.py`: Add `delete_data()` method

**Phase 1B: Replace server.py mutations with IPC calls**

Replace `_ck3_db_delete_internal()` body (~270 lines) with:
1. Check daemon availability
2. Call `daemon.delete_data(target, scope, ids, content_version_ids, confirm)`
3. Return the daemon's response

The preview mode (`confirm=False`) can remain as local read-only SELECTs — those
don't violate RO. Only the confirmed deletes need IPC.

**Phase 1C: Validate**

- `ck3_db(command="delete", target="build_tracking", confirm=False)` → preview via local SELECT (fast)
- `ck3_db(command="delete", target="build_tracking", confirm=True)` → daemon IPC (correct)
- `ck3_db(command="delete", ...)` when daemon offline → clear error message

### Phases and Testing

```
Phase 1A: Add delete_data to daemon IPC
  - Edit: qbuilder/ipc_server.py (_handle_delete_data + register)
  - Edit: daemon_client.py (delete_data method)
  ⏸ TEST: Start daemon, call daemon.delete_data() from Python REPL

Phase 1B: Replace server.py mutations
  - Edit: server.py (_ck3_db_delete_internal → IPC delegation)
  ⏸ TEST: Call ck3_db(command="delete", target="build_tracking", confirm=False)
  ⏸ TEST: Call ck3_db(command="delete", target="build_tracking", confirm=True) with daemon running

Phase 1C: Cleanup
  - Verify no remaining DELETE/INSERT/UPDATE in server.py
  - Commit
```

---

## Priority 2: Purge V1 Routing Debt (High)

### Problem

The routing system has two incompatible implementations:

| System | Location | Status |
|--------|----------|--------|
| **V1** (functional) | `discovery.py`: `get_routing_table()`, `get_envelope_for_file()`, `get_file_type()` | Works, used in production |
| **V2** (dead code) | `routing.py`: `Router` class expecting v2 JSON keys | Crashes — JSON keys don't exist |
| **Dead import** | `enqueue.py`: imports `get_router()` from dead v2 Router | 100% dead code |

`routing_table.json` has v1 schema with 4 lookup paths:
- `path_rules[]` → substring match → envelope
- `skip_extensions[]` → extension blacklist → E_SKIP
- `extension_to_type{}` → ext → file_type alias
- `type_to_envelope{}` → file_type → envelope alias

This must be consolidated into a single `match_rules[]` array (QBUILDER_ARCHITECTURE Mandate 1).

### Solution (Mandates 1 + 2 combined)

**Phase 2A: Rewrite routing_table.json to v2 schema**

Replace all v1 keys with the canonical v2 format:
```json
{
  "version": 2,
  "match_rules": [
    {"name": "GIT_INTERNALS", "match": {"path_contains": ".git/"}, "envelope": "SKIP"},
    {"name": "TITLE_HISTORY", "match": {"path_prefix": "history/titles/"}, "envelope": "LOOKUP_TITLE_HISTORY"},
    ...
    {"name": "PDX_SCRIPT", "match": {"extensions": [".txt"]}, "envelope": "SCRIPT"},
    {"name": "UNKNOWN", "match": {}, "envelope": "SKIP"}
  ],
  "envelopes": {
    "SCRIPT": {"steps": ["parse", "extract_symbols", "extract_refs"]},
    "LOC": {"steps": ["parse_loc", "extract_loc_entries"]},
    ...
    "SKIP": {"steps": []}
  }
}
```

Match semantics: `path_prefix`, `path_contains`, `extensions`, or `{}` (fallback). First match wins.

**Phase 2B: Rewrite Router class in routing.py**

The existing `Router` class structure is correct — it just expects JSON keys
that don't exist. Rewrite to consume v2 `match_rules`:

```python
class Router:
    def __init__(self, table: dict):
        self._rules = [MatchRule.from_dict(r) for r in table["match_rules"]]
        self._envelopes = table["envelopes"]

    def route(self, relpath: str) -> RouteResult:
        for rule in self._rules:
            if rule.matches(relpath):
                return RouteResult(
                    rule_name=rule.name,
                    envelope=rule.envelope,
                    steps=tuple(self._envelopes[rule.envelope]["steps"]),
                )
        raise ValueError(f"No routing rule matched: {relpath}")
```

**Phase 2C: Migrate callers from v1 to v2**

| Caller | Before | After |
|--------|--------|-------|
| `discovery.py` `_commit_batch()` | `get_envelope_for_file(relpath, self.routing_table)` | `get_router().route(relpath).envelope` |
| `api.py` `enqueue_file()` | `get_envelope_for_file(rel_path, routing_table)` | `get_router().route(rel_path).envelope` |
| `worker.py` `EnvelopeExecutor` | `routing.get('envelope_steps', {})` | `get_router().get_envelope_steps(envelope)` |

**Phase 2D: Delete dead code**

| Delete | Reason |
|--------|--------|
| `enqueue.py` | Dead code — imports dead Router, nothing calls `enqueue_all_files()` |
| `discovery.py` functions | `get_routing_table()`, `get_envelope_for_file()`, `get_file_type()` — replaced by Router |

### Phases and Testing

```
Phase 2A: Rewrite routing_table.json
  - Edit: qbuilder/routing_table.json (v1 → v2 schema)
  ⏸ TEST: Load JSON in Python, verify structure

Phase 2B: Rewrite Router class
  - Edit: qbuilder/routing.py (new Router consuming match_rules)
  ⏸ TEST: Router("common/traits/00_traits.txt").envelope == "SCRIPT"
  ⏸ TEST: Router("history/characters/a.txt").envelope == "LOOKUP_CHARACTERS"
  ⏸ TEST: Router(".git/config").envelope == "SKIP"
  ⏸ TEST: Router("file.dds").envelope == "SKIP"

Phase 2C: Migrate callers
  - Edit: qbuilder/discovery.py (import get_router, replace inline functions)
  - Edit: qbuilder/api.py (import get_router)
  - Edit: qbuilder/worker.py (use router for envelope steps)
  ⏸ TEST: Run discovery on fresh DB — same files get same envelopes
  ⏸ TEST: Run build worker — same envelopes produce same results

Phase 2D: Delete dead code
  - Delete: qbuilder/enqueue.py
  - Edit: qbuilder/discovery.py (remove old functions)
  ⏸ TEST: Full pipeline run — discovery + build + verify
  - Commit
```

---

## Priority 3: Realize the DB Watcher (Medium)

### Problem

When a mod file is edited on disk (via `ck3_file` or external editor), the
database does not reflect the change until a manual rebuild is triggered. The
architecture has:

- ✅ Persistence & queueing (`build_queue`, `enqueue_file()` with `PRIORITY_FLASH`)
- ✅ Single-writer daemon (daemon.py with `WriterLock` + `DaemonIPCServer`)
- ❌ No filesystem watcher — zero code importing `watchdog`

### Solution

**Phase 3A: Add watchdog dependency**

Add `watchdog` to `pyproject.toml` dependencies.

**Phase 3B: Create qbuilder/watcher.py**

New module — OS-level event listener that enqueues changed files:

```python
class ChangeHandler(FileSystemEventHandler):
    """Maps OS events to QBuilder DB operations."""
    
    def on_modified(self, event):
        if event.is_directory: return
        self._process_event(event.src_path)
    
    def _process_event(self, file_path):
        path = Path(file_path)
        if path.suffix not in {'.txt', '.yml', '.gui', '.gfx'}: return
        for root, mod_name in self.mod_map.items():
            if path.is_relative_to(root):
                enqueue_file(mod_name, str(path.relative_to(root)), priority=PRIORITY_FLASH)
                break

class WatcherService:
    """Manages the watchdog observer thread (Thread 3 in daemon)."""
    def __init__(self, conn): ...
    def start(self): ...  # Query DB for mod roots, schedule watches
    def stop(self): ...   # Stop observer
```

**Phase 3C: Wire into daemon**

Update `qbuilder/cli.py` `cmd_daemon()` to start watcher alongside IPC server:

```python
# Thread 1: Build Worker (main thread, polls queue)
# Thread 2: IPC Server (TCP localhost, receives commands)
# Thread 3: File System Watcher (watchdog, enqueues changes)  ← NEW
```

**Phase 3D: MCP integration hook**

`daemon_client.py` already has `notify_file_changed()` — ensure `ck3_file(command="write")`
and `ck3_file(command="edit")` call this after successful mod file writes. This
gives us two paths to the same outcome:

1. **External editor** → watchdog detects → enqueue_file → daemon rebuilds
2. **MCP ck3_file** → notify_file_changed IPC → enqueue_file → daemon rebuilds

### Phases and Testing

```
Phase 3A: Add dependency
  - Edit: pyproject.toml (add watchdog)
  - Run: pip install -e .
  ⏸ TEST: import watchdog succeeds

Phase 3B: Create watcher module
  - Create: qbuilder/watcher.py (ChangeHandler + WatcherService)
  ⏸ TEST: Unit test — mock file event → enqueue_file called

Phase 3C: Wire into daemon
  - Edit: qbuilder/cli.py (start/stop watcher in cmd_daemon)
  ⏸ TEST: Start daemon, edit a mod file, verify build_queue gets entry
  ⏸ TEST: Verify PRIORITY_FLASH — edited file jumps ahead of normal queue

Phase 3D: MCP integration
  - Edit: server.py or ck3_file handler — call daemon.notify_file_changed() after writes
  ⏸ TEST: ck3_file(command="write", path="mod:My Mod/common/traits/test.txt") →
          daemon gets notified → file appears in build_queue → gets processed
  - Commit
```

---

## Execution Order

```
Priority 1 (Critical) ──→ Priority 2 (High) ──→ Priority 3 (Medium)
   │                         │                      │
   │  Phases 1A→1B→1C       │  Phases 2A→2B→2C→2D │  Phases 3A→3B→3C→3D
   │  ⏸ test after each     │  ⏸ test after each   │  ⏸ test after each
   │                         │                      │
   └─── COMMIT ─────────────└─── COMMIT ───────────└─── COMMIT
```

Each priority is a single commit (or small commit series). Each phase has a
test pause. No phase starts until the previous phase's tests pass.

---

## Dependencies and Risks

| Risk | Mitigation |
|------|------------|
| Daemon not running → delete fails | Clear error: "Daemon required for mutations. Start with: python qbuilder/cli.py daemon" |
| Watchdog event storms (git ops) | Debounce + skip `.git/` paths in ChangeHandler |
| Routing v2 produces different envelopes | Test: compare v1 vs v2 routing on full vanilla file list |
| `is_fully_indexed()` never true for mods not in DB | Expected — unindexed mods keep `cvid=None`, tools handle gracefully |

---

## Related Fixes (Completed This Session)

| Fix | Commit | Description |
|-----|--------|-------------|
| Runtime cvid resolution | `fdd788b8` | CVIDs resolved from DB at session init, never persisted in JSON |
| Lazy cvid resolution | *(pending)* | `_get_session()` re-resolves cvids after playset switch |
| Parser hyphenated IDs | `5bd08e43` | Lexer/parser support for `724-Venice-1-Marcello` patterns |
| FK pragma workaround | `94936691` | `PRAGMA foreign_keys = OFF` in qbuilder until stale FK removed |
| QBuilder architecture doc | `234e4bb7` | 8 mandates for the full refactor |

---

## Files Changed (This Plan)

### Priority 1
- `qbuilder/ipc_server.py` — add `_handle_delete_data()` + register
- `tools/ck3lens_mcp/ck3lens/daemon_client.py` — add `delete_data()` method
- `tools/ck3lens_mcp/server.py` — replace `_ck3_db_delete_internal()` mutations with IPC

### Priority 2
- `qbuilder/routing_table.json` — v1 → v2 schema rewrite
- `qbuilder/routing.py` — rewrite Router for v2 match_rules
- `qbuilder/discovery.py` — replace v1 functions with Router import
- `qbuilder/api.py` — replace v1 import with Router
- `qbuilder/worker.py` — use Router for envelope step lookup
- `qbuilder/enqueue.py` — DELETE (dead code)

### Priority 3
- `pyproject.toml` — add watchdog dependency
- `qbuilder/watcher.py` — CREATE (ChangeHandler + WatcherService)
- `qbuilder/cli.py` — wire watcher into cmd_daemon
- `tools/ck3lens_mcp/server.py` — hook ck3_file writes to daemon.notify_file_changed()
