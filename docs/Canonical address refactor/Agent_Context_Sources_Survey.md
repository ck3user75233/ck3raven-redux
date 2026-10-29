# Agent Context Sources Survey — Path Addressing & Tool Usage

> **Date:** February 22, 2026
> **Updated:** February 23, 2026 — ck3_exec fully migrated (script signing complete), ck3_contract gains sign_script/review_script
> **Purpose:** Catalog all sources from which agents receive context about MCP tools, path formats, and addressing conventions. Identify gaps, incorrect guidance, and inconsistencies as the canonical addressing refactor proceeds.

---

## 0) v1 vs v2 — Tool Classification

Understanding which tools are on which system is essential context for this survey.

### v2 Tools (WA2 + enforcement_v2)

| Tool | Resolution | Enforcement | Notes |
|------|-----------|-------------|-------|
| `ck3_exec` | `world_adapter_v2.py` (WA2 `resolve()`) | `enforcement_v2.py` (`enforce()`) | **Fully migrated** — Branch 1 (whitelisted commands) and Branch 2 (signed script execution) both operational |
| `ck3_contract` (sign_script/review_script) | WA2 `resolve()` for script_path | N/A (signing, not enforcement) | **Uses v2 canonical addressing** — `script_path` must be `root:ck3raven_data/wip/...` |
| `ck3_dir` | `world_adapter_v2.py` (WA2 `resolve()`) | `enforcement_v2.py` (via OPERATIONS_MATRIX) | Fully migrated |

### v1 Tools (WorldAdapter + enforcement.py)

| Tool | Resolution | Enforcement | Notes |
|------|-----------|-------------|-------|
| `ck3_file` | `world_adapter.py` (`normalize_path_input()` → `WorldAdapter.resolve()`) | `enforcement.py` (`enforce()` with `OperationType`) | NOT migrated |
| `ck3_git` | `world_adapter.py` | `enforcement.py` | NOT migrated — planned for deprecation (all git commands whitelisted in ck3_exec) |

### Key Differences

| Aspect | v1 | v2 |
|--------|-----|-----|
| **Path resolution** | `WorldAdapter.resolve()` — accepts `mod:<name>/`, `ck3raven:/`, host-absolute paths, relative paths | `WA2.resolve()` — accepts `root:<key>/`, `mod:<name>/` only; host-absolute and relative paths rejected |
| **Enforcement** | `enforcement.py` with `OperationType` enum (READ/WRITE/DELETE) | `enforcement_v2.py` with single `enforce()`, `OperationRule` + condition predicates, no enum |
| **Path output** | Host-absolute paths may appear in replies | Session-absolute paths only (`root:` / `mod:` in replies); host paths contained via VisibilityRef |
| **Mod resolution** | `_mod_paths` dict lookup | `session.mods` direct consultation |
| **Visibility** | Implicit in resolution (configured roots) | Explicit `VISIBILITY_MATRIX` with `(mode, root_key, subdirectory)` keying |

### Important: `ROOT_*` Constants vs `root:` Canonical Addresses

These are **completely different things** and must not be conflated:

| Concept | Example | Type | Purpose |
|---------|---------|------|---------|
| `ROOT_*` Python constants | `ROOT_REPO = Path("C:/Users/.../ck3raven")` | **Host-absolute path** | Used internally by Python code to locate directories on disk |
| `root:` canonical address | `root:repo/docs/README.md` | **Session-absolute path** | Used by agents in MCP tool parameters — resolved by WA2 to host paths internally |

`ROOT_REPO` is a `Path` object pointing to the repo on THIS machine. `root:repo/` is a portable, session-scoped address that WA2 resolves to whatever `ROOT_REPO` happens to be. Agents should use `root:repo/` — they should never see or use `ROOT_REPO` as a path.

### Canonical Addressing in Practice — ck3_exec Script Signing

The script signing chain is the first feature that exercises v2 canonical addressing end-to-end across all layers:

