# Script Signing Specification

> **Created:** February 23, 2026
> **Status:** Approved for implementation
> **Scope:** Human-in-the-loop script signing for ck3_exec Branch 2

---

## Overview

Two new subcommands on `ck3_contract`:
- **`review_script`** — opens a script in VS Code with CodeLens Approve/Reject buttons via IPC. Returns immediately.
- **`sign_script`** — requests human approval for a script. If `user_reviewed=False`, executes `review_script` behavior and returns immediately. If `user_reviewed=True`, sends IPC request to extension to open file with CodeLens, polls for approval via IPC, returns Sigil signature in `Reply.data`.

The agent passes the returned signature dict to `ck3_contract(command="open", script_signature={...})`. The contract's own Sigil auto-signature then covers the `script_signature` field. At execution time, `exec_gate()` Branch 2 re-verifies the signature against the script's current content.

---

## Addressing

Agent-facing paths use canonical addressing: `root:ck3raven_data/wip/my_script.py`

Subfolders under wip are valid (e.g., `root:ck3raven_data/wip/scripts/analyze.py`).

MCP server resolves to host-absolute internally via WA2. Host-absolute paths never appear in tool parameters or responses.

---

## Communication Channel

Existing TCP JSON-RPC IPC between MCP server (`ipc_client.py`) and extension (`DiagnosticsServer`). No file-based IPC.

New IPC methods on `DiagnosticsServer`:

| Method | Direction | Purpose |
|--------|-----------|---------|
| `openScriptForReview` | MCP → Extension | Open script in editor with CodeLens Approve/Reject |
| `getScriptSignApproval` | MCP → Extension | Check if user clicked Approve/Reject |
| `cancelScriptSign` | MCP → Extension | Clean up CodeLens on timeout |

---

## Flow: `sign_script` with `user_reviewed=True`

```
Agent                          MCP Server                    Extension (IPC)               Human
  │                               │                              │                           │
  │ ck3_contract(                 │                              │                           │
  │   command="sign_script",      │                              │                           │
  │   script_path=                │                              │                           │
  │     "root:ck3raven_data/      │                              │                           │
  │      wip/analyze.py",         │                              │                           │
  │   user_reviewed=True)         │                              │                           │
  │ ──────────────────────────────>                              │                           │
  │                               │ 1. WA2 resolve script_path   │                           │
  │                               │ 2. Validate under wip/       │                           │
  │                               │ 3. Compute content_sha256    │                           │
  │                               │                              │                           │
  │                               │ IPC: openScriptForReview     │                           │
  │                               │ ────────────────────────────>│                           │
  │                               │                              │ 4. Open script in editor  │
  │                               │                              │ 5. Register CodeLens:     │
  │                               │                              │    [✓ Approve] [✗ Reject] │
  │                               │                              │                    ──────>│
  │                               │                              │                           │ 6. Reviews
  │                               │                              │                           │ 7. Clicks Approve
  │                               │                              │<──────────────────────────│
  │                               │                              │ 8. sigil_sign(payload)    │
  │                               │                              │ 9. Store result in memory │
  │                               │                              │ 10. Remove CodeLens       │
  │                               │                              │                           │
  │                               │ IPC: getScriptSignApproval   │                           │
  │                               │ (poll every 2s, max 5 min)   │                           │
  │                               │ ────────────────────────────>│                           │
  │                               │<────────────────────────────│                           │
  │                               │ {approved:true, sig, sha}    │                           │
  │                               │                              │                           │
  │<──────────────────────────────│                              │                           │
  │ Reply(code="CT-SIGN-S-001",  │                              │                           │
  │   data={                      │                              │                           │
  │     script_path:              │                              │                           │
  │       "root:ck3raven_data/...",                              │                           │
  │     content_sha256: "...",    │                              │                           │
  │     signature: "...",         │                              │                           │
  │     signed_at: "..."          │                              │                           │
  │   })                          │                              │                           │
  │                               │                              │                           │
  │ ck3_contract(                 │                              │                           │
  │   command="open",             │                              │                           │
  │   script_signature={...})     │                              │                           │
```

## Flow: `sign_script` with `user_reviewed=False`

MCP server sends IPC `openScriptForReview` (opens file + CodeLens), then returns immediately:

```
Reply(code="CT-SIGN-I-001",
  message="Script opened for review. It is recommended that you review
  the contents with the user before seeking approval, since the sign_script
  tool will wait for up to 5 minutes before timing out. If the user has
  never seen the script before, this is likely not enough time — especially
  with your chat window paused. Call sign_script again with
  user_reviewed=True when ready.")
```

## Flow: `review_script` (standalone)

Same as `sign_script(user_reviewed=False)` — opens file with CodeLens via IPC, returns immediately. No polling.

```
Reply(code="CT-REVIEW-S-001",
  message="Script opened in VS Code for review.")
```

---

## MCP Parameters

