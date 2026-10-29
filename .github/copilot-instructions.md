# CK3 Lens - Comprehensive Copilot Instructions

> **Last Updated:** February 23, 2026  
> **For use with:** ck3raven, CK3 Lens MCP, CK3 Lens Explorer

---

## REQUIRED READING FOR INFRASTRUCTURE WORK

If you are working on ck3raven source code (ck3raven-dev mode), read this first:

**[docs/CANONICAL_ARCHITECTURE.md](../docs/CANONICAL_ARCHITECTURE.md)** - The 5 rules every agent must follow

Key rules (violations will be rejected):
1. **ONE enforcement boundary** - only `enforcement.py` may deny operations
2. **NO permission oracles** - never ask "am I allowed?" outside enforcement
3. **mods[] is THE mod list** - no parallel lists like `local_mods[]`
4. **WorldAdapter = visibility** - describes what exists, NOT what's allowed
5. **Enforcement = decisions** - decides allow/deny at execution time only

**For MCP tool development:** See [Section 5: MCP Tool Architecture](../docs/CANONICAL_ARCHITECTURE.md#5-mcp-tool-architecture) for the canonical pattern all tools must follow.

---

## Instance ID and Tool Names (CRITICAL)

Each VS Code window gets a unique MCP instance ID (e.g., `5f4f-d9caa7`) that changes on every activation and subprocess restart.

**Tool names include only the first 4-char hex segment of the instance ID: `mcp_ck3lens-{first4}-_{tool_name}`**

Example: instance `5f4f-d9caa7` → `mcp_ck3lens-5f4f-_ck3_ping`

> **Why the shortened prefix?** VS Code truncates the MCP server name at the last hyphen-separated segment when generating tool prefixes. Server name `ck3lens-5f4f-d9caa7` becomes prefix `mcp_ck3lens-5f4f-_` (the `d9caa7` segment is dropped). The trailing `-_` is: `-` from our server name's remaining hyphen + `_` from VS Code's standard tool separator.

**NEVER construct tool names from memory, examples, or previous sessions.** The instanceId changes every time. The only authoritative sources are:

1. The response from `ck3_get_mode_instructions` — contains `instance_id` in session data. Call this first to initialize.
2. The CK3 Lens sidebar → Instance ID item (click to copy)

**⚠️ WARNING: The tool list VS Code injects into your context may be stale** if the window was reloaded since the conversation started. If tool calls fail with unknown tool errors, the instanceId has rotated.

**Recovery procedure (no new conversation needed):**
1. If the user's message contains an instance ID (e.g. `[zlah-b28716]`), use it directly — construct the prefix using only the first 4-char hex segment: `mcp_ck3lens-{first4}-_` (e.g., `[zlah-b28716]` → `mcp_ck3lens-zlah-_`, `[mh68-4f1adb]` → `mcp_ck3lens-mh68-_`). Call `ck3_ping` with that prefix. **User-provided IDs always take priority.**
2. If no ID was provided and the ping fails, call `tool_search("ck3 ping mode instructions")` **once** as a last resort to rediscover the live registry.
3. Then call `ck3_get_mode_instructions` to reinitialize.

**To bootstrap:** call `ck3_ping` then `ck3_get_mode_instructions` using the prefix visible in your available tools. Do not call `tool_search` unless both the user-provided ID (if any) and the context-visible prefix have failed.

The example ID `5f4f-d9caa7` in this file and any instance ID mentioned in conversation history are stale and must be ignored. Only the `ck3_get_mode_instructions` response is authoritative.

---

## ⛔ CRITICAL: MCP Configuration Prohibition

**NEVER create or modify `.vscode/mcp.json` in this workspace.**

The MCP server is provided **dynamically** by the CK3 Lens Explorer extension via `mcpServerProvider.ts`. Static configuration files conflict with this architecture and cause duplicate server instances.

### HARD BAN - Files Agents Must NEVER Create:

| Prohibited File | Why |
|-----------------|-----|
| `.vscode/mcp.json` | Creates duplicate MCP server with instance `default` |
| `mcp.json` (workspace root) | Same problem |
| Any `mcp.servers` block in settings | Deprecated - VS Code warns against this |

### If MCP appears disconnected:

1. **Check User Settings** - ensure `"chat.mcp.discovery.enabled": true`
2. **Reload VS Code window** - extension may need to re-register
3. **Check extension is running** - CK3 Lens Explorer must be active
4. **NEVER "fix" by creating mcp.json** - this makes it worse

### Architecture (READ THIS):

```
Extension (mcpServerProvider.ts)
    ↓ registers via vscode.lm.registerMcpServerDefinitionProvider()
    ↓ each window gets unique instance ID (e.g., f9hf-719e4f)
VS Code Copilot
    ↓ discovers servers via chat.mcp.discovery.enabled
    ↓ connects to extension-provided server
MCP Tools Available (mcp_ck3lens-XXXX_*)
```

**If you see `mcp_ck3lens-*` without an instanceId segment, something is wrong** — that pattern means the extension-provided server is not running correctly.

---

## Project Architecture

This workspace contains the **CK3 Lens** ecosystem - a complete AI-powered toolkit for Crusader Kings III modding:

### Core Components

| Component | Path | Purpose |
|-----------|------|---------|
| **ck3raven** | `/` (workspace root) | Python parser, resolver, SQLite database |
| **CK3 Lens MCP** | `tools/ck3lens_mcp/` | MCP server exposing 28+ tools to Copilot |
| **CK3 Lens Explorer** | `tools/ck3lens-explorer/` | VS Code extension for human UI |
| **CK3 Parser** | `src/ck3raven/parser/` | Paradox script parser (integrated, 100% vanilla pass rate) |

### Database
- **Path:** `~/.ck3raven/ck3raven.db`
- **Content:** ~81,000 files, 110+ content versions, indexed
- **Includes:** Vanilla CK3, Steam Workshop mods, local mods
- **Symbols:** Run `python builder/daemon.py start --symbols-only` after setup

---

## Canonical Addressing

MCP tools use **canonical addresses** - session-absolute paths that get resolved to host paths internally. Agents use canonical addresses in tool parameters; they never need to know the underlying host path.

### Format

```
root:<key>/<relative_path>     — infrastructure domains
mod:<mod_name>/<relative_path> — mods in active playset
```

### Root Keys

| Key | What it points to | Example |
|-----|-------------------|---------|
| `repo` | ck3raven source code | `root:repo/tools/ck3lens_mcp/server.py` |
| `ck3raven_data` | `~/.ck3raven/` data directory | `root:ck3raven_data/wip/analysis.py` |
| `game` | Vanilla CK3 installation | `root:game/common/defines/00_defines.txt` |
| `steam` | Steam Workshop content | `root:steam/2820384801/...` |
| `user_docs` | User mod folder | `root:user_docs/mod/My Mod/descriptor.mod` |

### Mod Addressing

```
mod:Ethnicities and Portraits Expanded/common/traits/custom.txt
mod:My Local Mod/common/decisions/my_decision.txt
```

### v1 vs v2 Tool Status

| Tool Status | Canonical Addressing | Legacy Paths |
|-------------|---------------------|--------------|
| **v2** (`ck3_exec`, `ck3_dir`, `ck3_file`, `ck3_contract` sign_script) | `root:` and `mod:` fully supported | N/A |
| _(none)_ | The former v1 Git tool `ck3_git` was **retired** (commit `72b0607`); no ordinary MCP Git tool exists. Use your normal local Git workflow outside the MCP. | N/A |

> **`ROOT_*` constants are NOT the same as `root:` addresses.** `ROOT_REPO` is a Python `Path` object (e.g., `Path("C:/Users/.../ck3raven")`) used internally by Python code. `root:repo/` is a portable canonical address used in MCP tool parameters. Do not confuse them.

---

## MCP Tools Reference (28+ Tools)

### Initialization (CRITICAL - Call First!)

| Tool | Purpose | Usage |
|------|---------|-------|
| `ck3_get_mode_instructions` | **THE initialization function** | **Call first with mode="ck3lens" or "ck3raven-dev"** |

**Single Entry Point:**
`ck3_get_mode_instructions(mode="ck3lens")`

This single call handles:
1. Database connection initialization
2. Mode setting (persisted)
3. WIP workspace initialization
4. Playset detection
5. Returns mode instructions + policy boundaries + session info

### Searching — ck3_search Tool (CRITICAL — Know the Commands)

| Tool | Purpose |
|------|---------|
| `ck3_search` | **PRIMARY SEARCH** — 6 commands (see below) |
| `ck3_confirm_not_exists` | Verify symbol truly missing before claiming it doesn't exist |
| `ck3_get_file` | Read indexed file content from database |
| `ck3_parse_content` | Parse script and return AST/errors |

**ck3_search Commands:**

| Command | What it searches | Data source | Use when |
|---------|------------------|-------------|----------|
| `auto` | Symbols + code snippets + file paths | **DATABASE** | Finding definitions by symbol name |
| `symbols` | Symbol definitions only | **DATABASE** | Same as auto, explicit |
| `content` | **Actual file contents** | **DISK** (grep) | Finding dates, specific text, code patterns |
| `paths` | File paths by pattern | DATABASE | Finding files by name |
| `mods` | Mod names/IDs | DATABASE | Finding mods |
| `filters` | Available filters vocabulary | DATABASE | Discovery |

**⚠️ The DB does NOT store full file contents.** The `auto`/`symbols` commands search symbol names and parsed code snippets — NOT arbitrary text like dates or specific strings.

**Examples:**

```python
# CORRECT — searching for "724.1.1" date in title history files:
ck3_search(command="content", query="724.1.1", path="root:game/history/titles")

# WRONG — this searches symbol NAMES, won't find the date string:
ck3_search(query="724.1.1", path="root:game/history/titles")

# CORRECT — finding trait definitions by name:
ck3_search(query="brave", symbol_type="trait")

# CORRECT — finding all mods that reference a symbol:
ck3_search(query="ambitious", verbose=True)
```

**`path` parameter** scopes searches using canonical addresses:
- `path="root:game/common/traits"` — vanilla game files
- `path="mod:MyMod/events"` — specific mod's folder
- ⚠️ `game_folder` is **DEPRECATED** — use `path` instead

### Playset, Scope & CVIDs

| Tool | Purpose |
|------|---------|
| `ck3_playset` | Get/switch playsets, list mods in playset |
| `ck3_get_scope_info` | Get active playset scope info |
| `ck3_get_db_status` | Check database health and build status |

**CVIDs (Content Version IDs) — How Search Scoping Works:**

Every mod in the active playset has a `cvid` — a numeric ID linking it to the database. Search tools (`ck3_search`, `ck3_conflicts`, etc.) **automatically filter by the active playset's CVIDs**. You do NOT need to find or pass CVIDs manually.

- **To see per-mod CVIDs:** `ck3_playset(command="mods")` — each entry includes `cvid`, `name`, `load_order`, `is_indexed`
- **NEVER query CVIDs from raw SQL.** The `content_versions` table schema has changed over time. Use `ck3_playset(command="mods")` instead.
- **Unresolved mods** (cvid = `null`) haven't been indexed yet. Run the builder to index them.

### Conflict Detection

| Tool | Purpose |
|------|---------|
| `ck3_get_symbol_conflicts` | Fast ID-level conflict detection |
| `ck3_qr_conflicts` | Quick-resolve conflicts using load order |

---

## Write Operations

### File Operations (v2 Canonical Addressing)

`ck3_file` uses **canonical paths** for all operations. Enforcement decides allow/deny based on the capability matrix.

**Canonical path format:**
- `root:<key>/<relative_path>` — infrastructure domains
- `mod:<mod_name>/<relative_path>` — mods in active playset

| Command | Example | Notes |
|---------|---------|-------|
| `read` | `ck3_file(command="read", path="mod:My Mod/common/traits/00_traits.txt")` | Returns content |
| `write` | `ck3_file(command="write", path="root:ck3raven_data/wip/script.py", content="...")` | Creates/overwrites |
| `edit` | `ck3_file(command="edit", path="mod:My Mod/common/traits/fix.txt", old_content="...", new_content="...")` | Search/replace |
| `delete` | `ck3_file(command="delete", path="...")` | Requires token |
| `index` | `ck3_file(command="index", path="root:repo/tools/server.py", parse=true)` | CST indexing |
| `create_patch` | `ck3_file(command="create_patch", mod_name="My Mod", source_path="common/traits/00_traits.txt", patch_mode="partial_patch")` | ck3lens only |

### WIP Workspace

The WIP workspace at `~/.ck3raven/wip/` (canonical: `root:ck3raven_data/wip/`) is for draft scripts and analysis output.

**How to write to WIP:**
```
ck3_file(command="write", path="root:ck3raven_data/wip/my_script.py", content="...")
```

No contract is needed — the capability matrix grants R/W/D on WIP in both modes.

### Script Execution (ck3_exec)

`ck3_exec` runs shell commands with policy enforcement. Two execution paths:

**Branch 1 - Whitelisted commands** (no contract needed):
```
ck3_exec("python -m pytest tests/")
```
> Note: raw Git through `ck3_exec` has been **closed** — `git ...` invocations are no longer whitelisted. The legacy `ck3_git` tool was retired at commit `72b0607` and no ordinary MCP Git capability currently exists.

**Branch 2 - Custom script execution** (requires script signing):
1. Write script to WIP
2. `ck3_contract(command="sign_script", script_path="root:ck3raven_data/wip/my_script.py")`
3. Human reviews script in VS Code, clicks Approve (CodeLens button)
4. Open contract with the returned signature: `ck3_contract(command="open", script_signature=..., script_path=...)`
5. `ck3_exec("python path/to/script.py")` - executes under contract

### Canonical Token Types

**Two token types exist in the canonical system:**

| Token | Purpose | TTL |
|-------|---------|-----|
| **NST** (New Symbol Token) | Required when creating new symbol identities not in baseline | 24 hours |
| **LXE** (Lint Exception) | Required when arch_lint violations exist at contract close | 8 hours |

Tokens are requested via `ck3_token(command="request", token_type="NST", reason="...")`

### Git Operations

> **Retired.** The `ck3_git` tool was removed (commit `72b0607`) and ordinary raw Git through `ck3_exec` is closed. There is **no ordinary MCP Git capability** at present. A diagnostic-first `ck3_git` is planned (Stage 0/1) but **not yet implemented**. Perform Git operations through your normal local workflow outside the MCP; no retired route is an authorized workaround.

---

## CK3 Merge/Override Rules (CRITICAL KNOWLEDGE)

### The 4 Core Merge Policies

| Policy | Behavior | Used By |
|--------|----------|---------|
| **OVERRIDE** | Last definition wins completely | ~95% of content (traits, events, decisions, scripted_effects, etc.) |
| **CONTAINER_MERGE** | Container merges, sublists append | `on_action` ONLY |
| **PER_KEY_OVERRIDE** | Each key independent, last wins | localization, defines |
| **FIOS** | First loaded wins | GUI types/templates (use `00_` prefix to load first) |

### CK3 Load Order (CRITICAL — Gets This Wrong = Broken Mods)

Load order determines which definition "wins" for OVERRIDE and FIOS policies. There are **two tiers**:

**Tier 1 — Mod position in playset (DOMINANT)**
Mods loaded later in the playset override mods loaded earlier. This is the primary mechanism. A mod at position 15 beats a mod at position 3, regardless of filenames.

**Tier 2 — Alphabetical filename order (SECONDARY, within same mod only)**
Within a **single mod**, files in the same directory load alphabetically. `zzz_patch.txt` loads after `00_base.txt`. This is how `zzz_` prefix overrides work — they load last **within that mod**.

**Common mistake:** Thinking `zzz_` prefix alone wins globally. It does NOT. A `zzz_` file in mod at position 3 still loses to `00_` file in mod at position 15. The `zzz_` prefix only guarantees last-within-its-own-mod.

### on_action Rules (CONTAINER_MERGE) - CRITICAL

**From Paradox's official `_on_actions.info`:**
> "You cannot have multiple triggers or effect blocks for a given named on-action."

#### Single-Slot Blocks (CONFLICT - only ONE allowed, last wins):
- `effect = { }` - ONE per on_action
- `trigger = { }` - ONE per on_action

#### List Blocks (APPEND - all entries merge from all files):
- `events = { }` - appends
- `on_actions = { }` - appends

#### CORRECT Pattern for Extending on_actions:
```pdx
# Append to the on_actions LIST - safe, no conflict
on_game_start = {
    on_actions = { my_mod_on_game_start }
}

# Your isolated on_action with its own effect block
my_mod_on_game_start = {
    effect = {
        # Your code here - completely isolated, no conflict!
        trigger_event = my_mod_setup.001
    }
}
```

---

## Best Practices for Mod Development

### 1. Always Search Before Creating
Use `ck3_search` to check if something exists before creating.

### 2. Never Claim "Not Found" Without Verification
Use `ck3_search` or `ck3_confirm_not_exists` to verify.

### 3. Validate Syntax Before Writing
Use `ck3_parse_content` to check syntax before `ck3_file(command="write", ...)`.

### 4. Use Proper File Naming
- `zzz_` prefix — loads LAST **within the same mod** (wins for OVERRIDE within that mod)
- `00_` prefix — loads FIRST **within the same mod** (wins for FIOS types like GUI)
- Remember: mod playset position is the dominant factor. `zzz_` only controls intra-mod ordering.

### 5. Commit Changes with Meaningful Messages
Commit through your normal local Git workflow. The `ck3_git` tool was retired (commit `72b0607`) and no ordinary MCP Git capability currently exists.

---

## Quick Reference: Key Files

| Purpose | Path |
|---------|------|
| Canonical Architecture | `docs/CANONICAL_ARCHITECTURE.md` |
| CK3Lens Policy | `docs/CK3LENS_POLICY_ARCHITECTURE.md` |
| Merge Rules | `docs/05_ACCURATE_MERGE_OVERRIDE_RULES.md` |
| MCP Server | `tools/ck3lens_mcp/server.py` |
