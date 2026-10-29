# Phase Progress Recap — 2026-03-16

Branch: `agent/fix-ipc-port-isolation`

This note captures the current state of the A / B / C migration work as reconstructed from the committed history and the current working tree.

## Executive Summary

- Phase A is complete.
- Phase B1 is complete.
- Phase B2 is complete.
- Phase C is in progress.
- `ck3_validate` was migrated for the `references` path in commit `26b3591`, and the follow-up cleanup is now complete: live MCP replies use MCP-owned success codes for found and not-found outcomes, with MCP invalid for missing required input.
- The next major remaining Phase C targets are still `ck3_folder` and `ck3_git`.

## Phase Breakdown

### Phase A

Status: complete

What this phase entailed:
- Remove the old search surface area so there is only one canonical search entrypoint.
- Delete duplicate v1 handlers that were still exposing parallel behavior.
- Make the v2 search path the only supported path before deeper routing work continued.

Primary completion commit:
- `a36ff1f` — delete v1 search tools and make v2 search the only search path

What changed in practice:
- Renamed `ck3_searchV2` to `ck3_search`
- Deleted the old inline v1 search handler
- Deleted `ck3_grep_raw`
- Deleted `ck3_file_search`
- Fixed the `ck3_search` spill threshold so normal search responses stopped spilling prematurely

### Phase B1

Status: complete

What this phase entailed:
- Build the actual v2 search execution model after the old search surface had been removed.
- Split search behavior into explicit executors instead of ad hoc inline logic.
- Introduce a DB-aware execution path so DB-backed search commands could be policy-gated without pretending they were geographic file operations.

Key commits:
- `1942206` — implement the v2 search executors and DB execution path
- `9f1ff30` — merge `ck3_search_mods` into `ck3_search(command="mods")`

What changed in practice:
- Added v2 executors for `symbols`, `content`, `paths`, `filters`, and `auto`
- Added `execute_db()` for DB-backed operations
- Added the DB capability matrix structure used for mode-scoped DB operations
- Consolidated mod search into the unified `ck3_search` tool

### Phase B2

Status: complete

What this phase entailed:
- Apply the same consolidation principle to the database tool surface.
- Remove multiple overlapping DB tools and replace them with one unified command surface.
- Finish the DB-side cleanup so later migration work was not split across legacy wrappers.

Primary completion commit:
- `f5cfaaf` — consolidate four DB tools into unified `ck3_db`

What changed in practice:
- Removed the separate DB query / delete / close style handlers
- Centralized DB operations behind the unified `ck3_db` surface
- Completed the planned B2 consolidation, which means the older summary saying “B2 next” is stale

### Phase C

Status: in progress

What this phase entails:
- Migrate the remaining unified tools onto the v2 reply and enforcement model.
- Eliminate legacy dict-return and wrapper-shim behavior where the tool should instead return canonical `Reply` objects directly.
- Route DB-backed commands through `execute_db()` and geographic commands through the v2 world/enforcement path, with correct reply ownership.
- Finish the audit of the remaining tools whose routing is still mixed or partially legacy.

Completed or partially completed Phase C items:
- `8c139e4` — `ck3_conflicts` migrated to the v2 EN2 pipeline
- `26b3591` — `ck3_validate` `references` path routed through v2 DB enforcement

Current Phase C assessment:
- `ck3_folder`
  - Partially migrated
  - The `list` path is on the v2 route
  - Other folder operations still need a full audit to confirm they are not relying on legacy helper behavior
- `ck3_git`
  - Not fully migrated
  - Still the heaviest remaining Phase C target
- `ck3_validate`
  - Finalized for the `references` route
  - The `references` route was moved onto v2 DB routing in `26b3591`
  - Follow-up cleanup completed the reply contract so found and not-found outcomes now return MCP-owned success replies, and missing required input returns MCP invalid

## Post-Phase Hardening and Regression Fixes

These are not separate phases, but they materially changed the migration state and should be remembered alongside A/B/C.

- `5316911` — initial `search_mods` host-path leak fix
- `3cf1858` — restore `WorldAdapterV2.host_to_session_abs()` and complete the WA2-based reverse resolution fix
- `fe2a07e` — restore dropped unified search content results and improve `content` search behavior
- `0f853e1` — fix BOM-prefixed validated edits and add the VSIX rebuild / reinstall helper
- `6ca8a96` — make parse-blocked validated `ck3_file` writes return blocking invalid replies instead of success-shaped parse replies

## ck3_validate Finalization Notes

The `ck3_validate` follow-up cleanup is complete.

What was finalized:
- `ck3_validate(target="references")` no longer emits WA-layer validation replies for a DB-backed lookup owned by the MCP tool contract
- “symbol not found” is now a success-shaped lookup outcome instead of an invalid reply
- `DATABASE_OPERATIONS_MATRIX` is documented and treated as a policy / scoping gate only, not a reply-ownership table
- the temporary Python test file was removed so this area is validated through MCP tool calls from chat instead of a misleading unit test

MCP validation calls used:
1. `ck3_validate(target="references", symbol_name="c_rennes", symbol_type="title")`
2. `ck3_validate(target="references", symbol_name="__copilot_missing_symbol__")`
3. `ck3_validate(target="references")`

Observed live results:
- found symbol: `MCP-VAL-S-001`
- missing symbol: `MCP-VAL-S-002`
- missing input: `MCP-SYS-I-001`

Additional verification:
- `python -m pytest tests/sprint0/ --tb=short -q` → `223 passed, 1 warning`

## Immediate Next Steps

1. Commit the completed `ck3_validate` cleanup and this recap.
2. Resume Phase C with `ck3_folder` as the next practical audit / migration target.
3. Tackle `ck3_git` after `ck3_folder`, since it remains the larger and riskier migration.
