"""
Enforcement v2 — THE single gate for all operation decisions.

Canonical Architecture Rule 1: only this module may deny operations.

Single path for ALL operations (reads AND mutations):
  1. find_operation_rule(mode, root_key, subdirectory, tool, command) -> rule or None
  2. None -> DENY (no rule governs this operation at this location)
  3. Rule found -> check conditions with **context -> all pass? ALLOW : DENY

Returns Reply (the canonical type), never a parallel concept.
Denial codes are produced HERE, not by conditions. Conditions just return True/False.

Logging: canonical JSONL to ~/.ck3raven/logs/ck3raven-mcp.log
  Category: policy.enforce
  Every enforcement decision is logged at INFO level with full context.

Design brief: docs/Canonical address refactor/v2_enforcement_design_brief.md
"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path
    from ck3lens.world_adapter_v2 import VisibilityRef, WorldAdapterV2

from ck3raven.core.reply import Reply
from ..capability_matrix_v2 import find_operation_rule


# --- Condition → denial code mapping ---
# When a condition fails, map it to a specific EN-GATE-D-XXX code.
# D-001 is fallback for unmapped conditions or "no rule found".
CONDITION_DENIAL_CODES: dict[str, str] = {
    "has_contract": "EN-GATE-D-002",
    "exec_gate": "EN-GATE-D-003",
    "scope_to_session_mods": "EN-GATE-D-001",
}


def enforce(
    rb: Any,           # ReplyBuilder -- passed from tool handler
    mode: str,
    tool: str,
    command: str,
    root_key: str,
    subdirectory: str | None,
    **context: Any,
) -> Reply:
    """
    Single enforcement entry point.

    Walks the operations matrix — one path, no bypass branches.
    Finds the matching rule for (tool, command) at (mode, root_key, subdirectory),
    evaluates its conditions, and returns allow or deny.

    Args:
        rb: ReplyBuilder from the tool handler (constructs Reply with trace/meta)
        mode: Agent mode ("ck3lens", "ck3raven-dev")
        tool: Tool name (e.g. "ck3_exec", "ck3_file", "ck3_git")
        command: Command/subcommand (e.g. "write", "delete", "commit", or shell string for exec)
        root_key: v2 root key ("repo", "game", "steam", etc.)
        subdirectory: First path component for sub-gating, or None
        **context: Forwarded to condition predicates (e.g. has_contract, script_host_path)

    Returns:
        Reply -- success or denied.
    """
    from ck3lens.logging import info

    # --- Auto-populate has_contract from actual contract state ---
    # Enforcement owns this check — callers don't need to pass it
    if "has_contract" not in context:
        from ck3lens.policy.contract_v1 import get_active_contract
        context["has_contract"] = get_active_contract() is not None

    # --- Log entry ---
    # --- Log entry ---
    # Redact large/sensitive context values for logging
    log_context = {}
    for k, v in context.items():
        if isinstance(v, str) and len(v) > 200:
            log_context[k] = v[:200] + "..."
        else:
            log_context[k] = repr(v)

    info("policy.enforce", "Enforcement entered",
         tool=tool, command=command, mode=mode,
         root_key=root_key, subdirectory=subdirectory,
         context_keys=list(context.keys()), context_summary=log_context)

    # --- 1. Find the operation rule governing this (tool, command) at this location ---
    rule = find_operation_rule(mode, root_key, subdirectory, tool, command)

    if rule is None:
        info("policy.enforce", "DENY: no rule found",
             tool=tool, command=command, mode=mode,
             root_key=root_key, subdirectory=subdirectory,
             code="EN-GATE-D-001")
        return rb.denied("EN-GATE-D-001", {
            "detail": f"No operation rule for ({tool}, {command}) at ({mode}, {root_key}, {subdirectory})",
        })

    # --- Log the matched rule ---
    condition_names = [c.name for c in rule.conditions]
    info("policy.enforce", "Rule matched",
         tool=tool, command=command, mode=mode,
         root_key=root_key, subdirectory=subdirectory,
         condition_count=len(rule.conditions),
         condition_names=condition_names)

    # --- 2. Check conditions — all must pass ---
    if not rule.conditions:
        # No conditions = operation allowed (e.g. reads, unconditioned writes)
        info("policy.enforce", "ALLOW: no conditions on rule",
             tool=tool, command=command, mode=mode,
             root_key=root_key, subdirectory=subdirectory,
             code="EN-GATE-S-001")
        return rb.success("EN-GATE-S-001", {})

    # Evaluate each condition, log individual results
    results = {}
    failed = []
    ctx: dict = {}  # Mutable dict — shared with condition check() for side effects
    # Add root_key and command to context for conditions that need them (e.g., has_contract)
    context["root_key"] = root_key
    context["command"] = command
    for c in rule.conditions:
        passed = c.check(ctx=ctx, **context)
        results[c.name] = passed
        if not passed:
            failed.append(c.name)

    info("policy.enforce", "Conditions evaluated",
         tool=tool, command=command, mode=mode,
         root_key=root_key, subdirectory=subdirectory,
         condition_results=results)

    if failed:
        # Use first failed condition's specific code, or D-001 fallback
        denial_code = CONDITION_DENIAL_CODES.get(failed[0], "EN-GATE-D-001")
        info("policy.enforce", "DENY: conditions failed",
             tool=tool, command=command, mode=mode,
             root_key=root_key, subdirectory=subdirectory,
             failed_conditions=failed, code=denial_code)
        # Build denial data, then merge any _denial_meta deposited by conditions
        denial_data: dict = {
            "detail": f"Conditions not met: {failed}",
            "failed_conditions": failed,
            "tool": tool,
            "command": command,
        }
        if "_denial_meta" in ctx:
            denial_data.update(ctx["_denial_meta"])
        return rb.denied(denial_code, denial_data)

    info("policy.enforce", "ALLOW: all conditions passed",
         tool=tool, command=command, mode=mode,
         root_key=root_key, subdirectory=subdirectory,
         code="EN-GATE-S-001")
    return rb.success("EN-GATE-S-001", {})


def execute(
    rb: Any,
    mode: str,
    tool: str,
    command: str,
    root_key: str,
    subdirectory: str | None,
    ref: "VisibilityRef",
    wa2: "WorldAdapterV2",
    params: dict[str, Any],
    **policy_context: Any,
) -> "Reply":
    """
    Combined enforcement + execution entry point.
    
    1. Walks capability matrix (policy check)
    2. If denied → return D
    3. If allowed → extract host_paths from VisibilityRef, dispatch to executor → return S/I/E
    
    Args:
        rb: ReplyBuilder
        mode: Agent mode
        tool: Tool name
        command: Command name
        root_key: v2 root key
        subdirectory: Sub-path for gating
        ref: VisibilityRef (opaque token — host paths extracted here, not by caller)
        wa2: WorldAdapterV2 instance (for token→path resolution)
        params: Operation-specific parameters (forwarded to executor)
        **policy_context: Forwarded to condition predicates
        
    Returns:
        Reply — always terminal (D, S, I, or E)
    """
    from pathlib import Path
    from ck3lens.logging import info
    from ck3lens.policy.executors import EXECUTORS

    # --- Auto-populate has_contract from actual contract state ---
    if "has_contract" not in policy_context:
        from ck3lens.policy.contract_v1 import get_active_contract
        policy_context["has_contract"] = get_active_contract() is not None

    # Add root_key and command to context for conditions
    policy_context["root_key"] = root_key
    policy_context["command"] = command

    # Extract paths ONLY for policy condition evaluation (e.g. path_in_session_mods)
    # Executors extract their own paths from ref at runtime
    if "host_abs" not in policy_context:
        host_paths = wa2.host_path(ref)
        if host_paths:
            policy_context["host_abs"] = host_paths[0] if len(host_paths) == 1 else host_paths

    # --- 1. Policy check (find and evaluate rule) ---
    rule = find_operation_rule(mode, root_key, subdirectory, tool, command)

    if rule is None:
        info("policy.execute", "DENY: no rule found",
             tool=tool, command=command, mode=mode,
             root_key=root_key, subdirectory=subdirectory,
             code="EN-GATE-D-001")
        return rb.denied("EN-GATE-D-001", {
            "detail": f"No operation rule for ({tool}, {command}) at ({mode}, {root_key}, {subdirectory})",
        })

    # Evaluate conditions
    ctx: dict = {}
    failed = []
    for c in rule.conditions:
        if not c.check(ctx=ctx, **policy_context):
            failed.append(c.name)

    if failed:
        # Use first failed condition's specific code, or D-001 fallback
        denial_code = CONDITION_DENIAL_CODES.get(failed[0], "EN-GATE-D-001")
        info("policy.execute", "DENY: conditions failed",
             tool=tool, command=command, mode=mode, failed_conditions=failed, code=denial_code)
        denial_data: dict = {
            "detail": f"Conditions not met: {failed}",
            "failed_conditions": failed,
        }
        if "_denial_meta" in ctx:
            denial_data.update(ctx["_denial_meta"])
        return rb.denied(denial_code, denial_data)

    # --- 2. Dispatch to executor ---
    executor = EXECUTORS.get((tool, command))
    if executor is None:
        return rb.error("EN-EXEC-E-001", {
            "error": f"No executor registered for ({tool}, {command})",
        })

    info("policy.execute", "ALLOW: executing",
         tool=tool, command=command, mode=mode)
    
    return executor(rb, ref=ref, wa2=wa2, **params)

def execute_db(
    rb: Any,
    mode: str,
    tool: str,
    command: str,
    params: dict[str, Any],
    **policy_context: Any,
) -> "Reply":
    """
    Combined enforcement + execution for database operations.

    Uses DATABASE_OPERATIONS_MATRIX instead of OPERATIONS_MATRIX.
    No host_path — scoping is via session.cvids, which executors read
    directly from the session object.

    scope_to_session_mods is a declarative condition on the matrix:
    bool(session.cvids). If zero cvids are resolved, EN denies. If
    cvids are present, the condition passes and the executor uses
    session.cvids as the DB filter.

    1. Walks DATABASE_OPERATIONS_MATRIX (policy check — all conditions)
    2. If denied → return D
    3. If allowed → dispatch to executor with policy_ctx → return S/I/E

    Args:
        rb: ReplyBuilder
        mode: Agent mode
        tool: Tool name
        command: Command name
        params: Operation-specific parameters (forwarded to executor)
        **policy_context: Forwarded to condition predicates

    Returns:
        Reply — always terminal (D, S, I, or E)
    """
    from ck3lens.logging import info
    from ck3lens.policy.executors import EXECUTORS
    from ck3lens.capability_matrix_v2 import find_database_rule

    # --- 1. Find the database rule ---
    rule = find_database_rule(mode, tool, command)

    if rule is None:
        info("policy.execute_db", "DENY: no database rule found",
             tool=tool, command=command, mode=mode,
             code="EN-GATE-D-001")
        return rb.denied("EN-GATE-D-001", {
            "detail": f"No database rule for ({tool}, {command}) in mode {mode}",
        })

    # --- Evaluate conditions ---
    ctx: dict = {}
    failed = []
    for c in rule.conditions:
        if not c.check(ctx=ctx, **policy_context):
            failed.append(c.name)

    if failed:
        denial_code = CONDITION_DENIAL_CODES.get(failed[0], "EN-GATE-D-001")
        info("policy.execute_db", "DENY: conditions failed",
             tool=tool, command=command, mode=mode,
             failed_conditions=failed, code=denial_code)
        return rb.denied(denial_code, {
            "detail": f"Conditions not met: {failed}",
            "failed_conditions": failed,
        })

    # --- 2. Dispatch to executor with policy_ctx ---
    executor = EXECUTORS.get((tool, command))
    if executor is None:
        return rb.error("EN-EXEC-E-001", {
            "error": f"No executor registered for ({tool}, {command})",
        })

    info("policy.execute_db", "ALLOW: executing",
         tool=tool, command=command, mode=mode)

    params["policy_ctx"] = ctx
    return executor(rb, **params)

