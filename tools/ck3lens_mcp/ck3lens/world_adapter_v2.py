"""
World Adapter v2 — Canonical Addressing with Opaque Token Registry.

Mode-agnostic: resolve() reads agent mode dynamically via get_agent_mode()
at call time. The WA instance never stores mode — if mode is None (agent
not initialized), resolve() fails with WA-SYS-I-001.

resolve() returns tuple[Reply, Optional[VisibilityRef]].
No VisibilityResolution in public API. No mod_paths dict.
Consults session.mods directly at resolution time.
Walks VISIBILITY_MATRIX for root category gating.

Canonical address syntax (ONLY supported formats):
    root:<key>/<relative_path>      e.g. root:repo/src/server.py
    mod:<mod_name>/<relative_path>  e.g. mod:Unofficial Patch/common/traits

No fallbacks. No legacy formats. Host-absolute paths are rejected.
VisibilityRef holds an opaque UUID4 token. The host-absolute path lives
ONLY in the token registry on this instance. Agent never sees host paths.

Design brief: docs/Canonical address refactor/v2_enforcement_design_brief.md
Directive: Canonical Addressing Refactor Directive (Authoritative)
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Dict, NamedTuple, Optional

from ck3raven.core.reply import Reply


# ============================================================================
# Constants
# ============================================================================

MAX_TOKENS = 10_000

# Valid v2 root keys (closed set) — re-exported from capability_matrix_v2
from .capability_matrix_v2 import VALID_ROOT_KEYS  # noqa: E402
from .name_matching import find_near_matches  # noqa: E402


# ============================================================================
# Parsed Address — replaces raw 5-tuple from _parse_and_compute
# ============================================================================

class ParsedAddress(NamedTuple):
    """Result of parsing a canonical address."""
    session_abs: str      # canonical address string (root:key/path or mod:name/path)
    host_abs: Path        # host-absolute path
    root_key: str         # infrastructure root key (for matrix lookup)
    rel_path: str         # relative path within root or mod
    mod_name: str | None  # mod display name (None for root addresses)


# ============================================================================
# Helpers
# ============================================================================

def _resolve_within(root: Path, rel_path: str) -> Path | None:
    """Join root + rel_path and verify containment. Returns None if path escapes root."""
    host_abs = root / rel_path if rel_path else root
    try:
        host_abs.resolve().relative_to(root.resolve())
        return host_abs
    except (ValueError, OSError):
        return None


def _get_mode() -> Optional[str]:
    """
    Read agent mode dynamically. Returns None if not initialized.

    This is THE mode accessor for WA v2. Every call hits the persisted
    mode file (~/.ck3raven/agent_mode_{instance_id}.json). If it returns
    None, the agent has not called ck3_get_mode_instructions yet.
    """
    from ck3lens.agent_mode import get_agent_mode
    return get_agent_mode()


@dataclass(frozen=True, slots=True)
class VisibilityRef:
    """Opaque tokens for host-path recovery. Agent-safe.

    Host-absolute paths live in WorldAdapterV2._token_registry,
    keyed by token. Canonical session paths are carried exclusively
    by Reply.data["resolved"].
    
    Usually contains one token, but supports multiple for filtered results.
    """
    tokens: tuple[str, ...]

    def __str__(self) -> str:
        return f"VisibilityRef({len(self.tokens)} paths)"

    def __repr__(self) -> str:
        return f"VisibilityRef(tokens={self.tokens!r})"
    
    @property
    def token(self) -> str:
        """Single token accessor for the common single-path case."""
        if not self.tokens:
            raise ValueError("VisibilityRef has no tokens")
        return self.tokens[0]


# ============================================================================
# WorldAdapterV2
# ============================================================================

class WorldAdapterV2:
    """
    World Adapter v2 — canonical addressing with opaque token registry.

    Mode-agnostic: does NOT store agent mode. resolve() reads mode
    dynamically via _get_mode() on every call. If mode is None (agent
    not initialized), resolve() returns WA-SYS-I-001.

    Construction: WorldAdapterV2(session=..., roots=...)
    - session: Session object with .mods list and .get_mod() method
    - roots: dict of v2 root key → host-absolute Path

    resolve() returns tuple[Reply, Optional[VisibilityRef]].
    No VisibilityResolution. No mod_paths dict. Consults session.mods directly.
    Walks VISIBILITY_MATRIX for root category gating.
    """

    def __init__(self, *, session: Any, roots: dict[str, Path]):
        """
        Args:
            session: Session object — session.mods is the authoritative mod list.
                     Each mod has .name (str) and .path (Path).
                     session.get_mod(name) returns ModEntry or None.
            roots: Mapping of v2 root key → host-absolute Path.
                   Keys must be from VALID_ROOT_KEYS.
        """
        for key in roots:
            if key not in VALID_ROOT_KEYS:
                raise ValueError(f"Unknown root key: {key!r}. Valid: {sorted(VALID_ROOT_KEYS)}")
        self._session = session
        self._roots: dict[str, Path] = dict(roots)
        self._token_registry: Dict[str, Path] = {}
        self._registry_lock = Lock()

    @classmethod
    def create(cls, *, session: Any) -> WorldAdapterV2:
        """
        Factory using paths.py constants.

        Args:
            session: Session object with .mods and .get_mod()
        """
        from ck3lens.paths import (
            ROOT_REPO,
            ROOT_CK3CHRONICLE_REPO,
            ROOT_CK3RAVEN_DATA,
            ROOT_GAME,
            ROOT_STEAM,
            ROOT_USER_DOCS,
            ROOT_VSCODE,
        )

        roots: dict[str, Path] = {}

        # ROOT_CK3RAVEN_DATA is always set (never None)
        roots["ck3raven_data"] = ROOT_CK3RAVEN_DATA

        if ROOT_REPO and ROOT_REPO.exists():
            roots["repo"] = ROOT_REPO
        if ROOT_CK3CHRONICLE_REPO and ROOT_CK3CHRONICLE_REPO.exists():
            roots["ck3chronicle"] = ROOT_CK3CHRONICLE_REPO
        if ROOT_GAME and ROOT_GAME.exists():
            roots["game"] = ROOT_GAME
        if ROOT_STEAM and ROOT_STEAM.exists():
            roots["steam"] = ROOT_STEAM
        if ROOT_USER_DOCS and ROOT_USER_DOCS.exists():
            roots["user_docs"] = ROOT_USER_DOCS
        if ROOT_VSCODE and ROOT_VSCODE.exists():
            roots["vscode"] = ROOT_VSCODE

        return cls(session=session, roots=roots)

    # ========================================================================
    # Public API
    # ========================================================================

    def resolve(
        self, input_str: str, *, require_exists: bool = True, rb: Any = None,
    ) -> tuple[Reply, Optional[VisibilityRef]]:
        """
        Resolve a canonical address.

        Reads agent mode dynamically via _get_mode(). If mode is None
        (agent not initialized), returns WA-SYS-I-001 immediately.

        For mod: addresses, visibility is structural — if the mod is in
        session.mods, it's visible. If not, resolution fails.

        Returns:
            (Reply, VisibilityRef) on success — Reply.data["resolved"] is the
            session-absolute path preserving input namespace.
            (Reply, None) on failure — Reply is Invalid.

        Args:
            input_str: Canonical address (root:key/path, mod:name/path, or legacy).
            require_exists: If True, non-existent paths fail.
            rb: ReplyBuilder for constructing Reply. If None, creates a minimal Reply.
        """
        from .capability_matrix_v2 import check_visibility

        # Dynamic mode read — every resolve() checks current mode
        mode = _get_mode()
        if mode is None:
            return self._reply_invalid(rb, "WA-SYS-I-001",
                {"input_path": input_str})

        parsed = self._parse_and_compute(input_str)
        if parsed is None:
            return self._reply_invalid(rb, "WA-RES-I-001", self._build_parse_failure_data(input_str, mode))

        # Existence check
        if require_exists and not parsed.host_abs.exists():
            return self._reply_invalid(rb, "WA-RES-I-001", self._build_missing_path_data(input_str, parsed, mode))

        # Derive subdirectory for matrix lookup
        if parsed.mod_name is not None:
            # mod: addresses — root_key already classified by _classify_mod_root.
            # Subdirectory signals where this mod lives under the root:
            #   user_docs mods → "mod" (matches matrix entry ("ck3lens","user_docs","mod"))
            #   steam mods     → None  (hits catch-all ("ck3lens","steam",None))
            subdirectory = "mod" if parsed.root_key == "user_docs" else None
        else:
            # root: addresses — subdirectory is first component of rel_path
            rel_normalized = parsed.rel_path.replace("\\", "/").strip("/")
            if rel_normalized:
                subdirectory = rel_normalized.split("/")[0] or None
            else:
                subdirectory = None

        # Visibility gate
        if not (paths := check_visibility(
            mode, parsed.root_key, subdirectory,
            session=self._session,
            host_abs=parsed.host_abs,
        )):
            return self._reply_invalid(rb, "WA-RES-I-003", {
                    "input_path": input_str,
                    "root_key": parsed.root_key,
                    "subdirectory": subdirectory,
                    "mode": mode,
                })

        # Mint tokens for resolved paths
        tokens = []
        with self._registry_lock:
            if len(self._token_registry) + len(paths) > MAX_TOKENS:
                return self._reply_error(rb, "WA-RES-E-001", {
                    "reason": "Token registry full",
                    "max_tokens": MAX_TOKENS,
                })
            for path in paths:
                token = str(uuid.uuid4())
                self._token_registry[token] = path
                tokens.append(token)

        ref = VisibilityRef(tokens=tuple(tokens))

        reply_data = {
            "resolved": parsed.session_abs,
            "root_key": parsed.root_key,
            "subdirectory": subdirectory,
            "relative_path": parsed.rel_path.replace("\\", "/").strip("/"),
            "mod_name": parsed.mod_name,
        }

        if rb is not None:
            reply = rb.success("WA-RES-S-001", reply_data)
        else:
            from ck3raven.core.reply import TraceInfo, MetaInfo
            from ck3lens.reply_codes import get_message
            reply = Reply.success(
                code="WA-RES-S-001",
                message=get_message("WA-RES-S-001"),
                data=reply_data,
                trace=TraceInfo(trace_id="", session_id=""),
                meta=MetaInfo(layer="WA", tool="world_adapter_v2"),
            )

        return (reply, ref)

    def host_path(self, ref: VisibilityRef) -> list[Path]:
        """
        Recover host-absolute Paths from a VisibilityRef's tokens.

        Only WA2 owns this — callers must go through WA2 to get real paths.
        Returns list of paths. Unknown tokens are silently skipped.
        """
        result = []
        with self._registry_lock:
            for token in ref.tokens:
                path = self._token_registry.get(token)
                if path is not None:
                    result.append(path)
        return result

    def host_to_session_abs(self, host_abs: str | Path) -> str | None:
        """Reverse-resolve a host-absolute path to a session-absolute address.

        Iterates ``self._roots`` and returns ``root:<key>/<rel>`` for the first
        root that contains *host_abs*.  Returns ``None`` when the path falls
        outside every known root.
        """
        resolved = Path(host_abs).resolve()
        for key, root in self._roots.items():
            try:
                rel = resolved.relative_to(root.resolve())
                return f"root:{key}/{rel.as_posix()}" if str(rel) != "." else f"root:{key}"
            except (ValueError, OSError):
                continue
        return None

    @property
    def mode(self) -> str:
        """
        Read agent mode dynamically. Always current.

        Returns 'uninitiated' if agent has not called ck3_get_mode_instructions.
        """
        return _get_mode() or "uninitiated"

    @property
    def root_keys(self) -> frozenset[str]:
        """Return the set of configured root keys."""
        return frozenset(self._roots.keys())

    @property
    def mod_names(self) -> frozenset[str]:
        """Return mod names from session.mods."""
        if self._session and hasattr(self._session, 'mods') and self._session.mods:
            return frozenset(m.name for m in self._session.mods if hasattr(m, 'name'))
        return frozenset()

    def _split_root_rest(self, rest: str) -> tuple[str, str]:
        """Split the body of a root: address into root key and relative path."""
        if "/" in rest:
            key, rel_path = rest.split("/", 1)
        else:
            key = rest
            rel_path = ""
        return key, rel_path.strip("/")

    def _split_mod_rest(self, rest: str) -> tuple[str, str]:
        """Split the body of a mod: address into mod name and relative path."""
        if ":/" in rest:
            mod_name, rel_path = rest.split(":/", 1)
        elif "/" in rest:
            mod_name, rel_path = rest.split("/", 1)
        else:
            mod_name = rest
            rel_path = ""
        return mod_name, rel_path.strip("/")

    def _canonical_address(self, prefix: str, name: str, rel_path: str = "") -> str:
        """Build a canonical address from parts."""
        rel_path = rel_path.replace("\\", "/").strip("/")
        if rel_path:
            return f"{prefix}:{name}/{rel_path}"
        return f"{prefix}:{name}"

    def _get_mod_near_matches(self, mod_name: str, *, limit: int = 5) -> list[str]:
        """Return near-match mod names from the session."""
        if not self._session:
            return []

        if hasattr(self._session, "get_mod_near_matches"):
            return list(self._session.get_mod_near_matches(mod_name, limit=limit))

        mods = getattr(self._session, "mods", []) or []
        return find_near_matches(mod_name, (mod.name for mod in mods if hasattr(mod, "name")), limit=limit)

    def _build_parse_failure_data(self, input_str: str, mode: str) -> dict:
        """Attach near-match suggestions for parse-time resolution failures."""
        data = {"input_path": input_str, "mode": mode}
        normalized = input_str.strip()

        if normalized.startswith("mod:"):
            mod_name, rel_path = self._split_mod_rest(normalized[4:])
            data["not_found_kind"] = "mod"
            data["mod_name"] = mod_name
            near_matches = [
                self._canonical_address("mod", candidate, rel_path)
                for candidate in self._get_mod_near_matches(mod_name)
            ]
            if near_matches:
                data["near_matches"] = near_matches
            return data

        if normalized.startswith("root:"):
            root_key, rel_path = self._split_root_rest(normalized[5:])
            if root_key not in self._roots:
                data["not_found_kind"] = "root"
                data["root_key"] = root_key
                near_matches = [
                    self._canonical_address("root", candidate, rel_path)
                    for candidate in find_near_matches(root_key, self._roots.keys())
                ]
                if near_matches:
                    data["near_matches"] = near_matches

        return data

    def _resolution_base(self, parsed: ParsedAddress) -> tuple[Path, str] | None:
        """Return the base host path and canonical prefix for a parsed address."""
        if parsed.mod_name is None:
            root = self._roots.get(parsed.root_key)
            if root is None:
                return None
            return root, self._canonical_address("root", parsed.root_key)

        mod = self._session.get_mod(parsed.mod_name) if self._session else None
        if mod is None:
            return None
        mod_path = Path(mod.path) if isinstance(mod.path, str) else mod.path
        return mod_path, self._canonical_address("mod", parsed.mod_name)

    def _find_missing_path_near_matches(self, parsed: ParsedAddress, *, limit: int = 5) -> list[str]:
        """Suggest canonical addresses for the first missing path component."""
        resolution_base = self._resolution_base(parsed)
        if resolution_base is None:
            return []

        current, canonical_prefix = resolution_base
        components = [component for component in parsed.rel_path.replace("\\", "/").split("/") if component]
        resolved_components: list[str] = []

        for index, component in enumerate(components):
            next_path = current / component
            if next_path.exists():
                current = next_path
                resolved_components.append(component)
                continue

            if not current.exists() or not current.is_dir():
                return []

            sibling_names = [child.name for child in current.iterdir()]
            matches = find_near_matches(component, sibling_names, limit=limit)
            if not matches:
                return []

            base = canonical_prefix
            if resolved_components:
                base = f"{base}/{'/'.join(resolved_components)}"

            tail = "/".join(components[index + 1:])
            suggestions: list[str] = []
            for match in matches:
                suggestion = f"{base}/{match}"
                if tail:
                    suggestion = f"{suggestion}/{tail}"
                suggestions.append(suggestion)
            return suggestions

        return []

    def _build_missing_path_data(self, input_str: str, parsed: ParsedAddress, mode: str) -> dict:
        """Attach near-match suggestions for missing files/directories."""
        data = {
            "input_path": input_str,
            "resolved": parsed.session_abs,
            "mode": mode,
            "not_found_kind": "path",
        }
        near_matches = self._find_missing_path_near_matches(parsed)
        if near_matches:
            data["near_matches"] = near_matches
        return data

    # ========================================================================
    # Private: Reply construction
    # ========================================================================

    def _reply_invalid(
        self, rb: Any, code: str, data: dict,
    ) -> tuple[Reply, None]:
        """Build an Invalid Reply with near-match suggestions when available.
        
        For WA-RES-I-001 (path not found) errors, if near_matches exist in data,
        the message is enriched with suggestions and a prompt to check for typos.
        This helps agents self-correct common mistakes like 'EC724 LSC Submod'
        vs 'EC724-LSC Submod' without human intervention.
        """
        from ck3lens.reply_codes import get_message
        
        # Build enhanced message when resolution failed with near-matches
        message = None
        if code == "WA-RES-I-001" and data.get("near_matches"):
            near = data["near_matches"][:3]  # Limit to top 3 in message
            kind = data.get("not_found_kind", "path")
            input_val = data.get("mod_name") or data.get("root_key") or data.get("input_path", "")
            
            message = f"Could not resolve {kind}: '{input_val}'. "
            message += f"Did you mean: {', '.join(near)}? "
            message += "Check input for typos (spaces vs hyphens, apostrophes, capitalization)."
        
        if rb is not None:
            return (rb.invalid(code, data, message=message), None)
        
        from ck3raven.core.reply import TraceInfo, MetaInfo
        return (Reply.invalid(
            code=code, message=message or get_message(code),
            data=data,
            trace=TraceInfo(trace_id="", session_id=""),
            meta=MetaInfo(layer="WA", tool="world_adapter_v2"),
        ), None)

    def _reply_error(
        self, rb: Any, code: str, data: dict,
    ) -> tuple[Reply, None]:
        """Build an Error Reply. Message comes from reply_codes registry."""
        if rb is not None:
            return (rb.error(code, data), None)
        from ck3raven.core.reply import TraceInfo, MetaInfo
        from ck3lens.reply_codes import get_message
        return (Reply.error(
            code=code, message=get_message(code),
            data=data,
            trace=TraceInfo(trace_id="", session_id=""),
            meta=MetaInfo(layer="WA", tool="world_adapter_v2"),
        ), None)

    # ========================================================================
    # Private: Parse + Compute
    # ========================================================================

    def _parse_and_compute(self, input_str: str) -> ParsedAddress | None:
        """
        Parse input and compute a ParsedAddress.

        Accepts ONLY canonical address formats:
          - root:key/path       canonical root address
          - mod:name/path       canonical mod address

        Returns None if the input is invalid or not in canonical format.
        No fallbacks. No legacy formats. Host-absolute paths are rejected.
        """
        from ck3lens.logging import info
        
        input_str = input_str.strip()
        info("world_adapter_v2", "_parse_and_compute called",
             input_str=input_str,
             is_empty=(not input_str),
             starts_with_root=input_str.startswith("root:") if input_str else False,
             available_roots=list(self._roots.keys()))
        if not input_str:
            return None

        # Canonical: root:key/path
        if input_str.startswith("root:"):
            return self._parse_root(input_str[5:])

        # Canonical: mod:name/path
        if input_str.startswith("mod:"):
            return self._parse_mod(input_str[4:])

        # Unrecognized — reject
        return None

    def _parse_root(self, rest: str) -> ParsedAddress | None:
        """Parse after 'root:' prefix."""
        from ck3lens.logging import info
        
        key, rel_path = self._split_root_rest(rest)
        
        info("world_adapter_v2", "_parse_root called",
             rest=rest, key=key, rel_path=rel_path,
             available_roots=list(self._roots.keys()),
             key_in_roots=(key in self._roots))

        if key not in self._roots:
            info("world_adapter_v2", "_parse_root: key not in roots", key=key)
            return None

        host_abs = _resolve_within(self._roots[key], rel_path)
        info("world_adapter_v2", "_parse_root: _resolve_within result",
             root=str(self._roots[key]), rel_path=rel_path,
             host_abs=str(host_abs) if host_abs else None)
        if host_abs is None:
            return None

        session_abs = f"root:{key}/{rel_path}" if rel_path else f"root:{key}"
        return ParsedAddress(session_abs, host_abs, key, rel_path, None)

    def _parse_mod(self, rest: str) -> ParsedAddress | None:
        """
        Parse after 'mod:' prefix. Consults session.mods via session.get_mod().

        Output preserves mod: namespace in session_abs.
        root_key is the underlying infrastructure root (steam or user_docs).
        """
        mod_name, rel_path = self._split_mod_rest(rest)

        # Consult session.mods directly — session.get_mod() is the authoritative lookup
        mod = self._session.get_mod(mod_name) if self._session else None
        if mod is None:
            return None
        mod_path = Path(mod.path) if isinstance(mod.path, str) else mod.path

        host_abs = _resolve_within(mod_path, rel_path)
        if host_abs is None:
            return None

        # Determine underlying root key (steam or user_docs)
        root_key = self._classify_mod_root(mod_path)

        # Session-absolute preserves mod: namespace
        session_abs = f"mod:{mod_name}/{rel_path}" if rel_path else f"mod:{mod_name}"
        return ParsedAddress(session_abs, host_abs, root_key, rel_path, mod_name)

    def _classify_mod_root(self, mod_path: Path) -> str:
        """
        Determine which root key a mod's path falls under.

        Iterates self._roots and returns the key whose path contains mod_path.
        Falls back to "user_docs" if no root matches (defensive default).
        """
        resolved = mod_path.resolve()
        for key, root in self._roots.items():
            try:
                resolved.relative_to(root.resolve())
                return key
            except (ValueError, OSError):
                continue
        return "user_docs"
