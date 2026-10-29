# Phase 2 Deterministic Gates — Readiness Assessment

> **Date:** March 25, 2026  
> **Assessor:** Agent (Copilot session)  
> **Context:** ck3raven-dev mode  
> **Purpose:** Determine readiness for Phase 2 deterministic gates implementation

---

## Executive Summary

**Phase 2 cannot start until P0 pre-work items are complete.** The audit pipeline (`audit_contract_close.py`) has broken imports from the deprecated `symbols_lock.py` module and will crash at runtime. Gate files (`contract_gates.py`, `close_gates.py`) do not exist and must be created as part of Phase 2.2+.

| Category | Status |
|----------|--------|
| Phase 1 deliverables | 8/8 ✅ Complete |
| Phase 1.5 deliverables | 5/7 (2 broken/missing) |
| Phase 2 prerequisites | 0/6 ❌ Not met |
| Architecture conflicts | 7 identified |
| Pre-work items | 6 (2 P0, 4 P1) |
| Decision points | 3 pending |

---

## Critical Finding: Broken Audit Pipeline

`audit_contract_close.py` imports 4 functions that no longer exist in `symbols_lock.py`:

| Line | Import | Function That Uses It |
|------|--------|----------------------|
| 408 | `get_active_playset_identity()` | `check_playset_drift()` |
| 462 | `SymbolsSnapshot` | `check_symbols_diff()` |
| 464 | `_resolve_snapshot_path()` | `check_symbols_diff()` |
| 837 | `get_active_playset_identity()` | (duplicate reference) |

**Impact:** `check_playset_drift()` and `check_symbols_diff()` will **crash at runtime** if invoked.

---

## Pre-Research Findings (Verified March 25, 2026)

### capability_matrix v1
- **Status:** ✅ DELETED — `capability_matrix.py` (v1) does not exist in active codebase
- **Current:** Only `capability_matrix_v2.py` exists, imported by `enforcement_v2.py`, `world_adapter_v2.py`, `dir_ops.py`
- **Action:** None required — v1 cleanup already complete

### symbols_lock
- **Status:** ✅ DEPRECATED with documented replacement
- **Documentation:** `symbols_lock.py` header states: "Migration path: Use semantic_validator.py with git-diff file list instead."
- **CANONICAL CONTRACT SYSTEM v2.md** confirms: "The semantic validator provides this guarantee more effectively by analyzing changed files identified through git diff."
- **Action:** Fix broken imports in `audit_contract_close.py`

### baseline_sha vs base_commit
- **Discovery:** Phase 2 plan references `baseline_sha` but ContractV1 uses `base_commit: Optional[str]`
- **Same purpose:** Stores `git rev-parse HEAD` at contract open for later `git diff`
- **Action:** Decision point D1 — update docs or code for consistency

---

## Phase Deliverables Checklist

### Phase 1 (Contract Law)

| Deliverable | Status | Evidence |
|-------------|--------|----------|
| Contract schema (ContractV1) | ✅ Complete | `contract_v1.py` |
| Root categories enum | ✅ Complete | `RootCategory` in paths.py |
| Capability matrix | ✅ Complete | `capability_matrix_v2.py` |
| Forbidden concepts enforcement | ✅ Complete | enforcement_v2.py |
| Decision model (Reply S/D only) | ✅ Complete | Reply system |
| Sigil signing primitive | ✅ Complete | `sigil.py` |
| Protected files mechanism | ✅ Complete | `protected_files.py` |
| Token architecture (HAT/NST/LXE) | ✅ Complete | `tokens.py` |

### Phase 1.5 (Deterministic Evidence)

| Deliverable | Status | Evidence |
|-------------|--------|----------|
| Linter lock system | ✅ Complete | `linter_lock.py` |
| Locked lint runner | ✅ Complete | `run_arch_lint_locked.py` |
| Semantic validator | ✅ Complete | `semantic_validator.py` |
| Token lifecycle (propose/approve) | ✅ Complete | `tokens.py` |
| Contract close audit pipeline | ⚠️ **BROKEN** | `audit_contract_close.py` has dead imports |
| Pre-commit hook script | ✅ Exists | `install_hooks.py` |
| Pre-commit hook installed | ❌ Missing | `.git/hooks/pre-commit` not installed |

