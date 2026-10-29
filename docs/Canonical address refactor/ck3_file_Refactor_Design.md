# ck3_file Canonical Address Refactor — Design Document

> **Date:** February 22, 2026
> **Status:** COMPLETE — Sprint 5 finished March 3, 2026. v1 pipeline removed, ck3_file runs purely on v2 (WA2 + EN2).
> **Prerequisite:** ck3_exec Tier 1 gaps must be resolved first (§1.2 script path WA2 resolution, §1.5 return type normalization) — **VERIFIED: COMPLETE** (see `ck3_exec_Migration_Policy.md` §8-§9 — all components implemented, 12/12 branch 1 tests + E2E branch 2 + 4 negative tests passed, commits `27013af` and `2f449bc`)

---

## 0) Context

`ck3_file` is the primary file I/O tool in the MCP toolkit. It currently uses v1 enforcement (`enforcement.py`) and v1 world adapter (`world_adapter.py`). Migration to WA2 + enforcement_v2 is required per Phase 2 Keystone Migration Directive §1.

### The Problem

Currently `ck3_file` accepts:
- `mod_name="ModName"` + `rel_path="common/traits/..."` (mod-relative)
- `mod_name="wip"` (pseudo-mod routing to WIP workspace)
- `mod_name="vanilla"` (pseudo-mod routing to game files)
- `path="wip:/analysis.py"` (legacy canonical addressing)

This is a mix of v1 path resolution patterns. The canonical addressing refactor requires all paths to go through WA2's `resolve()` which accepts `root:<key>/<path>` and `mod:Name/<path>`.

---

## 1) Design Decision: Single Tool with Internal Routing

**Decision:** There will be ONE `ck3_file` tool, not a parallel `ck3_fileV2`.

**Rationale:**
1. Two tools with overlapping functionality confuse agents — they don't know which to use
2. A parallel tool doubles the maintenance surface
3. Agents calling the wrong version could bypass v2 enforcement
4. Single entry point with internal routing avoids all of these problems

### 1.1 Routing Logic

The tool handler inspects the path format to determine which resolver to invoke:

```
if path starts with "root:" or "mod:" → v2 resolver (WA2)
if path is host-absolute (C:\..., /home/...) → v1 resolver (legacy)
if mod_name is provided → v1 resolver (legacy mod_name routing)
```

This means:
- Existing agent code continues to work (v1 paths still accepted)
- New canonical addresses are fully resolved via WA2 + enforcement_v2
- Migration happens gradually as agents adopt canonical addresses

### 1.2 No Silent Fallthrough

**Decision:** When v2 routing is selected (canonical address), there is NO fallback to v1.

If WA2 `resolve()` fails for a canonical address, the tool returns an error — it does NOT silently try v1 resolution. This ensures canonical addressing bugs are surfaced immediately rather than hidden behind v1 fallback.

---

## 2) dev_mode Parameter

**Decision:** `ck3_file` will accept a `dev_mode: bool = False` parameter.

### 2.1 Behavior

When `dev_mode=True`:
- **ALL paths are forced through v2 resolver** regardless of format
- Host-absolute paths → rejected (v2 does not accept them)
- Legacy `mod_name` routing → rejected (must use `mod:Name/` format)
- v1 resolver is NEVER invoked
- Any failure in v2 resolution is returned as-is, no fallback

When `dev_mode=False` (default):
- Normal routing logic applies (§1.1)
- v1 paths go through v1, canonical paths go through v2
- Backward compatible

### 2.2 Purpose

`dev_mode=True` is for **testing during the refactor**. It forces v2 resolution for ALL paths, which:
- Exposes v2 resolver bugs immediately (no v1 safety net)
- Confirms that canonical addressing works for every path pattern
- Can be toggled off instantly if v2 has a critical bug (agent just omits the parameter)

### 2.3 Lifecycle

`dev_mode` is a transitional parameter. After full migration:
- v1 resolver is removed entirely
- `dev_mode` parameter is removed (v2 is the only path)
- All paths must be canonical

---

## 3) ck3_file Refactor Safety

### The Circular Dependency Problem

If `ck3_file` breaks during refactor, the agent cannot write files to fix it. This was the original motivation for a parallel `ck3_fileV2` tool.

### Mitigation via Internal Routing

With single-tool internal routing (§1), this risk is substantially reduced:
- v1 resolver remains as the default for non-canonical paths
- If v2 resolver has a bug, agents using v1-style paths (the existing pattern) are unaffected
- Only agents explicitly using canonical addresses hit v2 bugs
- `dev_mode=False` (default) means the refactor doesn't change existing behavior

### Break-Glass Scenario

If the refactor breaks both v1 AND v2 in `ck3_file`:
1. `ck3_exec` with `python -c` is banned — cannot use for emergency fixes
2. But `ck3_exec` with a whitelisted command (e.g., `git checkout -- <file>`) can revert changes
3. Manual VS Code editing is always available as last resort
4. The Pylance linter catches type errors before runtime

---

## 4) Path Format Examples

### Current (v1) — continues to work

```
ck3_file(command="read", mod_name="My Mod", rel_path="common/traits/my_trait.txt")
ck3_file(command="write", mod_name="wip", rel_path="analysis.py", content="...")
ck3_file(command="read", mod_name="vanilla", rel_path="common/defines/00_defines.txt")
```

### New (v2) — canonical addressing

```
ck3_file(command="read", path="mod:My Mod/common/traits/my_trait.txt")
ck3_file(command="write", path="root:ck3raven_data/wip/analysis.py", content="...")
ck3_file(command="read", path="root:game/common/defines/00_defines.txt")
ck3_file(command="read", path="root:repo/docs/CANONICAL_ARCHITECTURE.md")
```

### Testing (dev_mode) — force v2 for everything

```
ck3_file(command="read", mod_name="My Mod", rel_path="common/traits/my_trait.txt", dev_mode=True)
# → Reply(type="I", code="CK3FILE-PATH-I-001",
#         data={"detail": "dev_mode requires canonical path format (root: or mod:)",
#               "invalid_field": "mod_name",
#               "example": "path='mod:My Mod/common/traits/my_trait.txt'"})
```

> **Note:** `dev_mode` errors are Reply type `I` (invalid input), not `E` (system error). Code follows `CK3FILE-PATH-I-001` convention. `data` includes: which field was wrong, a concrete example of the correct canonical path format.

---

## 5) Enforcement Differences

### v1 (current)
- `enforcement.py` decides allow/deny
- `world_adapter.py` resolves mod paths via `mod_paths` dict
- Reply contains host-absolute paths in some fields

### v2 (target)
- `enforcement_v2.py` with single `enforce()` function
- `world_adapter_v2.py` resolves via WA2 `resolve()` + VisibilityRef
- Reply contains only canonical paths (`root:` / `mod:`)
- Host paths only accessible via `wa2.host_path(ref)`
- Leak detector scans all success replies

---

## 6) Migration Checklist

### Phase A: Preparation (before any code changes)
- [ ] Write migration test battery for ck3_file (per Phase 2 Directive §4.1)
- [ ] Verify current v1 test coverage
- [ ] Identify all call sites for ck3_file in server.py

### Phase B: Add v2 Path (internal routing)
- [ ] Add `dev_mode: bool = False` parameter
- [ ] Add `path: str | None = None` parameter (if not already present)
- [ ] Implement routing logic (§1.1)
- [ ] For canonical paths: WA2 resolve → enforce_v2 → execute → leak scan
- [ ] For legacy paths: existing v1 flow unchanged
- [ ] Add `context_limit` condition factory to `capability_matrix_v2.py` (§9.2)
- [ ] Split `FILE_READ_COMMANDS` into `FILE_READ_UNGATED` + `FILE_READ_GATED` (§9.3)
- [ ] Update all OPERATIONS_MATRIX entries to use split read rules (§9.3)
- [ ] Add `context_limit` condition with CST index side effect to `capability_matrix_v2.py` (§9.5)
- [ ] Wire read enforcement gate in ck3_file dispatcher for v2-routed paths (§9.7)
- [ ] Run migration test battery with `dev_mode=False`

### Phase C: Validate v2 Path
- [ ] Run migration test battery with `dev_mode=True`
- [ ] Fix any v2 resolver issues
- [ ] Mutation testing on v2 path (per Phase 2 Directive §4.2)
- [ ] Purity gate extension (per Phase 2 Directive §4.3)

### Phase D: Transition
- [ ] Update agent instructions / docstrings to prefer canonical paths
- [ ] Monitor for v1 path usage reduction
- [ ] When zero v1 paths observed → proceed to Phase E

### Phase E: Cutover
- [ ] Remove v1 resolver from ck3_file
- [ ] Remove `dev_mode` parameter
- [ ] Remove v1 enforcement module (only if ck3_git is also migrated)
- [ ] Final migration test battery run

---

## 7) Dependencies

| Dependency | Status | Impact |
|------------|--------|--------|
| ck3_exec Tier 1 complete | **VERIFIED: COMPLETE** | Validated Feb 24 — `ck3_exec_Migration_Policy.md` §8-§9: all components DONE, 12/12 whitelist tests, E2E script signing, 4 negative tests pass. Commits `27013af`, `2f449bc`. |
| WA2 `resolve()` | DONE (Sprint 0) | Foundation for v2 path resolution |
| enforcement_v2 `enforce()` | DONE | Foundation for v2 enforcement |
| VisibilityRef + leak detector | DONE (Sprint 0) | Required for host path containment |
| ck3_file `format="json"` mode | DONE | JSON content handling already works |
| Agent instructions update | NOT DONE | Agents need canonical path examples in brief/docstrings |
| `context_limit` condition factory | NOT DONE | §9 — condition predicate using os.stat() pre-read gate, 15KB threshold |
| `_SPILL_EXEMPT` for ck3_file | DONE (Feb 24) | ck3_file exempt from global result spill system |
| `context_warning` in Reply.data | DONE (Feb 24) | Soft 16KB warning in `_file_read_raw` — coexists with future enforcement gate |
| File indexing service | NOT DONE | §10 — CST-based default interaction mode for files over 15KB threshold |
| `libcst` dependency | NOT DONE | Python CST parsing for file indexing + future CST edit mode |
| `tree-sitter` + `tree-sitter-typescript` | NOT DONE | TypeScript CST parsing for file indexing |
| `ck3_logs` 2KB spill cap | DONE (Feb 24) | ck3_logs replies >2KB spilled to file, same pattern as debug_get_logs |
| Reply spill architecture documented | DONE (Feb 24) | §10.14 — global 10KB cap, per-tool caps, spill vs denial distinction |
| v2 migration implementation plan | DONE (Feb 24) | §11 — 7-phase plan with validation gates, sub-agent strategy |

---

## 8) Design Decisions Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-02-22 | Single `ck3_file` with internal routing, not parallel `ck3_fileV2` | Avoids agent confusion, maintenance duplication, enforcement bypass |
| 2026-02-22 | No v1 fallback for canonical addresses | Surfaces v2 bugs immediately instead of hiding behind v1 |
| 2026-02-22 | `dev_mode=True` forces v2 for ALL paths | Testing parameter for refactor validation; transitional |
| 2026-02-22 | v1 remains as default for non-canonical paths | Backward compatibility; existing agent code unaffected |
| 2026-02-22 | Routing by path format (host abs vs canonical prefix) | Clean, unambiguous detection; no magic heuristics |
| 2026-02-22 | ck3_exec must finish before ck3_file refactor starts | ck3_exec is the simpler keystone; validates v2 infrastructure first |
| 2026-02-24 | `context_limit` condition on ck3_file read | Enforcement-layer gate prevents large reads from consuming agent context budget |
| 2026-02-24 | Condition uses `os.stat()` pre-read — not post-read content measurement | Runs before the read executes; fits EN v2 "gate before operation" model |
| 2026-02-24 | `ck3_file` exempt from global spill system | File content IS the point of the call; spilling it to a pointer is useless |
| 2026-02-24 | File indexing system as future context_limit reducer | Placeholder — may lower the gate threshold significantly by giving agents a smarter way to navigate large files |
| 2026-02-24 | CST (not AST) as the indexing abstraction | CST preserves formatting for round-trip editing; AST discards whitespace/comments, preventing lossless serialization |
| 2026-02-24 | CST index as DEFAULT interaction mode for large files | Agents almost never need entire files; index provides structural navigation at 2-7% of raw size |
| 2026-02-24 | On-demand indexing with mtime cache (Option A) | Infrastructure files change frequently; pre-indexing adds build time for unstable targets |
| 2026-02-24 | Auto-index on read (not separate `command="index"`) | Transparent to agents — they call read, get the right thing; no new protocol to learn |
| 2026-02-24 | `libcst` for Python, `tree-sitter` for TypeScript, regex for Markdown | Each tool fits its file type — libcst gives round-trip Python, tree-sitter handles TS complexity, regex suffices for headings |
| 2026-02-24 | Condition side effects for denial data (not enforcement enrichment) | Condition's `check()` generates CST index and writes it to file as side effect of returning `False`. Deposits denial metadata (including index file path) into explicit mutable `ctx` dict. Enforcement stays generic — merges `ctx["_denial_meta"]` if present. No tool-specific logic in EN. |
| 2026-02-24 | File-based CST index delivery (not inline in Reply) | CST index written to file; denial Reply.data contains `index_path` (session-absolute path). Agent reads the file as next step. Avoids embedding 4-5KB index in denial Reply, which would partially defeat context budget gate. Compliant with Canonical Reply System. |
| 2026-02-24 | Explicit mutable `ctx` dict for condition side effects | Enforcement creates `ctx = {}` and passes it to `condition.check(ctx=ctx)`. Conditions deposit `_denial_meta` into this dict. **Not `**kwargs`** — Python's keyword unpacking creates a new dict, so mutations wouldn't propagate. This is a guaranteed implementation footgun. |
| 2026-02-24 | Context limit threshold lowered from 25KB to 15KB | Since agents receive the CST index file path as a constructive next step in the denial, the threshold can be lower — the denial is no longer a dead end. 15KB balances context budget protection with practical usability. Can be further lowered to 8KB or 4KB as indexing matures. |
| 2026-02-24 | Estimated line counts labeled as estimates | `estimated_total_lines`, `estimated_bytes`, `avg_line_bytes` — clearly labeled in denial data so agents don't treat heuristic values as authoritative. Enables future tuning. |
| 2026-02-24 | `next_call` template in denial Reply | Agents do better with concrete next-step shapes. Denial data includes `next_call: {tool, command, path, note}` template the agent can follow. |
| 2026-02-24 | Cache versioning (parser + indexer versions) | Cache metadata includes `parser`, `parser_version`, `indexer_version`. Invalidate if any mismatch — library upgrades may produce different index elements for same source. |
| 2026-02-24 | Atomic cache writes (tmp + os.replace) | All index cache writes use `write_tmp → os.replace` pattern for crash-safety under MCP multi-thread/multi-process model. |
| 2026-02-24 | dev_mode error is Reply type I, not E | Non-canonical paths in `dev_mode=True` return `Reply(type="I", code="CK3FILE-PATH-I-001")` with `invalid_field` and `example` in data. Invalid input, not system error. |
| 2026-02-24 | ck3_exec Tier 1 verified COMPLETE | `ck3_exec_Migration_Policy.md` §8-§9: all components DONE, 12/12 whitelist tests, E2E signing test, 4 negative tests. Commits `27013af`, `2f449bc`. Prerequisite satisfied. |
| 2026-02-24 | Detailed sprint plan added as §12 | 5 sprints, 14 sub-agent tasks. Tasks marked `[SA]` have context files + acceptance criteria for focused delegation. `[MAIN]` tasks stay with orchestrating agent. Python CST recommended for SA-1.2 (enforcement edit), SA-1.3 (matrix replacements), SA-5.3 (v1 removal). |
| 2026-02-24 | CK3FILE-V2-DIR v1.0 — Canonical Directive Block locked | 5 directives locked as §9.0: (D1) no escape hatch — no allowlists/overrides/bypass, (D2) unified estimation Method B only — proportional formula `file_bytes * (requested_lines / estimated_total_lines)`, no alternatives, (D3) canonical paths only in agent-facing replies — CI gate for host-path leaks, (D4) Sprint 1 returns guidance + next_call template only — no indexer call/placeholder/stub, (D5) canonical outcome definition — `reply_type="D"`, `code="EN-GATE-D-001"`, required field set locked. |

