# Canonical Architecture: Single-Writer DB (QBuilder Daemon) + Read-Only MCP Servers

> **Status:** CANONICAL LAW  
> **Last Updated:** January 17, 2026  
> **Purpose:** Prevent SQLite write contention by construction

---

## Canonical Statement (non-negotiable)

1. **Exactly one process is allowed to write to the ck3raven SQLite DB:** the **QBuilder Daemon**.
2. **All MCP servers must open the DB in read-only mode** and are prohibited from executing any SQL statement that mutates state (INSERT/UPDATE/DELETE/DDL/PRAGMA that changes persistence).
3. **All state changes** (queue operations, file indexing updates, derived table rebuilds, diagnostics/conflicts writes) **must be requested via IPC** to the daemon.
4. **The DB is the "truth store"; the daemon is the "truth editor."** MCP tools are "query-only clients."

### Design intent

* Prevent SQLite write contention and lock cascades by construction.
* Make correctness and idempotence enforceable in one place.
* Ensure multiple VS Code windows can coexist safely.

---

## Hard Boundary Rules

### Rule A — Read-only enforcement is mechanical, not advisory

**MCP processes must connect to SQLite using `mode=ro`.**
Any accidental write attempt must fail at the SQLite layer.

**Allowed:** SELECT-only queries.
**Forbidden:** Any mutation statement, including:

* INSERT / UPDATE / DELETE / REPLACE
* CREATE / DROP / ALTER
* PRAGMA that changes journaling/synchronous mode, temp_store, etc.
* BEGIN IMMEDIATE / EXCLUSIVE write transactions

### Rule B — Daemon owns queue + derived tables

MCP servers **do not** enqueue work directly in DB tables. They call IPC.

### Rule C — File writes do not imply DB writes

When MCP edits files on disk (policy-gated), it **must** call daemon `notify_file_changed` (or `enqueue_files`) and **not** update DB tables itself.

---

## Database Ownership: Table-Level Authority

> The simplest safe rule: **MCP: read-only for all tables**; **Daemon: read-write for all tables**.
> If you want stricter conceptual ownership, use the list below.

### Authoritative writer: QBuilder Daemon (RW)

These are **daemon-owned** tables (no exceptions):

#### Queue / orchestration

* `build_queue` (enqueue, lease, release, finalize)
* Any leasing or work-claim tables (`leases`, `work_items`, etc.)

#### Canonical file index (recommended daemon-owned)

* `files` (or equivalent): path, content_root, discovered metadata, content hash, mtime/size, parse state flags
  * Rationale: the daemon is the only place that should decide "what needs rebuild."

#### Derived/semantic tables

* `asts`
* `symbols`
* `refs`
* `diagnostics`
* `conflicts`
* any caches/materialized tables (`*_cache`, `*_summary`, etc.)

#### Schema and pragmas

* All schema migrations and persistent pragmas (WAL mode, synchronous, etc.) are done by daemon at startup.

### Read-only clients: MCP servers (RO)

MCP servers may query:

* anything (including `files`, `symbols`, etc.)
* but must not mutate any table.

---

## IPC Contract (Daemon API)

### Transport, discovery, and framing

* **Transport:** localhost TCP bound to port `0`; the OS assigns one free port per daemon run.
* **Shared endpoint record:** `~/.ck3raven/qbuilder_endpoint.json` publishes host, port, daemon PID, run ID, DB path, and protocol version via atomic replacement.
* **Client discovery:** every MCP server reads the same endpoint record and validates it against `health`. MCP instance IDs remain distinct client leases; they do not select separate QBuilder ports.
* **Lifecycle ordering:** acquire writer lock, bind socket, publish endpoint, serve; on orderly shutdown, clear the matching PID/run ID endpoint before releasing the writer lock.
* **Port domains:** Explorer diagnostics uses a separate dynamically assigned `CK3LENS_IPC_PORT` per VS Code window. That port is never the QBuilder endpoint.
* **Override:** `CK3RAVEN_IPC_PORT` may force a port for tests or diagnostics only; ordinary startup uses OS assignment.
* **Encoding:** JSON messages.
* **Framing:** newline-delimited JSON (NDJSON).
* **Auth:** none required because the socket is localhost-only; optional shared authentication may be added later.

### Message envelope (canonical)

```json
{
  "v": 1,
  "id": "uuid-or-monotonic-client-id",
  "method": "enqueue_files",
  "params": { }
}
```

### Response envelope (canonical)

```json
{
  "v": 1,
  "id": "same-as-request",
  "ok": true,
  "result": { }
}
```

Error response:

```json
{
  "v": 1,
  "id": "same-as-request",
  "ok": false,
  "error": {
    "code": "LOCK_HELD|BAD_PARAMS|INTERNAL|NOT_READY",
    "message": "human-readable",
    "details": {}
  }
}
```

---

## IPC Methods (Minimal Set)

### 1) health

Used by MCP/VS Code to show status + "connected to daemon".

**Request**
```json
{"v":1,"id":"...","method":"health","params":{}}
```

**Result**
```json
{
  "daemon_pid": 12345,
  "run_id": "daemon-a1b2c3d4",
  "ipc_port": 49152,
  "db_path": "/abs/path/db.sqlite",
  "writer_lock": "held",
  "state": "idle|busy|starting|error",
  "queue": {"pending": 120, "leased": 2, "failed": 1},
  "versions": {"schema": 7, "parser": "1.3.0"}
}
```

