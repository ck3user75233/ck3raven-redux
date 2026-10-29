# MCP Tool Reply Cleanup Briefing

**Date:** February 25, 2026  
**Status:** DEFERRED - Layer enforcement disabled pending dedicated sprint  
**Related:** BUG-LAYER-001, Canonical Address Refactor Sprint 2+

---

## Executive Summary

Layer ownership enforcement was added to `ReplyBuilder._validate_and_get_layer()` (safety.py L743-748) but immediately broke 25+ MCP tools because they emit codes from layers (WA/EN/CT) that don't match their default `layer="MCP"` builder configuration.

**Decision:** Comment out the enforcement check for now. Schedule a dedicated "Terminal Reply Cleanup Sprint" after the canonical address refactor to properly audit and fix each tool's layer assignments.

---

## Core Concept: Terminal Replies

A **terminal reply** is the reply that determines the final outcome of a tool call. The layer that issues the terminal reply should match the ReplyBuilder's declared layer.

### Layer Responsibilities

| Layer | Responsibility | Terminal When |
|-------|---------------|---------------|
| **WA** | Path resolution, visibility, read I/O | Resolution succeeds/fails, visibility confirmed/denied |
| **EN** | Policy enforcement, authorization | Policy permits/denies operation |
| **CT** | Contract lifecycle | Contract state changes |
| **MCP** | Infrastructure only | Uncaught exceptions (handled by @mcp_safe_tool) |

### Key Insight

Most tools don't do domain work themselves — they delegate to `*_impl` functions that call WA/EN/CT. The terminal reply comes from whichever layer makes the final decision. For example:

- If WA can't resolve a path → WA issues terminal reply (I, never E)
- If WA resolves successfully → WA issues reply (S), which is only terminal if there are not subsequent terminal points before completion of the tool's functions
- The MCP tool wrapper should **pass through** the layer's terminal reply

MCP itself is only terminal for:
- Pure infrastructure tools with no WA/EN/CT involvement
- Uncaught exceptions (handled by `@mcp_safe_tool` wrapper)

---

## Audit Results

### Category A: Pure MCP Infrastructure

These tools have NO WA/EN/CT layer calls — MCP is correctly terminal:

| Tool | Current Codes | Status |
|------|--------------|--------|
| `ck3_get_instance_info` | `MCP-SYS-S-001` | ✅ Correct |
| `ck3_ping` | `MCP-SYS-S-001` | ✅ Correct |
| `ck3_playset` | `WA-VIS-*` | ❌ Wrong — no WA called, should use MCP codes |
| `debug_get_logs` | `WA-LOG-S-001` | ❌ Wrong — no WA called, should use `MCP-LOG-S-001` |
| `ck3_repair` | `MCP-SYS-*` | ✅ Correct |
| `ck3_get_mode_instructions` | `MCP-SYS-*` | ✅ Correct |

### Category B: WA-Primary Tools

These tools call WA for resolution/visibility — WA is terminal:

| Tool | Impl Calls WA? | Current Builder | Fix |
|------|---------------|-----------------|-----|
| `ck3_folder` | Yes (`world.resolve()`) | `layer="MCP"` | Change to `layer="WA"` |
| `ck3_dir` | Yes (`wa2` param) | `layer="MCP"` | Change to `layer="WA"` |
| ~~`ck3_git`~~ | — | — | **Obsolete** — `ck3_git` was retired (commit `72b0607`); no reply-layer fix applies |
| `ck3_logs` | Yes (via impl) | `layer="MCP"` | Change to `layer="WA"` |
| `ck3_validate` | Indirect | `layer="MCP"` | Change to `layer="WA"` |
| `ck3_parse_content` | No (pure parser) | `layer="MCP"` | Consider `layer="WA"` for PARSE codes |

### Category C: CT-Primary Tools

| Tool | Terminal Layer | Fix |
|------|---------------|-----|
| `ck3_contract` | CT (contract lifecycle) | Change to `layer="CT"` |

### Category D: Mixed-Layer Tools (Complex)

These tools have multiple code paths with different terminal layers:

| Tool | Code Paths | Analysis |
|------|-----------|----------|
| `ck3_file` | Read→WA, Write→EN | Primary: write path → use `layer="EN"` OR split logic |
| `ck3_exec` | Resolution→WA, Execution→EN | Primary: execution → use `layer="EN"` |
| `ck3_search` | Mode check→EN denial, Query→WA | Gate pattern: EN denies early, WA is primary |
| `ck3_db_query` | Query→WA, Policy→EN denial | EN denies mutating queries, WA handles reads |