---

## 9) Context Budget Gate (`context_limit`)

> **Added:** February 24, 2026
> **Status:** Design complete, implementation deferred to ck3_file v2 refactor

### 9.0 Canonical Directive Block — CK3FILE-V2-DIR v1.0

> **Version:** `CK3FILE-V2-DIR v1.0` — Locked February 24, 2026
> **Scope:** All `context_limit` behavior, denial semantics, and Sprint phasing
> **Authority:** This block supersedes any conflicting language elsewhere in §9–§12.
> **Rule:** Changes to this block require a new version tag (v1.1, v2.0, etc.)

#### D1 — No Escape Hatch

There is **no** mechanism to bypass the `context_limit` gate. The following are permanently prohibited:

| Prohibited Pattern | Why |
|--------------------|-----|
| `ack_context_cost` parameter | Agents must not self-authorize large reads |
| Per-file or per-path allowlists | Creates maintenance burden and defeats the budget gate |
| Higher caps for `root:repo` or "core infra" files | All domains are equal under the gate |
| `dev_mode` / `dev_only` overrides for the gate | dev_mode controls routing, not gate bypass |
| Any "soft success" conversion of denials | A denial is a denial — no tool-layer interception |

For large files, the system redirects agents via denial metadata (guidance, `next_call` template, and — post-indexer — a structural index). The gate itself is never bypassed.

#### D2 — Unified Size Estimation (Method B)

One estimation method, everywhere, inside `context_limit`:

```python
AVG_LINE_BYTES = 45  # single named constant

file_bytes = os.stat(host_abs).st_size
estimated_total_lines = max(1, file_bytes // AVG_LINE_BYTES)

if start_line is not None and end_line is not None:
    requested_lines = max(1, end_line - start_line + 1)
    estimated_bytes = int(file_bytes * (requested_lines / estimated_total_lines))
else:
    estimated_bytes = file_bytes
```

Decision rule: `estimated_bytes <= threshold_bytes` → **allow**, otherwise → **deny**.

All values in denial data are labeled as **estimates**: `estimated_bytes`, `estimated_total_lines`, `avg_line_bytes`.

No alternative formulas (e.g., `requested_lines * AVG_LINE_BYTES`) are permitted — the proportional formula `file_bytes * (requested_lines / estimated_total_lines)` is the single source of truth.

#### D3 — Path Exposure Rules

**Agent-facing replies must contain only session-absolute canonical addresses.** They must **never** contain host-absolute paths.

| Context | Path Form | Example |
|---------|-----------|---------|
| Agent-facing denial data | Canonical | `root:ck3raven_data/wip/MCP_tool_replies/ck3_file/indexes/a1b2c3.json` |
| Agent-facing `next_call.path` | Canonical | `root:repo/tools/ck3lens_mcp/server.py` |
| Internal condition logic | Host-absolute | `Path("C:/Users/.../server.py")` — never exposed |
| Index file writes | Host-absolute internally | Converted to canonical before placing in reply |