### 2) enqueue_files

Client tells daemon "these files changed / should be (re)built".

**Request**
```json
{
  "v":1,"id":"...","method":"enqueue_files",
  "params":{
    "paths":[ "/abs/path/a.txt", "/abs/path/b.txt" ],
    "reason":"file_changed|user_request|startup_scan",
    "content_root_id":"ROOT_STEAM|ROOT_USER_DOCS|ROOT_GAME",
    "priority":"normal|high"
  }
}
```

**Result**
```json
{"enqueued": 2, "deduped": 1}
```

### 3) playset_scan

Daemon scans the active playset and enqueues discovery tasks. Requires explicit `playset_file`.

**Request**
```json
{
  "v":1,"id":"...","method":"playset_scan",
  "params":{
    "playset_file": "/path/to/playset.json",
    "include_globs":[ "**/*.txt", "**/*.yml" ],
    "exclude_globs":[ "**/history/**" ],
    "priority":"normal"
  }
}
```

**Result**
```json
{"scheduled": true}
```

### 4) await_idle

Used for tests and for "sync now" UX.

**Request**
```json
{"v":1,"id":"...","method":"await_idle","params":{"timeout_ms": 30000}}
```

**Result**
```json
{"idle": true, "queue_pending": 0}
```

### 5) get_status

Lightweight status query (more stable than health if you want).

**Request**
```json
{"v":1,"id":"...","method":"get_status","params":{}}
```

**Result**
```json
{
  "state":"idle|busy",
  "active_job": {"type":"parse","path":"/abs/...","started_at":"..."},
  "queue":{"pending":12,"leased":1,"failed":0}
}
```

### 6) shutdown (optional)

**Request**
```json
{"v":1,"id":"...","method":"shutdown","params":{"graceful":true}}
```

---

## Daemon Writer Election (Single Writer Guarantee)

### Canonical writer lock

At daemon startup:

* Acquire the OS-level lock on `{db_path}.writer.lock` before opening the DB read-write or publishing an endpoint.
* The winner binds localhost port `0`, reads back the assigned port, and atomically publishes the shared endpoint record.
* A contender that cannot acquire the lock must not open the DB read-write or publish an endpoint. Its launcher waits for the winner's endpoint and connects as a client.
* Endpoint cleanup is accepted only from the matching daemon PID and run ID.

**This prevents two daemons from writing while allowing every VS Code window to reach the one elected writer.**

---

## Behavioral Rules for Agents (Agent-Facing)

### "Never Write DB From MCP"

* If you're implementing an MCP tool and you need a DB update, you are doing it wrong.
* The correct behavior is: **call the daemon IPC method**.

### "Queue is the boundary"

* MCP can *request* rebuilds; daemon decides *how* and *when* to build.
* MCP must not implement a parallel pipeline.

### "Derived tables are daemon-only"

* Anything that depends on parsing/analysis (ASTs/symbols/refs/conflicts/diagnostics) is written only by daemon.

---

## Suggested Minimal UI State Objects (VS Code Extension stays thin)

These are the exact JSON-ish state blobs you can drive your webviews with.

### 1) Connection + mode indicator

```json
{
  "connected": true,
  "daemon_state": "idle|busy|starting|error",
  "writer": true,
  "db_path": "/abs/path/db.sqlite",
  "last_error": null
}
```

### 2) Queue summary badge

```json
{
  "pending": 12,
  "leased": 1,
  "failed": 0,
  "last_build_finished_at": "2026-01-17T21:12:03Z"
}
```

### 3) Active job panel

```json
{
  "active": true,
  "job": {
    "kind": "scan|parse|extract",
    "path": "/abs/path/common/cultures/foo.txt",
    "started_at": "2026-01-17T21:11:22Z",
    "progress": { "done": 32, "total": 120 }
  }
}
```

---

## Testing Contract (Quick + decisive)

### Smoke tests

1. Start daemon → verify it holds writer lock
2. Start MCP server A and B → both open DB `mode=ro`
3. From both MCP servers, run SELECT queries concurrently → no contention
4. Trigger file change via MCP A → it calls IPC → daemon enqueues and processes
5. Confirm MCP B can read updated symbols after daemon completes

### Negative tests

* Attempt `INSERT` from MCP: must fail immediately (SQLite read-only error)
* Launch second daemon: must fail to acquire writer lock

---

## File Locations

| Component | Path |
|-----------|------|
| Architecture doc | `docs/SINGLE_WRITER_ARCHITECTURE.md` |
| Daemon IPC server | `qbuilder/ipc_server.py` |
| IPC client for MCP | `tools/ck3lens_mcp/ck3lens/daemon_client.py` |
| Writer lock logic | `qbuilder/writer_lock.py` |
| Read-only DB API | `tools/ck3lens_mcp/ck3lens/db_api.py` |

---

## Related Documents

| Document | Purpose |
|----------|---------|
| [CANONICAL_ARCHITECTURE.md](CANONICAL_ARCHITECTURE.md) | Main architecture, enforcement rules |
| [PLAYSET_ARCHITECTURE.md](PLAYSET_ARCHITECTURE.md) | Playset management |
