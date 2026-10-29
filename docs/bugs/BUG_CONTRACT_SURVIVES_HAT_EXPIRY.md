# BUG: Contracts Survive HAT Token Expiry — Sigil Validation Bypassed

## Severity: High (authorization model breach)

## Summary
When a HAT token expires and a new one is issued via `ck3_get_mode_instructions`, contracts created under the old HAT remain active and usable. The enforcement layer does not invalidate them or verify the signing sigil matches the current HAT. This allows writes to succeed under a contract the current human-authorization session never approved.

## Reproduction Steps
1. Initialize agent mode with HAT token A (session 1)
2. Create a contract: `ck3_contract(action="propose", mod_name="X", files=[...], description="intent A")`
3. Contract `v1-2026-02-22-06972a` is created, presumably signed under HAT A's sigil
4. HAT A expires (session ends/times out)
5. User clicks "Initialize Agent" again, generating HAT token B
6. Agent calls `ck3_get_mode_instructions(hat_token=B)` — new session established
7. Agent calls `ck3_file(command="edit", mod_name="X", ...)` — **the old contract is still active**
8. **Result:** Write succeeds. No sigil mismatch error. No re-authorization required.
9. **Expected:** Write should fail. Contract was signed under expired HAT A. HAT B never authorized it.

## Actual Observed Behavior
- `ck3_contract(action="propose")` returned the OLD contract as "Active contract" rather than rejecting it or requiring a new one
- `ck3_file(command="edit")` completed successfully using that stale contract
- The contract's stated intent ("Remove enabled = yes parse errors from 4 MSC DNA files") did not match the actual edit (removing orphaned brace block from `zzz_msc_core_tenets.txt`) — intent was not enforced either

## Two Distinct Flaws

### Flaw 1: Contracts outlive their signing HAT
Contracts should be invalidated when the HAT that signed them expires. A new HAT represents a new human-authorization session. Carrying forward old contracts means the human is implicitly approving work they may not have reviewed.

### Flaw 2: Intent/file scope not enforced at write time
The contract declared specific files and a specific intent. At write time, enforcement only checked:
- Mod name matches
- Operation type (WRITE) matches
- File list NOT checked (edited a file not in the contract's file list)
- Intent NOT checked (performed unrelated edit)

## Impact
- **Authorization bypass:** Agent can accumulate contracts in permissive sessions and reuse them across HAT rotations without re-authorization
- **Scope creep:** A narrowly-scoped contract can be used for any write to the same mod
- **Audit trail broken:** Contract intent doesn't reflect actual changes made

## Expected Behavior
1. `ck3_get_mode_instructions` with a new HAT should invalidate all contracts signed under previous HATs
2. `ck3_contract(action="propose")` should reject or warn about stale contracts from expired sessions
3. `ck3_file` enforcement should verify the target file is in the contract's declared file list
4. Intent description should be logged against actual operations for audit

## Environment
- Date: 2026-02-22
- Stale Contract ID: `v1-2026-02-22-06972a`
- Original intent: "Remove enabled = yes parse errors from 4 MSC DNA files"
- Actual operation: Removed 22-line orphaned block from zzz_msc_core_tenets.txt
- Mode: ck3lens
- Tool versions: as of session c1106b5c572049f1
