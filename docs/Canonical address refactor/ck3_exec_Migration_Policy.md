# ck3_exec Migration Policy — Keystone Tool #2

> **Original:** February 21, 2026
> **Updated:** February 23, 2026 — Script signing FULLY IMPLEMENTED and E2E tested. §3, §8, §11, §13 rewritten.
> **Scope:** `ck3_exec` tool — shell command execution via MCP
> **Status:** COMPLETE. Branch 1 (whitelisted commands) and Branch 2 (signed script execution) both operational. E2E and negative tests passed.

---

## 0) Classification

`ck3_exec` is a **keystone tool** (Phase 2 §1). It is the highest-privilege MCP tool — it runs arbitrary shell commands. Migration to WA2 addressing + enforcement_v2 is mandatory before Phase 2.1 deterministic gates.

---

## 1) Inline Execution Ban — IMPLEMENTED ✅

**Policy:** No inline code execution via `python -c`, `python -m` (bare), or shell interpreters.

**Implementation:** `_is_command_whitelisted()` in `capability_matrix_v2.py` (lines 258–299):
- Prefix matching against `policy/command_whitelist.json` entries
- Word boundary enforcement: `"git status"` matches `git status --short` but NOT `git statusx`
- Shell metacharacter rejection: `|`, `&&`, `;`, `` ` ``, `$()` are treated as command terminators — only the portion BEFORE the first metachar is checked
- `python -c` is NOT in the whitelist → DENIED
- `python -m` (bare) is NOT in the whitelist → DENIED
- Only specific `python -m <module>` entries are whitelisted (e.g., `python -m pytest`, `python -m qbuilder`)

### Whitelist Infrastructure

| Component | Location | Status |
|-----------|----------|--------|
| `_load_command_whitelist()` | `capability_matrix_v2.py` L215–254 | **DONE** — loads from `ROOT_REPO/policy/command_whitelist.json`, cached |
| `_is_command_whitelisted()` | `capability_matrix_v2.py` L258–299 | **DONE** — prefix match + metachar rejection |
| `policy/command_whitelist.json` | `policy/command_whitelist.json` | **POPULATED** — 34 commands, HAT-protected |
| `exec_gate()` Branch 1 | `capability_matrix_v2.py` L142–163 | **DONE** — checks whitelist, returns True if match |

### Whitelist Contents (34 commands)

| Category | Count | Commands |
|----------|-------|----------|
| **Git** | 15 | `git status`, `git diff`, `git log`, `git add`, `git commit`, `git pull`, `git push`, `git branch`, `git checkout`, `git stash`, `git rev-parse`, `git show`, `git remote`, `git tag`, `git fetch` |
| **Python** | 5 | `python -m pytest`, `python -m qbuilder`, `python -m tools.compliance.run_arch_lint_locked`, `python -m tools.arch_lint`, `python builder/daemon.py`, `python --version` |
| **Pip** | 3 | `pip install`, `pip list`, `pip show` |
| **NPM** | 6 | `npm run compile`, `npm run package`, `npm run watch`, `npm run esbuild`, `npm run lint`, `npm run test`, `npm install` |
| **NPX** | 1 | `npx vsce package` |
| **VS Code** | 1 | `code --install-extension` |

### Intentionally Excluded

| Excluded | Why |
|----------|-----|
| `python -c` | Inline execution ban |
| `python -m` (bare) | Too broad — only specific modules whitelisted |
| `rm`, `del`, `rmdir` | Destructive — not part of normal workflow |
| `powershell`, `cmd`, `bash` | Shell interpreters — whitelist specific commands, not shells |
| `curl`, `wget` | Network operations — not part of normal dev flow |

---

## 2) Script Location Restriction — DONE ✅

**Policy:** Scripts executed via `python <path>` must be in `root:ck3raven_data/wip/` only.

**Implementation:**
- `OPERATIONS_MATRIX` restricts `EXEC_COMMANDS` to `("*", "ck3raven_data", "wip")` only
- `working_dir` resolved via WA2 (server.py), defaults to `root:ck3raven_data/wip`
- `_detect_script_path()` (server.py) extracts script path from `python <path>` commands
- **Script path resolved via WA2** (server.py): after detection, the script's host-absolute path is iterated against `wa2._roots` to find which root it belongs to, then resolved canonically to get the script's own `root_key`/`subdirectory`. Enforcement receives the script's coordinates, not the working directory's.
- Commit: `27013af`

---

## 3) Script Signing — FULLY IMPLEMENTED ✅

> **Updated February 23, 2026** — Replaces prior "VERIFICATION EXISTS, NO HUMAN TRIGGER" section.

**Policy:** Script execution requires a Sigil HMAC signature binding the script content hash to a session-absolute canonical path. The signature is produced by the CK3 Lens extension after a human reviews the script content and clicks an "Approve" CodeLens button.

### Architecture Overview

The script signing chain has 4 participants:

| Participant | Role |
|-------------|------|
| **Agent** | Writes script to wip, calls `ck3_contract(command="sign_script", script_path=...)` |
| **MCP Server** (Python) | Resolves canonical path, hashes content, sends IPC to extension, polls for approval |
| **Extension** (TypeScript) | Opens file in VS Code, shows CodeLens, computes HMAC on human approval |
| **Enforcement** (Python) | Verifies HMAC at contract open time AND at execution time |

### End-to-End Flow

1. **Agent writes script** to wip via `ck3_file(command="write", path="<absolute-wip-path>", content="...")`

2. **Agent calls** `ck3_contract(command="sign_script", script_path="root:ck3raven_data/wip/my_script.py")`

3. **MCP server** (`_ck3_contract_script_signing` in server.py):
   - Resolves `script_path` via WA2 (`wa2.resolve()`)
   - Validates script is under `wip` subdirectory (rejects with `CT-SIGN-I-002` if not)
   - Computes `content_sha256 = hashlib.sha256(file_content).hexdigest()`
   - Sends IPC to extension: `open_script_for_review(host_abs_path, content_sha256, canonical_path=script_path)`
   - Polls `get_script_sign_approval(host_abs_path)` every 2 seconds, up to 5 minutes

4. **Extension** (`diagnosticsServer.ts`):
   - Opens file in VS Code editor (`vscode.workspace.openTextDocument()`)
   - Registers CodeLens provider showing "✅ Approve" and "❌ Reject" buttons
   - Stores pending request keyed by normalized host path, with `canonical_path` and `content_sha256`
   - On human click "Approve":
     - Reads Sigil secret from `getSigilSecret()` callback
     - Computes HMAC: `crypto.createHmac('sha256', Buffer.from(sigilSecret, 'hex')).update(payload).digest('hex')`
     - Payload: `script:${pending.canonical_path}|${pending.content_sha256}`
     - Stores signature + `signed_at` timestamp on the pending request
   - On poll (`get_script_sign_approval`): returns `{approved: true, signature, content_sha256, signed_at}`

5. **MCP server receives approval**, returns `CT-SIGN-S-001` with:
   ```json
   {
     "script_path": "root:ck3raven_data/wip/my_script.py",
     "content_sha256": "<sha256>",
     "signature": "<hmac_hex>",
     "signed_at": "<iso_timestamp>"
   }
   ```

6. **Agent opens contract** with signature attached:
   ```
   ck3_contract(command="open", ..., script_signature={...}, script_path="root:ck3raven_data/wip/my_script.py")
   ```

7. **Contract open validation** (Script Signature Gate in `contract_v1.py`):
   - `script_signature` and `script_path` are mutually required — providing one without the other is rejected
   - Structural validation: `signature` and `content_sha256` keys must be non-empty
   - HMAC verification: `sigil_verify(f"script:{script_path}|{content_sha256}", stored_signature)`
   - Rejection raises `ValueError` → returned as `CT-VAL-I-001`
   - On success: `contract.script_signature` and `contract.script_path` are set (these fields are only populated inside the validated block)

8. **At execution time**, `exec_gate()` Branch 2 in `capability_matrix_v2.py`:
   - Checks script is in wip (`exec_subdirectory == "wip"`)
   - Checks open contract exists
   - Checks `sigil_available()`
   - Checks `script_path` matches `contract.script_path` (authoritative)
   - Checks `content_sha256` matches `contract.script_signature["content_sha256"]`
   - Verifies HMAC: `sigil_verify(f"script:{script_path}|{content_sha256}", stored_signature)`
   - All checks pass → returns True → enforcement allows execution

### HMAC Payload Format

```
script:{session_absolute_canonical_path}|{sha256_hex_digest}
```

Example:
```
script:root:ck3raven_data/wip/test_script.py|a1b2c3d4...
```

This payload is used identically in three places:
1. **Extension signing** (`diagnosticsServer.ts` — production side)
2. **Contract open validation** (`contract_v1.py` — gate at creation)
3. **exec_gate verification** (`capability_matrix_v2.py` — gate at execution)

The payload does NOT contain `contract_id`. This is by design — the signature is bound to the script content and its canonical location. The contract binding is enforced structurally: the signature is stored ON the contract, and exec_gate reads it FROM the contract.

### IPC Protocol

| Method | Direction | Parameters | Purpose |
|--------|-----------|------------|---------|
| `open_script_for_review` | MCP → Extension | `{path, content_sha256, canonical_path}` | Open file, register CodeLens |
| `get_script_sign_approval` | MCP → Extension | `{path}` | Poll for approval status |
| `cancel_script_sign` | MCP → Extension | `{path}` | Cancel pending request (timeout cleanup) |

Transport: TCP JSON-RPC on port 9847. Client: `ck3lens.ipc_client.VSCodeIPCClient`.

### Reply Codes

| Code | Meaning |
|------|---------|
| `CT-SIGN-S-001` | Script signed successfully — signature returned |
| `CT-SIGN-S-002` | Script opened for review (review_script command, no approval) |
| `CT-SIGN-I-002` | Script not in wip directory |
| `CT-SIGN-I-003` | User rejected the script |
| `CT-SIGN-I-004` | Timeout (5 minutes elapsed without approval) |
| `CT-SIGN-I-005` | script_path parameter missing |
| `CT-SIGN-E-001` | Failed to read script file |
| `CT-SIGN-E-002` | IPC communication error |

### `script_path` as First-Class Contract Field

`script_path` is a first-class field on `ContractV1` (dataclass field, line 309 of contract_v1.py). It stores the session-absolute canonical path of the signed script (e.g., `root:ck3raven_data/wip/test.py`).

This field is:
- Set during `open_contract()` inside the Script Signature Gate (only after HMAC verification passes)
- Read by `_ck3_exec_internal()` in server.py to resolve the script for hashing
- Checked by `exec_gate()` against the enforcement context's `script_path`

### HAT has NO role in this chain

HAT (Human Approval Token) is a one-shot human authorization for mode initialization and protected file access. Script signing is a different mechanism entirely:

| | HAT | Script Signing |
|--|-----|---------------|
| **Artifact** | Token (consumed once) | Sigil HMAC signature (verified repeatedly) |
| **Lifecycle** | One-shot per session/protected operation | Per-script, per-contract, content-bound |
| **What human sees** | Confirmation dialog ("Initialize agent?") | Actual script content in VS Code editor with CodeLens |
| **Stored where** | Consumed (deleted after use) | `contract.script_signature` + `contract.script_path` fields |
| **Verified by** | HAT consumption logic | `sigil_verify()` in both contract open and exec_gate |

---

## 4) Working Directory — IMPLEMENTED ✅

- Resolved via WA2: `root:ck3raven_data/wip` default
- Clamped to visible roots via VISIBILITY_MATRIX
- server.py L4075–4085

---

## 5) Output Leak Protection — DEFERRED

Stdout/stderr returned without `check_no_host_paths()` scanning.

**Demotion rationale:** While the canonical addressing refactor is only partially complete, agents see host paths from many other tools (ck3_file, ck3_git still on v1). Leak-scanning ck3_exec in isolation creates false coverage. Deferred until ck3_file and ck3_git are migrated.

---

## 6) Non-Goals

- No AST inspection of scripts
- No sandboxing — ck3_exec remains a privileged break-glass tool
- No network access control beyond command whitelist
- No `python -c` or inline code even with approval — the script must be a file

---

## 7) Return Type — DONE ✅

All code paths in `_ck3_exec_internal` now return `Reply` via `ReplyBuilder`. Normalized as of Feb 22, 2026, commit `27013af`.

---

## 8) Implementation Status — COMPLETE (February 23, 2026)

> **Supersedes** all prior §8 status tables.

### All components implemented and tested

| Component | Status |
|-----------|--------|
| WA2 resolution for working_dir | **DONE** — defaults to `root:ck3raven_data/wip` |
| Script path WA2 resolution (§2) | **DONE** — commit `27013af` |
| `enforce()` v2 integration | **DONE** |
| OPERATIONS_MATRIX gating | **DONE** — `EXEC_COMMANDS` at `("*", "ck3raven_data", "wip")` only |
| `exec_gate()` Branch 1 (whitelisted commands) | **DONE** — 12 validation tests passed |
| `exec_gate()` Branch 2 (signed script verification) | **DONE** — Sigil HMAC verification via `sigil_verify()` direct calls |
| Script signing MCP commands | **DONE** — `sign_script` and `review_script` in `ck3_contract` tool |
| Script signing extension UI | **DONE** — CodeLens "Approve"/"Reject" buttons in `diagnosticsServer.ts` |
| Script signing IPC protocol | **DONE** — TCP JSON-RPC port 9847, 3 methods |
| Contract open HMAC validation | **DONE** — Script Signature Gate in `open_contract()` |
| `_is_command_whitelisted()` | **DONE** — prefix match + metachar rejection |
| `_load_command_whitelist()` | **DONE** — ROOT_REPO-based, cached |
| `policy/command_whitelist.json` | **POPULATED** — 34 commands |
| `_detect_script_path()` | **DONE** |
| Return type normalization | **DONE** — all paths return Reply, commit `27013af` |
| `script_path` first-class contract field | **DONE** — ContractV1 dataclass field |
| Reply codes (CT-SIGN-*) | **DONE** — 8 codes, CT-SIGN-I-001 deleted (was dead code) |
| Inline `message=` overrides | **REMOVED** — all 10 stripped from `_ck3_contract_script_signing` |

### Remaining gaps (not ck3_exec-specific)

| Gap | Impact | Priority |
|-----|--------|----------|
| No output leak scanning | Host paths visible in stdout/stderr | Deferred (§5) |
| Contract session boundary bug | Contracts survive session rotations | §1.7 in OUTSTANDING_TASKS |

### Deleted components (no longer exist)
- `sign_script_for_contract()` — DELETED (commit `2f449bc`). Was a wrapper around `sigil_sign()`.
- `validate_script_signature()` — DELETED (commit `2f449bc`). Was a wrapper around `sigil_verify()`.
- `CT-SIGN-I-001` reply code — DELETED (was for removed `user_reviewed=False` flow).

---

## 9) Validation Testing Results

### 9.1 Branch 1: Whitelist Tests (February 22, 2026)

12 validation tests executed via MCP, all passed:

| Test | Command | Expected | Result |
|------|---------|----------|--------|
| Prefix match | `git status --short` | ALLOW | ✅ ALLOW |
| Exact match | `pip list` | ALLOW | ✅ ALLOW |
| Non-whitelisted | `curl http://evil.com` | DENY | ✅ DENY |
| Shell pipe injection | `git status \| cat /etc/passwd` | DENY | ✅ DENY |
| Shell && injection | `git status && rm -rf /` | DENY | ✅ DENY |
| Shell semicolon injection | `git status; rm -rf /` | DENY | ✅ DENY |
| Backtick injection | `` git status `rm -rf /` `` | DENY | ✅ DENY |
| $() injection | `git status $(rm -rf /)` | DENY | ✅ DENY |
| Word boundary | `git statusx` | DENY | ✅ DENY |
| Qbuilder prefix | `python -m qbuilder build` | ALLOW | ✅ ALLOW |
| npm prefix | `npm run compile -- --sourcemap` | ALLOW | ✅ ALLOW |
| Empty command | ` ` (whitespace) | DENY | ✅ DENY |

