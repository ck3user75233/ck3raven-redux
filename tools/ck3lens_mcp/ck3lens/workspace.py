"""
Workspace and Session Configuration

Manages session state for the MCP server.

CANONICAL MODEL (January 2026):
- Session contains: playset info, db_path, local_mods_folder, mods[]
- mods[0] is ALWAYS vanilla (injected automatically, load_order=0)
- mods[1:] are user mods from playset
- mods[] is NEVER empty - vanilla is always present even if playset fails to load
- Each mod has .cvid resolved from database
- DB filtering = {m.cvid for m in mods if m.cvid}
- local_mods_folder is a path - mods under it are potentially editable
- NO permission oracles here - enforcement.py decides writability

BANNED CONCEPTS (do not recreate):
- vanilla_cvid (vanilla is mods[0])
- PlaysetLens (just use mods[].cvid)
- local_mods[] / editable_mods[] (derive from mods[] + local_mods_folder)
- ui_hint_potentially_editable (no UI consumes this - deleted)
- is_writable / can_write / can_edit (permission oracles)
- is_vanilla (derive from load_order == 0 or check mods[0]; never expose as field/property)

CONFLICT ANALYSIS MODEL:
- File-level: Same relpath in multiple mods = file conflict
  - Exact match filename = full override (later wins)
  - Prefixed filename (zzz_traits.txt) = partial override/append intent
- Block-level (Symbol-level): Same block ID within a relpath = ID conflict
  - on_action blocks: CONTAINER_MERGE - blocks with SAME ID override, different IDs append
  - traits/events/decisions: OVERRIDE - last definition wins
  - Only blocks/symbols with IDENTICAL names conflict
- Use mods[] load order to determine winners (higher index = later load = wins)
- Agent can derive all conflict info from mods[].cvid + relpath + block names

"""
from __future__ import annotations
import json
import logging
import os
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Any, TYPE_CHECKING, Set

from ck3lens.name_matching import find_near_matches
from ck3lens.paths import ROOT_USER_DOCS, ROOT_GAME, ROOT_CK3RAVEN_DATA

if TYPE_CHECKING:
    from .db_queries import DBQueries

# Try to import yaml, fall back to json-only if not available
try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


# Module logger
_logger = logging.getLogger(__name__)

@dataclass
class ModEntry:
    """A mod entry from the active playset.
    
    mods[0] is always game files (name="CK3 Game Files", load_order=0).
    mods[1:] are user mods in load order.
    
    cvid is resolved from database when playset is loaded.
    """
    name: str
    path: Path
    load_order: int = 0
    cvid: Optional[str] = None  # resolved from DB after descriptor hydration
    workshop_id: Optional[str] = None  # descriptor remote_file_id when present
    
    def exists(self) -> bool:
        return self.path.exists()
    
    @property
    def is_indexed(self) -> bool:
        """True if this mod has been indexed in the database."""
        return self.cvid is not None


