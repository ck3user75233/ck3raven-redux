"""
Capability Matrix v2 — pure data, maximum transparency.

Two matrices, both with conditions:

1. VISIBILITY_MATRIX — can this mode see this (root_key, subdirectory)?
   Consulted by WA v2 on every resolve(). Entry exists = visible, subject to conditions.
   No entry = not visible. Conditions are evaluated with context from WA.

2. OPERATIONS_MATRIX — what operations are allowed at this (root_key, subdirectory)?
   Each rule carries the exact (tool, command) pairs it governs, plus conditions.
   No entry = operation denied. Enforcement finds the matching rule and checks conditions.
   Reads are rules with no conditions (always allowed if visible).
   Mutations carry conditions (e.g. has_contract, exec_gate).

Plus:

- Conditions — standalone named predicates returning True/False, built via factories.
  No denial codes. WA and EN produce their own response codes.
  Modular: swap/add conditions without touching WA or EN.
  Session-level: conditions check session.mods, not host paths.

Enforcement (enforcement_v2.py) walks OPERATIONS_MATRIX for ALL operations (reads and mutations).
WA (world_adapter_v2.py) walks VISIBILITY_MATRIX.
This module is data only (conditions may call sigil_verify for cryptographic checks).

Design brief: docs/Canonical address refactor/v2_enforcement_design_brief.md
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable


# =============================================================================
# CONDITION — frozen named predicate: check(**context) -> bool
# =============================================================================

@dataclass(frozen=True)
class Condition:
    """A named predicate: check(**context) -> bool."""
    name: str
    check: Callable[..., bool]


# =============================================================================
# CONDITION FACTORIES
#
# Each factory returns a Condition instance. Conditions are pure bool predicates.
# They receive context kwargs at evaluation time. No side effects, no denial codes.
# WA and EN own their own response codes.
# =============================================================================

def mod_in_session() -> Condition:
    """True if the addressed mod is in session.mods.

    Pure session-level check — no host path containment.
    WA passes mod_name (display name) from the mod: address.
    Checked via session.get_mod(mod_name).

    Context keys:
        session: Session | None — the session object (has .mods, .get_mod())
        mod_name: str | None — mod display name (from mod: address)
    """
    def _check(
        *,
        session: object | None = None,
        mod_name: str | None = None,
        **_: object,
    ) -> bool:
        if session is None or not mod_name:
            return False
        mods = getattr(session, 'mods', None)
        if not mods:
            return False

        get_mod = getattr(session, 'get_mod', None)
        if get_mod:
            return get_mod(mod_name) is not None
        return any(getattr(m, 'name', None) == mod_name for m in mods)

    return Condition(name="mod_in_session", check=_check)


def has_contract() -> Condition:
    """True if active contract covers the requested operation.

    Validates:
    1. Contract exists and is not expired
    2. Contract root_category matches the target root_key
    3. Contract operations include the requested command type

    Context keys (auto-populated by enforcement_v2):
        has_contract: bool — whether get_active_contract() returned non-None
        root_key: str — the v2 root key (e.g., "repo", "ck3raven_data")
        command: str — the command being executed (e.g., "write", "edit", "delete")
    """
    # Mapping from root_key (v2) to RootCategory (v1)
    ROOT_KEY_TO_CATEGORY = {
        "repo": "ROOT_REPO",
        "ck3chronicle": "ROOT_CK3CHRONICLE_REPO",
        "ck3raven_data": "ROOT_CK3RAVEN_DATA",
        "game": "ROOT_GAME",
        "steam": "ROOT_STEAM",
        "user_docs": "ROOT_USER_DOCS",
    }

    # Mapping from command to operation type
    # NOTE: delete maps to WRITE, not DELETE. A write-scoped contract covers
    # all mutations including deletes. This matches the capability matrix where
    # FILE_WRITE_COMMANDS and FILE_DELETE_COMMANDS share the same rule conditions.
    COMMAND_TO_OPERATION = {
        "read": "READ",
        "write": "WRITE",
        "edit": "WRITE",  # edit is a write operation
        "delete": "WRITE",  # delete is a mutation — WRITE contract covers it
        "rename": "WRITE",
        "list": "READ",
        "index": "READ",
    }

    def _check(
        *,
        ctx: dict | None = None,
        has_contract: bool = False,
        root_key: str = "",
        command: str = "",
        **_: object,
    ) -> bool:
        if not has_contract:
            if ctx is not None:
                ctx["_denial_meta"] = {"contract_denial_reason": "no_active_contract"}
            return False

        # Get actual contract to validate root_category and operations
        from ck3lens.policy.contract_v1 import get_active_contract
        contract = get_active_contract()
        if contract is None:
            if ctx is not None:
                ctx["_denial_meta"] = {"contract_denial_reason": "contract_disappeared"}
            return False

        # Check root_category matches
        expected_category = ROOT_KEY_TO_CATEGORY.get(root_key)
        if expected_category is None:
            if ctx is not None:
                ctx["_denial_meta"] = {
                    "contract_denial_reason": "unknown_root_key",
                    "root_key": root_key,
                }
            return False

        from ck3lens.policy.contract_v1 import RootCategory
        contract_category = (
            contract.root_category.value
            if isinstance(contract.root_category, RootCategory)
            else str(contract.root_category)
        )
        if contract_category != expected_category:
            if ctx is not None:
                ctx["_denial_meta"] = {
                    "contract_denial_reason": "root_mismatch",
                    "contract_root": contract_category,
                    "expected_root": expected_category,
                    "contract_id": contract.contract_id,
                }
            return False

        # Check operation is covered
        required_op = COMMAND_TO_OPERATION.get(command, "WRITE")
        if required_op not in contract.operations:
            if ctx is not None:
                ctx["_denial_meta"] = {
                    "contract_denial_reason": "operation_not_covered",
                    "required_op": required_op,
                    "contract_ops": [str(op) for op in contract.operations],
                    "contract_id": contract.contract_id,
                }
            return False

        return True

    return Condition(name="has_contract", check=_check)


def path_in_session_mods(session: object, paths: "Path | list[Path]") -> list["Path"]:
    """Return session-visible paths.

    Two cases:
      - Normal: path is inside a mod dir → return [path].
      - Container: path IS a mod-root parent (e.g. ROOT_STEAM or ROOT_USER_DOCS/mod)
        → expand to all session mod paths that live under it.

    This is the only place container expansion logic lives.  The two capability-
    matrix entries that invoke it are ("ck3lens","steam",None) and
    ("ck3lens","user_docs","mod") — both produce multiple paths when given a
    container root.
    """
    if isinstance(paths, Path):
        paths = [paths]

    mods = getattr(session, 'mods', None)
    if not mods:
        return []

    result = []
    for path in paths:
        resolved = path.resolve() if isinstance(path, Path) else Path(path).resolve()
        matched = False
        for mod in mods:
            mod_path_raw = getattr(mod, 'path', None)
            if mod_path_raw is None:
                continue
            mp = Path(mod_path_raw).resolve()
            try:
                resolved.relative_to(mp)
                result.append(path)
                matched = True
                break
            except ValueError:
                pass
        if not matched:
            # Container case: check whether session mod paths live *under* this path.
            for mod in mods:
                mod_path_raw = getattr(mod, 'path', None)
                if mod_path_raw is None:
                    continue
                mp = Path(mod_path_raw).resolve()
                try:
                    mp.relative_to(resolved)
                    result.append(Path(mod_path_raw))
                except ValueError:
                    pass

    return result


def scope_to_session_mods() -> Condition:
    """Gate: True if session.mods contains at least one indexed mod."""
    def _check(
        *,
        session: object | None = None,
        **_: object,
    ) -> bool:
        mods = getattr(session, "mods", ()) if session is not None else ()
        return any(getattr(mod, "cvid", None) is not None for mod in mods)

    return Condition(name="scope_to_session_mods", check=_check)


def exec_gate() -> Condition:
    """Global predicate for ALL ck3_exec calls. Single decision tree:

    1. Whitelisted command? → True (fast path, no contract/signature needed)
    2. Script execution detected (script_path + content_sha256 provided)?
       a. Script in wip (subdirectory == "wip")? → no → False
       b. Open contract exists? → no → False
       c. sigil_verify(f"script:{path}|{hash}", stored_sig)? → no → False
       d. All pass → True
    3. Neither whitelisted nor valid script → False

    The caller (_ck3_exec_internal) extracts the actual script path from the
    shell command and hashes the actual file. The signature cryptographically
    binds (path, hash) — sigil_verify is the only check needed.

    NOT a permission oracle — this is a predicate called BY enforcement
    as part of walking the capability matrix. Enforcement makes the
    allow/deny decision based on this predicate's True/False return.

    Context keys:
        exec_command: str — the shell command string
        exec_subdirectory: str | None — subdirectory from WA2 resolution
        has_contract: bool — whether an active contract exists
        script_path: str | None — canonical path of the actual script being executed
        content_sha256: str | None — current SHA-256 of the actual script content
    """
    def _check(
        *,
        exec_command: str = "",
        exec_subdirectory: str | None = None,
        has_contract: bool = False,
        script_path: str | None = None,
        content_sha256: str | None = None,
        **_: object,
    ) -> bool:
        if not exec_command:
            return False

        # --- Branch 1: Whitelisted commands (fast path) ---
        if _is_command_whitelisted(exec_command):
            return True

        # --- Branch 2: Script execution ---
        if script_path and content_sha256:
            # 2a. Script must be in wip
            if exec_subdirectory != "wip":
                return False

            # 2b. Open contract required
            if not has_contract:
                return False

            # 2c. Script signature valid (Sigil HMAC)
            # The signature cryptographically binds (path, hash).
            # sigil_verify is the ONLY check needed — if either the path
            # or content differs from what was signed, the payload changes
            # and verification fails. No separate comparisons required.
            from ck3lens.policy.contract_v1 import get_active_contract
            from tools.compliance.sigil import sigil_verify, sigil_available

            contract = get_active_contract()
            if contract is None:
                return False

            if not sigil_available():
                return False

            ss = contract.script_signature
            if not ss:
                return False

            stored_sig = ss.get("signature", "")
            if not stored_sig:
                return False

            # Sigil verify — payload format: "script:{session_absolute_path}|{hash}"
            payload = f"script:{script_path}|{content_sha256}"
            return sigil_verify(payload, stored_sig)

        # --- Branch 3: Neither whitelisted nor valid script ---
        return False

    return Condition(name="exec_gate", check=_check)


# =============================================================================
# CONTEXT_LIMIT — Gate for reads based on estimated/actual response size.
#
# Per CK3FILE-V2-DIR v1.0 (docs/Canonical address refactor/ck3_file_Refactor_Design.md §9.0):
#   D1: No escape hatch — no allowlists, bypass flags, or overrides
#   D2: Unified estimation Method B — proportional formula only
#   D3: Canonical paths only in agent-facing replies
#   D4: Sprint 1 — guidance + next_call template only, no indexer
#   D5: Required field set locked
#
# Two modes:
#   on_exceed="deny"  — DENY access, guide agent to narrow request (ck3_file read)
#   on_exceed="spill" — ALLOW access, but write results to file instead of inline
# =============================================================================

AVG_LINE_BYTES = 45  # D2 — single named constant for estimation

# Literal type for on_exceed parameter
from typing import Literal

OnExceedMode = Literal["deny", "spill"]


def context_limit(
    threshold_bytes: int = 15 * 1024,
    *,
    on_exceed: OnExceedMode = "deny",
    spill_format: str = "json",     # "json" or "md" — for spill mode
    spill_folder: str = "default",  # subfolder in MCP_Tool_Reply/
) -> Condition:
    """Context limit condition with two modes: deny or spill.

    Self-contained predicate — does os.stat() internally via wa2.host_path(ref).

    Deny mode (ck3_file read):
        - Gets file size via wa2.host_path(ref).stat().st_size
        - Returns False (blocks operation) when over threshold
        - Auto-generates INDEX + CST for supported file types (Python for Sprint 2.1)
        - Deposits _denial_meta with guidance, index_path, cst_path

    Spill mode (ck3_search, ck3_logs, etc.):
        - Always returns True (allows operation)
        - Deposits _spill_meta for post-execution handling by tool wrapper

    Context keys (deny mode):
        wa2: WorldAdapterV2 — resolver with host_path() method
        ref: VisibilityRef — token for host path lookup
        start_line: int | None — requested start line (None = whole file)
        end_line: int | None — requested end line (None = whole file)
        ctx: dict | None — mutable dict for metadata deposit

    Context keys (spill mode):
        ctx: dict | None — mutable dict; deposits _spill_meta for tool wrapper
    """

    def _check(
        *,
        wa2: object | None = None,
        ref: object | None = None,
        start_line: int | None = None,
        end_line: int | None = None,
        ctx: dict | None = None,
        **_: object,
    ) -> bool:
        # --- Spill mode: always allow, deposit metadata for post-exec handling ---
        if on_exceed == "spill":
            if ctx is not None:
                ctx["_spill_meta"] = {
                    "threshold_bytes": threshold_bytes,
                    "spill_format": spill_format,
                    "spill_folder": spill_folder,
                }
            return True  # Operation proceeds; tool wrapper handles spill

        # --- Deny mode: resolve host path and check size ---
        if wa2 is None or ref is None:
            return True  # No wa2/ref → can't check, let natural failure happen

        # Get host path via WA2 token lookup
        host_path_method = getattr(wa2, 'host_path', None)
        if host_path_method is None:
            return True  # wa2 doesn't have host_path method

        host_abs = host_path_method(ref)
        if host_abs is None:
            return True  # Token not found or expired

        # Get canonical path for agent-facing reply
        canonical_path: str = str(_.get('canonical_path', '') or '')

        # os.stat() — single syscall for file size
        try:
            file_bytes = host_abs.stat().st_size
        except (OSError, AttributeError):
            return True  # Can't stat → let natural failure happen

        if file_bytes <= 0:
            return True

        estimated_total_lines = max(1, file_bytes // AVG_LINE_BYTES)

        if start_line is not None and end_line is not None:
            requested_lines = max(1, end_line - start_line + 1)
            estimated_bytes = int(file_bytes * (requested_lines / estimated_total_lines))
        else:
            # Whole-file read
            estimated_bytes = file_bytes

        if estimated_bytes <= threshold_bytes:
            return True

        # --- Deny path: auto-generate INDEX + CST for oversized file ---
        import hashlib
        import json
        from ck3lens.indexing import index_file, detect_file_type
        from ck3lens.paths import ROOT_CK3RAVEN_DATA
        
        path_label = canonical_path or "<unknown>"
        
        # Generate INDEX and CST for supported file types
        index_path_str = None
        cst_path_str = None
        file_type = None
        element_count = 0
        
        try:
            file_type = detect_file_type(host_abs)
            # All file types supported - Sprint 2.2 indexers complete
            index_dict, cst_dict = index_file(host_abs, canonical_path, file_type, include_cst=True)
            
            # Write outputs to wip/indexes/
            out_dir = ROOT_CK3RAVEN_DATA / "wip" / "indexes"
            out_dir.mkdir(parents=True, exist_ok=True)
            
            filename = host_abs.name
            path_hash = hashlib.sha256(canonical_path.encode()).hexdigest()[:8]
            
            # Write INDEX
            index_filename = f"INDEX-{filename}-{path_hash}.json"
            index_path = out_dir / index_filename
            index_path.write_text(json.dumps(index_dict, indent=2, ensure_ascii=False), encoding="utf-8")
            index_path_str = str(index_path)
            element_count = len(index_dict.get("elements", []))
            
            # Write CST
            if cst_dict:
                cst_filename = f"CST-{filename}-{path_hash}.json"
                cst_path = out_dir / cst_filename
                cst_path.write_text(json.dumps(cst_dict, indent=2, ensure_ascii=False), encoding="utf-8")
                cst_path_str = str(cst_path)
        except Exception:
            pass  # Indexing failed — denial still proceeds without index
        
        meta: dict = {
            "file_bytes": file_bytes,
            "threshold_bytes": threshold_bytes,
            "estimated_total_lines": estimated_total_lines,
            "estimated_bytes": estimated_bytes,
            "avg_line_bytes": AVG_LINE_BYTES,
            "file_type": file_type,
            "index_path": index_path_str,
            "cst_path": cst_path_str,
            "element_count": element_count,
            "guidance": (
                f"File exceeds context budget ({file_bytes}B > {threshold_bytes}B). "
                + (f"INDEX and CST generated at {index_path_str}. " if index_path_str else "")
                + "Use start_line/end_line to read a specific range, or read the INDEX to find what you need."
            ),
            "next_call": {
                "tool": "ck3_file",
                "command": "read",
                "path": path_label,
                "start_line": "<fill>",
                "end_line": "<fill>",
            },
            "alternatives": [
                "Read the INDEX file to see function/class structure with line ranges",
                "Use start_line/end_line to read a narrower range",
                "Use ck3_search to find specific symbols or content",
            ],
        }

        # Deposit into the mutable ctx dict
        if ctx is not None:
            ctx["_denial_meta"] = meta

        return False

    return Condition(name="context_limit", check=_check)


# =============================================================================
# COMMAND WHITELIST — HAT-protected file at policy/command_whitelist.json
#
# Loaded once per process, cached. File is HAT-protected so only a human
# can add entries. If file doesn't exist, no commands are whitelisted
# (all exec requires script signing).
# =============================================================================

_WHITELIST_CACHE: list[str] | None = None


def _load_command_whitelist() -> list[str]:
    """Load command whitelist from ROOT_REPO/policy/command_whitelist.json.

    Returns list of allowed command patterns. Cached after first load.
    If ROOT_REPO is not configured or file doesn't exist, returns empty list.
    """
    global _WHITELIST_CACHE
    if _WHITELIST_CACHE is not None:
        return _WHITELIST_CACHE

    import json
    from ck3lens.paths import ROOT_REPO
    from ck3lens.logging import info

    info("exec.whitelist", "Loading command whitelist",
         root_repo=str(ROOT_REPO), root_repo_is_none=(ROOT_REPO is None))

    if ROOT_REPO is None:
        info("exec.whitelist", "ROOT_REPO is None, no whitelist")
        _WHITELIST_CACHE = []
        return _WHITELIST_CACHE

    whitelist_path = ROOT_REPO / "policy" / "command_whitelist.json"
    info("exec.whitelist", "Whitelist path check",
         path=str(whitelist_path), exists=whitelist_path.exists())

    if not whitelist_path.exists():
        _WHITELIST_CACHE = []
        return _WHITELIST_CACHE

    try:
        data = json.loads(whitelist_path.read_text(encoding="utf-8"))
        cmds: list[str] = data.get("commands", [])
        _WHITELIST_CACHE = cmds
        info("exec.whitelist", "Whitelist loaded",
             count=len(cmds), commands=cmds)
    except (json.JSONDecodeError, OSError) as exc:
        info("exec.whitelist", "Whitelist load error", error=str(exc))
        _WHITELIST_CACHE = []

    assert _WHITELIST_CACHE is not None  # assigned above in all branches
    return _WHITELIST_CACHE


def _is_command_whitelisted(command: str) -> bool:
    """Check if a shell command matches any whitelisted pattern.

    Whitelist entries are exact prefix matches against the command string.
    Example whitelist entry: "git status" matches "git status" and
    "git status --short" but not "git statusx".

    Shell metacharacters (|, &&, ;, `, $() ) are treated as command
    terminators — only the portion before the first metachar is matched.
    This prevents "git status && rm -rf /" from matching "git status".
    """
    import re

    whitelist = _load_command_whitelist()
    cmd_stripped = command.strip()

    if not cmd_stripped:
        return False

    # Reject commands containing shell metacharacters that could chain
    # additional commands after a whitelisted prefix.
    # Split at first metachar and only match against the safe prefix.
    _SHELL_META = re.compile(r'[|;&`]|\$\(|&&|\|\|')
    meta_match = _SHELL_META.search(cmd_stripped)
    if meta_match is not None:
        # Command contains shell metacharacters — reject entirely.
        # Whitelisted commands should never need pipes, semicolons, or chaining.
        return False

    for pattern in whitelist:
        if cmd_stripped == pattern or cmd_stripped.startswith(pattern + " "):
            return True
    return False


# =============================================================================
# VISIBILITY RULE — entry exists = visible, conditions must all pass
# =============================================================================

@dataclass(frozen=True)
class VisibilityRule:
    """Visibility rule. Data only.
    
    visibility_fn: If set, called with (session, path) -> list[Path].
                   Empty list = not visible. Non-empty = visible (returns valid paths).
    conditions: Legacy bool conditions (deprecated, use visibility_fn).
    """
    visibility_fn: Callable[..., list] | None = None
    conditions: tuple[Condition, ...] = ()


# =============================================================================
# OPERATION RULE — commands this rule governs + conditions
# =============================================================================

@dataclass(frozen=True)
class OperationRule:
    """
    Exact (tool, command) pairs this rule governs, plus conditions.
    Enforcement finds the rule whose commands contain the (tool, command),
    then checks conditions. Data only.

    Reads: rules with no conditions (always allowed if visible).
    Mutations: rules with conditions (e.g. has_contract, exec_gate).
    """
    commands: frozenset[tuple[str, str]]
    conditions: tuple[Condition, ...] = ()



# =============================================================================
# VALID ROOT KEYS (closed set)
# =============================================================================

VALID_ROOT_KEYS: frozenset[str] = frozenset({
    "repo", "ck3chronicle", "game", "steam", "user_docs", "ck3raven_data", "vscode",
})


# =============================================================================
# VISIBILITY_MATRIX
#
# Key: (mode, root_key, subdirectory | None)
# Entry exists -> visible (subject to conditions). No entry -> not visible.
# WA calls check_visibility() which evaluates conditions with context.
#
# Conditions on individual entries can further restrict visibility
# (e.g., requiring a mod to be in session.mods). WA passes context
# kwargs that conditions can inspect.
# =============================================================================

VisibilityKey = tuple[str, str, str | None]

_V = VisibilityRule  # shorthand

VISIBILITY_MATRIX: dict[VisibilityKey, VisibilityRule] = {

    # =================================================================
    # ck3lens mode
    #
    # steam and user_docs/mod declare path_in_session_mods as visibility_fn.
    # WA2 sees this, calls the function, interprets result.
    # root:steam expands to all steam mod paths in active playset (container expansion via path_in_session_mods).
    # root:user_docs without "mod" subdirectory → no entry → invisible.
    # =================================================================

    ("ck3lens", "game", None):              _V(),
    ("ck3lens", "steam", None):             _V(path_in_session_mods),
    ("ck3lens", "user_docs", "mod"):        _V(path_in_session_mods),
    ("ck3lens", "ck3raven_data", None):     _V(),
    ("ck3lens", "vscode", None):            _V(),
    ("ck3lens", "repo", None):              _V(),
    ("ck3lens", "ck3chronicle", None):      _V(),

    # =================================================================
    # ck3raven-dev mode
    # =================================================================

    ("ck3raven-dev", "game", None):             _V(),
    ("ck3raven-dev", "steam", None):            _V(),
    ("ck3raven-dev", "user_docs", None):        _V(),
    ("ck3raven-dev", "ck3raven_data", None):    _V(),
    ("ck3raven-dev", "vscode", None):           _V(),
    ("ck3raven-dev", "repo", None):             _V(),
    ("ck3raven-dev", "ck3chronicle", None):     _V(),
}


def check_visibility(
    mode: str,
    root_key: str,
    subdirectory: str | None,
    *,
    session: object | None = None,
    host_abs: Path | None = None,
) -> list[Path]:
    """
    Resolve visible paths.

    Matrix lookup:
        (mode, root_key, subdirectory) first, falls back to
        (mode, root_key, None). No entry → empty list.
        If entry has visibility_fn, call it to filter paths.

    Returns:
        List of resolved paths. Empty if not visible.
    """
    # Subdirectory-specific lookup first
    rule = None
    if subdirectory:
        rule = VISIBILITY_MATRIX.get((mode, root_key, subdirectory))

    # Fall back to root-level
    if rule is None:
        rule = VISIBILITY_MATRIX.get((mode, root_key, None))

    if rule is None:
        return []

    # visibility_fn takes precedence
    if rule.visibility_fn is not None:
        if session is None or host_abs is None:
            return []
        return rule.visibility_fn(session, host_abs)

    # Legacy conditions check
    context: dict[str, object] = {"session": session, "host_abs": host_abs}
    for c in rule.conditions:
        if not c.check(**context):
            return []

    # No conditions, entry exists = visible, echo back input path
    return [host_abs] if host_abs else []


# =============================================================================
# OPERATIONS_MATRIX
#
# Key: (mode, root_key, subdirectory | None)
# Value: tuple of OperationRules. Each rule carries its command set + conditions.
# Enforcement looks up key, scans rules for matching (tool, command), checks
# conditions. No entry -> operation denied. No matching command in any rule -> denied.
#
# ALL operations go through this matrix — reads AND mutations. No bypass branches.
# =============================================================================

OperationKey = tuple[str, str, str | None]


# --- Read command sets ---
# FILE_READ_GATED: subject to context_limit condition (CK3FILE-V2-DIR §9.0)
# FILE_READ_UNGATED: always allowed if visible (no context budget gate)

FILE_READ_UNGATED: frozenset[tuple[str, str]] = frozenset({
    ("ck3_file", "get"),      # DB lookup — already bounded by DB
    ("ck3_file", "list"),     # Directory listing — naturally compact
    ("ck3_file", "refresh"),  # Cache refresh — no content returned
})

FILE_READ_GATED: frozenset[tuple[str, str]] = frozenset({
    ("ck3_file", "read"),     # Filesystem read — unbounded content, needs gate
})

# Legacy alias for backwards compat in tests/typechecks
FILE_READ_COMMANDS: frozenset[tuple[str, str]] = FILE_READ_UNGATED | FILE_READ_GATED

DIR_READ_COMMANDS: frozenset[tuple[str, str]] = frozenset({
    ("ck3_dir", "pwd"),
    ("ck3_dir", "cd"),
    ("ck3_dir", "list"),
    ("ck3_dir", "tree"),
})

CONTENT_SEARCH_COMMANDS: frozenset[tuple[str, str]] = frozenset({
    ("ck3_search", "content"),   # Disk grep — geographic, goes through OPERATIONS_MATRIX
})

DB_SEARCH_COMMANDS: frozenset[tuple[str, str]] = frozenset({
    ("ck3_search", "symbols"),   # DB symbol search — goes through DATABASE_OPERATIONS_MATRIX
    ("ck3_search", "paths"),     # DB file path search
    ("ck3_search", "filters"),   # DB filter vocabulary
    ("ck3_search", "auto"),      # DB auto (symbols + paths)
    ("ck3_search", "mods"),      # DB mod search (name/workshop_id)
})

DB_VALIDATE_COMMANDS: frozenset[tuple[str, str]] = frozenset({
    ("ck3_validate", "references"),
})

DB_CONFLICT_COMMANDS: frozenset[tuple[str, str]] = frozenset({
    ("ck3_conflicts", "symbols"),     # DB symbol conflict detection (Golden Join)
    ("ck3_conflicts", "files"),       # DB file conflict detection (reads files table)
    ("ck3_conflicts", "summary"),     # DB conflict summary statistics
    ("ck3_conflicts", "diff"),        # DB file diff by relpath + CVID pair
    ("ck3_conflicts", "diff_index"),  # DB changed-file index by path prefix + CVID pair
})

# Legacy alias — union of both for any code referencing the full set
SEARCH_READ_COMMANDS: frozenset[tuple[str, str]] = CONTENT_SEARCH_COMMANDS | DB_SEARCH_COMMANDS

ALL_READ_COMMANDS: frozenset[tuple[str, str]] = (
    FILE_READ_COMMANDS | DIR_READ_COMMANDS | SEARCH_READ_COMMANDS
)

# Read commands NOT subject to context_limit (everything except ck3_file read)
# NOTE: Only geographic search commands (CONTENT_SEARCH_COMMANDS) are here.
# DB search commands go through DATABASE_OPERATIONS_MATRIX, not OPERATIONS_MATRIX.
ALL_READ_UNGATED: frozenset[tuple[str, str]] = (
    FILE_READ_UNGATED | DIR_READ_COMMANDS | CONTENT_SEARCH_COMMANDS
)

# --- Mutation command sets ---

FILE_WRITE_COMMANDS: frozenset[tuple[str, str]] = frozenset({
    ("ck3_file", "write"),
    ("ck3_file", "edit"),
    ("ck3_file", "rename"),
    ("ck3_file", "create_patch"),
    ("ck3_file", "create_patch_batch"),
})

FILE_DELETE_COMMANDS: frozenset[tuple[str, str]] = frozenset({
    ("ck3_file", "delete"),
})

FILE_ALL_COMMANDS: frozenset[tuple[str, str]] = FILE_WRITE_COMMANDS | FILE_DELETE_COMMANDS

EXEC_COMMANDS: frozenset[tuple[str, str]] = frozenset({
    # ck3_exec has no sub-command; the "command" param is the shell string.
    # find_operation_rule() special-cases tool="ck3_exec".
})

# Shared condition instances (created once from factories)
_HAS_CONTRACT = has_contract()
_EXEC_GATE = exec_gate()
_CONTEXT_LIMIT = context_limit()  # CK3FILE-V2-DIR — context budget gate for reads

# Shared read rules
# _READS_UNGATED: all reads EXCEPT ck3_file read (no conditions)
# _READS_GATED: ck3_file read only, subject to context_limit condition
_READS_UNGATED = OperationRule(ALL_READ_UNGATED)
_READS_GATED = OperationRule(FILE_READ_GATED, (_CONTEXT_LIMIT,))

# Legacy alias — kept for any code that references _READS
_READS = OperationRule(ALL_READ_COMMANDS)

_R = OperationRule  # shorthand

OPERATIONS_MATRIX: dict[OperationKey, tuple[OperationRule, ...]] = {

    # =================================================================
    # ck3lens mode
    # =================================================================

    # Game: read-only
    ("ck3lens", "game", None): (
        _READS_UNGATED,
        _READS_GATED,
    ),

    # Steam: read-only. Workshop-ID subdirectories (e.g., "2820384801") have
    # no specific entries — they fall back here via _operation_keys().
    # Visibility is gated by path_in_session_mods in VISIBILITY_MATRIX.
    ("ck3lens", "steam", None): (
        _READS_UNGATED,
        _READS_GATED,
    ),

    # User docs / mod: reads + writes
    # Reads: visibility matrix gates to session mods.
    # Mutations: path_in_session_mods at operations level for extra safety.
    ("ck3lens", "user_docs", "mod"): (
        _READS_UNGATED,
        _READS_GATED,
        _R(FILE_ALL_COMMANDS, (_HAS_CONTRACT,)),
          
    ),

    # ck3raven_data root: read-only
    ("ck3lens", "ck3raven_data", None): (
        _READS_UNGATED,
        _READS_GATED,
    ),

    # ck3raven_data / wip: reads + writes + exec
    ("ck3lens", "ck3raven_data", "wip"): (
        _READS_UNGATED,
        _READS_GATED,
        _R(FILE_ALL_COMMANDS),          # no contract needed
        _R(EXEC_COMMANDS, (_EXEC_GATE,)),
    ),

    # ck3raven_data / playsets: reads + writes with contract
    ("ck3lens", "ck3raven_data", "playsets"): (
        _READS_UNGATED,
        _READS_GATED,
        _R(FILE_ALL_COMMANDS, (_HAS_CONTRACT,)),
    ),

    # VS Code: read-only
    ("ck3lens", "vscode", None): (
        _READS_UNGATED,
        _READS_GATED,
    ),

    # Repo: read-only
    ("ck3lens", "repo", None): (
        _READS_UNGATED,
        _READS_GATED,
    ),

    # ck3chronicle repo: read-only
    ("ck3lens", "ck3chronicle", None): (
        _READS_UNGATED,
        _READS_GATED,
    ),

    # =================================================================
    # ck3raven-dev mode
    # =================================================================

    # Game: read-only
    ("ck3raven-dev", "game", None): (
        _READS_UNGATED,
        _READS_GATED,
    ),

    # Steam: read-only. Workshop-ID subdirectories fall back here via
    # _operation_keys() — same pattern as ck3lens mode.
    ("ck3raven-dev", "steam", None): (
        _READS_UNGATED,
        _READS_GATED,
    ),

    # User docs / mod: reads + writes with contract
    ("ck3raven-dev", "user_docs", "mod"): (
        _READS_UNGATED,
        _READS_GATED,
        _R(FILE_ALL_COMMANDS, (_HAS_CONTRACT,)),
          
    ),

    # ck3raven_data root: read-only
    ("ck3raven-dev", "ck3raven_data", None): (
        _READS_UNGATED,
        _READS_GATED,
    ),

    # Repo: full file with contract
    ("ck3raven-dev", "repo", None): (
        _READS_UNGATED,
        _READS_GATED,
        _R(FILE_ALL_COMMANDS, (_HAS_CONTRACT,)),
          
    ),

    # ck3chronicle repo: full file with contract
    ("ck3raven-dev", "ck3chronicle", None): (
        _READS_UNGATED,
        _READS_GATED,
        _R(FILE_ALL_COMMANDS, (_HAS_CONTRACT,)),
          
    ),

    # ck3raven_data / wip: same as ck3lens
    ("ck3raven-dev", "ck3raven_data", "wip"): (
        _READS_UNGATED,
        _READS_GATED,
        _R(FILE_ALL_COMMANDS),
        _R(EXEC_COMMANDS, (_EXEC_GATE,)),
    ),

    # ck3raven_data per-subdirectory: full access with contract
    ("ck3raven-dev", "ck3raven_data", "config"): (
        _READS_UNGATED,
        _READS_GATED,
        _R(FILE_ALL_COMMANDS, (_HAS_CONTRACT,)),
    ),
    ("ck3raven-dev", "ck3raven_data", "playsets"): (
        _READS_UNGATED,
        _READS_GATED,
        _R(FILE_ALL_COMMANDS, (_HAS_CONTRACT,)),
    ),
    ("ck3raven-dev", "ck3raven_data", "logs"): (
        _READS_UNGATED,
        _READS_GATED,
        _R(FILE_ALL_COMMANDS, (_HAS_CONTRACT,)),
    ),
    ("ck3raven-dev", "ck3raven_data", "cache"): (
        _READS_UNGATED,
        _READS_GATED,
        _R(FILE_ALL_COMMANDS, (_HAS_CONTRACT,)),
    ),
    ("ck3raven-dev", "ck3raven_data", "artifacts"): (
        _READS_UNGATED,
        _READS_GATED,
        _R(FILE_ALL_COMMANDS, (_HAS_CONTRACT,)),
    ),

    # VS Code settings: reads + write with contract (no delete)
    ("ck3raven-dev", "vscode", None): (
        _READS_UNGATED,
        _READS_GATED,
        _R(FILE_WRITE_COMMANDS, (_HAS_CONTRACT,)),
    ),

    # =================================================================
    # Global capabilities — fallback when no geographic rule matches
    #
    # These use root_key="*" (wildcard) and are checked LAST by
    # _operation_keys(). They cover operations that are capabilities
    # of the agent, not properties of a location.
    #
    # Example: whitelisted ck3_exec commands (python -m
    # qbuilder, etc.) work regardless of where the working directory
    # points. The exec_gate condition still runs — the whitelist is
    # the gate, not the geography.
    # =================================================================

    ("ck3lens", "*", None): (
        _R(EXEC_COMMANDS, (_EXEC_GATE,)),
    ),

    ("ck3raven-dev", "*", None): (
        _R(EXEC_COMMANDS, (_EXEC_GATE,)),
    ),
}


def find_operation_rule(
    mode: str,
    root_key: str,
    subdirectory: str | None,
    tool: str,
    command: str,
) -> OperationRule | None:
    """
    Find the OperationRule governing this (tool, command) at this location.

    Looks up (mode, root_key, subdirectory) first, then (mode, root_key, None).
    Scans rules for one whose commands contain (tool, command).
    Returns None if no match -> enforcement should deny.

    Special case: tool="ck3_exec" matches any rule with EXEC_COMMANDS
    (since ck3_exec's "command" param is the shell string, not a subcommand).
    """
    for key in _operation_keys(mode, root_key, subdirectory):
        rules = OPERATIONS_MATRIX.get(key)
        if rules is None:
            continue
        for rule in rules:
            if tool == "ck3_exec" and rule.commands is EXEC_COMMANDS:
                return rule
            if (tool, command) in rule.commands:
                return rule
    return None


def _operation_keys(mode: str, root_key: str, subdirectory: str | None):
    """Yield lookup keys in priority order:
    1. Subdirectory-specific: (mode, root_key, subdirectory)
    2. Root-level:            (mode, root_key, None)
    3. Global capabilities:   (mode, "*", None)
    """
    if subdirectory:
        yield (mode, root_key, subdirectory)
    yield (mode, root_key, None)
    # Global fallback — capabilities not tied to geography
    yield (mode, "*", None)


# =============================================================================
# DATABASE_OPERATIONS_MATRIX
#
# Key: mode string
# Value: tuple of DatabaseRules. Each rule carries command set + conditions.
# execute_db() looks up mode, scans rules for matching (tool, command),
# then evaluates the attached predicates before dispatching to the DB executor.
#
# This table is a policy gate only. It answers:
#   - is this DB-backed command allowed in the current mode?
#   - if allowed, should scope predicates apply first?
#
# It does NOT decide reply ownership, reply layer, or result semantics.
# Those remain with the dispatched tool/executor after policy passes.
#
# Separate from OPERATIONS_MATRIX because DB operations have no geography —
# there's no (root_key, subdirectory) to key on. Scoping is via session.mods
# cvids, not host paths.
# =============================================================================

_SCOPE_MODS = scope_to_session_mods()

DATABASE_OPERATIONS_MATRIX: dict[str, tuple[OperationRule, ...]] = {

    # ck3lens: DB queries require session mod scoping.
    # EN evaluates scope_to_session_mods (= bool(session.cvids)) in execute_db;
    # executors then use session.cvids as the DB filter.
    "ck3lens": (
        _R(DB_SEARCH_COMMANDS, (_SCOPE_MODS,)),
        _R(DB_VALIDATE_COMMANDS, (_SCOPE_MODS,)),
        _R(DB_CONFLICT_COMMANDS, (_SCOPE_MODS,)),
    ),

    # ck3raven-dev: full DB access, no scoping
    "ck3raven-dev": (
        _R(DB_SEARCH_COMMANDS),
        _R(DB_VALIDATE_COMMANDS),
        _R(DB_CONFLICT_COMMANDS),
    ),
}


def find_database_rule(
    mode: str,
    tool: str,
    command: str,
) -> OperationRule | None:
    """
    Find the OperationRule governing this (tool, command) in this mode.

    Returns None if no match -> enforcement should deny.
    """
    rules = DATABASE_OPERATIONS_MATRIX.get(mode)
    if rules is None:
        return None
    for rule in rules:
        if (tool, command) in rule.commands:
            return rule
    return None

