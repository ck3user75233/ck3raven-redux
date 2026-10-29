"""
Playset Operations Implementation

Shared implementation module for playset operations.
Used by both MCP server (server.py) and VS Code extension bridge.

This module provides pure functions that take explicit parameters,
avoiding reliance on global state. Callers are responsible for
providing the appropriate context (paths, database connections, etc.).

Architecture:
- MCP server calls these with parameters from _get_session()
- Bridge calls these with parameters from its initialization
- Both get consistent behavior without import issues
"""
from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime
from typing import Optional, Literal


def get_playsets_dir() -> Path:
    """Get the canonical playsets directory path."""
    from ..paths import ROOT_CK3RAVEN_DATA
    return ROOT_CK3RAVEN_DATA / "playsets"


def get_manifest_file() -> Path:
    """Get the canonical playset manifest file path."""
    return get_playsets_dir() / "playset_manifest.json"


def list_playsets(playsets_dir: Optional[Path] = None) -> dict:
    """
    List all available playsets.
    
    Args:
        playsets_dir: Path to playsets directory (defaults to canonical location)
    
    Returns:
        Dict with success, playsets list, active name, and manifest path
    """
    if playsets_dir is None:
        playsets_dir = get_playsets_dir()
    
    manifest_file = playsets_dir / "playset_manifest.json"
    
    playsets = []
    
    for f in playsets_dir.glob("*.json"):
        if f.name.endswith(".schema.json") or f.name == "playset_manifest.json" or f.name == "sub_agent_templates.json":
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8-sig"))
            mod_entries = [m for m in data.get("mods", []) if m.get("enabled", True)]
            playsets.append({
                "filename": f.name,
                "name": data.get("playset_name", f.stem),
                "description": data.get("description", ""),
                "mod_count": len(mod_entries),
            })
        except Exception as e:
            playsets.append({
                "filename": f.name,
                "name": f.stem,
                "error": str(e),
            })
    
    return {
        "success": True,
        "playsets": playsets,
        "manifest_path": str(manifest_file),
    }


def get_active_playset(playsets_dir: Optional[Path] = None) -> dict:
    """
    Get the currently active playset.
    
    Args:
        playsets_dir: Path to playsets directory
    
    Returns:
        Dict with playset info or error
    """
    from ..workspace import DEFAULT_PLAYSET_FILENAME, load_session

    if playsets_dir is None:
        playsets_dir = get_playsets_dir()

    session = load_session()
    active_filename = DEFAULT_PLAYSET_FILENAME
    manifest_file = playsets_dir / "playset_manifest.json"
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8-sig"))
        requested = manifest.get("active")
        if requested and (playsets_dir / requested).exists():
            active_filename = requested
    except Exception:
        pass

    return {
        "success": True,
        "active_file": active_filename,
        "playset_name": session.playset_name,
        "source": "canonical_session",
        "mod_count": max(0, len(session.mods) - 1),
        "has_agent_briefing": False,
        "vanilla_root": str(session.vanilla.path) if session.vanilla else None,
        **session.get_load_report(),
    }


def get_playset_mods(
    playsets_dir: Optional[Path] = None,
    limit: Optional[int] = None
) -> dict:
    """
    Get mods in the active playset.
    
    Args:
        playsets_dir: Path to playsets directory
        limit: Maximum mods to return (None = all)
    
    Returns:
        Dict with playset name, mod counts, and mod list
    """
    from ..workspace import load_session

    session = load_session()
    mods = [
        {
            "name": mod.name,
            "load_order": mod.load_order,
            "path": str(mod.path),
            "enabled": True,
            "workshop_id": mod.workshop_id,
            "content_version_id": mod.cvid,
        }
        for mod in session.mods
    ]
    return {
        "success": True,
        "playset_name": session.playset_name,
        "enabled_count": len(mods),
        "disabled_count": 0,
        "mods": mods[:limit] if limit else mods,
        "truncated": limit is not None and len(mods) > limit,
        **session.get_load_report(),
    }