@dataclass
class Session:
    """Current agent session state.
    
    CANONICAL MODEL:
    - mods[0] = vanilla (always present, injected automatically)
    - mods[1:] = user mods from playset in load order
    - mods[] is NEVER empty - vanilla is always present
    - DB filtering = {m.cvid for m in mods if m.cvid}
    - local_mods_folder: Path to folder where editable mods live
    - load_warnings: Warnings from playset loading (e.g., workshop_id mismatches)
    - NO permission methods - enforcement.py handles that
    """
    playset_name: Optional[str] = None
    db_path: Optional[Path] = None
    mods: list[ModEntry] = field(default_factory=list)
    local_mods_folder: Path = field(default_factory=lambda: ROOT_USER_DOCS / "mod")
    load_warnings: list[str] = field(default_factory=list)
    load_errors: list[dict[str, str]] = field(default_factory=list)

    def get_load_report(self) -> dict[str, Any]:
        """Report loading errors separately from mods excluded during hydration."""
        excluded_mods = [
            error for error in self.load_errors
            if error["code"] in {"PLAYSET_MOD_PATH_REQUIRED", "PLAYSET_MOD_UNRESOLVED"}
        ]
        return {
            "warnings": self.load_warnings,
            "errors": self.load_errors,
            "excluded_mods": excluded_mods,
            "excluded_mod_count": len(excluded_mods),
            "exclusion_notice": (
                f"Excluded {len(excluded_mods)} unresolved mod(s) from the loaded playset: "
                + ", ".join(error["name"] for error in excluded_mods)
                + ". See excluded_mods for paths and reasons."
            ) if excluded_mods else None,
        }
    
    def get_mod(self, mod_name: str) -> Optional[ModEntry]:
        """Get mod by name from mods[]."""
        for mod in self.mods:
            if mod.name == mod_name:
                return mod
        return None

    def get_mod_near_matches(self, mod_name: str, *, limit: int = 5) -> list[str]:
        """Return likely mod-name matches from the active session."""
        return find_near_matches(mod_name, (mod.name for mod in self.mods), limit=limit)

    def resolve_mod(self, mod_name: str, *, limit: int = 5) -> tuple[Optional[ModEntry], list[str]]:
        """Resolve a mod name and return near matches if the lookup misses."""
        mod = self.get_mod(mod_name)
        if mod is not None:
            return mod, []
        return None, self.get_mod_near_matches(mod_name, limit=limit)
    
    @property
    def vanilla(self) -> Optional[ModEntry]:
        """Get vanilla (mods[0]). Always returns a value since vanilla is always present."""
        return self.mods[0] if self.mods else None
    
    def get_unresolved_mods(self) -> list[ModEntry]:
        """Get mods that haven't been indexed yet (no cvid)."""
        return [m for m in self.mods if m.cvid is None]
    
    def is_fully_indexed(self) -> bool:
        """True if all mods (including vanilla) have cvids resolved."""
        return all(m.cvid is not None for m in self.mods)
    
    def _sync_playset_json(self) -> bool:
        """Sync canonical mod names and workshop_ids back to playset JSON.
        
        CVIDs are runtime-only (resolved from DB each session) and are
        never written to JSON. Any stale cvid keys are stripped.
        
        Returns:
            True if sync succeeded, False if no playset or sync failed.
        """
        from .paths import ROOT_CK3RAVEN_DATA
        
        _playsets = ROOT_CK3RAVEN_DATA / "playsets"
        manifest_file = _playsets / "playset_manifest.json"
        
        if not manifest_file.exists():
            return False
        
        try:
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
            active_filename = manifest.get("active")
            if not active_filename:
                return False
            
            playset_file = _playsets / active_filename
            if not playset_file.exists():
                return False
            
            # Load current playset JSON
            content = playset_file.read_text(encoding="utf-8")
            if content.startswith('\ufeff'):
                content = content[1:]
            playset_data = json.loads(content)
            
            # Build path -> mod mapping from session.mods (skip vanilla at index 0)
            # Use lowercase paths for matching
            path_to_mod: dict[str, ModEntry] = {}
            for mod in self.mods[1:]:  # Skip vanilla
                norm_path = str(mod.path).lower().replace("/", "\\")
                path_to_mod[norm_path] = mod
            
            # Update each mod entry in the playset JSON
            updated = False
            for mod_entry in playset_data.get("mods", []):
                mod_path = mod_entry.get("path", "")
                norm_path = mod_path.lower().replace("/", "\\")
                
                # Strip stale cvid keys — cvids are runtime-only
                if "cvid" in mod_entry:
                    del mod_entry["cvid"]
                    updated = True
                
                if norm_path in path_to_mod:
                    session_mod = path_to_mod[norm_path]
                    
                    # Update name if it differs (session.mods uses its *.mod descriptor as source)
                    if mod_entry.get("name") != session_mod.name:
                        mod_entry["name"] = session_mod.name
                        updated = True
                    
                    # Update workshop_id if session has it
                    if session_mod.workshop_id:
                        old_wid = mod_entry.get("steam_id") or mod_entry.get("workshop_id")
                        if old_wid != session_mod.workshop_id:
                            mod_entry["steam_id"] = session_mod.workshop_id
                            updated = True
            
            # Write back if anything changed
            if updated:
                playset_file.write_text(
                    json.dumps(playset_data, indent=2, ensure_ascii=False),
                    encoding="utf-8"
                )
                _logger.info(f"Synced names to playset: {playset_file.name}")
            
            return updated
            
        except Exception as e:
            _logger.warning(f"Failed to sync playset JSON: {e}")
            return False

    def _write_session_state(self) -> None:
        """Write session_state.json to ~/.ck3raven/ after every full hydration.

        This file is the single authoritative snapshot of session.mods (names,
        paths, load order, CVIDs, workshop IDs) for external consumers such as
        the SDK. It is written after resolve_cvids() completes — i.e. on MCP
        server startup, playset switch, add_mod, and reload.

        External consumers must read this file only. They must never reconstruct
        session.mods independently (no direct *.mod descriptor reads, no playset
        JSON parsing, no DB queries for mod identity).

        Format: UTF-8, no BOM. mods[0] is always vanilla.
        """
        from datetime import datetime
        from .paths import ROOT_CK3RAVEN_DATA

        state = {
            "playset_name": self.playset_name,
            "written_at": datetime.utcnow().isoformat(),
            "warnings": self.load_warnings,
            "errors": self.load_errors,
            "mods": [
                {
                    "name": m.name,
                    "path": str(m.path),
                    "load_order": m.load_order,
                    "cvid": m.cvid,
                    "workshop_id": m.workshop_id,
                }
                for m in self.mods
            ],
        }

        state_path = ROOT_CK3RAVEN_DATA / "session_state.json"
        state_path.write_text(
            json.dumps(state, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        _logger.debug(f"Wrote session_state.json: {len(self.mods)} mods")

    def publish(self) -> None:
        """Persist the fully-resolved session exactly once.

        Syncs canonical names back to the active playset JSON and writes
        session_state.json. Call this only on the FINAL selected session —
        never on an intermediate, partially resolved session. Non-raising:
        failures are logged as warnings.
        """
        try:
            self._sync_playset_json()
        except Exception as e:
            _logger.warning(f"playset sync failed (non-fatal): {e}")
        try:
            self._write_session_state()
        except Exception as e:
            _logger.warning(f"session state write failed (non-fatal): {e}")

    # DEPRECATED - use get_mod instead
    def get_local_mod(self, mod_name: str) -> Optional[ModEntry]:
        """DEPRECATED: Use get_mod() instead."""
        return self.get_mod(mod_name)


# Playsets directory: ROOT_CK3RAVEN_DATA / "playsets"
# NOTE: Import ROOT_CK3RAVEN_DATA at function scope to avoid circular imports

# Legacy location (deprecated)
LEGACY_PLAYSETS_DIR = Path.home() / ".ck3raven" / "playsets"

# Default config file location (portable - uses standard user data location)
DEFAULT_CONFIG_PATH = Path.home() / ".ck3raven" / "ck3lens_config.yaml"
DEFAULT_PLAYSET_FILENAME = "Default.json"
DEFAULT_PLAYSET_NAME = "Default"


def _default_playset_data() -> dict[str, Any]:
    """Return the vanilla-only fallback using the normal playset schema."""
    return {
        "$schema": "./playset.schema.json",
        "playset_name": DEFAULT_PLAYSET_NAME,
        "vanilla": {
            "path": str(ROOT_GAME),
            "enabled": True,
        },
        "mods": [],
    }


def ensure_default_playset() -> Path:
    """Ensure the always-loadable vanilla-only playset exists on disk."""
    playsets_dir = ROOT_CK3RAVEN_DATA / "playsets"
    playsets_dir.mkdir(parents=True, exist_ok=True)
    playset_path = playsets_dir / DEFAULT_PLAYSET_FILENAME
    if not playset_path.exists():
        playset_path.write_text(
            json.dumps(_default_playset_data(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    return playset_path


def _expand_path(path_str: str) -> Path:
    """Expand ~ and return Path."""
    return Path(path_str).expanduser()


def _session_absolute_path(path_value: str | Path) -> Path:
    """Return the one comparable absolute path representation used by sessions."""
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"Path must be absolute: {path_value}")
    resolved = path.resolve(strict=True)
    return Path(os.path.normcase(str(resolved)))


def _read_mod_descriptor(mod_path: Path) -> tuple[str, Optional[str]]:
    """Resolve the formal mod name only when all descriptor names agree."""
    descriptor_paths = sorted(
        (path for path in mod_path.glob("*.mod") if path.is_file()),
        key=lambda path: (
            path.name.casefold() != "descriptor.mod", path.name.casefold(), path.name
        ),
    )
    if not descriptor_paths:
        raise ValueError(
            f"No mod descriptor found; the formal mod name could not be determined: {mod_path}"
        )

    descriptor_names: dict[str, str] = {}
    workshop_id: Optional[str] = None
    for descriptor_path in descriptor_paths:
        try:
            content = descriptor_path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as exc:
            raise ValueError(
                f"Mod descriptor is unreadable; the formal mod name could not be determined: "
                f"{descriptor_path}: {exc}"
            ) from exc

        name: Optional[str] = None
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if re.match(r"name\b", line):
                match = re.fullmatch(r'name\s*=\s*"([^"]*)"\s*(?:#.*)?', line)
                if match is None:
                    raise ValueError(
                        f"Mod descriptor name is unreadable; expected name=\"...\": {descriptor_path}"
                    )
                candidate = match.group(1).strip()
                if not candidate:
                    raise ValueError(
                        f"No formal mod name found in mod descriptor: {descriptor_path}"
                    )
                if name is not None and candidate != name:
                    raise ValueError(f"Mod names provided are in conflict in descriptor: {descriptor_path}")
                name = candidate
            elif line.startswith("remote_file_id=") and workshop_id is None:
                candidate = line.split("=", 1)[1].strip().strip('"')
                if candidate:
                    workshop_id = candidate

        if name is None:
            raise ValueError(f"No formal mod name found in mod descriptor: {descriptor_path}")
        descriptor_names[descriptor_path.name] = name

    if len(set(descriptor_names.values())) > 1:
        details = ", ".join(f'{filename}: "{name}"' for filename, name in descriptor_names.items())
        raise ValueError(
            f"Two or more mod descriptor files were found, and the mod names provided are in conflict: "
            f"{mod_path}: {details}"
        )
    return next(iter(descriptor_names.values())), workshop_id


def _load_json_robust(path: Path) -> Optional[dict]:
    """Load JSON file, handling UTF-8 BOM if present.
    
    Windows tools (especially PowerShell) often write UTF-8 with BOM.
    This function strips the BOM if present and continues.
    """
    try:
        content = path.read_text(encoding="utf-8")
        # Strip BOM if present (common from Windows/PowerShell)
        if content.startswith('\ufeff'):
            content = content[1:]
        return json.loads(content)
    except json.JSONDecodeError as e:
        _logger.warning(f"Failed to parse JSON from {path}: {e}")
        return None
    except Exception as e:
        _logger.warning(f"Failed to load {path}: {e}")
        return None


def _add_base_game() -> ModEntry:
    """Create the stable base game entry from the canonical game root."""
    return ModEntry(
        name="Base Game",
        path=_session_absolute_path(ROOT_GAME),
        load_order=0,
    )


def load_session(
    *,
    playset_path: Optional[Path] = None,
    conn: sqlite3.Connection | None = None,
) -> Session:
    """Return one fully hydrated snapshot of the selected playset."""
    ensure_default_playset()
    session = Session(db_path=ROOT_CK3RAVEN_DATA / "ck3raven.db")

    fallback_warning: Optional[str] = None
    if playset_path is not None:
        playset_data = _load_json_robust(playset_path)
        if playset_data is None:
            raise ValueError(f"Playset could not be loaded: {playset_path}")
    else:
        playset_data, fallback_warning = _load_active_playset()

    owned_conn: sqlite3.Connection | None = None
    db_error: dict[str, str] | None = None
    if conn is None and session.db_path.exists():
        try:
            owned_conn = sqlite3.connect(
                f"{session.db_path.as_uri()}?mode=ro",
                uri=True,
            )
            conn = owned_conn
        except sqlite3.Error as exc:
            db_error = {
                "code": "SESSION_DB_UNAVAILABLE",
                "name": "",
                "path": str(session.db_path),
                "message": str(exc),
            }

    try:
        _hydrate_session_mods(session, playset_data, conn)
    finally:
        if owned_conn is not None:
            owned_conn.close()

    if db_error is not None:
        session.load_errors.append(db_error)
    if fallback_warning:
        session.load_warnings.append(fallback_warning)
    return session


def _load_default_playset() -> dict:
    """Load the persisted default, repairing it if it is unreadable."""
    default_path = ensure_default_playset()
    playset_data = _load_json_robust(default_path)
    if playset_data is not None:
        return playset_data

    playset_data = _default_playset_data()
    default_path.write_text(
        json.dumps(playset_data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return playset_data


def _load_active_playset() -> tuple[dict, Optional[str]]:
    """Load the active playset or the exact-format vanilla default."""
    playsets_dir = ROOT_CK3RAVEN_DATA / "playsets"
    manifest_file = playsets_dir / "playset_manifest.json"

    if not manifest_file.exists():
        return _load_default_playset(), "No playset manifest; using Default"

    manifest = _load_json_robust(manifest_file)
    if manifest is None:
        return _load_default_playset(), "Playset manifest is unreadable; using Default"

    active_filename = manifest.get("active")
    if not active_filename:
        return _load_default_playset(), "No active playset selected; using Default"

    playset_file = playsets_dir / active_filename
    if not playset_file.exists():
        return (
            _load_default_playset(),
            f"Active playset file not found ({active_filename}); using Default",
        )

    playset_data = _load_json_robust(playset_file)
    if playset_data is None:
        return (
            _load_default_playset(),
            f"Active playset is unreadable ({active_filename}); using Default",
        )
    return playset_data, None


def _hydrate_session_mods(
    session: Session,
    data: dict,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Construct complete Session.mods entries from one playset snapshot."""
    session.playset_name = data.get("playset_name", data.get("name"))
    session.load_warnings = []
    session.load_errors = []

    if "local_mods_folder" in data:
        session.local_mods_folder = _expand_path(data["local_mods_folder"])

    session.mods = [_add_base_game()]

    for entry_index, mod_data in enumerate(data.get("mods", [])):
        if not isinstance(mod_data, dict) or not mod_data.get("enabled", True):
            continue

        playset_name = str(mod_data.get("name") or f"entry {entry_index}")
        path_value = mod_data.get("path")
        if not isinstance(path_value, str) or not path_value.strip():
            session.load_errors.append({
                "code": "PLAYSET_MOD_PATH_REQUIRED",
                "name": playset_name,
                "path": "",
                "message": "Enabled playset entry has no asserted absolute path.",
            })
            continue

        try:
            mod_path = _session_absolute_path(path_value)
            if not mod_path.is_dir():
                raise ValueError(f"Mod path is not a directory: {mod_path}")
            descriptor_name, descriptor_workshop_id = _read_mod_descriptor(mod_path)
        except (OSError, ValueError) as exc:
            session.load_errors.append({
                "code": "PLAYSET_MOD_UNRESOLVED",
                "name": playset_name,
                "path": path_value,
                "message": str(exc),
            })
            continue

        playset_workshop_id = mod_data.get("steam_id", mod_data.get("workshop_id"))
        if (
            playset_workshop_id
            and descriptor_workshop_id
            and str(playset_workshop_id) != descriptor_workshop_id
        ):
            session.load_warnings.append(
                f"Workshop ID mismatch for '{descriptor_name}': playset says "
                f"{playset_workshop_id}, descriptor says {descriptor_workshop_id}."
            )

        session.mods.append(ModEntry(
            name=descriptor_name,
            path=mod_path,
            load_order=len(session.mods),
            workshop_id=descriptor_workshop_id,
        ))

    if conn is None:
        return

    for mod in session.mods:
        try:
            rows = conn.execute(
                "SELECT content_version_id FROM content_versions WHERE source_path = ?",
                (str(mod.path),),
            ).fetchall()
        except Exception as exc:
            session.load_errors.append({
                "code": "CVID_LOOKUP_FAILED",
                "name": mod.name,
                "path": str(mod.path),
                "message": str(exc),
            })
            continue

        if len(rows) == 1:
            row = rows[0]
            mod.cvid = row[0] if isinstance(row, tuple) else row["content_version_id"]
        elif len(rows) > 1:
            session.load_errors.append({
                "code": "CVID_PATH_AMBIGUOUS",
                "name": mod.name,
                "path": str(mod.path),
                "message": "Multiple database rows claim this session path.",
            })


def get_validation_rules_config(config_path: Optional[Path] = None) -> dict[str, dict[str, Any]]:
    """Load validation rules configuration from config file."""
    if config_path is None:
        config_path = DEFAULT_CONFIG_PATH
    
    if not config_path.exists():
        return {}
    
    try:
        content = config_path.read_text(encoding="utf-8")
        # Strip BOM if present
        if content.startswith('\ufeff'):
            content = content[1:]
            
        if config_path.suffix in ('.yaml', '.yml') and HAS_YAML:
            data = yaml.safe_load(content)
        else:
            data = json.loads(content)
        
        return data.get("validation_rules", {})
    except Exception:
        return {}


def validate_relpath(relpath: str) -> tuple[bool, str]:
    """
    Validate a relative path for safety.
    
    Returns (is_valid, error_message).
    """
    p = Path(relpath)
    
    # No absolute paths
    if p.is_absolute():
        return False, "Path must be relative"
    
    # No parent traversal
    if ".." in p.parts:
        return False, "Path must not contain '..'"
    
    # Must have valid top-level
    allowed_toplevel = {"common", "events", "gfx", "localization", "interface", "music", "sound", "history", "map_data", "descriptor.mod"}
    if p.parts and p.parts[0] not in allowed_toplevel:
        # Allow descriptor.mod at root
        if relpath != "descriptor.mod":
            return False, f"Top-level must be one of {sorted(allowed_toplevel)}"
    
    return True, ""


# =============================================================================
# DEPRECATED ALIASES - kept for backwards compatibility
# =============================================================================

# LocalMod is now ModEntry
LocalMod = ModEntry