### Phase 2 Prerequisites (from CANONICAL_CONTRACT_LAW.md)

| Prerequisite | Status | Evidence |
|--------------|--------|----------|
| Capability matrix aligned with code | ✅ Complete | v2 only exists |
| Audit pipeline uses semantic_validator | ❌ **BROKEN** | Still imports deprecated symbols_lock |
| Pre-commit hook installed | ❌ Missing | Only .sample files in .git/hooks/ |
| contract_gates.py created | ❌ Missing | File does not exist |
| close_gates.py created | ❌ Missing | File does not exist |
| Test infrastructure | ❌ Missing | No test_contract_*.py files |

---

## Architecture Conflict Register

| # | Plan Assumption | Current Reality | Impact | Resolution |
|---|-----------------|-----------------|--------|------------|
| 1 | Field named `baseline_sha` | Field named `base_commit` | Low | Update docs to use `base_commit` |
| 2 | `enforce()` in `enforcement.py` | `enforce()` in `enforcement_v2.py` | Medium | Update all plan references to v2 |
| 3 | symbols_lock provides snapshots | symbols_lock is deprecated stubs | **HIGH** | Fix audit_contract_close.py imports |
| 4 | `contract_gates.py` exists | Does not exist | **HIGH** | Create as Phase 2.2 work |
| 5 | `close_gates.py` exists | Does not exist | **HIGH** | Create as Phase 2.4 work |
| 6 | Test files exist | None exist | Medium | Create as Phase 2.5 work |
| 7 | Pre-commit hook installed | Not installed | Medium | Run `install_hooks.py` |

---

## Pre-Work Checklist (Before Phase 2 Can Start)

| Priority | Task | Effort | Assignee |
|----------|------|--------|----------|
| **P0** | Fix `audit_contract_close.py` broken imports (4 locations) | 2-3 hours | Agent |
| **P0** | Migrate `check_symbols_diff()` to use `semantic_validator.py` | 2 hours | Agent |
| **P1** | Migrate `check_playset_drift()` to use session playset hash | 1 hour | Agent |
| **P1** | Install pre-commit hook: `python tools/compliance/install_hooks.py` | 5 min | Human |
| **P1** | Update Phase 2 plan: `baseline_sha` → `base_commit` | 30 min | Agent |
| **P1** | Update Phase 2 plan: `enforcement.py` → `enforcement_v2.py` | 30 min | Agent |

---

## Decision Points (Require User Input)

### D1: baseline_sha / base_commit Naming

**Context:** `ContractV1` already has `base_commit: Optional[str]` which stores the git commit SHA at contract open. The Phase 2 plan calls this `baseline_sha`.

**Question:** Is `base_commit` sufficient, or should we rename for doc consistency?

**Options:**
- (A) Keep `base_commit` — update docs to match code ← **Recommended**
- (B) Rename to `baseline_sha` — update code to match docs
- (C) Remove entirely — derive from git log (adds complexity)

### D2: symbols_lock Migration Strategy

**Context:** `audit_contract_close.py` calls 4 functions from `symbols_lock.py` that no longer exist. Two audit checks are broken: `check_playset_drift()` and `check_symbols_diff()`.

**Proposed approach:**
- `check_symbols_diff()` → Use `semantic_validator.validate_files()` on git-diff file list
- `check_playset_drift()` → Use `Session.playset_hash` or similar from workspace.py

**Question:** Do you approve this migration approach?

### D3: Phase 2 Scope Confirmation

**Context:** Phase 2 has 5 sub-phases (2.1-2.5). Phase 2.1 was reported 80% complete in Feb 2026.

**Question:** Should we verify Phase 2.1 completion status before starting new Phase 2 work, or proceed directly to fixing pre-work items?

---

## Compliance Tools Inventory

