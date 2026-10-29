# Outstanding Tasks — Post-Document Sync

> **Original:** February 21, 2026
> **Updated:** February 23, 2026 — ck3_exec COMPLETE (script signing fully implemented, E2E tested, negative tests passed); §1.3 and §1.6 marked done; §4.1 done; §1.7 still outstanding
> **Status convention:** ✅ DONE = implemented and tested, ⚠️ PARTIAL = code exists but not validated, ❌ NOT DONE = no implementation

---

## Priority 1: ck3_exec Completion — ✅ COMPLETE

> All ck3_exec items are done except §1.7 (contract session boundary bug), which affects all contract types, not just ck3_exec.

### 1.1 Inline ban condition predicate — ✅ DONE
- `policy/command_whitelist.json` created and populated (34 commands, HAT-protected)
- `_load_command_whitelist()` loads from `ROOT_REPO/policy/command_whitelist.json`, cached
- `_is_command_whitelisted()` does prefix matching with word boundary + metachar rejection
- `exec_gate()` Branch 1 checks whitelist → returns True if match
- **12 validation tests passed** (prefix, exact, non-whitelisted, 5 metachar injections, word boundary, qbuilder, npm, empty)
- Commits: `10d45af`, `ce4f37e`

### 1.2 Script path resolution via WA2 — ✅ DONE
- Script path now resolved from the contract's `script_path` field (first-class ContractV1 field, set during contract open after HMAC verification)
- `_ck3_exec_internal` reads `active_contract.script_path`, resolves via `wa2.resolve()` to get host path for content hashing
- Enforcement receives the script's coordinates (`root_key`, `subdirectory`), not the working directory's
- Commit: `27013af`

### 1.3 Script approval via Sigil — ✅ FULLY IMPLEMENTED

> **Updated February 23, 2026** — Replaces prior "VERIFICATION CODE EXISTS, NO HUMAN TRIGGER" status.

**Complete implementation:**
- `ck3_contract(command="sign_script", script_path="root:ck3raven_data/wip/...")` — opens script in VS Code with CodeLens approval buttons
- Extension (`diagnosticsServer.ts`) registers CodeLens "✅ Approve" / "❌ Reject" buttons via `ScriptSignCodeLensProvider`
- Human reviews script content in editor, clicks Approve
- Extension computes HMAC: `crypto.createHmac('sha256', sigilSecret).update("script:{canonical_path}|{content_sha256}").digest('hex')`
- MCP server polls `get_script_sign_approval()` every 2s (up to 5 min), receives signature
- Returns `CT-SIGN-S-001` with `{script_path, content_sha256, signature, signed_at}`
- Agent opens contract with `script_signature=...` and `script_path=...`
- **Contract open validates HMAC** — Script Signature Gate in `open_contract()` verifies signature via `sigil_verify()` before accepting. Forged signatures rejected as `CT-VAL-I-001`.
- At execution time, `exec_gate()` Branch 2 re-verifies: script in wip, contract exists, path matches, content_sha256 matches, HMAC valid

**HMAC payload format:** `f"script:{session_absolute_path}|{content_sha256}"` — no `contract_id` in payload. Contract binding is structural (signature stored ON the contract).

**IPC protocol:** TCP JSON-RPC port 9847. Methods: `open_script_for_review`, `get_script_sign_approval`, `cancel_script_sign`.

**Deleted components:**
- `sign_script_for_contract()` — wrapper deleted (commit `2f449bc`), callers use `sigil_sign()` directly
- `validate_script_signature()` — wrapper deleted (commit `2f449bc`), callers use `sigil_verify()` directly
- `user_reviewed` parameter — fully removed from tool signature, routing, and signing function
- `CT-SIGN-I-001` reply code — deleted (existed solely for removed `user_reviewed=False` flow)
- All 10 inline `message=` overrides stripped from `_ck3_contract_script_signing`

**E2E test passed:** `sign_script` → open contract with signature → `ck3_exec("python test_signing_e2e.py")` → `EN-EXEC-S-001`, exit code 0.

**Negative tests passed:**
- Forged signature → rejected at contract open (`CT-VAL-I-001`)
- Missing signature (script_path without script_signature) → rejected (`CT-VAL-I-001`)
- Exec with no contract → denied (`EN-GATE-D-001`)

**HAT has NO role in this chain.** Script signing uses Sigil HMAC signatures (content-bound, verified repeatedly). HAT is a one-shot consumed token for mode init / protected files. Different artifact, different lifecycle.