### 9.2 Branch 2: Script Signing E2E Test (February 23, 2026)

Full end-to-end test: agent signs script → opens contract with signature → executes script.

| Step | Action | Result |
|------|--------|--------|
| 1 | Call `ck3_contract(command="sign_script", script_path="root:ck3raven_data/wip/test_signing_e2e.py")` | `CT-SIGN-S-001` — signature returned |
| 2 | Open contract with `script_signature={...}` and `script_path=...` | `CT-OPEN-S-001` — contract opened |
| 3 | Call `ck3_exec(command="python test_signing_e2e.py")` | `EN-EXEC-S-001` — exit code 0, script ran successfully |

### 9.3 Negative Tests (February 23, 2026)

| Test | Action | Expected | Result |
|------|--------|----------|--------|
| Forged signature | Open contract with fabricated `script_signature` dict | Rejected at open | ✅ `CT-VAL-I-001` — "Script signature HMAC verification failed" |
| Missing signature | Open contract with `script_path` but no `script_signature` | Rejected at open | ✅ `CT-VAL-I-001` — "script_signature is required when script_path is provided" |
| No contract | Call `ck3_exec` with `python script.py` and no active contract | Denied by enforcement | ✅ `EN-GATE-D-001` |
| No signature on contract | Open contract without signature fields, then `ck3_exec` | Denied by exec_gate | ✅ `EN-GATE-D-001` |