1. **Agent provides** `script_path="root:ck3raven_data/wip/my_script.py"` to `ck3_contract(command="sign_script")`
2. **MCP server** resolves canonical path via `wa2.resolve()`, converts to host path for I/O
3. **Extension** receives `canonical_path` (NOT host path) — uses it in HMAC payload
4. **Sigil HMAC payload** contains the canonical path: `script:root:ck3raven_data/wip/my_script.py|<sha256>`
5. **Contract storage** stores canonical `script_path` — never host paths
6. **exec_gate verification** reconstructs canonical path and verifies HMAC

This demonstrates that canonical addressing is functional across Python ↔ TypeScript boundaries, HMAC signing, contract storage, and enforcement verification.

---

## 1) Context Source Inventory

Agents receive tool context from **5 distinct layers**, each loaded at different times and with different update mechanisms.

### Layer 1: VS Code System Prompt (copilot-instructions.md)

| Property | Value |
|----------|-------|
| **File** | `.github/copilot-instructions.md` |
| **Loaded** | Automatically by VS Code at chat start |
| **Visibility** | Always visible — injected into every conversation |
| **Updatable** | Requires file edit + new chat window |

**Content:** Tool reference table, WIP workspace examples, mod_name special values, merge/override rules, project architecture overview.

**Path guidance present:**
- `mod_name="wip"` pseudo-routing to `~/.ck3raven/wip/`
- `path="wip:/analysis.py"` canonical address (legacy scheme)
- `mod_name="vanilla"` pseudo-routing to game files
- Host-absolute path `~/.ck3raven/ck3raven.db` for database
- `~/.ck3raven/wip/` referenced as WIP workspace location

**Problems (not just gaps — actively wrong):**
- **`mod_name="wip"` is BROKEN even in v1.** v1 `normalize_path_input` converts this to `mod:wip/` → v1 WorldAdapter looks for a mod named "wip" in `_mod_paths` → not found → fails. There is no special routing for pseudo-mod names in unified_tools.py or server.py.
- **`path="wip:/file.py"` is BANNED in v1.** v1 WorldAdapter's `_parse_canonical()` explicitly lists `wip:/` as a BANNED SCHEME (February 2026 comment in code). It resolves to `ROOT_EXTERNAL` → not found.
- **`mod_name="vanilla"` is similarly BROKEN.** Same mechanism — there is no mod named "vanilla" in the playset.
- The `path` parameter is described for `ck3_file` writes as working with `mod_name`, but the tool actually works with host-absolute paths via `WorldAdapter._translate_raw_path()` for reads in ck3raven-dev mode.
- **No mention of v2 canonical addressing** (`root:<key>/`, `mod:<name>/`)
- **No distinction between v1 and v2 tools** — agents don't know which system they're using
- **No mention of `ck3_contract(command="sign_script")` or script signing flow** — agents unaware of script execution prerequisites

### Layer 2: Mode Instructions (returned by ck3_get_mode_instructions)

| Property | Value |
|----------|-------|
| **Files** | `.github/COPILOT_RAVEN_DEV.md`, `.github/COPILOT_LENS_COMPATCH.md` |
| **Loaded** | When agent calls `ck3_get_mode_instructions(mode=...)` |
| **Visibility** | Visible after initialization |
| **Updatable** | Requires file edit (no restart needed) |

**Content (ck3raven-dev):** Write access domains table, tools table, validation requirements, architecture overview, key documents list.

**Path guidance present:**
- Domain names: `ROOT_REPO`, `ROOT_CK3RAVEN_DATA`, `ROOT_GAME`, `ROOT_STEAM`, `ROOT_USER_DOCS`
- `~/.ck3raven/wip/` as WIP location
- Reference to `capability_matrix.py` as full matrix

**Problems:**
- Uses `ROOT_*` Python constant names in the domains table. Agents may interpret these as path values they can use in tool parameters. They are NOT — they are internal Python globals containing host-absolute paths.
- Going forward, the domains table should use the WA2 root key names (`repo`, `ck3raven_data`, `game`, `steam`, `user_docs`) and explain canonical addressing format.
- No canonical address examples
- No mention of `ck3_exec` script signing flow or `ck3_contract(command="sign_script")`