| File | Purpose | Status | Phase |
|------|---------|--------|-------|
| `audit_contract_close.py` | Contract close audit (6 checks) | ⚠️ BROKEN (dead imports) | 1.5 |
| `linter_lock.py` | Hash-lock arch_lint ruleset | ✅ Active | 1.5 |
| `run_arch_lint_locked.py` | Locked lint with watermarking | ✅ Active | 1.5 |
| `semantic_validator.py` | Symbol evidence generator | ✅ Active | 1.5 |
| `tokens.py` | NST/LXE/MIT/HAT lifecycle | ✅ Active | 1.5 |
| `protected_files.py` | HAT-gated file protection | ✅ Active | 1.5 |
| `sigil.py` | HMAC-SHA256 signing | ✅ Active | Core |
| `tasl.py` | Tool architecture linter | ✅ Active | Core |
| `symbols_lock.py` | Playset snapshots | ❌ DEPRECATED (stubs) | Legacy |
| `install_hooks.py` | Pre-commit hook installer | ✅ Active | 1.5 |

---

## Contract System Components (policy/ directory)

### Files Present
```
agent_policy.yaml
audit.py
contract_v1.py
enforcement_v2.py
executors.py
loader.py
paths.py
python_inline_scanner.py
trace_helpers.py
types.py
wip_workspace.py
__init__.py
```

### Files Missing (Phase 2 deliverables)
- `contract_gates.py` — ❌ To be created in Phase 2.2
- `close_gates.py` — ❌ To be created in Phase 2.4

### ContractV1 Dataclass Fields

**Verified fields:**
- `contract_id: str`
- `mode: AgentMode | str`
- `root_category: RootCategory | str`
- `intent: str`
- `operations: list[Operation | str]`
- `targets: list[ContractTarget]`
- `work_declaration: WorkDeclaration`
- `created_at: str`
- `author: str`
- `expires_at: Optional[str]`
- `status: Literal["open", "closed", "expired", "cancelled"]`
- `closed_at: Optional[str]`
- `notes: Optional[str]`
- `schema_version: str`
- `base_commit: Optional[str]` ← Git commit SHA (this is what Phase 2 plan calls `baseline_sha`)
- `session_signature: Optional[str]`
- `script_signature: Optional[dict]`
- `script_path: Optional[str]`

### enforcement_v2.py Interface

```python
def enforce(
    rb: Any,                           # ReplyBuilder
    mode: str,                         # "ck3lens" or "ck3raven-dev"
    tool: str,                         # Tool name
    command: str,                      # Command/subcommand
    root_key: str,                     # v2 root key
    subdirectory: str | None,          # First path component
    **context: Any,                    # Context for conditions
) -> Reply:
```

---

## Test Infrastructure Status

| Expected File | Exists? |
|---------------|---------|
| `tests/test_contract_gates.py` | ❌ NO |
| `tests/test_enforcement_integration.py` | ❌ NO |
| `tests/test_contract_lifecycle.py` | ❌ NO |

**Note:** These are Phase 2.5 deliverables.

---

## Pre-commit Hook Status

| Item | Status |
|------|--------|
| Hook script exists | ✅ `tools/compliance/install_hooks.py` |
| Hook installed in `.git/hooks/` | ❌ Not installed |

**Installation:** `python tools/compliance/install_hooks.py`

---

## Appendix: Key File References

| Purpose | Path |
|---------|------|
| Phase 2 Plan | `docs/phase2contracts/PHASE_2_DETERMINISTIC_GATES_PLAN.md` |
| Feb 2026 Assessment | `docs/phase2contracts/PHASE_2_READINESS_ASSESSMENT.md` |
| Feb 2026 Audit | `docs/phase2contracts/PHASE_2_AUDIT_REPORT_2026-02-13.md` |
| Canonical Contract System | `docs/CANONICAL CONTRACT SYSTEM v2.md` |
| Enforcement (v2) | `tools/ck3lens_mcp/ck3lens/policy/enforcement_v2.py` |
| Capability Matrix (v2) | `tools/ck3lens_mcp/ck3lens/capability_matrix_v2.py` |
| Contract Lifecycle | `tools/ck3lens_mcp/ck3lens/policy/contract_v1.py` |
| Audit Pipeline | `tools/compliance/audit_contract_close.py` |
| Semantic Validator | `tools/compliance/semantic_validator.py` |
| Deprecated Symbols Lock | `tools/compliance/symbols_lock.py` |

---

*End of Phase 2 Readiness Assessment — March 25, 2026*