### 1.5 Return type normalization — ✅ DONE
- All code paths in `_ck3_exec_internal` now return Reply via ReplyBuilder
- Annotation at server.py L3968: "normalized as of Feb 22, 2026"
- Commit: `27013af`

### 1.6 ck3_exec Comprehensive Validation Tests — ✅ DONE

> **Updated February 23, 2026** — All tests executed through MCP tool interface.

**Tests completed:**

Branch 1 (whitelisted commands) — 12 tests via `ck3_exec` MCP tool:
- Prefix match, exact match, non-whitelisted, 5 metachar injections (pipe, &&, semicolon, backtick, $()), word boundary, qbuilder prefix, npm prefix, empty command
- All results verified through `ck3_exec` MCP interface, not sub-functions

Branch 2 (signed script execution) — E2E test:
- Full chain: `ck3_contract(command="sign_script")` → human approval via CodeLens → `ck3_contract(command="open", script_signature=..., script_path=...)` → `ck3_exec("python test_signing_e2e.py")` → exit code 0

Negative tests:
- Forged signature at contract open → `CT-VAL-I-001`
- Missing signature at contract open → `CT-VAL-I-001`
- Exec without contract → `EN-GATE-D-001`
- Exec with contract but no script fields → `EN-GATE-D-001`

**Not yet formally covered** (edge cases for future test harness):
- Script modified between signing and execution (content_sha256 mismatch)
- Sigil secret rotation mid-contract
- Concurrent contracts with different scripts
- Script outside wip via symlink

### 1.7 Fix: Contracts Survive Session Boundaries — ❌ NOT DONE

**Bug:** Contracts created in one MCP session may remain active and usable after a new session begins.

**Current state of verification code:**
- `load_contract()` calls `_verify_contract_signature()` which calls `sigil_verify()` on every load
- `get_active_contract()` iterates contracts and skips any that fail signature verification
- `list_contracts()` also filters by valid signatures
- The Sigil secret is generated per VS Code window activation (random 16-byte hex, held in extension memory, passed to MCP via `CK3LENS_SIGIL_SECRET` env var)
- In theory: new window → new secret → old contract signatures fail verification → old contracts become invisible

**Why it's still NOT DONE:**
1. **Not explicitly tested.** The user identified this bug before Sigil verification was fully exercised. While the code structure SHOULD prevent cross-session validity, this hasn't been validated with a deliberate test (e.g., create contract → reload window → check if old contract is accessible).
2. **VS Code "Reload Window" edge case.** It's unclear whether a VS Code window reload spawns a new MCP subprocess with a new Sigil secret, or whether the same process (and same env var) persists.
3. **No explicit session initialization cleanup.** There's no code in mode init that explicitly invalidates old contracts. The protection is implicit (HMAC fails because secret changed). If the secret somehow doesn't change, old contracts remain valid.
4. **File-list enforcement still missing.** Even within a valid session, contracts declare specific files and intent, but enforcement only checks mod name + operation type. The file list in `work_declaration.edits` is not checked at write time.

**Required to close:**
- Explicit test: create contract → force new Sigil secret → verify old contract is rejected
- Confirm Sigil secret rotation behavior on VS Code reload
- Consider explicit session ID tracking on contracts (belt + suspenders)
- File-list enforcement is a separate concern (deferred)

---

## Priority 2: Enforcement Improvements

### 2.1 Area-specific denial codes — ❌ NOT DONE
- Current: `EN-GATE-D-001` used uniformly for all denials
- Required: Area-specific codes (e.g., `EN-EXEC-D-001`, `EN-WRITE-D-002`)
- Low priority — cosmetic improvement, doesn't affect security

---

## Priority 3: Tool Migration to v2

### 3.1 Migrate ck3_file to WA2 + enforcement_v2 — ✅ DONE (March 3, 2026)
- ck3_file now uses WA2 + EN2 exclusively (v1 pipeline removed)
- All commands (read, write, edit, delete, create_patch, index) use canonical addressing
- Legacy `mod_name` + `rel_path` syntax still supported (translated to `mod:Name/path` internally)
- See `ck3_file_Refactor_Design.md` for full design

**ck3_file is the last consequential keystone migration.** Once migrated, all write operations are on v2 (ck3_exec for commands, ck3_file for files). Everything after this is read-only cleanup.

**ck3_file validation tests required after refactor** — same standard as §1.6: real calls through ck3_file, not tests wired into sub-functions.

### 3.2 Remaining v1 read tools — batch cleanup, two phases