**Content (ck3lens):** CK3 modding-focused — merge rules, override rules, on_action patterns, decision best practices.

**Path guidance:** Minimal — focuses on game content semantics, not infrastructure paths. `relpath` mentioned in merge rule context (game-relative path). Not problematic.

### Layer 3: Tool Docstrings (visible in MCP tool schema)

| Property | Value |
|----------|-------|
| **Location** | Python docstrings in `server.py` on `@mcp.tool()` functions |
| **Loaded** | When VS Code discovers MCP tools (extension startup) |
| **Visibility** | Always visible — part of tool schema |
| **Updatable** | Requires MCP server restart |

**ck3_file docstring:**
- `mod_name` parameter: "Mod name from active playset", plus claims "wip" and "vanilla" as special values
- `path` parameter: "File path (for get/read from filesystem)"
- The `mod_name` special values guidance (`"wip"` → WIP, `"vanilla"` → game files) **does not work** — there is no pseudo-mod routing in the code

**ck3_exec docstring:**
- `working_dir` parameter: internally uses WA2 resolution
- References `enforcement.py` (should be `enforcement_v2.py` but cosmetic — doesn't affect agent behavior)
- Mentions "cat" as safe command (not actually whitelisted — minor inaccuracy)
- Not directly relevant to agent path usage (agents use command strings, not paths, with ck3_exec)

**ck3_contract docstring:**
- Now includes `sign_script` and `review_script` in command enum
- `script_path` parameter documented with canonical path example
- `script_signature` parameter documented with correct schema
- This is the **primary teaching point** for script signing — agents see it in the tool schema

**Gap:** Tool docstrings are the **highest-impact** update target because they're always loaded. Fixing the `ck3_file` docstring would immediately stop teaching agents broken patterns.

### Layer 4: Agent Briefing (ck3_get_agent_briefing)

| Property | Value |
|----------|-------|
| **Source** | Playset JSON file (`agent_briefing` key) |
| **Loaded** | When agent calls `ck3_get_agent_briefing()` |
| **Visibility** | Per-playset, user-authored |
| **Updatable** | Edit playset JSON |

**Content:** User-written notes about priorities, error analysis, conflict resolution, mod relationships.

**Path guidance:** None — this is for domain context, not path addressing. Not a problem.

### Layer 5: Documentation (docs/)

| Property | Value |
|----------|-------|
| **Location** | `docs/` directory |
| **Loaded** | Only when agent reads files explicitly |
| **Visibility** | NOT automatically loaded |
| **Updatable** | File edit |

**Relevant docs:**

| Document | Path Guidance | Accuracy |
|----------|--------------|----------|
| `CANONICAL_ARCHITECTURE.md` | 5 rules, enforcement boundary, mods[] as THE mod list | Accurate but doesn't cover canonical address format |
| `Canonical address refactor/v2_enforcement_design_brief.md` | Full WA2 `resolve()` spec, `root:<key>/<path>`, `mod:Name/<path>`, VisibilityRef workflow | **Accurate and complete** |
| `Canonical address refactor/ck3_file_Refactor_Design.md` | v2 path format examples, routing design | Accurate (design doc) |
| `Canonical address refactor/ck3_exec_Migration_Policy.md` | Full ck3_exec spec with script signing E2E flow | **Accurate — updated February 23, 2026** |

**Gap:** The most comprehensive and accurate path documentation lives in `docs/Canonical address refactor/` but agents **never see it** unless explicitly directed. It's not loaded at startup, not in tool schemas, not in mode instructions.

---

## 2) Gap Analysis Summary

### 2.1 Broken Guidance (actively wrong, causes failures)

| Source | Problem | Impact |
|--------|---------|--------|
| copilot-instructions.md | `mod_name="wip"` — no pseudo-mod routing exists | Agent writes fail silently or return "Mod 'wip' not in active playset" |
| copilot-instructions.md | `path="wip:/"` — explicitly banned in v1 WorldAdapter | Path resolution returns "Unresolvable input" |
| copilot-instructions.md | `mod_name="vanilla"` — no pseudo-mod routing exists | Same failure as "wip" |
| ck3_file docstring | Documents "wip"/"vanilla" as mod_name special values | Same failures — docstring matches copilot-instructions but both are wrong |
| Session note | References `<repo>/.wip/` for WIP workspace | Path doesn't exist — WIP is at `~/.ck3raven/wip/` |

### 2.2 Stale Terminology (not wrong but misleading for v2)

| Source | Issue |
|--------|-------|
| COPILOT_RAVEN_DEV.md | Uses `ROOT_*` constant names in domains table — agents may think these are usable paths |
| copilot-instructions.md | No mention of `root:` or `mod:` canonical addressing |
| COPILOT_RAVEN_DEV.md | No mention of `root:` or `mod:` canonical addressing |
| ck3_file docstring | `path` described only as "file path for get/read from filesystem" |
| ck3_exec docstring | References `enforcement.py` instead of `enforcement_v2.py` (cosmetic) |

### 2.3 Canonical Addressing Never Taught

No agent-visible source currently teaches the WA2 canonical address format:
- `root:<key>/<relative_path>` — for infrastructure paths (repo, game, steam, ck3raven_data, user_docs)
- `mod:<mod_name>/<relative_path>` — for mod-specific paths

This format exists only in internal engineering docs (`docs/Canonical address refactor/`).

**Exception:** The `ck3_contract` tool docstring now documents `script_path` as canonical path, but only for the signing context. No general canonical addressing primer exists in agent-visible sources.

### 2.4 Script Signing Not Taught (NEW)

The full script signing flow (sign → open contract with signature → execute) is documented in `ck3_exec_Migration_Policy.md` §3 but this document is NOT automatically loaded. The `ck3_contract` docstring mentions `sign_script` and `review_script` commands, which provides minimal discoverability, but no agent-visible source explains the complete workflow.

---

## 3) Update Plan

### Canonical Addressing Primer (for all updated sources)

All updated sources should teach this consistently:

**Canonical addresses** are session-absolute paths that WA2 resolves to host-absolute paths internally. Agents use canonical addresses in MCP tool parameters; they never need to know the underlying host path.

**Format:**
```
root:<key>/<relative_path>     — infrastructure domains
mod:<mod_name>/<relative_path> — mods in active playset
```

**Root keys:**
| Key | What it points to | Example canonical address |
|-----|-------------------|--------------------------|
| `repo` | ck3raven source code | `root:repo/tools/ck3lens_mcp/server.py` |
| `ck3raven_data` | `~/.ck3raven/` data directory | `root:ck3raven_data/wip/analysis.py` |
| `game` | Vanilla CK3 installation | `root:game/common/defines/00_defines.txt` |
| `steam` | Steam Workshop content | `root:steam/2820384801/...` |
| `user_docs` | User mod folder | `root:user_docs/mod/My Mod/descriptor.mod` |
| `vscode` | VS Code user data | `root:vscode/settings.json` |

**Mod addressing:**
```
mod:Ethnicities and Portraits Expanded/common/traits/custom.txt
mod:My Local Mod/common/decisions/my_decision.txt
```

**These are NOT the same as `ROOT_*` Python constants.** `ROOT_REPO` is a `Path` object like `Path("C:/Users/.../ck3raven")` — a host-specific value used inside Python code. `root:repo/` is a portable session address used in MCP tool parameters.

### Must Update — Immediate (blocks adoption when ck3_file v2 routing is added)

| Source | What to change |
|--------|---------------|
| **ck3_file docstring** (server.py) | (1) Remove broken `"wip"`/`"vanilla"` mod_name special values claim. (2) Update `path` parameter description to explain canonical addresses. (3) Add `dev_mode` parameter when implemented. (4) Add examples of `root:` and `mod:` usage. |
| **copilot-instructions.md** | (1) Remove broken `mod_name="wip"` and `path="wip:/..."` examples. (2) Replace with `path="root:ck3raven_data/wip/file.py"` for WIP. (3) Replace `mod_name="vanilla"` with `path="root:game/..."`. (4) Add canonical addressing primer section. (5) Keep `mod_name="ModName"` + `rel_path` as the current working pattern for mods until v2 migration. (6) Add script signing flow overview and `ck3_contract(command="sign_script")` usage. |
| **COPILOT_RAVEN_DEV.md** | (1) Replace `ROOT_*` column in domains table with root key names (`repo`, `ck3raven_data`, etc.). (2) Explain that `ROOT_*` are Python globals (for code), `root:<key>/` is canonical addressing (for tools). (3) Add canonical path examples. (4) Add note about `ck3_exec` script signing flow. |
| **Session note** (`_get_mode_session_note()`) | Fix `<repo>/.wip/` → `~/.ck3raven/wip/` or `root:ck3raven_data/wip/` |

### Should Update — After ck3_file migration

| Source | What to change |
|--------|---------------|
| **CANONICAL_ARCHITECTURE.md** | Add section on canonical address format now that WA2 is stable |
| **COPILOT_LENS_COMPATCH.md** | Add `mod:Name/` addressing when agents need to reference mod files |

### Do Not Update

| Source | Why |
|--------|-----|
| **Agent briefing** | User-authored playset context, not infrastructure docs |
| **ck3_exec docstring** | Minor cosmetic issues only (references enforcement.py, mentions "cat") — not causing agent failures |

---

## 4) Session Note Content (returned with mode instructions)

The `_get_mode_session_note()` function (server.py L5500–5518) returns inline guidance with each mode initialization.

**Current (ck3raven-dev):**
```
CK3 Raven Dev mode active. You can:
- Read all source code and mods (for parser/ingestion testing)
- Write/edit ck3raven infrastructure code
- Execute commands via ck3_exec (NOT run_in_terminal)
- Write analysis scripts to <repo>/.wip/

ABSOLUTE PROHIBITION: You CANNOT write to ANY mod files.
Git push/rebase/amend requires explicit approval token.
```

**Problem:** References `<repo>/.wip/` which has never been a valid WIP path. WIP is at `~/.ck3raven/wip/` (or canonically, `root:ck3raven_data/wip/`).

---

## 5) Summary of Findings

1. **Three agent-visible sources contain actively broken path guidance** — `mod_name="wip"`, `mod_name="vanilla"`, and `path="wip:/"` all fail at runtime. These are not just stale — they cause errors.

2. **No agent-visible source teaches canonical addressing** (`root:<key>/`, `mod:<name>/`). The format exists only in internal engineering docs. The `ck3_contract` docstring mentions canonical paths for `script_path` but provides no general primer.

3. **`ROOT_*` constants and `root:` addresses are fundamentally different things** — one is a host-absolute Python Path, the other is a session-absolute canonical address. Documentation must never present them as interchangeable or side-by-side as if they're alternate names for the same thing.

4. **Tool docstrings are the highest-impact update target** because they're always loaded. Fixing the `ck3_file` docstring would immediately stop teaching agents broken patterns.

5. **copilot-instructions.md is the second-highest impact** but the most sensitive to change — agents follow its examples literally.

6. **The session note has a stale WIP path** that has likely never worked.

7. **Currently, the only working path patterns for `ck3_file` writes are:** (a) host-absolute paths in ck3raven-dev mode, or (b) `mod_name="RealModName"` + `rel_path` for mods in the active playset. Everything else documented is broken.

8. **Script signing is the first feature using v2 canonical addressing end-to-end** — canonical paths flow from agent → MCP → extension → HMAC payload → contract storage → enforcement verification. This validates the canonical addressing design across Python/TypeScript boundaries.