### `ck3_contract(command="sign_script")`

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `script_path` | `str` | yes | Canonical: `root:ck3raven_data/wip/...` |
| `user_reviewed` | `bool` | yes | Has user already reviewed the script? |

### `ck3_contract(command="review_script")`

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `script_path` | `str` | yes | Canonical: `root:ck3raven_data/wip/...` |

### `ck3_contract(command="open")` — existing, new optional param

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `script_signature` | `dict` | no | Signature dict from prior `sign_script` call |

When provided, set on `contract.script_signature` before `save_contract()`. The contract's Sigil auto-signature then covers this field.

---

## Payload Format

```
script:{host_abs_path}|{content_sha256}
```

No contract ID in the payload. The contract's own Sigil auto-signature covers the `script_signature` field after it is attached at `open` time. Any modification to the contract (including tampering with `script_signature`) invalidates the contract's session signature.

**Files affected by payload format change:**
- `capability_matrix_v2.py` — exec_gate Branch 2
- `contract_v1.py` — payload convention comment
- `sigil.py` — payload convention comment

---

## Extension Side

### IPC Handlers (added to `diagnosticsServer.ts`)

**`openScriptForReview(params: {path: string, content_sha256: string})`**
- Opens script file in VS Code editor via `vscode.workspace.openTextDocument` + `vscode.window.showTextDocument`
- Registers a `CodeLensProvider` for that document showing at line 1:
  - **✓ Approve Script** — command: `ck3lens.scriptSign.approve`
  - **✗ Reject Script** — command: `ck3lens.scriptSign.reject`
- Stores pending request in memory: `{path, content_sha256, status: "pending"}`
- Returns `{opened: true}`

**`getScriptSignApproval(params: {path: string})`**
- Looks up pending request by path
- If pending: returns `{pending: true}`
- If approved: returns `{approved: true, signature: "...", content_sha256: "...", signed_at: "..."}`
- If rejected: returns `{approved: false}`
- If no request found: returns `{error: "No pending request"}`

**`cancelScriptSign(params: {path: string})`**
- Disposes CodeLens provider for that file
- Removes pending request
- Returns `{cancelled: true}`

### CodeLens Commands

On **Approve** click:
1. Read Sigil secret via `getSigilSecret()`
2. `crypto.createHmac('sha256', Buffer.from(sigilSecret, 'hex')).update("script:{path}|{sha256}").digest('hex')`
3. Update pending request to `{status: "approved", signature, signed_at}`
4. Dispose CodeLens provider

On **Reject** click:
1. Update pending request to `{status: "rejected"}`
2. Dispose CodeLens provider

### getSigilSecret Access

`DiagnosticsServer` receives `getSigilSecret` via constructor parameter or setter, passed from `extension.ts` where the Sigil secret is already available (same closure used by `AgentViewProvider`).

---

## Reply Codes

| Code | Type | Meaning |
|------|------|---------|
| `CT-SIGN-S-001` | Success | Script signed, signature in `data` |
| `CT-SIGN-D-001` | Denied | Timeout — no approval within 5 minutes |
| `CT-SIGN-D-002` | Denied | User rejected the script |
| `CT-SIGN-I-001` | Info | Script opened for review (`user_reviewed=False`) |
| `CT-REVIEW-S-001` | Success | Script opened for review (standalone) |

---

## exec_gate Branch 2 Change

Only the payload format changes:

**Old:** `f"script:{contract.contract_id}|{script_host_path}|{content_sha256}"`
**New:** `f"script:{script_host_path}|{content_sha256}"`

Branch 2 reads `contract.script_signature`, compares stored path/hash against the script being executed, calls `sigil_verify()` with the new payload. Logic unchanged.

---

## File Inventory

| File | Change | What |
|------|--------|------|
| `docs/Canonical address refactor/Script_Signing_Spec.md` | **new** | This document |
| `tools/ck3lens_mcp/server.py` | modify | `sign_script` + `review_script` subcommands, `script_signature` on `open` |
| `tools/ck3lens_mcp/ck3lens/policy/contract_v1.py` | modify | `open_contract()` accepts `script_signature`; payload comment |
| `tools/ck3lens_mcp/ck3lens/capability_matrix_v2.py` | modify | Payload format in exec_gate Branch 2 |
| `tools/ck3lens_mcp/ck3lens/ipc_client.py` | modify | Add `open_script_for_review()`, `get_script_sign_approval()`, `cancel_script_sign()` |
| `tools/compliance/sigil.py` | modify | Payload convention comment |
| `tools/ck3lens-explorer/src/ipc/diagnosticsServer.ts` | modify | IPC handlers + CodeLens provider + Sigil signing |
| `tools/ck3lens-explorer/src/extension.ts` | modify | Pass `getSigilSecret` to `DiagnosticsServer` |

## What Is NOT Changing

- exec_gate Branch 2 logic (just payload format)
- Contract auto-signing
- `save_contract()` / `load_contract()` serialization
- `tokens.py`
- HAT