---

## 10) Bug Fixes Applied This Session

### 10.1 Whitelist JSON Format (FIXED)

**Root cause:** MCP tool `ck3_file` was writing `\n` as literal two-character sequences (`\` + `n`) instead of real newlines. This caused `json.loads()` to fail when loading the whitelist, which defaulted to an empty list, making all commands denied.

**Fix:** Implemented `format="json"` parameter in `ck3_file` (server.py). When `format="json"`:
- Type annotations widened: `content: str | dict | list | None`
- `_ensure_str()` helper auto-converts `dict|list` → `json.dumps(indent=2)` with trailing newline
- Handles MCP framework behavior where valid JSON content strings are deserialized into dicts before reaching the tool handler

**Commit:** `10d45af` — "feat(exec): fix ck3_exec whitelist + ck3_file JSON format mode"

### 10.2 Whitelist Loader ROOT_REPO (FIXED)

**Root cause:** Original whitelist loader walked up from `.git` directory to find the repo root. This was fragile.

**Fix:** Changed to use `ROOT_REPO` from `ck3lens.paths` directly. Added debug logging for troubleshooting.

### 10.3 Pylance Type Narrowing (FIXED)

**Root cause:** `len(_WHITELIST_CACHE)` where `_WHITELIST_CACHE` is typed `list[str] | None`. Pylance can't narrow a global variable across function boundaries.

**Fix:** Introduced local `cmds: list[str] = data.get("commands", [])` variable, assigned to `_WHITELIST_CACHE = cmds`, then logged `len(cmds)`.

### 10.4 Contract Open Accepts Forged Signatures (FIXED)

**Root cause:** `open_contract()` accepted any `script_signature` dict without HMAC verification. Forged signatures were stored on the contract and only caught later by `exec_gate()` at execution time. This was inconsistent with how `work_declaration` is validated at open time.

**Fix:** Added Script Signature Gate in `contract_v1.py` `open_contract()`:
- Mutual requirement: `script_signature` and `script_path` must both be present or both absent
- Structural validation: `signature` and `content_sha256` keys must be non-empty
- HMAC verification: `sigil_verify(f"script:{script_path}|{sha_value}", stored_signature)` — same payload as exec_gate
- Raises `ValueError` on failure (consistent with existing `work_declaration` validation pattern)
- `contract.script_signature` and `contract.script_path` are set ONLY inside the validated block

### 10.5 Error Message Wording (FIXED)

**Root cause:** HMAC failure message said "or forged" — speculative about intent. Most common cause is window reload (session secret rotates).

**Fix:** Changed to: "signature does not match. This typically happens after a window reload (the session secret rotates)."

---

## 11) Remaining Priorities (ck3_exec-Adjacent)

> **ck3_exec itself is complete.** These items are adjacent concerns.

### 1. Contract session boundary bug (§1.7 in OUTSTANDING_TASKS)
Contracts survive session rotations. A contract signed under a previous Sigil secret should fail verification, but this hasn't been validated. This affects ALL contract types, not just script execution contracts.

### 2. Output leak scanning (§5)
Deferred until all tools are on v2 — scanning one tool while others leak host paths provides false coverage.

### 3. Area-specific denial codes
Current: `EN-GATE-D-001` used uniformly. Planned: `EN-EXEC-D-001`, etc. Low priority cosmetic improvement.

---

## 12) Extended Test Plan

> The validation tests in §9 are real E2E tests run through MCP. Formal test file infrastructure is a separate concern.

### Covered by §9 tests:
- Whitelist integration (prefix, exact, injection, boundary)
- Script signing full chain (sign → open → execute)
- Negative cases (forged sig, missing sig, no contract)

### Not yet formally covered:
- Script path WA2 resolution edge cases (script outside wip via symlink, etc.)
- Concurrent contracts with different scripts
- Content modification between signing and execution (should fail — content_sha256 mismatch)
- Sigil secret rotation mid-contract (should fail — signature becomes invalid)

---

## 13) Design Decisions Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-02-21 | `exec_gate()` is a 3-branch condition predicate, not an enforcement function | Conditions are pure predicates; enforcement decides |
| 2026-02-21 | Whitelist is HAT-protected in `policy/command_whitelist.json` | Prevents agent self-modification of allowed commands |
| 2026-02-22 | Whitelist populated with 34 commands across 7 categories | Covers git, python modules, pip, npm, npx, VS Code, builder operations |
| 2026-02-22 | `ck3_file` gains `format="json"` mode with `_ensure_str()` | Fixes MCP framework dict deserialization of JSON content |
| 2026-02-22 | `pip install` added to whitelist (was previously excluded) | Nate approved — needed for operational workflow |
| 2026-02-22 | npm/npx commands added to whitelist | Extension development workflow requires build/package/lint commands |
| 2026-02-23 | Deleted `sign_script_for_contract()` and `validate_script_signature()` | Unnecessary wrappers around `sigil_sign`/`sigil_verify`. Callers use Sigil primitives directly. |
| 2026-02-23 | HAT has no role in the script signing chain | HAT is a token (consumed once), script approval needs a Sigil HMAC signature (verified repeatedly). Different artifacts, different lifecycles, different mechanisms. |
| 2026-02-23 | Script signing uses CodeLens/IPC pattern, NOT codesense directive | Extension opens file, registers CodeLens provider, human reviews and clicks Approve/Reject. No magic header needed. |
| 2026-02-23 | HMAC payload excludes `contract_id` | Payload is `script:{path}\|{hash}`. Contract binding is structural (signature stored ON the contract, read FROM the contract by exec_gate). |
| 2026-02-23 | `script_path` is a first-class ContractV1 field | Session-absolute canonical path, set only inside validated Script Signature Gate block. |
| 2026-02-23 | Contract open validates signature HMAC (not just exec_gate) | Consistency with `work_declaration` validation at open time. Defense in depth — forged signatures rejected immediately, not deferred to execution. |
| 2026-02-23 | `user_reviewed` parameter deleted from signing flow | Was unnecessary — human approval is implicit in CodeLens click. Removed from tool signature, routing, and signing function. |
| 2026-02-23 | Inline `message=` overrides stripped from signing function | All 10 sites in `_ck3_contract_script_signing` used registry messages via ReplyBuilder default fallback. |
| 2026-02-23 | CT-SIGN-I-001 reply code deleted | Existed solely for the removed `user_reviewed=False` flow. Dead code. |