After ck3_file, the remaining v1 tools are **all read-only** with lightweight WorldAdapter usage. Migrate in two phases (exact tool assignments per phase TBD when we get there):

| Tool | WA Usage | Migration Effort |
|------|----------|-----------------|
| **ck3_folder** (`list` cmd only) | `world.resolve()` for directory listing validation. DB commands already use session.mods[] CVIDs. | Low |
| **ck3_grep_raw** | `world.resolve()` for path visibility check | Low |
| **ck3_file_search** | `world.resolve()` for base path validation | Low |
| **ck3_search** | `world.normalize()` — one string normalization call | Trivial |

These share the same pattern (validate path → do read operation) and should be migrated in batches, not treated as individual keystones.

### 3.3 Deprecate ck3_git — DONE (retired at commit `72b0607`)

The legacy `ck3_git` tool has been **retired**: the tool and its v1 enforcement code were removed, and the raw-Git prefixes it duplicated were removed from the `ck3_exec` whitelist at the same time. Ordinary raw Git through `ck3_exec` is therefore **closed**, and there is currently **no ordinary MCP Git capability**. A diagnostic-first `ck3_git` redesign (Stage 0/1) is planned but **not yet implemented**.

### 3.4 Remove v1 enforcement modules — FAR OFF
- `ck3_git` is already retired (commit `72b0607`); this remains gated on ALL remaining tools migrating to v2
- Remove: enforcement.py (v1), world_adapter.py (v1)
- Audit for any remaining v1 imports
- This is end-of-road cleanup, not near-term work

---

## Priority 4: Extension UI — DONE (Script Signing)

### 4.1 Script approval UI — ✅ DONE

> **Updated February 23, 2026** — Was "NOT STARTED", now fully implemented.

- **CodeLens pattern** (not codesense directive — no magic header needed)
- `diagnosticsServer.ts`: `openScriptForReview()` opens file in editor, registers `ScriptSignCodeLensProvider` with "✅ Approve" and "❌ Reject" buttons
- `handleScriptApproval()` produces Sigil HMAC on approve, stores on `PendingScriptSign`
- `getScriptSignApproval()` returns result to polling MCP server
- `cancelScriptSign()` handles timeout cleanup
- Uses `canonical_path` (session-absolute) in HMAC payload — host paths never leak into signing
- Sigil secret accessed via `getSigilSecret()` callback (set via `setSigilSecret()`)
- Extension commands registered: `ck3lens.scriptSign.approve`, `ck3lens.scriptSign.reject`

---

## Priority 5: Deferred Items

### 5.1 Leak scanning on output — DEFERRED (BOTTOM OF LIST)
- Stdout/stderr from ck3_exec returned without scanning
- Not meaningful in isolation — leak scanning only provides real coverage when applied uniformly across ALL tools that return content (ck3_file reads, ck3_grep_raw, ck3_search results, etc.)
- Defer until the entire MCP tool surface is on v2 and a uniform output scanning layer makes sense

---

## Recent Completions

| Item | Status | Commit | Date |
|------|--------|--------|------|
| Whitelist JSON fix (literal `\n` chars) | ✅ | `10d45af` | Feb 22 |
| `ck3_file` JSON format mode | ✅ | `10d45af` | Feb 22 |
| Whitelist populated (34 commands) | ✅ | `10d45af` | Feb 22 |
| Shell metachar sanitization | ✅ | `ce4f37e` | Feb 22 |
| Script path WA2 resolution (§1.2) | ✅ | `27013af` | Feb 22 |
| Return type normalization (§1.5) | ✅ | `27013af` | Feb 22 |
| Pylance type narrowing fix | ✅ | `0168447` | Feb 22 |
| ck3_exec Migration Policy rewrite | ✅ | `0168447` | Feb 22 |
| ck3_file Refactor Design doc | ✅ | `0168447` | Feb 22 |
| Agent Context Sources Survey | ✅ | `a3e28b2` | Feb 22 |
| Fix broken agent guidance (4 sources) | ✅ | `5c4192e` | Feb 22 |
| ROOT_REPO description correction | ✅ | `d960ebd` | Feb 22 |
| v1 WIP write guidance (label + fix) | ✅ | `5a4ccae` | Feb 22 |
| OUTSTANDING_TASKS priority reorder | ✅ | `200edaf` | Feb 23 |
| Script signing MCP commands (sign_script/review_script) | ✅ | — | Feb 23 |
| Extension CodeLens script approval UI | ✅ | — | Feb 23 |
| IPC protocol (open_script_for_review, get_script_sign_approval, cancel_script_sign) | ✅ | — | Feb 23 |
| Sigil wrapper functions deleted (sign_script_for_contract, validate_script_signature) | ✅ | `2f449bc` | Feb 23 |
| `user_reviewed` parameter removed | ✅ | — | Feb 23 |
| `script_path` promoted to first-class ContractV1 field | ✅ | — | Feb 23 |
| Host-path-to-session-absolute-path refactoring in server.py | ✅ | — | Feb 23 |
| Contract open HMAC validation (Script Signature Gate) | ✅ | — | Feb 23 |
| CT-SIGN-I-001 deleted (dead code for user_reviewed flow) | ✅ | — | Feb 23 |
| Inline message= overrides stripped from signing function | ✅ | — | Feb 23 |
| E2E test: sign → open → execute (full chain) | ✅ | — | Feb 23 |
| Negative tests: forged sig, missing sig, no contract | ✅ | — | Feb 23 |
| Error message wording fix (HMAC failure) | ✅ | — | Feb 23 |
| ck3_exec_Migration_Policy.md updated to final state | ✅ | — | Feb 23 |
| Agent_Context_Sources_Survey.md updated | ✅ | — | Feb 23 |
| OUTSTANDING_TASKS.md updated | ✅ | — | Feb 23 |