---

## ck3_playset Deep Dive

**Observation:** `ck3_playset` emits `WA-VIS-*` codes but makes NO calls to WA.

The implementation (`_ck3_playset_internal`) manipulates the local playset manifest directly:
- Reads/writes playset JSON files
- No path resolution through WorldAdapter
- No visibility checks

**Current codes used:**
- `WA-VIS-I-001` — but there's no WA involvement
- `WA-VIS-S-001` — but there's no WA involvement

**Correct approach:**
Since this is pure MCP infrastructure managing playset state, use MCP codes. However, we need more granular codes for different invalid states:

| Situation | Proposed Code |
|-----------|--------------|
| No playsets in manifest | `MCP-CFG-I-001` |
| Mod already in playset | `MCP-CFG-I-002` |
| Mod not in playset | `MCP-CFG-I-003` |
| Playset not found | `MCP-CFG-I-004` |
| Success | `MCP-CFG-S-001` |

---

## ck3_folder Deep Dive

**Observation:** `ck3_folder` calls `ck3_folder_impl`, which DOES call WA:

```python
# unified_tools.py ~L1877
if world is not None:
    resolution = world.resolve(str(dir_path))
    if not resolution.found:
        return {
            "success": False,
            "error": f"Path not found: {path}",
            "mode": world.mode,
        }
```

**Current flow:**
1. `ck3_folder` creates `ReplyBuilder(..., layer="MCP")`  ← Wrong
2. Calls `ck3_folder_impl` which calls `world.resolve()`
3. Returns dict (not Reply) to `ck3_folder`
4. `ck3_folder` wraps result in Reply using WA codes  ← Layer mismatch

**Correct approach:**
1. `ck3_folder` creates `ReplyBuilder(..., layer="WA")`
2. `ck3_folder_impl` returns dict OR could return Reply directly
3. WA codes are appropriate because WA determined the outcome

Alternatively, `ck3_folder_impl` could be refactored to return `Reply` objects directly, making the terminal reply explicit at the point of decision.

---

## Missing Registry Entries

Codes used in tools but NOT defined in `reply_codes.py`:

| Code | Used In | Fix |
|------|---------|-----|
| `EN-MODE-D-001` | `ck3_search`, `ck3_grep_raw`, `ck3_file_search` | Add to registry |
| `MCP-LOG-S-001` | Needed for `debug_get_logs` | Add to registry |
| `MCP-CFG-*` | Needed for `ck3_playset` | Add to registry |

---

## Proposed Sprint Plan

### Sprint: Terminal Reply Cleanup

**Objectives:**
1. Audit each tool's call graph to identify true terminal reply point
2. Update ReplyBuilder layer declarations to match terminal layer
3. Add missing codes to registry
4. Refactor impls that return dicts to return Replies (where sensible)
5. Re-enable layer enforcement

**Approach per tool:**

```
For each tool:
  1. Trace call graph: tool → impl → layer calls
  2. Identify which layer issues terminal reply
  3. Set ReplyBuilder(layer=X) to match
  4. Ensure all emitted codes match layer X
  5. If mixed layers, decide:
     a) Use dominant layer
     b) Refactor into separate entry points
     c) Accept impl returns Reply directly
```

**Scheduling options:**
- **Option A:** After ck3_file Sprint 2 completes
- **Option B:** After full canonical address refactor completes
- **Option C:** Interleave with remaining MCP tool migrations

---

## Immediate Action (Completed)

The layer enforcement check has been commented out:

```python
# safety.py L743-748 - DISABLED pending Terminal Reply Cleanup Sprint
# if code_layer != self.layer:
#     raise ValueError(
#         f"Layer mismatch: ReplyBuilder(layer='{self.layer}') cannot mint "
#         f"code '{code}' (prefix '{code_layer}'). ..."
#     )
```

This allows tools to continue functioning while we plan proper fixes.

---

## References

- [safety.py](../../tools/ck3lens_mcp/safety.py) — ReplyBuilder and enforcement
- [reply_codes.py](../../tools/ck3lens_mcp/ck3lens/reply_codes.py) — Code registry
- [unified_tools.py](../../tools/ck3lens_mcp/ck3lens/unified_tools.py) — Tool implementations
- [server.py](../../tools/ck3lens_mcp/server.py) — MCP tool entry points