**CI test gate:** Denial replies must fail CI if any host-path signatures appear (`/home/`, `C:\`, `C:/Users`, `\\\\` patterns). This is a unit test on denial `Reply.data`, not a runtime check.

#### D4 — Sprint-1 Behavior (Pre-Indexer)

In Sprint 1 (before the indexing module from Sprint 2 exists):

- On `context_limit` failure, do **NOT** write an index file
- Do **NOT** call any indexer placeholder or stub
- Return **only**:
  - `guidance`: brief human-readable string
  - `next_call`: template for a narrower read (see below)
  - `file_bytes`, `threshold_bytes`, `estimated_bytes`, `estimated_total_lines`, `avg_line_bytes`
  - `alternatives`: list of alternative approaches

Sprint 1 `next_call` template:

```json
"next_call": {
  "tool": "ck3_file",
  "command": "read",
  "path": "<same canonical path>",
  "start_line": "<fill>",
  "end_line": "<fill>"
}
```

Post-Sprint 2 additions (when indexers exist):
- `index_path`: canonical session-absolute path to the cached index file
- `index_type`: `"cst"`, `"heading"`, `"fallback"`, etc.
- `index_element_count`: integer
- `next_call` may point to the index file instead

#### D5 — Canonical Outcome Definition

When `context_limit` fails, enforcement returns **DENIED**:

```
reply_type = "D"
code       = "EN-GATE-D-001"
```

**Required `data` fields (all sprints):**

| Field | Type | Description |
|-------|------|-------------|
| `detail` | `str` | `"Conditions not met: ['context_limit']"` |
| `failed_conditions` | `list[str]` | `["context_limit"]` |
| `file_bytes` | `int` | Actual file size from `os.stat()` |
| `threshold_bytes` | `int` | Gate threshold (15360) |
| `estimated_bytes` | `int` | Estimated bytes for the requested range |
| `estimated_total_lines` | `int` | `max(1, file_bytes // AVG_LINE_BYTES)` |
| `avg_line_bytes` | `int` | The `AVG_LINE_BYTES` constant used (45) |
| `guidance` | `str` | Brief actionable message |
| `next_call` | `dict` | Template for a narrower read |
| `alternatives` | `list[str]` | Alternative approaches |

**Additional `data` fields (post-Sprint 2 only):**

| Field | Type | Description |
|-------|------|-------------|
| `index_path` | `str` | Canonical path to cached index file |
| `index_type` | `str` | `"cst"`, `"heading"`, `"fallback"` |
| `index_element_count` | `int` | Number of elements in the index |

No tool-layer interception. No "soft success" conversion. The denial Reply passes through `ck3_file` unmodified.

---

### 9.1 Problem

Agent context windows are finite. A single `ck3_file(command="read")` on a large file can consume 15KB+ of context budget, permanently occupying space that limits the agent's ability to reason about subsequent tasks. The agent often doesn't need the entire file — it needs specific functions, sections, or blocks.

### 9.2 Design: `context_limit` Condition Predicate

A new condition factory in `capability_matrix_v2.py` that gates `("ck3_file", "read")` operations based on the estimated result size.

#### Condition Factory

```python
AVG_LINE_BYTES = 45  # single named constant — see D2

def context_limit(threshold_bytes: int = 15 * 1024) -> Condition:
    """True if the estimated read result is within the context budget threshold.

    Uses os.stat() on the resolved host path to get file size in bytes.
    Estimation follows D2 (Unified Size Estimation — Method B):
        estimated_total_lines = max(1, file_bytes // AVG_LINE_BYTES)
        If start_line/end_line present:
            requested_lines = max(1, end_line - start_line + 1)
            estimated_bytes = int(file_bytes * (requested_lines / estimated_total_lines))
        Else:
            estimated_bytes = file_bytes
        Allow if estimated_bytes <= threshold_bytes.

    Context keys:
        host_abs: Path — resolved host-absolute path from WA2
        start_line: int | None — requested start line (1-based)
        end_line: int | None — requested end line (1-based)
        ctx: dict — mutable dict; condition deposits _denial_meta here on failure
    """
```

#### Why `os.stat()` Pre-Read (Not Post-Read Content Measurement)

The condition runs BEFORE the operation executes, consistent with EN v2's "single gate before operation" model. `os.stat()` gives the file's byte size without reading its content. Combined with a quick line count (or estimation), this determines whether the read would exceed the threshold.

**Trade-off:** `os.stat()` gives raw file bytes, not the exact byte count of the selected line range. The estimate `file_bytes * (requested_lines / total_lines)` is approximate. This is acceptable — the gate is a budget guardrail, not a precision instrument.

**Line count estimation:** Use a single named constant `AVG_LINE_BYTES = 45` and the proportional formula from D2: `estimated_total_lines = max(1, file_bytes // AVG_LINE_BYTES)`. If `start_line` and `end_line` are provided, `estimated_bytes = int(file_bytes * (max(1, end_line - start_line + 1) / estimated_total_lines))`. No alternative formulas permitted (see D2).

### 9.3 Capability Matrix v2 Changes

#### Split `FILE_READ_COMMANDS`

Currently `("ck3_file", "read")` is part of `FILE_READ_COMMANDS` which uses the shared `_READS` rule (no conditions). To add a condition to reads only for `("ck3_file", "read")`, split the command sets:

```python
# Reads that are always unconditioned
FILE_READ_UNGATED: frozenset[tuple[str, str]] = frozenset({
    ("ck3_file", "get"),      # DB lookup — already bounded by DB
    ("ck3_file", "list"),     # Directory listing — naturally compact
    ("ck3_file", "refresh"),  # Cache refresh — no content returned
})

# Reads subject to context_limit gate
FILE_READ_GATED: frozenset[tuple[str, str]] = frozenset({
    ("ck3_file", "read"),     # Filesystem read — unbounded content
})

# Shared condition instance
_CONTEXT_LIMIT = context_limit(threshold_bytes=15 * 1024)
```

#### Updated OPERATIONS_MATRIX Entries

Every location that currently uses `_READS` (which includes all `FILE_READ_COMMANDS`) needs to be updated. The pattern:

```python
# Before (current):
("ck3lens", "game", None): (
    _READS,
)

# After:
_READS_UNGATED = OperationRule(
    FILE_READ_UNGATED | DIR_READ_COMMANDS | GIT_READ_COMMANDS | FOLDER_READ_COMMANDS
)
_READS_GATED = OperationRule(FILE_READ_GATED, (_CONTEXT_LIMIT,))

("ck3lens", "game", None): (
    _READS_UNGATED,
    _READS_GATED,
)
```

This means: `ck3_file(command="read")` on any location is subject to the `context_limit` condition. All other read operations remain unconditioned.

### 9.4 Enforcement Flow

When the agent calls `ck3_file(command="read", path="root:repo/tools/ck3lens_mcp/server.py")`:

1. **WA2 resolves** `root:repo/...` → host-absolute path, root_key=`"repo"`, subdirectory=`None`
2. **enforcement_v2.enforce()** finds the matching rule:
   - Looks up `("ck3raven-dev", "repo", None)` in OPERATIONS_MATRIX
   - Scans rules for `("ck3_file", "read")` → matches `_READS_GATED`
   - Evaluates `context_limit` condition with context: `{host_abs: Path(...), start_line: None, end_line: None}`
3. **Condition evaluates:**
   - `os.stat(host_abs).st_size` → e.g., 250,000 bytes (server.py is ~250KB)
   - `start_line=None, end_line=None` → full file requested
   - 250,000 > 15,360 → condition returns `False`
4. **Enforcement denies** with `EN-GATE-D-001`:
   ```json
   {
     "reply_type": "D",
     "code": "EN-GATE-D-001",
     "data": {
       "detail": "Conditions not met: ['context_limit']",
       "failed_conditions": ["context_limit"],
       "file_bytes": 250000,
       "threshold_bytes": 15360,
       "estimated_total_lines": 5556,
       "estimated_bytes": 250000,
       "avg_line_bytes": 45,
       "guidance": "File exceeds context budget (250000B > 15360B). Use start_line/end_line to read a specific range.",
       "next_call": {
         "tool": "ck3_file",
         "command": "read",
         "path": "root:repo/tools/ck3lens_mcp/server.py",
         "start_line": "<fill>",
         "end_line": "<fill>"
       },
       "alternatives": [
         "Use start_line/end_line to read a narrower range (e.g., 1-200)",
         "Use ck3_search to find the specific symbols or content you need",
         "Write a Python script helper via ck3_exec for targeted extraction",
         "Spawn a sub-agent with a focused task (sub-agent context is separate)"
       ]
     }
   }
   ```

   > **Note:** This example shows Sprint 1 output. Post-Sprint 2, the data will also include `index_path`, `index_type`, and `index_element_count` (see D4/D5).

#### With Narrow Range (Passes)

`ck3_file(command="read", path="root:repo/server.py", start_line=100, end_line=200)`:
- `os.stat()` → 250,000 bytes
- `estimated_total_lines = max(1, 250000 // 45)` → 5556
- `requested_lines = max(1, 200 - 100 + 1)` → 101
- `estimated_bytes = int(250000 * (101 / 5556))` → 4,540
- 4,540 ≤ 15,360 → condition returns `True` → enforcement allows

### 9.5 Condition-Driven Denial Data

> **Phased behavior:** See D4 for Sprint 1 vs post-Sprint 2 distinction.

When the `context_limit` condition fails, the denial Reply must include structured guidance. **All of this is driven by the condition predicate in the capability matrix, not by enforcement-specific code.**

**Core Insight:** A condition predicate is a function that returns `bool`. But nothing prevents that function from having **side effects**. This is the power of the architecture — condition predicates can trigger arbitrary work as part of their evaluation, without any changes to enforcement's generic plumbing.

#### 9.5.1 Sprint 1 Behavior (Pre-Indexer)

In Sprint 1, the `context_limit` condition's `check()` function, when it determines the file exceeds the threshold:

1. Builds denial metadata with sizes, guidance, `next_call` template, and alternatives
2. Deposits the metadata into `ctx["_denial_meta"]` — the explicit mutable dict passed by enforcement
3. Returns `False`

**No indexer is called. No index file is written. No placeholder or stub.**

#### 9.5.2 Post-Sprint 2 Behavior (With Indexers)

After the indexing module (§10) is implemented in Sprint 2, the condition additionally:

1. Generates (or retrieves from cache) the CST index for the file
2. Writes the index to `~/.ck3raven/wip/MCP_tool_replies/ck3_file/indexes/{sha256}.json` (atomic write: tmp file → `os.replace()`)
3. Converts the host-absolute cache path to a **canonical session-absolute address** (`root:ck3raven_data/wip/MCP_tool_replies/ck3_file/indexes/{sha256}.json`) — see D3
4. Adds `index_path`, `index_type`, and `index_element_count` to the denial metadata

**The agent receives a denial Reply** whose `data` field contains:
- `guidance` — actionable message
- `next_call` — Sprint 1: template for narrower read on the same file; post-Sprint 2: may point to index file
- `estimated_total_lines`, `estimated_bytes`, `avg_line_bytes` — clearly labeled **estimates**
- `file_bytes`, `threshold_bytes` — factual size info
- `alternatives` — guidance list
- Post-Sprint 2 only: `index_path` (canonical session-absolute path to the cached CST index)

**Why not inline the CST index in `Reply.data`?** The index can be 4-5KB for large files. Embedding it in the denial Reply would partially defeat the purpose of the context budget gate. Instead, the agent reads the ~5KB index file explicitly, making the context cost visible and intentional.

**Critical: `ctx` Must Be an Explicit Mutable Dict**

The `check()` function receives `ctx` as a **named keyword parameter** bound to a mutable dict that enforcement creates and passes. This is NOT `**kwargs` — Python's `**kwargs` creates a new dict for the callee, so mutations don't propagate back to the caller. The `ctx` pattern ensures side effects are visible to enforcement.

#### 9.5.3 Code — Sprint 1 Implementation

```python
AVG_LINE_BYTES = 45  # D2 — single named constant

def context_limit(threshold_bytes: int = 15 * 1024) -> Condition:
    def _check(*, host_abs=None, start_line=None, end_line=None,
               canonical_path: str | None = None,
               ctx: dict | None = None, **_kw) -> bool:
        if host_abs is None:
            return True
        file_bytes = os.stat(host_abs).st_size
        estimated_total_lines = max(1, file_bytes // AVG_LINE_BYTES)

        if start_line is not None and end_line is not None:
            requested_lines = max(1, end_line - start_line + 1)
            estimated_bytes = int(file_bytes * (requested_lines / estimated_total_lines))
        else:
            estimated_bytes = file_bytes

        if estimated_bytes <= threshold_bytes:
            return True

        # --- Side effects (denial path only) ---
        path_label = canonical_path or "<unknown>"

        meta: dict = {
            "file_bytes": file_bytes,
            "threshold_bytes": threshold_bytes,
            "estimated_total_lines": estimated_total_lines,
            "estimated_bytes": estimated_bytes,
            "avg_line_bytes": AVG_LINE_BYTES,
            "guidance": (
                f"File exceeds context budget ({file_bytes}B > {threshold_bytes}B). "
                "Use start_line/end_line to read a specific range."
            ),
            "next_call": {
                "tool": "ck3_file",
                "command": "read",
                "path": path_label,
                "start_line": "<fill>",
                "end_line": "<fill>",
            },
            "alternatives": [
                "Use start_line/end_line to read a narrower range",
                "Use ck3_search to find specific symbols or content",
                "Spawn a sub-agent with a focused task",
            ],
        }

        # Deposit into the mutable ctx dict (NOT **kwargs)
        if ctx is not None:
            ctx["_denial_meta"] = meta

        return False

    return Condition("context_limit", _check)
```

> **Sprint 2 addition:** After the indexing module exists, add an indexer call between the `meta` construction and the `ctx` deposit. The indexer writes the cache file, converts the host path to canonical form, and adds `index_path`, `index_type`, `index_element_count` to `meta`. The `next_call` may then point to the canonical index path instead.

#### 9.5.4 Enforcement — Generic `ctx["_denial_meta"]` Merge

**Enforcement remains completely generic:**

```python
# In enforcement_v2.enforce():
ctx: dict = {}  # mutable dict — shared with condition check()

# ... evaluate condition ...
result = condition.check(host_abs=host_abs, start_line=start_line,
                         end_line=end_line, canonical_path=canonical_path,
                         ctx=ctx)

# ... if condition failed, build denial ...
if "_denial_meta" in ctx:
    denial_data.update(ctx["_denial_meta"])
```

Two additions to enforcement, both fully generic:
1. Create `ctx = {}` before evaluating conditions
2. After building denial data, merge `ctx["_denial_meta"]` if present

No knowledge of CST indexes, file sizes, or thresholds. Enforcement just passes through whatever the condition deposited. Any future condition can use the same `_denial_meta` mechanism via the shared `ctx` dict.

#### 9.5.5 Resulting Denial Reply — Sprint 1

```json
{
  "reply_type": "D",
  "code": "EN-GATE-D-001",
  "data": {
    "detail": "Conditions not met: ['context_limit']",
    "failed_conditions": ["context_limit"],
    "file_bytes": 265000,
    "threshold_bytes": 15360,
    "estimated_total_lines": 5889,
    "estimated_bytes": 265000,
    "avg_line_bytes": 45,
    "guidance": "File exceeds context budget (265000B > 15360B). Use start_line/end_line to read a specific range.",
    "next_call": {
      "tool": "ck3_file",
      "command": "read",
      "path": "root:repo/tools/ck3lens_mcp/server.py",
      "start_line": "<fill>",
      "end_line": "<fill>"
    },
    "alternatives": [
      "Use start_line/end_line to read a narrower range",
      "Use ck3_search to find specific symbols or content",
      "Spawn a sub-agent with a focused task"
    ]
  }
}
```

#### 9.5.6 Resulting Denial Reply — Post-Sprint 2 (With Index)

```json
{
  "reply_type": "D",
  "code": "EN-GATE-D-001",
  "data": {
    "detail": "Conditions not met: ['context_limit']",
    "failed_conditions": ["context_limit"],
    "file_bytes": 265000,
    "threshold_bytes": 15360,
    "estimated_total_lines": 5889,
    "estimated_bytes": 265000,
    "avg_line_bytes": 45,
    "guidance": "File exceeds context budget (265000B > 15360B). CST index written with 62 elements. Read the index file, then use start_line/end_line for specific elements.",
    "index_path": "root:ck3raven_data/wip/MCP_tool_replies/ck3_file/indexes/a1b2c3d4.json",
    "index_type": "cst",
    "index_element_count": 62,
    "next_call": {
      "tool": "ck3_file",
      "command": "read",
      "path": "root:ck3raven_data/wip/MCP_tool_replies/ck3_file/indexes/a1b2c3d4.json"
    },
    "alternatives": [
      "Use start_line/end_line to read a narrower range",
      "Use ck3_search to find specific symbols or content",
      "Spawn a sub-agent with a focused task"
    ]
  }
}
```

#### 9.5.7 CI Test Gate — No Host Paths in Denial Data

Per D3, denial `Reply.data` must never contain host-absolute paths. Add a CI unit test:

```python
def test_denial_data_no_host_paths():
    """Denial Reply.data must contain only canonical session-absolute addresses."""
    import re
    HOST_PATH_RE = re.compile(r'C:\\|C:/Users|/home/|\\\\')
    
    # ... trigger a context_limit denial ...
    for key, value in denial_reply.data.items():
        if isinstance(value, str):
            assert not HOST_PATH_RE.search(value), (
                f"Host path leaked in denial data['{key}']: {value}"
            )
        if isinstance(value, dict):
            for k, v in value.items():
                if isinstance(v, str):
                    assert not HOST_PATH_RE.search(v), (
                        f"Host path leaked in denial data['{key}']['{k}']: {v}"
                    )
```

This test must run against both Sprint 1 (no index) and post-Sprint 2 (with index) denial replies.

**Directive:** All denial payload data — alternatives, sizes, index pointers, next_call templates — is produced by the condition factory in `capability_matrix_v2.py`. Enforcement's only role is the generic `ctx["_denial_meta"]` merge. No enforcement-level special shaping for this or any other condition.

**Alternative approaches (documented for future reference):**

- **Option B — Two-phase read:** Split `ck3_file read` into two capability matrix entries: (1) an always-allowed "produce index" step, (2) a conditioned "return raw content" step. The index is always produced; raw content is gated.
- **Option C — `denial_data_fn` on Condition:** Add an optional callback to the `Condition` dataclass that enforcement calls on failure. More structured but requires a Condition schema change.
- **Option D — `**context` mutation:** Use `**kwargs` to pass context. **REJECTED** — Python's `**kwargs` creates a new dict for the callee; mutations don't propagate back. Guaranteed implementation footgun.

The side-effects approach (Option A) with an explicit mutable `ctx` dict is simplest — the `Condition` class stays unchanged, enforcement gets two generic lines, and all the logic lives in the condition factory.

### 9.6 Context Warning (Interim — Already Implemented)

Until the full v2 enforcement gate is wired during the ck3_file refactor, a soft `context_warning` field exists in `Reply.data` for reads between 10KB–15KB. This is a data-level nudge within the WA layer (not a denial) and coexists with the future enforcement gate:

- **10–15KB**: `data.context_warning` = "This read returned ~NKB. Consider using start_line/end_line..."
- **>15KB**: Enforcement-layer denial via `context_limit` condition (once v2 is wired)

The 10KB warning remains useful even after the 15KB gate is active — it catches reads that pass the gate but are still heavy.

### 9.7 Wiring in ck3_file Dispatcher

Currently, `ck3_file` in `unified_tools.py` only calls enforcement for `write_commands`. The v2 refactor must add enforcement for reads too:

```python
# In ck3_file dispatcher, BEFORE routing to _file_read_raw:
if command == "read" and resolution and resolution.absolute_path:
    from ck3lens.policy.enforcement_v2 import enforce as enforce_v2
    from ck3lens.world_adapter_v2 import _ROOT_KEY_FROM_ENUM

    root_key = _ROOT_KEY_FROM_ENUM.get(
        resolution.root_category.name, "repo"
    ) if resolution.root_category else "repo"

    result = enforce_v2(
        rb,
        mode=mode,
        tool="ck3_file",
        command="read",
        root_key=root_key,
        subdirectory=resolution.subdirectory,
        host_abs=resolution.absolute_path,
        canonical_path=canonical_address,  # D3: needed for next_call template
        start_line=start_line,
        end_line=end_line,
    )
    if result.is_denied:
        return result
```

This is the v2 enforcement call pattern. During the refactor (§1.1), v2-routed paths will use this. v1-routed paths continue to skip read enforcement (until cutover).

---

## 10) CST-Based File Indexing — Default Interaction Mode

> **Added:** February 24, 2026
> **Revised:** February 24, 2026 — expanded from placeholder to full design
> **Status:** Design complete — implementation deferred until §9 context gate is wired
> **Dependencies:** `context_limit` condition (§9), `libcst` (Python), `tree-sitter` (TypeScript)

### 10.1 Design Thesis

**CST indexing is the DEFAULT way agents interact with files through `ck3_file`, not an optional enhancement.**

When an agent calls `ck3_file(command="read")` on a file over the context threshold, it receives a **Concrete Syntax Tree index** instead of raw content. This index is a compact structural map — names, types, signatures, and line ranges for every top-level element — that lets the agent:

1. **Understand the file's architecture** without consuming the full content (~5KB index vs ~265KB raw)
2. **Request specific ranges** for the elements it actually needs (`start_line=532, end_line=590`)
3. **Edit via node replacement** — modify a named CST node, serialize back, round-trip safe

The key insight: **agents almost never need an entire file**. They need to find something, read that something, and optionally change it. The CST index is the navigation layer that eliminates the wasteful "read 6,000 lines to find one function" pattern.

### 10.2 Size Analysis — Real Files

Using actual files from this project to demonstrate the compression ratio:

| File | Lines | Raw (~bytes) | Top-Level Defs | CST Index (~bytes) | Ratio |
|------|-------|-------------|----------------|-------------------|-------|
| `server.py` | 6,636 | ~265KB | 62 functions | ~4.3KB | **1.6%** |
| `unified_tools.py` | 2,775 | ~111KB | 42 functions | ~2.9KB | **2.6%** |
| `extension.ts` | 1,156 | ~52KB | 15 exports/functions | ~1.5KB | **2.9%** |
| `diagnosticsServer.ts` | 900 | ~40KB | 8 classes/interfaces | ~1.0KB | **2.5%** |
| `safety.py` | 767 | ~31KB | 11 functions + 1 class (10 methods) | ~1.4KB | **4.5%** |
| `capability_matrix_v2.py` | 722 | ~29KB | 12 defs/classes | ~1.2KB | **4.1%** |
| `mcpServerProvider.ts` | 370 | ~17KB | 12 exports/functions | ~1.2KB | **7.1%** |
| `ck3_file_Refactor_Design.md` | ~520 | ~25KB | ~15 headings | ~0.9KB | **3.6%** |

**Index entry size estimate:** ~70 bytes per function (name + signature + line range), ~100 bytes for classes with method listing. One level into classes (listing methods with signatures), add ~60 bytes per method.

**Conclusion:** CST indexes are **2–7% of raw file size**, consistently fitting within 5KB even for the largest files. A 5KB index replaces a 265KB read — a **50x compression** that leaves the agent's context budget nearly untouched.

### 10.3 Process Flow — Current vs CST-Enabled

#### Current v2 Plan (§9 only — no indexing)

```
Agent: ck3_file(command="read", path="root:repo/tools/ck3lens_mcp/server.py")
  │
  ├─ WA2 resolve → host path, root_key
  ├─ enforcement_v2.enforce() → evaluates context_limit
  │   └─ os.stat() → 265KB > 15KB threshold
  │   └─ DENIED: "Use start_line/end_line, use ck3_search, ..."
  │
  ╰─ Agent is stuck. Doesn't know where the function it needs lives.
     Must use ck3_search or grep_raw to find it first, THEN re-read
     with a narrow range. Minimum 3 tool calls to read one function.
```

**Problem:** The denial tells the agent to use narrower ranges but doesn't tell it *which* ranges contain what. The agent must fumble through search tools to discover the file's structure before it can even start working.

#### CST-Enabled Plan (§9 + §10)

```
Agent: ck3_file(command="read", path="root:repo/tools/ck3lens_mcp/server.py")
  │
  ├─ WA2 resolve → host path, root_key
  ├─ enforcement_v2.enforce() → evaluates context_limit
  │   └─ os.stat() → 265KB > 15KB threshold
  │   └─ condition side effect: generates CST index → writes to file
  │   └─ condition deposits _denial_meta into ctx dict → returns False
  │   └─ enforcement builds DENIAL (EN-GATE-D-001)
  │   └─ merges ctx["_denial_meta"] into denial data
  │
  ├─ Reply: DENIAL with CST index file path in data
  │   reply_type = "D"
  │   code = "EN-GATE-D-001"
  │   data.index_path = "root:ck3raven_data/wip/MCP_tool_replies/ck3_file/indexes/a1b2c3.json"
  │   data.index_element_count = 62
  │   data.file_bytes = 265000
  │   data.threshold_bytes = 15360
  │   data.estimated_total_lines = 5889
  │   data.avg_line_bytes = 45
  │   data.guidance = "File exceeds context budget. Use start_line/end_line..."
  │   data.next_call = {tool: "ck3_file", command: "read",
  │                     path: "root:ck3raven_data/wip/.../indexes/a1b2c3.json"}
  │
  ╰─ Agent reads the index file (~4.3KB):
     ck3_file(command="read", path="<index_path>")
     → Sees: "ck3_file function at L1756-L1924"
     ck3_file(command="read", path="...", start_line=1756, end_line=1924)
     → Returns 169 lines (~6.8KB). Context cost: 4.3KB + 6.8KB = 11.1KB
```

**Improvement:** 3 tool calls (read → read index → read range) vs the old "fumbling search" pattern that could cost 15-30KB across multiple search/read cycles. Agent gets structural understanding immediately. Total context: ~11KB instead of ~265KB.

### 10.4 Interaction with `context_limit` Gate (§9)

The CST indexer enriches the `context_limit` denial with a constructive next step — the agent gets a file pointer to the CST index, not just a bare "too big" message:

#### Behavior Matrix

| File Size | Line Range | Result |
|-----------|-----------|--------|
| Under threshold (≤15KB) | Any | **Raw content returned** — no indexing needed |
| Over threshold (>15KB) | None / full file | **DENIAL** with CST index file path in `Reply.data` — agent reads the index file, then narrows |
| Over threshold (>15KB) | Narrow range (estimated ≤15KB) | **Raw content returned** — agent already knows what it wants |
| Over threshold (>15KB) | Wide range (estimated >15KB) | **DENIAL** with CST index file path — still too much content |

The `context_limit` condition in `capability_matrix_v2.py` remains as designed in §9 — it returns `True`/`False` based on estimated size. When it returns `False`, the condition's side effects (§9.5) write the CST index to a file and deposit metadata into the mutable `ctx` dict. Enforcement then produces a denial Reply with the metadata merged in:

- **Enforcement denies** with the standard `EN-GATE-D-001` code
- **Condition side effects** generate the CST index, write it to cache, and deposit `_denial_meta` (including `index_path`, `next_call`, guidance) into the `ctx` dict
- **Enforcement merges** `ctx["_denial_meta"]` into the denial Reply's `data` field (generic, one `if` statement)
- **ck3_file does NOT intercept** — the denial Reply IS the response, containing everything the agent needs

No special-case code in enforcement or ck3_file. The condition in the capability matrix owns the denial data entirely.

This means the `context_limit` condition *fires* → side effects write the index file + populate `ctx["_denial_meta"]` → enforcement *denies* → denial Reply.data contains the index file path and guidance. Other enforcement denials (wrong mode, wrong location, etc.) are unaffected — they don't set `_denial_meta`.

#### Future: Lowered Threshold

The 15KB threshold can be further lowered to 8KB or even 4KB as CST indexing matures. The agent experience remains good — it gets index file paths for more files but the indexes are tiny (1-5KB), so total context consumption stays low.

### 10.5 Language-Specific CST Strategies

Different file types need different parsing approaches. All produce the same index format (§10.7) but use different backend parsers.

#### 10.5.1 Python (`.py`) — `libcst`

**Library:** `libcst` (Concrete Syntax Tree — preserves formatting, whitespace, comments)

**Why CST, not AST:** Python's stdlib `ast` module discards formatting. `libcst` preserves everything, enabling round-trip editing (parse → modify → serialize back with identical formatting for untouched nodes). This is critical for the edit pathway (§10.6).

**Extraction depth:** Top-level + one level into classes:
- **Top-level:** functions, classes, global assignments, import groups
- **Class-level:** methods (with signatures), class variables, inner classes (name only)
- **Nested functions/classes:** listed by name + line range only (no deeper)

**Example index for `safety.py`:**
```
imports                                           L1-L30
_SPILL_GLOBAL_THRESHOLD = 10 * 1024               L56
_SPILL_PER_TOOL: dict[str, int]                    L58-L61
_SPILL_EXEMPT: set[str]                            L66
def _get_spill_dir(tool_name: str) -> Path         L68-L73
def _generate_search_summary(data: Dict) -> str    L75-L152
def _spill_result(tool_name, trace_id, ...) -> str L154-L198
def initialize_window_trace(window_id: str) -> Path L247-L277
def _log_trace_event(event: dict) -> None          L279-L289
def _log_tool_call(...) -> None                    L291-L344
def _log_canonical_tool_end(...) -> None           L346-L413
def get_current_trace_info() -> TraceInfo          L415-L426
def mcp_safe_tool(func: Callable) -> Callable      L428-L576
def _log_exception(trace_id, tool_name, ...) -> None L578-L596
class ReplyBuilder                                 L598-L767
  def __init__(self, tool, trace, meta)            L605-L620
  def success(self, code, data) -> Reply           L622-L640
  def error(self, code, data) -> Reply             L642-L660
  def denied(self, code, data) -> Reply            L662-L680
  def invalid(self, code, data) -> Reply           L682-L700
  def _build(self, reply_type, code, data) -> Reply L702-L720
  ...
```

**Parse cost:** `libcst.parse_module()` on a 6,636-line file takes ~50-100ms. Acceptable for on-demand indexing, and cached afterward.

#### 10.5.2 TypeScript (`.ts`) — `tree-sitter`

**Library:** `tree-sitter` with `tree-sitter-typescript` grammar

**Why tree-sitter:** Fast C-based parser with Python bindings. Produces a CST (preserves all source text). Same parser family VS Code uses internally. Handles generics, decorators, and complex TS syntax that regex cannot.

**Extraction depth:** Same as Python — top-level + one level into classes:
- **Top-level:** `export function`, `export class`, `export interface`, `export type`, `export const`, `import` groups
- **Class-level:** methods, properties, constructor
- **Complex types:** type aliases shown with a truncated signature

**Example index for `extension.ts`:**
```
imports                                            L1-L12
function cleanupStaleModeFiles(logger: Logger)     L58-L100
export async function activate(context)            L102-L390
function registerCommands(context, session, ...)   L392-L1041
function registerFileWatchers(context, linting)    L1043-L1091
export async function deactivate()                 L1093-L1156
```

**Dependency:** `tree-sitter` (pip) + `tree-sitter-typescript` (pip). Both are wheels — no C compiler needed at install time on most platforms.

#### 10.5.3 Markdown (`.md`) — Heading Parser

**Library:** None required — regex-based heading extraction is reliable for well-formed Markdown.

**Strategy:** Parse ATX headings (`^#{1,6}\s`) and compute the line range each heading covers (from the heading to the next heading at the same or higher level, or end of file).

**Extraction depth:** All heading levels (typically 1–4). Each heading is an index entry with level, text, and line range.

**Example index for this file (`ck3_file_Refactor_Design.md`):**
```
# ck3_file Canonical Address Refactor              L1-L519
  ## 0) Context                                    L10-L25
  ## 1) Design Decision: Single Tool               L27-L62
    ### 1.1 Routing Logic                          L38-L52
    ### 1.2 No Silent Fallthrough                  L54-L62
  ## 2) dev_mode Parameter                         L64-L98
    ### 2.1 Behavior                               L70-L80
    ### 2.2 Purpose                                L82-L92
    ### 2.3 Lifecycle                              L94-L98
  ## 9) Context Budget Gate                        L255-L430
  ## 10) CST-Based File Indexing                   L433-L519
```

**Regex pattern:** `^(#{1,6})\s+(.+)$` with `re.MULTILINE`. Line ranges computed by tracking heading positions.

**Parse cost:** Negligible — regex scan on a <50KB file is sub-millisecond.

#### 10.5.4 CK3 Paradox Script (`.txt`) — Existing Parser (AST)

**Library:** `src/ck3raven/parser/` — already integrated, 100% vanilla pass rate.

**Parser type:** AST (not CST). The CK3 parser outputs `ast_json` without formatting preservation. This is acceptable because CK3 script writes use raw text through `ck3_file(command="write")` — round-trip CST editing is not needed for Paradox script. See §10.x CST vs AST Decision Table.

**Strategy:** Parse to AST, extract top-level blocks with type and line range.

**Example index for a traits file:**
```
trait = martial_prowess_1                          L15-L45
trait = martial_prowess_2                          L47-L82
scripted_trigger = is_valid_knight                 L84-L120
scripted_effect = add_knight_effect                L122-L165
```

**Parse cost:** Already optimized — the parser is fast enough for real-time use.

#### 10.5.5 Other File Types — Fallback

For files not matching any known type (`.json`, `.yml`, `.cfg`, etc.):
- Return a **line-count summary** with first/last few lines as preview
- No structural index — the agent must use `start_line`/`end_line` ranges
- The `context_limit` denial message (§9.4) provides guidance

### 10.6 CST Edit Mode

CST indexing enables a powerful edit workflow where the agent edits by naming a node rather than providing search/replace text.

#### Current Edit Workflow (ck3_file `command="edit"`)

```
1. Agent reads file (or range) to see current content
2. Agent identifies the exact text to replace (old_content)
3. Agent constructs the replacement text (new_content)
4. Agent calls ck3_file(command="edit", old_content="...", new_content="...")
5. Tool does string search/replace
```

**Problems:**
- `old_content` must be an exact match — whitespace, indentation, trailing newlines all matter
- Large functions require large `old_content` strings, consuming context budget
- Search/replace can match in wrong location if the text appears multiple places

#### CST Edit Workflow (future `command="cst_edit"`)

```
1. Agent reads index (automatic via CST indexing)
2. Agent knows the target node: "ck3_file function at L1756-L1924"
3. Agent calls:
   ck3_file(command="cst_edit",
            path="root:repo/tools/ck3lens_mcp/server.py",
            node="ck3_file",             # function name
            node_type="function",        # disambiguation
            new_content="def ck3_file(...):\n    ...")
4. Tool: parses file → finds node → replaces it → serializes back
   All formatting outside the replaced node is PERFECTLY preserved
```

**Advantages:**
- No need to match exact `old_content` — node identified by name + type
- Round-trip safe — `libcst` preserves all formatting for untouched nodes
- Unambiguous — `node_type` + `node_name` uniquely identifies the target
- Agent never needs to read the old content of the function it's replacing

**Implementation:** `libcst.CSTTransformer` subclass that visits the target node and replaces its body. For TypeScript, `tree-sitter` edit API with `node.edit()`.

**Status:** Design only. This is a significant feature beyond the initial CST indexing scope, but the index infrastructure enables it naturally. Should be a separate design phase after indexing is proven.

### 10.7 Index Format (Reply Schema)

All language-specific indexers produce the same `Reply.data` structure:

```python
{
    "index_type": "cst",              # "cst" | "heading" | "fallback"
    "file_type": "python",            # "python" | "typescript" | "markdown" | "ck3_script" | "unknown"
    "file_path": "root:repo/tools/ck3lens_mcp/server.py",
    "file_lines": 6636,
    "file_bytes": 265440,
    "threshold_bytes": 15360,
    "index_bytes": 4340,              # Size of this index
    "cached": true,                   # Whether this came from cache
    "elements": [
        {
            "name": "_verify_python_environment",
            "type": "function",        # function | class | method | interface | type_alias | import | heading | assignment | block
            "signature": "() -> None", # For callables; heading text for Markdown; None for assignments
            "lines": [34, 93],         # [start_line, end_line] (1-based, inclusive)
            "decorators": [],          # For Python/TS callables
            "children": []             # For classes: list of method elements (same schema)
        },
        {
            "name": "ReplyBuilder",
            "type": "class",
            "signature": null,
            "lines": [598, 767],
            "decorators": [],
            "children": [
                {"name": "__init__", "type": "method", "signature": "(self, tool, trace, meta)", "lines": [605, 620], "decorators": [], "children": []},
                {"name": "success", "type": "method", "signature": "(self, code, data) -> Reply", "lines": [622, 640], "decorators": [], "children": []}
            ]
        }
    ],
    "note": "File exceeds context budget (265KB > 15KB threshold). CST index returned with 62 elements. Use start_line/end_line to read specific elements."
}
```

#### Markdown Index Elements

```python
{
    "name": "## 9) Context Budget Gate",
    "type": "heading",
    "signature": null,       # Heading text is the name
    "lines": [255, 430],
    "decorators": [],
    "children": [
        {"name": "### 9.1 Problem", "type": "heading", "signature": null, "lines": [260, 275], "decorators": [], "children": []},
        {"name": "### 9.2 Design", "type": "heading", "signature": null, "lines": [277, 340], "decorators": [], "children": []}
    ]
}
```

### 10.8 Index Caching & Storage

**Strategy:** On-demand indexing with mtime-based cache invalidation (Option A from original design).

**Storage location:** `~/.ck3raven/wip/MCP_tool_replies/ck3_file/indexes/`

This follows the established spill directory convention (§10.14). CST indexes are stored alongside other `ck3_file` reply data, not in a separate cache hierarchy. The agent can access these files via `ck3_file(command="read", path="root:ck3raven_data/wip/MCP_tool_replies/ck3_file/indexes/...")` if needed.

**Cache key:** SHA-256 hash of the canonical file path (to avoid filesystem issues with special characters).

**Cache format:** JSON file containing:
```python
{
    "source_path": "root:repo/tools/ck3lens_mcp/server.py",
    "source_mtime": 1740412800.123456,   # os.stat().st_mtime
    "source_bytes": 265440,
    "index_created": "2026-02-24T14:30:00Z",
    "parser": "libcst",                  # or "tree-sitter", "regex", "ck3_parser"
    "parser_version": "1.5.0",           # semver of the parsing library
    "indexer_version": 1,                # bump when index extraction logic changes
    "elements": [...]                     # Same as Reply schema
}
```

**Invalidation:** At read time, compare `os.stat(host_path).st_mtime` against cached `source_mtime`. If different, rebuild index. If `os.stat()` fails (file deleted), evict cache entry. **Also invalidate** if `parser_version` or `indexer_version` in the cached file differs from the current runtime values — a library upgrade or indexer logic change may produce different elements for the same source.

**Cache size:** With ~50 actively-read files, each index ~5KB → total cache ~250KB. Negligible disk footprint.

**Atomic writes (concurrency safety):** All index cache writes use the tmp-file-then-rename pattern:
```python
tmp = cache_path.with_suffix(".tmp")
tmp.write_text(json.dumps(index_data, indent=2))
os.replace(str(tmp), str(cache_path))  # atomic on same filesystem
```
This prevents cache corruption from concurrent reads/writes or process termination mid-write. Under the MCP multi-thread / multi-process model, this is required — not optional. If contention is observed under high concurrency, add a lock file (`{hash}.lock`) as a secondary safeguard.

**No pre-indexing:** Mod/game files in the database already have content indexed by QBuilder. The CST index is for live filesystem reads via `ck3_file(command="read")` — infrastructure source code, design docs, WIP scripts. These change frequently and must be indexed on-demand.

### 10.9 Implementation Architecture

```
ck3_file(command="read", path="root:repo/server.py")
  │
  ├─ WA2 resolve → host_path, root_key
  │
  ├─ enforcement_v2.enforce() → finds _READS_GATED rule
  │   ├── creates ctx = {} (mutable dict, shared with condition)
  │   ├── evaluates context_limit condition:
  │   │   ├── os.stat() → 265KB > 15KB threshold
  │   │   ├── SIDE EFFECT: generates CST index → atomic write (tmp + os.replace)
  │   │   ├── SIDE EFFECT: deposits index file path + metadata into ctx["_denial_meta"]
  │   │   └── returns False
  │   │
  │   ├── condition failed → builds denial Reply (EN-GATE-D-001)
  │   │   └── merges ctx["_denial_meta"] into denial data (generic, 1 line)
  │   │
  │   └── Reply.data contains: file_bytes, threshold_bytes, estimated_total_lines,
  │       estimated_bytes, avg_line_bytes, guidance, next_call, alternatives
  │       (post-Sprint 2: + index_path, index_type, index_element_count)
  │
  ├─ ck3_file returns the denial Reply as-is (no interception)
  │
  ╰─ Agent receives denial with index file path:
     1. Reads index file: ck3_file(command="read", path="<index_path>")
        → ~4.3KB structural map (62 elements with line ranges)
     2. "ck3_file is at L1756-L1924"
        → ck3_file(command="read", path="...", start_line=1756, end_line=1924)
        → enforcement passes (narrow range < threshold) → raw content returned
```

**Key:** No interceptor in ck3_file. The condition predicate in the capability matrix does all the work — size check, index generation (with atomic file writes), metadata population — as side effects of its `check()` returning `False`. Enforcement generically includes the metadata in its denial Reply via the shared `ctx` dict.

**Module structure:**
```
tools/ck3lens_mcp/ck3lens/
  indexing/
    __init__.py          # detect_file_type(), get_index()
    cst_cache.py         # Cache read/write/invalidation
    python_indexer.py    # libcst-based Python CST extraction
    typescript_indexer.py # tree-sitter TypeScript extraction
    markdown_indexer.py  # Heading-based Markdown indexing
    ck3_indexer.py       # CK3 script indexing (wraps existing parser)
    fallback_indexer.py  # Line-count summary for unknown types
```

### 10.10 Dependency Analysis

| Package | Purpose | Size | Install |
|---------|---------|------|---------|
| `libcst` | Python CST parsing + round-trip editing | ~2MB wheel | `pip install libcst` |
| `tree-sitter` | C-based incremental parser framework | ~500KB wheel | `pip install tree-sitter` |
| `tree-sitter-typescript` | TypeScript grammar for tree-sitter | ~300KB wheel | `pip install tree-sitter-typescript` |
| (none) | Markdown heading parser | 0 — stdlib regex | Built-in |
| (none) | CK3 script parser | 0 — existing `src/ck3raven/parser/` | Already integrated |

**Total new dependencies:** ~2.8MB. All are wheels on PyPI — no C compiler needed.

**Risk:** `libcst` requires Python 3.9+. Current project uses 3.11+ — no issue.

### 10.11 Resolved Design Questions

| # | Original Question | Decision | Rationale |
|---|-------------------|----------|-----------|
| 1 | Index replaces or accompanies content? | **Replaces** — Reply contains only the index when over threshold | Prevents the agent from getting raw content AND index (double context cost) |
| 2 | Separate `command="index"` or auto-index on read? | **Auto-index on read** — `context_limit` condition generates index as side effect when denying | Agent doesn't need to know about indexing — it just calls read and gets the right thing |
| 3 | What depth is useful? | **Top-level + one into classes** — methods listed with signatures, no deeper | Full signatures are essential for agents; going deeper bloats the index without proportional value |
| 4 | libcst/tree-sitter dependency acceptable? | **Yes** — ~2.8MB total, wheels only, already needed for CST edit future | The alternative (regex-based parsing) is fragile for Python/TS and prevents round-trip editing |
| 5 | Include signatures or just names + lines? | **Include full signatures** — parameters + return types | Agents need signatures to understand what to call and what to pass; names alone aren't enough |
| 6 | CST edit: ck3_file concern or separate tool? | **ck3_file `command="cst_edit"`** — same tool, different command | Keeps file I/O unified; the CST infrastructure is already loaded for indexing |

### 10.12 Migration Checklist Additions

These items should be added to Phase B (§6):

- [ ] Add `libcst` and `tree-sitter` + `tree-sitter-typescript` to `pyproject.toml` dependencies
- [ ] Create `tools/ck3lens_mcp/ck3lens/indexing/` module with all indexers
- [ ] Wire `context_limit` condition with CST index side effect in `capability_matrix_v2.py` (§9.5)
- [ ] Create index storage directory at `~/.ck3raven/wip/MCP_tool_replies/ck3_file/indexes/`
- [ ] Add CST index tests: Python (libcst), TypeScript (tree-sitter), Markdown (regex), CK3 (parser)
- [ ] Validate index size stays under 10KB for largest files in project
- [ ] (Future) Add `command="cst_edit"` to ck3_file — separate design phase

### 10.13 CST vs AST Decision Table

> **Why this matters:** Future agents or developers encountering this codebase may attempt to build parallel CST and AST pathways to the same result. This table establishes the canonical decision: **CST is the default; AST is permitted only where CST doesn't exist or adds no value.**

#### Definitions

| Term | Meaning | Key Property |
|------|---------|-------------|
| **CST** (Concrete Syntax Tree) | Parse tree that preserves ALL source text — whitespace, comments, formatting | Round-trip safe: `serialize(parse(source)) == source` |
| **AST** (Abstract Syntax Tree) | Parse tree that discards formatting, retaining only semantic structure | NOT round-trip safe: `compile(parse(source))` produces semantically equivalent but differently formatted code |

#### Decision Table

| File Type | Parser | Tree Type | Round-Trip Edit? | Rationale |
|-----------|--------|-----------|-----------------|-----------|
| **Python** (`.py`) | `libcst` | **CST** | Yes | `libcst` is explicitly designed for round-trip editing. Enables future `cst_edit` command. stdlib `ast` is NOT used because it discards formatting. |
| **TypeScript** (`.ts`, `.js`) | `tree-sitter` | **CST** | Yes | `tree-sitter` produces a CST natively. Same parser family VS Code uses. Enables future `cst_edit` for TS. |
| **Markdown** (`.md`) | Regex | Neither | N/A | Heading extraction via `^#{1,6}\s` regex. No tree is built — just line ranges. Round-trip editing of Markdown is done via text replacement (existing `ck3_file edit`). |
| **CK3 Script** (`.txt`) | `ck3raven/parser` | **AST** | No | The existing CK3 parser outputs `ast_json` without formatting preservation. This is the ONLY case where AST is used. Round-trip editing of CK3 script is done via text replacement (existing `ck3_file edit`), not through the AST. |
| **Other** | None | Neither | N/A | Line-count fallback summary. No parsing occurs. |

#### Why NOT Convert CK3 Parser to CST?

The CK3 parser is already built, battle-tested (100% vanilla pass rate), and serves its purpose: **indexing** (extracting top-level block names + line ranges). The parser is not used for editing — all CK3 script edits go through `ck3_file(command="edit")` as text replacement. Building a CST-preserving CK3 parser would be significant work with no practical benefit, since:

1. CK3 script formatting conventions are simple (consistent indentation, no complex nesting)
2. Agents already edit CK3 script via text replacement successfully
3. The index (names + line ranges) is identical whether extracted from AST or CST

**Rule:** Do NOT build a CST parser for CK3 script. The AST parser is sufficient for indexing, and text replacement is sufficient for editing.

### 10.14 Reply System Integration — Spill Architecture

> **Added:** February 24, 2026
> **Implementation status:** Spill system is IMPLEMENTED in `safety.py`. This section documents the architecture and its interaction with ck3_file.

#### 10.14.1 Global Reply Size Management

MCP tool replies flow back to the agent's context window. Large replies permanently consume context budget, reducing the agent's ability to reason about subsequent tasks. The spill system manages this at the `mcp_safe_tool` wrapper level in `safety.py`.

**Architecture:**

```
Tool handler returns Reply
  │
  ├─ mcp_safe_tool serializes to JSON → measures byte size
  │
  ├─ Check: tool in _SPILL_EXEMPT? → YES → return as-is (ck3_file)
  │
  ├─ Check: per-tool cap exists? → YES → use per-tool threshold
  │  └── NO → use global threshold (10KB)
  │
  ├─ result_bytes > threshold?
  │   ├── YES → write full result to spill file → return pointer + summary
  │   └── NO  → return full result in Reply
  │
  └── Log: result_bytes + spilled flag in tool_end event
```

#### 10.14.2 Thresholds and Caps

| Scope | Threshold | Behavior When Exceeded |
|-------|-----------|----------------------|
| **Global default** | 10KB | Reply written to `~/.ck3raven/wip/MCP_tool_replies/{tool_name}/{trace_id}.json`. Reply.data replaced with summary + file path. |
| **`debug_get_logs`** | 2KB | Per-tool cap. Full log output written to spill file, Reply contains truncated summary + path. |
| **`ck3_search`** | 2KB | Per-tool cap. Full search results written to spill file, Reply contains clustered summary + path. |
| **`ck3_logs`** | 2KB | Per-tool cap. Same pattern as debug_get_logs. |
| **`ck3_file`** | EXEMPT | Not subject to global spill. Instead, ck3_file uses its own context management: CST indexing (§10.1-10.9) + enforcement gate (§9). |

#### 10.14.3 Spill Directory Structure

```
~/.ck3raven/wip/MCP_tool_replies/
  ├── debug_get_logs/
  │   └── {trace_id}.json          # Full log results
  ├── ck3_search/
  │   └── {trace_id}.json          # Full search results
  ├── ck3_logs/
  │   └── {trace_id}.json          # Full log results
  ├── ck3_file/
  │   └── indexes/
  │       └── {sha256_of_path}.json # CST index cache
  └── {other_tool}/
      └── {trace_id}.json          # Any tool exceeding global 10KB cap
```

#### 10.14.4 Spill vs Denial — Critical Distinction

These are two different systems solving different problems:

| Mechanism | Layer | What It Does | Agent Experience |
|-----------|-------|-------------|-----------------|
| **Spill** (`safety.py`) | Tool wrapper | Diverts large Reply to file, returns summary + pointer | Agent gets a useful summary and can read the full result from the file if needed. The tool call SUCCEEDED. |
| **Denial** (`enforcement_v2.py`) | Enforcement | Blocks the operation entirely | Agent is told the operation is not allowed. Must find an alternative approach. The tool call was DENIED. |

**ck3_file >15KB** is a DENIAL (enforcement layer) — the agent receives the denial Reply with the CST index file path in `Reply.data` as a constructive next step. The index is generated by the `context_limit` condition's side effects (§9.5) and written to a file. No interception by ck3_file occurs — the denial Reply flows through as-is.

**ck3_search >2KB** is a SPILL (tool wrapper layer) — the agent gets a summary and a file pointer. The search SUCCEEDED; we just didn't put the full result in the context window.

#### 10.14.5 ck3_file Behavior: CST Always Produced

When `ck3_file(command="read")` is called on a parseable file:

| File Size | Reply Contains | CST Index Written To | Full CST Written To |
|-----------|---------------|---------------------|-------------------|
| ≤15KB | Raw file content + `cst_index_path` pointer | `MCP_tool_replies/ck3_file/indexes/{hash}.json` | N/A (file content IS the "CST source") |
| >15KB, no line range | DENIAL with CST index file path in `Reply.data` | `MCP_tool_replies/ck3_file/indexes/{hash}.json` | N/A (file too large; agent reads index file to narrow) |
| >15KB, narrow range (≤15KB estimated) | Raw content of requested range | Already cached from prior index call | N/A |

**Key behavior:** The CST index is ALWAYS produced and cached when ck3_file reads a parseable file, regardless of size. For small files, the raw content is returned in the Reply AND the index is cached for future reference. For large files, only the index is returned. The agent always has the structural map available.

---

## 11) ck3_file v2 Migration — Implementation Plan

> **Added:** February 24, 2026
> **Status:** Plan only — no implementation started
> **Prerequisite:** ck3_exec Tier 1 gaps must be resolved first
> **Estimated effort:** 8-12 sub-agent work sessions

### 11.1 Migration Strategy

The migration follows the **parallel pipeline** pattern established in §1-§3:

1. Add `dev_mode` flag to `ck3_file` — when `True`, forces all paths through v2 pipeline
2. Internal routing detects canonical (`root:`/`mod:`) vs legacy paths
3. v1 pipeline remains DEFAULT for non-canonical paths throughout migration
4. v2 pipeline handles canonical paths with full EN v2 enforcement
5. Once v2 is proven stable, remove v1 pipeline entirely

**Core principle:** At every stage of migration, `ck3_file` works correctly for existing agents using v1 paths. The v2 pipeline is additive — it never disrupts v1 behavior until the explicit cutover.

### 11.2 Validation Philosophy

Every migration phase ends with a **validation gate** — a set of concrete tests/checks that must pass before proceeding. Gates are designed so that failure is safe (v1 still works) and informative (tells you exactly what broke).

**Gate types:**
- **Unit gate:** Automated tests that run in CI or via `ck3_exec`
- **Integration gate:** End-to-end tool calls via MCP that exercise the full pipeline
- **Observability gate:** Log/trace analysis confirming correct behavior patterns
- **Manual gate:** Human inspects specific outputs before proceeding

### 11.3 Sub-Agent Strategy

The migration involves heavy context: reading multiple large files (`server.py` 6636L, `unified_tools.py` 2775L, `capability_matrix_v2.py` 722L, `enforcement_v2.py` 132L). A single agent session cannot hold all of this in context simultaneously.

**Approach:** Break the migration into focused sub-tasks, each executable by a context-limited sub-agent with a specific brief:

| Sub-Agent | Focus | Key Files | Context Budget |
|-----------|-------|-----------|---------------|
| SA-1 | `context_limit` condition factory | `capability_matrix_v2.py`, `enforcement_v2.py` | ~30KB (2 files + brief) |
| SA-2 | OPERATIONS_MATRIX rewiring | `capability_matrix_v2.py` only | ~25KB (1 file + brief) |
| SA-3 | ck3_file dispatcher v2 routing | `unified_tools.py` (ck3_file_impl section only) | ~25KB (partial file + brief) |
| SA-4 | CST indexing module | New files only (`indexing/*.py`) | <10KB (new code + brief) |
| SA-5 | Test battery | `tests/` + tool contracts | ~20KB (test specs + brief) |
| SA-6 | Reply system adherence audit | `safety.py`, `unified_tools.py` | ~30KB (2 files + brief) |
| SA-7 | Logging system adherence audit | `safety.py`, `CANONICAL_LOGS.md` | ~25KB (2 files + brief) |
| SA-8 | Integration testing + cutover | `server.py` (ck3_file registration) | ~25KB (partial file + brief) |

Each sub-agent receives:
- A focused brief (what to do, what files to touch, what NOT to touch)
- The relevant section of this design document
- A validation gate to satisfy before marking complete
- Instructions to commit with a descriptive message referencing this plan

### 11.4 Phase 0: Prerequisites (Before Migration Starts)

#### 0.1 ck3_exec Tier 1 Completion — VERIFIED COMPLETE
- ck3_exec script path WA2 resolution (§1.2 of ck3_exec design) — **DONE** (commit `27013af`)
- ck3_exec return type normalization (§1.5) — **DONE** (commit `27013af`)
- Validation testing: 12/12 whitelist, E2E signing, 4 negative tests — **ALL PASSED**
- These are merged and stable — ck3_file migration can proceed

#### 0.2 Migration Test Battery Design
Write test specifications covering:
- All existing ck3_file commands: `read`, `write`, `edit`, `delete`, `rename`, `list`, `get`, `refresh`, `create_patch`
- v1 path formats: `mod_name` + `rel_path`, host-absolute, `wip:/`, `vanilla` pseudo-mod
- v2 path formats: `root:repo/`, `root:game/`, `root:ck3raven_data/wip/`, `mod:Name/`
- Edge cases: missing files, permission denied, binary files, very large files
- Enforcement: reads under/over 15KB, writes to allowed/forbidden locations

#### 0.3 Baseline Metrics
Record current ck3_file performance:
- Average tool call latency (from trace logs)
- Success/error/denial ratio (from tool_end events)
- Context budget consumption patterns (from result_bytes in logs)

**Validation Gate 0:** Test battery spec reviewed and approved. ck3_exec Tier 1 merged. Baseline metrics recorded.

### 11.5 Phase 1: Foundation — `context_limit` Condition + OPERATIONS_MATRIX (SA-1, SA-2)

#### 1.1 Add `context_limit` Condition Factory (SA-1)

**Target file:** `tools/ck3lens_mcp/ck3lens/capability_matrix_v2.py`

Add the `context_limit()` condition factory as designed in §9.2:
- `os.stat()` pre-read gate
- Line count estimation heuristic (`file_bytes / 45`)
- Threshold parameter (default 15KB)
- Context keys: `host_abs`, `start_line`, `end_line`

**Target file:** `tools/ck3lens_mcp/ck3lens/policy/enforcement_v2.py`

Add generic `ctx` dict + `_denial_meta` merge hook (§9.5):
- Create `ctx = {}` before evaluating conditions
- Pass `ctx=ctx` to condition `check()` calls
- After building denial data, `if "_denial_meta" in ctx: denial_data.update(ctx["_denial_meta"])`
- NO condition-specific logic — enforcement remains generic

#### 1.2 Split FILE_READ_COMMANDS + Rewire OPERATIONS_MATRIX (SA-2)

**Target file:** `tools/ck3lens_mcp/ck3lens/capability_matrix_v2.py`

- Split `FILE_READ_COMMANDS` into `FILE_READ_UNGATED` and `FILE_READ_GATED` (§9.3)
- Create `_READS_UNGATED` and `_READS_GATED` OperationRule instances
- Update every OPERATIONS_MATRIX entry that currently uses `_READS`
- Verify: all entries for both `ck3lens` and `ck3raven-dev` modes updated

**Validation Gate 1:**
- [ ] Unit tests: `context_limit` condition returns True for <15KB files
- [ ] Unit tests: `context_limit` condition returns False for >15KB files with no line range
- [ ] Unit tests: `context_limit` condition returns True for >15KB files with narrow line range
- [ ] Unit tests: enforcement denial includes `_denial_meta` fields (alternatives, file_bytes, index_path, next_call)
- [ ] Unit tests: enforcement `ctx` dict correctly propagates `_denial_meta` from condition to denial Reply
- [ ] Unit tests: `FILE_READ_UNGATED` commands (`get`, `list`, `refresh`) are NOT gated
- [ ] Unit tests: `FILE_READ_GATED` commands (`read`) ARE gated
- [ ] Integration: `ck3_file(command="read")` on safety.py (~31KB) with no line range → denied
- [ ] Integration: `ck3_file(command="read")` on safety.py with `start_line=1, end_line=50` → succeeds
- [ ] Observability: tool_end logs show `context_limit` in denial reason for gated reads
- [ ] No regression: existing v1 ck3_file calls unaffected (v2 enforcement only fires on v2-routed paths)

### 11.6 Phase 2: CST Indexing Module (SA-4)

#### 2.1 Build Indexing Infrastructure

**New directory:** `tools/ck3lens_mcp/ck3lens/indexing/`

Create all indexer modules:
- `__init__.py` — `detect_file_type()`, `get_index()` dispatcher
- `cst_cache.py` — read/write/invalidate index cache in `MCP_tool_replies/ck3_file/indexes/`
- `python_indexer.py` — `libcst.parse_module()` → extract top-level + class methods
- `typescript_indexer.py` — `tree-sitter` parse → extract exports, classes, functions
- `markdown_indexer.py` — regex heading extraction with line ranges
- `ck3_indexer.py` — wraps existing CK3 parser for top-level block extraction
- `fallback_indexer.py` — line-count summary for unknown types

#### 2.2 Add Dependencies

Add to `pyproject.toml`:
- `libcst>=1.0`
- `tree-sitter>=0.21`
- `tree-sitter-typescript>=0.21`

**Validation Gate 2:**
- [ ] Unit tests: Python indexer produces correct elements for safety.py (11 functions + 1 class)
- [ ] Unit tests: TypeScript indexer produces correct elements for extension.ts (5 top-level exports)
- [ ] Unit tests: Markdown indexer produces correct heading structure for this design doc
- [ ] Unit tests: CK3 indexer produces correct top-level blocks for a sample traits file
- [ ] Unit tests: Fallback indexer produces line-count summary for unknown file type
- [ ] Unit tests: Cache hit returns cached result when mtime matches
- [ ] Unit tests: Cache miss rebuilds index when mtime differs
- [ ] Unit tests: Index size < 10KB for server.py (6636 lines, 62 functions)
- [ ] Index schema matches §10.7 exactly (all fields present, correct types)

### 11.7 Phase 3: v2 Pipeline in ck3_file (SA-3)

#### 3.1 Add `dev_mode` Parameter + Path Routing

**Target file:** `tools/ck3lens_mcp/ck3lens/unified_tools.py` (function `ck3_file_impl`)

Add:
- `dev_mode: bool = False` parameter
- `path: str | None = None` parameter (if not already present)
- Routing logic (§1.1): canonical prefix → v2, host-absolute → v1, mod_name → v1
- When `dev_mode=True`: ALL paths forced through v2, non-canonical → error

**Also update:** `tools/ck3lens_mcp/server.py` (function `ck3_file`) to pass `dev_mode` through

#### 3.2 v2 Read Path with CST Integration

For v2-routed reads:
1. WA2 `resolve()` → host path, root_key, subdirectory
2. `enforcement_v2.enforce()` → evaluate `context_limit` (condition side effects generate CST index if denied)
3. If denied by `context_limit` → return denial Reply as-is (contains index_path in data via `_denial_meta`)
4. If denied by other condition → return denial as-is
5. If allowed → read file → return content
6. For ALL parseable files: produce and cache CST index (even for small files)
7. Small file Reply includes `cst_index_path` pointer to cached index

#### 3.3 v2 Write/Edit/Delete Paths

For v2-routed writes:
1. WA2 `resolve()` → host path, root_key, subdirectory
2. `enforcement_v2.enforce()` → evaluate all conditions (mod_in_session, has_contract, etc.)
3. If denied → return denial
4. If allowed → execute write → leak scan → return success
5. Reply contains canonical paths only (no host paths)

#### 3.4 Reply System Adherence

All v2 path replies must follow established patterns:
- `Reply.data` matches documented schemas (§10.7 for CST reads)
- `result_bytes` tracked by `mcp_safe_tool` wrapper (already implemented)
- `spilled` flag set when applicable (already implemented)
- `context_warning` retained for v1 path (10-15KB nudge)
- v2 path uses enforcement denial + CST index file path instead of context_warning
- `cst_index_path` field added to successful read replies

#### 3.5 Logging System Adherence

All v2 path operations must produce canonical log events:
- `tool_start` event with v2-specific fields (canonical path, root_key)
- `tool_end` event with `result_bytes`, `spilled`, `enforcement_result`
- `enforcement_deny` event when `context_limit` or other condition triggers
- `cst_index_generated` event when a new index is built (DEBUG level)
- `cst_index_cache_hit` event when cached index is returned (DEBUG level)
- Log events conform to `CANONICAL_LOGS.md` field specifications

**Validation Gate 3:**
- [ ] Integration: `ck3_file(command="read", path="root:repo/docs/CANONICAL_ARCHITECTURE.md")` returns content (small file, v2 path)
- [ ] Integration: `ck3_file(command="read", path="root:repo/tools/ck3lens_mcp/server.py")` returns CST index (large file, v2 path, denied by context_limit)
- [ ] Integration: `ck3_file(command="read", path="root:repo/tools/ck3lens_mcp/server.py", start_line=1756, end_line=1924)` returns raw content (narrow range, passes context_limit)
- [ ] Integration: `ck3_file(command="read", mod_name="My Mod", rel_path="common/traits/test.txt")` still works (v1 path, unaffected)
- [ ] Integration: `ck3_file(command="write", path="root:ck3raven_data/wip/test.py", content="...")` succeeds via v2
- [ ] Integration: `ck3_file(command="write", path="root:game/common/traits/test.txt", content="...")` denied (vanilla is read-only)
- [ ] Observability: tool_end logs include `result_bytes` for v2 reads
- [ ] Observability: enforcement_deny events logged for context_limit denials
- [ ] Observability: CST index file exists in `MCP_tool_replies/ck3_file/indexes/` after v2 read
- [ ] dev_mode=True: `ck3_file(command="read", mod_name="My Mod", rel_path="...")` → error (non-canonical not allowed in dev_mode)
- [ ] Reply schema: CST index reply matches §10.7 exactly
- [ ] Reply schema: Successful read reply includes `cst_index_path` field

### 11.8 Phase 4: Reply + Logging Audit (SA-6, SA-7)

#### 4.1 Reply System Audit (SA-6)

Systematic review of all ck3_file reply paths ensuring:

| Check | v1 Path | v2 Path |
|-------|---------|---------|
| `Reply.reply_type` is correct (`S`, `D`, `I`, `E`) | ✓ existing | ✓ verify |
| `Reply.code` follows naming convention | ✓ existing | ✓ verify |
| `Reply.data` contains no host-absolute paths | Not enforced | ✓ leak scan |
| `result_bytes` logged in tool_end | ✓ (safety.py) | ✓ (same wrapper) |
| Spill handled for appropriate tools | ✓ ck3_file exempt | ✓ ck3_file exempt |
| `context_warning` present for 10-15KB reads | ✓ existing | N/A (replaced by enforcement + CST index file) |

#### 4.2 Logging System Audit (SA-7)

Review all log events against `CANONICAL_LOGS.md`:
- Verify field names match documented schema
- Verify log levels match documented levels
- Verify `trace_id` and `session_id` present in all events
- Verify `result_bytes` and `spilled` in all tool_end events
- Add any missing events for v2-specific operations

**Validation Gate 4:**
- [ ] Audit report: all v2 Reply fields documented and correct
- [ ] Audit report: no host paths in v2 success replies
- [ ] Audit report: all log events match CANONICAL_LOGS.md schema
- [ ] Audit report: result_bytes present in 100% of tool_end events

### 11.9 Phase 5: Integration Testing + `dev_mode` Validation (SA-5, SA-8)

#### 5.1 Full Test Battery with `dev_mode=False` (SA-5)

Run the complete migration test battery (designed in Phase 0):
- All ck3_file commands via v1 paths → must work exactly as before
- Canonical path commands via v2 paths → must work correctly
- Mixed scenarios (some v1, some v2 in same session) → no interference

#### 5.2 Full Test Battery with `dev_mode=True` (SA-8)

Force all paths through v2:
- v1-format paths → error (non-canonical rejected in dev_mode)
- Canonical paths → full v2 pipeline
- Document any gaps or failures

#### 5.3 Stress Testing

- Read 20+ different files in rapid succession → no cache corruption
- Concurrent reads on same file → no race conditions in CST cache
- Large CST index (if a file has 100+ functions) → still under 10KB

**Validation Gate 5:**
- [ ] Test battery: 100% pass rate with `dev_mode=False`
- [ ] Test battery: 100% pass rate with `dev_mode=True` for v2 paths
- [ ] Test battery: v1 paths correctly rejected in `dev_mode=True`
- [ ] Stress test: no cache corruption under concurrent access
- [ ] Stress test: CST indexes consistently under 10KB

### 11.10 Phase 6: Agent Instructions + Transition (Manual)

#### 6.1 Update Agent Instructions

- Update `copilot-instructions.md` with canonical path examples for ck3_file
- Update `ck3_get_mode_instructions` return text to prefer canonical paths
- Update `ck3_get_agent_briefing` to explain CST indexing behavior

#### 6.2 Monitor Transition

- Track v1 vs v2 path usage in logs
- Identify agents still using v1 paths
- When v1 usage approaches zero → proceed to cutover

**Validation Gate 6:**
- [ ] Agent instructions updated and reviewed
- [ ] v1 path usage tracked in logs
- [ ] Manual review: agents successfully using canonical paths

### 11.11 Phase 7: Cutover — Remove v1 Pipeline

#### 7.1 v1 Removal

- Remove v1 routing branch from `ck3_file_impl`
- Remove `dev_mode` parameter (v2 is now default and only)
- Remove `context_warning` soft nudge (replaced by enforcement gate + CST)
- Remove v1 enforcement calls from ck3_file
- Remove `mod_name` pseudo-mod routing (`wip`, `vanilla`)

#### 7.2 Cleanup

- Remove dead code from `unified_tools.py`
- Update all tests to use canonical paths only
- Update documentation to remove v1 references

**Validation Gate 7:**
- [ ] Test battery: 100% pass rate (v2 only)
- [ ] No v1 code remains in ck3_file path
- [ ] `dev_mode` parameter removed
- [ ] Documentation updated

### 11.12 Risk Mitigation

| Risk | Mitigation | Rollback |
|------|-----------|----------|
| v2 resolver bug breaks canonical paths | v1 pipeline remains default; v2 only active for `root:`/`mod:` or `dev_mode=True` | Remove v2 routing branch; instant rollback |
| CST indexer crashes on edge-case file | Try/except wrapper → fallback to text line-count summary | Disable CST indexing flag; return enforcement denial |
| `libcst` dependency conflicts | Pin version in pyproject.toml; test in CI | Remove dependency; use regex-based Python indexing (degraded) |
| `context_limit` gate too aggressive | Threshold is configurable (default 15KB, can raise) | Set threshold to 1MB (effectively disabling gate) |
| Agent confusion during transition | v1 paths always work until explicit cutover | Re-enable v1 default if agents struggle |
| Cache file corruption | mtime-based invalidation rebuilds on any mismatch | Delete entire indexes/ directory; caches rebuilt on demand |

### 11.13 Sprint Mapping (Summary)

| Sprint | Phases | Sub-Agents | Key Deliverable |
|--------|--------|------------|----------------|
| Sprint 1 | Phase 0 + Phase 1 | SA-1, SA-2 | `context_limit` condition wired, `ctx` dict in enforcement, OPERATIONS_MATRIX split |
| Sprint 2 | Phase 2 | SA-4 | All indexers built and unit-tested |
| Sprint 3 | Phase 3 | SA-3 | v2 pipeline wired in ck3_file with CST integration |
| Sprint 4 | Phase 4 + Phase 5 | SA-5, SA-6, SA-7, SA-8 | Full test battery passing, audits complete |
| Sprint 5 | Phase 6 + Phase 7 | Manual + SA-8 | Agent transition + v1 removal |

**Detail:** See **§12 Detailed Sprint Plan** for per-task breakdowns, sub-agent briefs, and precise file references.

### 11.14 What This Plan Does NOT Cover

- `ck3_git` migration to v2 (separate plan, can use same infrastructure)
- `ck3_folder` migration to v2 (separate plan)
- `command="cst_edit"` implementation (separate design phase, dependent on indexing being proven)
- Build daemon / QBuilder integration with CST indexes (mod files already indexed in SQLite)
- v2 migration for ANY tool other than ck3_file

---

## 12) Detailed Sprint Plan

> **Date:** February 24, 2026
> **Purpose:** Actionable sprint-level breakdown of §11, with sub-agent briefs for tasks that benefit from focused context and/or Python CSTs for efficient editing.
>
> **Sub-agent convention:** Tasks marked `[SA]` are candidates for sub-agent delegation. Each `[SA]` brief includes:
> - **Context files** — the files the sub-agent needs loaded (nothing else)
> - **Deliverable** — exactly what the sub-agent produces
> - **Acceptance criteria** — how to know it's done
>
> Tasks marked `[MAIN]` are done by the orchestrating agent directly.

---

### 12.1 Sprint 1 — Enforcement Infrastructure + OPERATIONS_MATRIX Split

**Goal:** Wire `context_limit` condition into enforcement, add `ctx` dict plumbing, split read commands in the matrix. No changes to `ck3_file` itself — purely infrastructure.

#### Task 1.1 `[SA]` — Add `context_limit()` condition factory

**Target:** `tools/ck3lens_mcp/ck3lens/capability_matrix_v2.py`

Add a new condition factory function after the existing factories (L38-170):

```python
AVG_LINE_BYTES = 45  # D2 — single named constant

def context_limit(threshold_bytes: int = 15 * 1024) -> Condition:
    """True if file is under threshold OR a narrow line range is requested.
    
    Sprint 1 behavior (D4): On failure, deposits denial metadata into
    ctx["_denial_meta"] with guidance, next_call template, and size info.
    Does NOT call any indexer or write any index file.
    
    Context keys:
        host_abs: Path | None — resolved host path (from WA2)
        start_line: int | None — requested start line
        end_line: int | None — requested end line
        canonical_path: str | None — canonical address for next_call template
        ctx: dict — mutable dict; condition deposits _denial_meta here
    """
```

Implementation requirements (from D2, D4, §9.2, §9.5.3):
- `os.stat(host_abs).st_size` for byte check — no file read
- **Unified estimation (D2):** `estimated_total_lines = max(1, file_bytes // AVG_LINE_BYTES)`
- If `start_line` and `end_line` provided: `requested_lines = max(1, end_line - start_line + 1)`, `estimated_bytes = int(file_bytes * (requested_lines / estimated_total_lines))` — **proportional formula only, no alternative formulas**
- Else: `estimated_bytes = file_bytes`
- Allow if `estimated_bytes <= threshold_bytes`
- **On failure (Sprint 1 — D4):** Do NOT call any indexer. Do NOT write any index file. Build denial metadata and deposit into `ctx["_denial_meta"]`
- `_denial_meta` schema (Sprint 1):

```json
{
  "file_bytes": 265000,
  "threshold_bytes": 15360,
  "estimated_total_lines": 5889,
  "estimated_bytes": 265000,
  "avg_line_bytes": 45,
  "guidance": "File exceeds context budget (265000B > 15360B). Use start_line/end_line to read a specific range.",
  "next_call": {
    "tool": "ck3_file",
    "command": "read",
    "path": "<same canonical path>",
    "start_line": "<fill>",
    "end_line": "<fill>"
  },
  "alternatives": [
    "Use start_line/end_line to read a narrower range",
    "Use ck3_search to find specific symbols or content",
    "Spawn a sub-agent with a focused task"
  ]
}
```

- **Path exposure (D3):** `next_call.path` must be the canonical session-absolute address, never a host path. Use the `canonical_path` context key.
- **No escape hatch (D1):** No allowlists, no `ack_context_cost`, no overrides

**Context files:** `capability_matrix_v2.py` (full), design doc §9.0 (D1-D5), §9.2, §9.5.3
**Deliverable:** `context_limit()` factory function added, returns `Condition` instance
**Acceptance:** Unit test — file <15KB → `True`; file >15KB no range → `False` + `ctx["_denial_meta"]` populated with all D5 fields; file >15KB with narrow range → `True`; no host paths in any denial data field (D3 CI gate)

---

#### Task 1.2 `[SA]` — Add `ctx` dict plumbing to `enforcement_v2.enforce()`

**Target:** `tools/ck3lens_mcp/ck3lens/policy/enforcement_v2.py`

Current state (L24-132): `enforce()` uses `**context` kwargs and calls `c.check(**context)`. This must change to support the mutable `ctx` dict pattern.

Changes:
1. Create `ctx: dict = {}` at the top of `enforce()`, before condition evaluation
2. Add `ctx` to the kwargs passed to `c.check()`: `c.check(ctx=ctx, **context)`
3. After building denial data dict, merge: `if "_denial_meta" in ctx: denial_data.update(ctx["_denial_meta"])`
4. This is generic — enforcement knows nothing about `context_limit` specifically

**The change is small but architecturally critical.** The `ctx` dict is the communication channel from condition side effects to denial Reply.data.

Precise edit (around L105-115):

```python
# BEFORE:
for c in rule.conditions:
    passed = c.check(**context)
    ...

# AFTER:
ctx: dict = {}
for c in rule.conditions:
    passed = c.check(ctx=ctx, **context)
    ...
```

And around L118-125 (denial path):

```python
# BEFORE:
return rb.denied("EN-GATE-D-001", {
    "detail": f"Conditions not met: {failed}",
    "failed_conditions": failed,
    "tool": tool,
    "command": command,
})

# AFTER:
denial_data: dict = {
    "detail": f"Conditions not met: {failed}",
    "failed_conditions": failed,
    "tool": tool,
    "command": command,
}
if "_denial_meta" in ctx:
    denial_data.update(ctx["_denial_meta"])
return rb.denied("EN-GATE-D-001", denial_data)
```

**Context files:** `enforcement_v2.py` (full, 132 lines)
**Deliverable:** `ctx` dict created, passed to conditions, merged into denial
**Acceptance:** Existing tests still pass (ctx is empty when no condition deposits into it); new test — mock condition that deposits `_denial_meta` → denial Reply.data contains deposited fields

> **Note:** Callers of `enforce()` (e.g., `ck3_file` dispatcher in §9.7) must include `canonical_path=...` in the context kwargs so that the `context_limit` condition can use it in `next_call` templates (per D3 — no host paths in agent-facing replies).

---

#### Task 1.3 `[SA]` — Split `FILE_READ_COMMANDS` + update OPERATIONS_MATRIX

**Target:** `tools/ck3lens_mcp/ck3lens/capability_matrix_v2.py`

Current state (L462-525): `FILE_READ_COMMANDS` is one frozenset containing `read`, `get`, `list`, `refresh`. All are included in `ALL_READ_COMMANDS` → `_READS` (no conditions).

Changes:
1. Split into two sets (§9.3):

```python
FILE_READ_UNGATED: frozenset[tuple[str, str]] = frozenset({
    ("ck3_file", "get"),
    ("ck3_file", "list"),
    ("ck3_file", "refresh"),
})

FILE_READ_GATED: frozenset[tuple[str, str]] = frozenset({
    ("ck3_file", "read"),
})
```

2. Create `ALL_READ_UNGATED` excluding `read`, keep `ALL_READ_COMMANDS` as union for backwards compat
3. Create new rule instances:

```python
_CONTEXT_LIMIT = context_limit()  # or context_limit(threshold=15360)

_READS_UNGATED = OperationRule(ALL_READ_UNGATED)
_READS_GATED = OperationRule(FILE_READ_GATED, conditions=[_CONTEXT_LIMIT])
```

4. Replace every `_READS` in `OPERATIONS_MATRIX` (18 entries across L529-620) with `(_READS_UNGATED, _READS_GATED)` — this means each matrix entry that currently has `(_READS,)` becomes `(_READS_UNGATED, _READS_GATED, ...existing_mutation_rules...)`

**This is a mechanical replacement across 18 matrix entries** — ideal for a sub-agent with Python CST tooling (libcst or ast rewrite) to avoid line-counting errors.

**Context files:** `capability_matrix_v2.py` (full, 722 lines)
**Deliverable:** Split frozensets, new rules, all 18 matrix entries updated
**Acceptance:** `find_operation_rule("ck3lens", "game", None, "ck3_file", "read")` returns rule with `context_limit` condition; `find_operation_rule("ck3lens", "game", None, "ck3_file", "list")` returns rule with no conditions

---

#### Task 1.4 `[MAIN]` — Sprint 1 integration testing + commit

Run validation gate from §11.5:
- Unit tests for `context_limit` condition (pass/fail/narrow-range)
- Unit tests for `ctx` dict propagation in enforcement
- Unit tests for OPERATIONS_MATRIX split (gated vs ungated lookups)
- Integration: `enforce()` with `ck3_file` + `read` at `("ck3lens", "game", None)` → conditions include `context_limit`
- **D5 field completeness:** Denial `Reply.data` must contain ALL required fields from D5 table: `detail`, `failed_conditions`, `file_bytes`, `threshold_bytes`, `estimated_bytes`, `estimated_total_lines`, `avg_line_bytes`, `guidance`, `next_call`, `alternatives`
- **D3 CI gate:** No host-path signatures (`C:\`, `C:/Users`, `/home/`, `\\`) in any string value in denial `Reply.data` (see §9.5.7 test)
- **D4 Sprint 1:** Denial data must NOT contain `index_path`, `index_type`, or `index_element_count`
- **D1 no escape hatch:** No test that exercises a bypass mechanism (there is none to test)
- No regression: existing `ck3_exec` enforcement unaffected
- Commit with message: `sprint-1: context_limit condition + ctx dict + matrix split`

---

### 12.2 Sprint 2 — CST Indexing Module

**Goal:** Build all indexers and the cache layer. Self-contained module with no dependencies on enforcement or ck3_file.

#### Task 2.1 `[SA]` — Indexer infrastructure + Python indexer

**New directory:** `tools/ck3lens_mcp/ck3lens/indexing/`

Create:

1. **`__init__.py`** — `detect_file_type(path: Path) -> str` (returns `"python"`, `"typescript"`, `"markdown"`, `"ck3"`, `"unknown"`), `get_index(path: Path) -> dict | None` dispatcher that routes to correct indexer

2. **`python_indexer.py`** — Uses `libcst.parse_module()` to extract:
   - Module-level functions: name, line range, parameter count
   - Classes: name, line range, list of methods with their line ranges
   - Output schema matches §10.7:
   ```json
   {
     "file_path": "root:repo/tools/ck3lens_mcp/server.py",
     "file_bytes": 215040,
     "estimated_total_lines": 4778,
     "estimate_method": "byte_heuristic",
     "parser": "libcst",
     "parser_version": "1.5.1",
     "indexer_version": "1.0.0",
     "elements": [
       {"kind": "function", "name": "ck3_file", "line_start": 1756, "line_end": 1924, "depth": 0},
       {"kind": "class", "name": "WorldAdapterV2", "line_start": 140, "line_end": 480, "depth": 0,
        "children": [
          {"kind": "method", "name": "resolve", "line_start": 206, "line_end": 298, "depth": 1}
        ]}
     ]
   }
   ```

**Context files:** design doc §10.5-§10.7, `libcst` API docs (external)
**Deliverable:** `python_indexer.py` producing correct index for arbitrary Python files
**Acceptance:** Index of `safety.py` (768 lines, ~31KB) contains all top-level functions, `SafeToolWrapper` class with methods, all line ranges correct. Index is <5KB JSON.

---

#### Task 2.2 `[SA]` — Markdown, CK3, TypeScript, and fallback indexers

Create:

1. **`markdown_indexer.py`** — Regex heading extraction (`^#{1,6} `), produces elements with heading level as depth. No external dependency.

2. **`ck3_indexer.py`** — Wraps existing CK3 parser (`src/ck3raven/parser/`) to extract top-level blocks. Elements: `{"kind": "block", "name": "trait_name", "line_start": N, "line_end": M}`. Falls back to `fallback_indexer` if parser fails.

3. **`typescript_indexer.py`** — Uses `tree-sitter` + `tree-sitter-typescript` to extract exports, classes, functions. Elements follow same schema as Python indexer.

4. **`fallback_indexer.py`** — For unknown file types. Returns: `{"kind": "file_summary", "estimated_total_lines": N, "line_ranges": [{"label": "first_third", "start": 1, "end": N//3}, ...]}`. No parsing, just line count segmentation.

**Context files:** design doc §10.5, CK3 parser API (`src/ck3raven/parser/__init__.py`)
**Deliverable:** All 4 indexers producing correct output
**Acceptance:** Markdown indexer on this design doc → correct heading structure. CK3 indexer on a sample traits file → correct block extraction. Fallback on binary file → line-count summary.

---

#### Task 2.3 `[SA]` — CST cache layer

**Target:** `tools/ck3lens_mcp/ck3lens/indexing/cst_cache.py`

Implements:
- **`get_cached_index(host_path: Path) -> dict | None`** — returns cached index if mtime matches, else `None`
- **`write_index(host_path: Path, index: dict) -> Path`** — writes index to `~/.ck3raven/wip/MCP_tool_replies/ck3_file/indexes/<hash>.json` using atomic `tmp + os.replace`
- **`invalidate(host_path: Path) -> None`** — removes cached index
- Cache key: SHA256 of canonical path → deterministic filename
- Cache metadata includes `parser`, `parser_version`, `indexer_version`, `mtime` for invalidation
- Max cache size: 1000 entries. LRU eviction on write if exceeded.

**Context files:** design doc §10.8, §10.9
**Deliverable:** Cache read/write/invalidate working with mtime-based invalidation
**Acceptance:** Cache hit returns same result. Modified file → cache miss → rebuild. Concurrent writes don't corrupt. Index files use atomic write pattern.

---

#### Task 2.4 `[MAIN]` — Add dependencies + Sprint 2 integration testing + commit

- Add `libcst>=1.0` and `tree-sitter>=0.21` + `tree-sitter-typescript>=0.21` to `pyproject.toml`
- Wire `get_index()` into `context_limit()` condition (Sprint 1 has no indexer call per D4; Sprint 2 adds it)
- Run validation gate from §11.6
- Commit: `sprint-2: CST indexing module — 5 indexers + cache layer`

---

### 12.3 Sprint 3 — v2 Pipeline in `ck3_file`

**Goal:** Add canonical path routing + `dev_mode` to ck3_file. Wire v2 enforcement + CST indexing into the read path. This is the core migration sprint.

#### Task 3.1 `[SA]` — `dev_mode` parameter + path routing in `ck3_file_impl`

**Target:** `tools/ck3lens_mcp/ck3lens/unified_tools.py` (function `ck3_file_impl`, L741+)

Changes:
1. Add `dev_mode: bool = False` parameter to `ck3_file_impl` signature
2. Add routing logic as first step after parameter validation:

```python
# Detect path type
is_canonical = path and (path.startswith("root:") or path.startswith("mod:"))
is_host_absolute = path and (len(path) >= 2 and path[1] == ":" and path[2] in "\\/")

if dev_mode and not is_canonical:
    return rb.invalid("CK3FILE-PATH-I-001", {
        "error": "dev_mode requires canonical paths (root: or mod:)",
        "input_path": path or f"{mod_name}:{rel_path}",
        "hint": "Use root:<key>/<path> or mod:<name>/<path>",
    })

if is_canonical or dev_mode:
    return _ck3_file_v2(command, path, ...)  # v2 pipeline
else:
    # existing v1 pipeline continues below
    ...
```

3. Create `_ck3_file_v2()` internal dispatch function (new function, ~50 lines) that:
   - Calls `wa2.resolve(path)` for WA2 resolution
   - Calls `enforcement_v2.enforce()` for enforcement (passes `host_abs`, `start_line`, `end_line` in context)
   - Routes to read/write/edit/delete sub-handlers

**Also update:** `tools/ck3lens_mcp/server.py` — `ck3_file` function (~L1756-1924) to accept and forward `dev_mode` parameter

**Context files:** `unified_tools.py` (L741-830 for signature, L830-1200 for read/write handlers), `server.py` (L1756-1924 for ck3_file tool definition)
**Deliverable:** Canonical paths route through v2; non-canonical paths still use v1
**Acceptance:** `ck3_file(command="read", path="root:repo/README.md")` → v2 pipeline; `ck3_file(command="read", mod_name="My Mod", rel_path="common/traits/test.txt")` → v1 pipeline; `dev_mode=True` with `mod_name` → error

---

#### Task 3.2 `[SA]` — v2 read path with enforcement + CST integration

**Target:** `tools/ck3lens_mcp/ck3lens/unified_tools.py` (inside `_ck3_file_v2`)

The v2 read path:
1. WA2 `resolve()` → Reply with `root_key`, `subdirectory`, `resolved` (canonical path)
2. Get `host_abs` via `wa2.host_path(ref)` — only for I/O, never in Reply
3. Call `enforce(rb, mode, tool="ck3_file", command="read", root_key=..., subdirectory=..., host_abs=host_abs, start_line=start_line, end_line=end_line)`
4. If denied → return denial Reply as-is (contains `_denial_meta` with index_path, next_call from `context_limit` condition side effect)
5. If allowed → read file → produce Reply with content
6. For ALL parseable files (even small ones): generate and cache CST index, include `cst_index_path` in success Reply.data
7. Leak scan: verify no host paths in Reply.data (use existing leak scanner if available)

Reply schema for successful read:
```json
{
  "content": "...",
  "path": "root:repo/README.md",
  "file_bytes": 1234,
  "cst_index_path": "root:ck3raven_data/wip/MCP_tool_replies/ck3_file/indexes/<hash>.json"
}
```

Reply schema for denial (produced by enforcement, not by ck3_file — see D5):
```json
{
  "detail": "Conditions not met: ['context_limit']",
  "failed_conditions": ["context_limit"],
  "file_bytes": 215040,
  "threshold_bytes": 15360,
  "estimated_bytes": 215040,
  "estimated_total_lines": 4778,
  "avg_line_bytes": 45,
  "guidance": "File exceeds context budget (215040B > 15360B). CST index written with 62 elements.",
  "index_path": "root:ck3raven_data/wip/MCP_tool_replies/ck3_file/indexes/<hash>.json",
  "index_type": "cst",
  "index_element_count": 62,
  "next_call": {"tool": "ck3_file", "command": "read", "path": "root:ck3raven_data/wip/MCP_tool_replies/ck3_file/indexes/<hash>.json"},
  "alternatives": ["Use start_line/end_line to read a specific range", "Read the CST index file for a structural overview"]
}
```

**Context files:** `unified_tools.py` (read handler around L830-1000), `enforcement_v2.py`, `indexing/__init__.py`
**Deliverable:** Working v2 read path with enforcement + CST
**Acceptance:** Large file → denial with index_path; small file → content + cst_index_path; narrow range on large file → content

---

#### Task 3.3 `[SA]` — v2 write/edit/delete paths

**Target:** `tools/ck3lens_mcp/ck3lens/unified_tools.py` (inside `_ck3_file_v2`)

The v2 write path:
1. WA2 `resolve()` → Reply with `root_key`, `subdirectory`
2. Get `host_abs` via `wa2.host_path(ref)`
3. Call `enforce(rb, mode, tool="ck3_file", command=command, root_key=..., subdirectory=..., has_contract=..., session=..., mod_name=...)`
4. If denied → return denial
5. If allowed → execute write/edit/delete
6. Leak scan Reply.data: replace host paths with canonical equivalents
7. Return success Reply with canonical paths only

Re-use existing write/edit/delete logic from v1 handlers but with:
- v2 enforcement instead of v1
- Canonical paths in Reply instead of host paths
- `host_abs` from `wa2.host_path(ref)` instead of `normalize_path_input()`

**Context files:** `unified_tools.py` (write handler around L1000-1400), `enforcement_v2.py`
**Deliverable:** Working v2 write/edit/delete with v2 enforcement + canonical replies
**Acceptance:** Write to mod in local_mods → success; write to vanilla → denied; canonical paths in all replies

---

#### Task 3.4 `[MAIN]` — Sprint 3 integration testing + commit

Run validation gate from §11.7:
- v2 read path: small file → content, large file → CST denial, narrow range → content
- v2 write path: allowed locations → success, forbidden → denied
- v1 path: all existing behavior unchanged
- dev_mode=True: non-canonical → error
- Logging: trace events present for all v2 operations
- Commit: `sprint-3: v2 pipeline in ck3_file — canonical routing + CST reads`

---

### 12.4 Sprint 4 — Reply/Logging Audit + Full Test Battery

**Goal:** Systematic validation that all reply fields, log events, and edge cases are correct.

#### Task 4.1 `[SA]` — Reply system audit

**Target:** All v2 Reply paths in `unified_tools.py` and `enforcement_v2.py`

Checklist (from §11.8):
- All `Reply.reply_type` values correct (`S`, `D`, `I`, `E`)
- All `Reply.code` follows naming convention (`CK3FILE-*`, `EN-*`, `WA-*`)
- No `Reply.data` field contains host-absolute paths (regex scan: `[A-Z]:\\` or `/home/`)
- `result_bytes` tracked by `mcp_safe_tool` wrapper
- `spilled` flag correct (ck3_file is exempt, should always be False)
- CST denial Reply matches §9.5 schema exactly
- Successful read Reply includes `cst_index_path`
- `dev_mode` error is Reply type I (`CK3FILE-PATH-I-001`)

**Context files:** `unified_tools.py` (v2 paths), `server.py` (ck3_file tool wrapper), `safety.py` (spill system)
**Deliverable:** Audit report (pass/fail for each check), fixes for any failures
**Acceptance:** All checks pass. No host paths leak in v2 replies.

---

#### Task 4.2 `[SA]` — Logging system audit

**Target:** Log events in `unified_tools.py`, `enforcement_v2.py`, `indexing/`

Checklist (from §11.8):
- `tool_start` event with v2-specific fields (canonical path, root_key)
- `tool_end` event with `result_bytes`, `spilled`, `enforcement_result`
- `enforcement_deny` event when conditions trigger
- `cst_index_generated` / `cst_index_cache_hit` events (DEBUG level)
- All events have `trace_id` and `session_id`
- Field names match `CANONICAL_LOGS.md` spec

**Context files:** `docs/CANONICAL_LOGS.md`, `enforcement_v2.py`, log call sites in `unified_tools.py`
**Deliverable:** Audit report + fixes
**Acceptance:** All events match spec. Trace/session IDs present.

---

#### Task 4.3 `[SA]` — Migration test battery (dev_mode=False)

Design and run comprehensive test battery (from §11.4 Phase 0.2):

| Category | Tests |
|----------|-------|
| v1 read | `mod_name` + `rel_path`, host-absolute path, `wip:/` scheme |
| v2 read | `root:repo/`, `root:game/`, `root:ck3raven_data/wip/`, `mod:Name/` |
| v1 write | `mod_name` + `rel_path` to local mod |
| v2 write | `root:ck3raven_data/wip/`, `mod:Name/` to local mod |
| v1 edit | search-replace via `mod_name` |
| v2 edit | search-replace via canonical path |
| v1 delete | `mod_name` + `rel_path` (requires token) |
| Cross-path | v1 and v2 reads interleaved in same session |
| Enforcement | v2 write to vanilla → denied, v2 write to WIP → allowed |
| CST | Large file read → CST index generated + cached; second read → cache hit |
| Edge cases | Missing file, binary file, empty file, file with BOM |

**Context files:** `unified_tools.py`, `capability_matrix_v2.py` (allowed operations), test framework setup
**Deliverable:** Test suite + all tests passing
**Acceptance:** 100% pass rate with `dev_mode=False`

---

#### Task 4.4 `[SA]` — Migration test battery (dev_mode=True)

Same battery but with `dev_mode=True`:
- All v1-format paths → `CK3FILE-PATH-I-001` error
- All v2-format paths → correct behavior
- Stress: 20+ rapid reads → no cache corruption

**Context files:** Same as 4.3
**Deliverable:** Test suite + all tests passing in dev_mode
**Acceptance:** 100% pass rate. v1 paths correctly rejected.

---

#### Task 4.5 `[MAIN]` — Sprint 4 integration + commit

- Review all audit reports
- Fix any issues found
- Run both test batteries
- Commit: `sprint-4: reply/logging audit + full test battery passing`

---

### 12.5 Sprint 5 — Agent Instructions + Transition + Cutover

**Goal:** Update agent-facing documentation, monitor transition, then remove v1 pipeline.

#### Task 5.1 `[MAIN]` — Update agent instructions

Files to update:
- `.github/copilot-instructions.md` — add canonical path examples for ck3_file, document CST indexing behavior
- Mode instructions text (returned by `ck3_get_mode_instructions`) — prefer canonical paths
- Agent briefing text (returned by `ck3_get_agent_briefing`) — explain CST denial flow

#### Task 5.2 `[MAIN]` — Transition monitoring

- Track v1 vs v2 path usage in logs (grep for `WA-RES` vs v1 resolution events)
- When zero v1 ck3_file paths observed across 5+ sessions → proceed to cutover

#### Task 5.3 `[SA]` — v1 pipeline removal (cutover)

**Target:** `tools/ck3lens_mcp/ck3lens/unified_tools.py`

Changes:
1. Remove v1 routing branch from `ck3_file_impl` — all paths go through `_ck3_file_v2`
2. Remove `dev_mode` parameter (v2 is now the only path)
3. Remove `context_warning` soft nudge code
4. Remove v1 `normalize_path_input()` calls from ck3_file
5. Remove `mod_name` pseudo-mod routing (`wip`, `vanilla` keywords)
6. Clean up dead imports
7. Keep `mod_name` + `rel_path` parameters working — they get translated to `mod:Name/rel_path` canonical form internally

**Context files:** `unified_tools.py` (full — significant deletion)
**Deliverable:** ck3_file runs purely on v2 pipeline
**Acceptance:** All tests from Sprint 4 pass with v1 code removed. No `dev_mode` parameter.

#### Task 5.4 `[MAIN]` — Final commit + documentation update

- Commit: `sprint-5: v1 pipeline removed — ck3_file fully on v2`
- Update this design doc §0 status to "COMPLETE"
- Update `OUTSTANDING_TASKS.md` §3.1 to "DONE"

---

### 12.6 Sub-Agent Summary

| ID | Sprint | Task | Why Sub-Agent? | Estimated Complexity |
|----|--------|------|----------------|---------------------|
| SA-1.1 | 1 | `context_limit()` factory | Self-contained function, clear spec, needs focused testing | Medium |
| SA-1.2 | 1 | `ctx` dict in enforcement | Small but precise edit (132-line file), Python CST ideal | Low |
| SA-1.3 | 1 | OPERATIONS_MATRIX split | 18 mechanical replacements, Python CST avoids line errors | Medium |
| SA-2.1 | 2 | Python indexer | Self-contained module, needs libcst expertise | Medium |
| SA-2.2 | 2 | Other indexers | 4 small modules, independent of each other | Medium |
| SA-2.3 | 2 | Cache layer | Self-contained module, atomic write + LRU | Medium |
| SA-3.1 | 3 | dev_mode + routing | Clear spec, affects two files (unified_tools + server) | Medium |
| SA-3.2 | 3 | v2 read path + CST | Core integration — needs enforcement + indexing context | High |
| SA-3.3 | 3 | v2 write/edit/delete | Re-uses existing handlers with v2 wrappers | Medium |
| SA-4.1 | 4 | Reply audit | Systematic checklist, small fixes | Low |
| SA-4.2 | 4 | Logging audit | Systematic checklist, small fixes | Low |
| SA-4.3 | 4 | Test battery (dev_mode=False) | Comprehensive test design | Medium |
| SA-4.4 | 4 | Test battery (dev_mode=True) | Variant of 4.3 | Low |
| SA-5.3 | 5 | v1 removal | Large deletion, needs careful dead code identification | Medium |

**Total: 14 sub-agent tasks across 5 sprints.**

Tasks that most benefit from Python CST editing: **SA-1.2** (precise 4-line edit in 132-line file), **SA-1.3** (18 mechanical matrix replacements), **SA-5.3** (large-scale deletion with dependency tracing).

---

### 12.7 Sprint Dependencies

```
Sprint 1 ──→ Sprint 2 ──→ Sprint 3 ──→ Sprint 4 ──→ Sprint 5
  │              │              │
  │              │              └─ requires enforcement (S1) + indexing (S2)
  │              └─ Sprint 2 adds indexer call to context_limit (Sprint 1 has none per D4)
  └─ no external dependencies (starts immediately)
```

Sprint 1 and Sprint 2 have one coupling point: `context_limit()` in Sprint 1 does NOT call any indexer (per D4 — guidance + next_call template only). Sprint 2 adds the indexer call, cache write, and canonical path conversion to `context_limit()`. Everything else is strictly sequential.