---

## Notes

- **Sigil is the signing primitive.** `sigil_sign(payload)` / `sigil_verify(payload, sig)` in `tools/compliance/sigil.py`. All signing use cases (contracts, scripts, future) should call Sigil directly. Callers build their own payload strings and decide where to store the output. There is no reason for application-specific signing wrappers.
- **v1 vs v2 tool classification:** ck3_exec, ck3_dir, and ck3_file use WA2 + enforcement_v2 (v2). The former `ck3_git` (the last v1 WorldAdapter + enforcement.py consumer) has been **retired** (commit `72b0607`). See Agent_Context_Sources_Survey.md §0.
- **WIP writes:** Use `ck3_file(command="write", path="root:ck3raven_data/wip/...", ...)`. No contract needed. Canonical addressing now fully supported.
- Contract v1 `open_contract()` validates root_category but NOT subdirectory. Defense-in-depth relies on OPERATIONS_MATRIX denying operations outside allowed subdirectories at enforcement time.
- **Contract v1 `open_contract()` validates script_signature HMAC.** Script Signature Gate (lines ~753-798 of contract_v1.py) checks mutual requirement of `script_signature`+`script_path`, structural validation, and `sigil_verify()` at contract open time. Forged/invalid signatures are rejected immediately.
- `EXEC_COMMANDS` is an empty frozenset used as identity sentinel — `find_operation_rule()` special-cases `tool == "ck3_exec"` via `rule.commands is EXEC_COMMANDS`.
- WA2 is mode-agnostic: reads mode dynamically via `get_agent_mode()`. No cache clearing needed on mode change.
- **ck3_git deprecation rationale (now executed):** `ck3_git` duplicated git commands that were also whitelisted in `ck3_exec`, adding a redundant v1 enforcement layer. It was retired at commit `72b0607`; at the same time the raw-Git prefixes were removed from the `ck3_exec` whitelist, so ordinary raw Git via `ck3_exec` is now **closed** rather than the fallback the original rationale assumed.
- **Validation test standard:** Tests must call the actual MCP tool, not sub-functions. Past tests proved to be wired into internals with predetermined results. This requirement applies to ck3_file (after §3.1 refactor).
- **Contract session boundary bug (§1.7):** Contracts have Sigil verification on every load (`_verify_contract_signature()` in `load_contract()` and `get_active_contract()`). This SHOULD prevent cross-session validity since the Sigil secret rotates per VS Code window. Not explicitly tested.
- **Contracts vs HAT — critical distinction:** Contracts are Sigil-signed by the MCP server to protect integrity (proves server issued them, not forged). HAT is a one-shot human click for mode initialization. They are completely independent systems. Any doc/code/context that says "contract HAT" or "contracts signed under HAT" is wrong.
- **`_detect_script_path()` no longer exists.** The script path is read from the contract's first-class `script_path` field (set during `open_contract()` after HMAC verification), not parsed from the shell command string.
- **Script signing uses CodeLens pattern, NOT codesense directive.** No magic header in the script file. The extension opens the file and registers a `ScriptSignCodeLensProvider` dynamically via IPC.