def switch_playset(
    playset_name: str,
    playsets_dir: Optional[Path] = None
) -> dict:
    """
    Switch to a different playset.
    
    Args:
        playset_name: Name or filename of playset to switch to
        playsets_dir: Path to playsets directory
    
    Returns:
        Dict with success and new playset info
    """
    if playsets_dir is None:
        playsets_dir = get_playsets_dir()
    
    manifest_file = playsets_dir / "playset_manifest.json"
    
    if not playset_name:
        return {"success": False, "error": "playset_name required for switch"}
    
    # Find the playset file
    target_file = None
    playset_data = None
    
    for f in playsets_dir.glob("*.json"):
        if f.name.endswith(".schema.json") or f.name == "playset_manifest.json":
            continue
        # Match by filename or playset_name in content
        if f.name == playset_name or f.stem == playset_name:
            target_file = f
            try:
                playset_data = json.loads(f.read_text(encoding="utf-8-sig"))
            except Exception:
                pass
            break
        try:
            data = json.loads(f.read_text(encoding="utf-8-sig"))
            if data.get("playset_name") == playset_name:
                target_file = f
                playset_data = data
                break
        except Exception:
            pass
    
    if not target_file:
        return {"success": False, "error": f"Playset '{playset_name}' not found"}
    
    # Update manifest
    manifest = {
        "active": target_file.name,
        "last_switched": datetime.now().isoformat(),
        "notes": "Updated by playset_ops.switch_playset"
    }
    manifest_file.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    
    enabled_mods = [m for m in playset_data.get("mods", []) if m.get("enabled", True)] if playset_data else []
    
    return {
        "success": True,
        "message": f"Switched to playset: {target_file.name}",
        "active_playset": target_file.name,
        "playset_name": playset_data.get("playset_name") if playset_data else target_file.stem,
        "mod_count": len(enabled_mods),
    }


def reorder_mod(
    mod_identifier: str,
    new_position: int,
    playsets_dir: Optional[Path] = None
) -> dict:
    """
    Reorder a mod in the active playset.
    
    Args:
        mod_identifier: Mod name or workshop ID
        new_position: New 0-indexed position in load order
        playsets_dir: Path to playsets directory
    
    Returns:
        Dict with success and updated load order
    """
    if playsets_dir is None:
        playsets_dir = get_playsets_dir()
    
    manifest_file = playsets_dir / "playset_manifest.json"
    
    if not mod_identifier:
        return {"success": False, "error": "mod_identifier required"}
    if new_position is None:
        return {"success": False, "error": "new_position required"}
    
    # Get active playset file
    if not manifest_file.exists():
        return {"success": False, "error": "No playset manifest found"}
    
    try:
        manifest = json.loads(manifest_file.read_text(encoding='utf-8-sig'))
        active_filename = manifest.get("active", "")
        if not active_filename:
            return {"success": False, "error": "No active playset"}
        
        active_file = playsets_dir / active_filename
        if not active_file.exists():
            return {"success": False, "error": f"Active playset file not found: {active_filename}"}
        
        data = json.loads(active_file.read_text(encoding="utf-8-sig"))
    except Exception as e:
        return {"success": False, "error": f"Failed to read playset: {e}"}
    
    mods = data.get("mods", [])
    
    # Find the mod by name or workshop ID
    mod_index = None
    for i, mod in enumerate(mods):
        if mod.get("name") == mod_identifier or str(mod.get("steam_id", "")) == str(mod_identifier):
            mod_index = i
            break
    
    if mod_index is None:
        return {"success": False, "error": f"Mod '{mod_identifier}' not found in playset"}
    
    # Clamp new_position to valid range
    new_position = max(0, min(new_position, len(mods) - 1))
    
    # Move the mod
    mod = mods.pop(mod_index)
    mods.insert(new_position, mod)
    
    # Update load_order values
    for i, m in enumerate(mods):
        m["load_order"] = i
    
    # Write back
    data["mods"] = mods
    try:
        active_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as e:
        return {"success": False, "error": f"Failed to write playset: {e}"}
    
    return {
        "success": True,
        "message": f"Moved '{mod.get('name', mod_identifier)}' to position {new_position}",
        "mod_name": mod.get("name"),
        "new_position": new_position,
    }
