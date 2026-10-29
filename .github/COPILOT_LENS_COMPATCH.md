# CK3 Lens Mode — Agent Instructions

**Mode:** `ck3lens` | **Purpose:** Help users create compatibility patches, fix bugs in mods, and build new mods using the ck3raven toolchain. When the toolchain malfunctions or lacks features, file bug reports and feature requests to `~/.ck3raven/wip/`.

---

## Two Levels of Conflict

Always check BOTH. Missing one leads to broken patches.

**File-Level:** When two sources provide the same `relpath` (path + filename), the later mod **completely replaces** the earlier file. All content from the earlier file is gone.

**Symbol-Level:** When different-named files define the same symbol ID (e.g. both define `brave = { ... }`), all files survive but the **last definition wins** (LIOS). Symbols that appear in only one file coexist without conflict.

Example: A mod adds `zzz_00_traits.txt` in `common/traits/` with one new trait and one redefined trait. The new trait merges in (unique ID, no conflict). The redefined trait overrides the original (LIOS). Everything else in `00_traits.txt` is untouched.

Use `ck3_conflicts(command="files")` AND `ck3_conflicts(command="symbols")`.

---

## Override Strategy

**Prefer the approach that minimizes interference** with vanilla and other mods. Full overrides make patches harder to maintain and more fragile when upstream content changes.

| Approach | When | Effect |
|----------|------|--------|
| **Partial** (`zzz_[original].txt`) | Default — when you can solve the problem by redefining specific symbols | Original file survives; only matching symbol IDs override |
| **Full** (exact filename) | When partial can't work (see below) | Original completely replaced; your file must be complete |

**Full override required when:** nested structures force it (religion with faiths), broken lists need correction (lists only append), blanking a file, or GUI windows (filename-specific).

**Friendly exceptions:** `defines` (per-key, any filename) and `localization/replace/` (per-key).

⚠️ **Never mutate filenames.** `00_traits_fix.txt` is NOT a partial override of `00_traits.txt` — it's an independent file. Use `zzz_00_traits.txt` for partial override, or `00_traits.txt` exactly for full override.

---

## Merge Rules

Two universal rules: **Lists merge/append across all files. Same-ID blocks overwrite (LIOS).**

These apply everywhere — events, on_actions, traditions, men-at-arms, characters, province assignments. Exceptions: `defines`/`localization` (per-key), GUI (FIOS — first wins, use `00_` prefix).

**Implication for patching:** You cannot fix a broken list by adding a correction file — that appends more entries. Broken lists require full override.

📖 Full reference: `docs/05_ACCURATE_MERGE_OVERRIDE_RULES.md`

---

## on_actions

Mods chain custom on_actions off vanilla ones via `on_actions = { }` — a list, so all entries merge/append. When mods follow this pattern, no compatch is needed. Compatching IS needed when a mod directly overwrites `effect` or `trigger` blocks on a vanilla on_action.

---

## Loading Order

1. Same filename → mod load order (playset position) determines winner
2. Different filenames in same folder → all survive, alphabetical order (`00_` early, `zzz_` late)

---

## Tools

| Tool | Purpose |
|------|---------|
| `ck3_search` | Search symbols, content, files |
| `ck3_file` | Read/write/edit mod files |
| `ck3_conflicts` | File AND symbol conflicts |
| `ck3_parse_content` | Validate CK3 syntax |
| `ck3_playset` | Active playset and mods |
| `ck3_logs` | Error and game log analysis |

**Search scoping is automatic.** `ck3_search` filters by your active playset's mods — you never need to find or pass CVIDs. If you need per-mod details (cvid, load_order, indexed status), use `ck3_playset(command="mods")`. **Never query the `content_versions` table directly.**

Mutations require a contract (`ck3_contract`), except writes to `~/.ck3raven/wip/`.

### Script signing

Before **every** `ck3_contract(command="sign_script", ...)`, use the VS Code
question tool to ask:
"Are you at the workstation and ready to review and approve a sign_script request? Y/N"
Wait for an explicit Y. If N or unanswered, do not submit the request.
The synchronous approval wait is at most 180 seconds; the client may time out
sooner. Do not automatically retry a timed-out request or submit another while
the first may still be pending.

---

## Workflow

1. **Search** → `ck3_search` to understand existing content
2. **Check conflicts** → `ck3_conflicts` — both file AND symbol levels
3. **Write** → `ck3_file` — prefer partial override
4. **Validate** → `ck3_parse_content`
5. **Commit** → through your normal local Git workflow (`ck3_git` was retired at commit `72b0607`; no MCP Git tool exists)

📖 Override deep dive: `docs/05_ACCURATE_MERGE_OVERRIDE_RULES.md` · Per-type merge policy: `docs/06_CONTAINER_MERGE_OVERRIDE_TABLE.md`

---

## After a Window Reload

If `ck3_ping` fails with "tool does not exist", the instance ID has rotated. **Do not start a new conversation.** Instead:
1. Call `tool_search("ck3 ping mode instructions")` — this rediscovers the live tool registry.
2. Use the returned prefix to call `ck3_ping`.
3. Call `ck3_get_mode_instructions(mode="ck3lens")` with a fresh hat_token from the sidebar.
