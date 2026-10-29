"""
Canonical path CONSTANTS for CK3 Lens.

ALL paths are CONSTANTS loaded at module import time.
Import directly: `from ck3lens.paths import ROOT_REPO, ROOT_GAME`

8 Root Categories:
- ROOT_REPO: from config (required for ck3raven-dev, optional for ck3lens)
- ROOT_CK3CHRONICLE_REPO: from config (optional)
- ROOT_CK3RAVEN_DATA: CK3RAVEN_DATA env var, or ~/.ck3raven if not set
- ROOT_GAME: from config (required)
- ROOT_STEAM: from config (required)
- ROOT_USER_DOCS: from config or OS-default (contains launcher-v2.sqlite, dlc_load.json, saves, local mods)
- ROOT_VSCODE: from config or OS-default
- ROOT_EXTERNAL: not a path (catch-all classifier)

CRITICAL: ROOT_REPO is CONFIG-BASED, not computed from __file__.
This is required because __file__ computation breaks when the package
is pip-installed into a venv at a different location.
"""

from pathlib import Path
from enum import Enum
import logging
from .config_loader import get_ck3raven_data_root

logger = logging.getLogger(__name__)


# =============================================================================
# LOAD CONFIG AT MODULE IMPORT
# =============================================================================

def _load_paths_from_config():
    """Load paths from workspace.toml. Creates default if missing."""
    from ck3lens.config_loader import load_config
    return load_config()

_config = _load_paths_from_config()


# =============================================================================
# ROOT CATEGORY ENUM (for classify_path return type)
# =============================================================================

class RootCategory(Enum):
    """Canonical root categories for path classification."""
    ROOT_REPO = "ROOT_REPO"
    ROOT_CK3CHRONICLE_REPO = "ROOT_CK3CHRONICLE_REPO"
    ROOT_CK3RAVEN_DATA = "ROOT_CK3RAVEN_DATA"
    ROOT_GAME = "ROOT_GAME"
    ROOT_STEAM = "ROOT_STEAM"
    ROOT_USER_DOCS = "ROOT_USER_DOCS"
    ROOT_VSCODE = "ROOT_VSCODE"
    ROOT_EXTERNAL = "ROOT_EXTERNAL"


# =============================================================================
# PATH CONSTANTS
# =============================================================================

# From config (NO __file__ computation - that breaks in venvs)
# ROOT_REPO is None if not configured - paths_doctor will warn
ROOT_REPO: Path | None = _config.paths.root_repo

# Optional source repo root for ck3chronicle
ROOT_CK3CHRONICLE_REPO: Path | None = _config.paths.root_ck3chronicle_repo

# Computed from CK3RAVEN_DATA env var; falls back to ~/.ck3raven (not from workspace.toml —
# this is the bootstrap root that workspace.toml itself lives inside)
ROOT_CK3RAVEN_DATA: Path = get_ck3raven_data_root()

# From config (required - assert at import, fail if missing)
_root_game = _config.paths.root_game
_root_steam = _config.paths.root_steam
_root_user_docs = _config.paths.root_user_docs

assert _root_game is not None, \
    "ROOT_GAME not configured in workspace.toml — ck3raven cannot function without it"
assert _root_steam is not None, \
    "ROOT_STEAM not configured in workspace.toml — ck3raven cannot function without it"
assert _root_user_docs is not None, \
    "ROOT_USER_DOCS not configured in workspace.toml — ck3raven cannot function without it"

ROOT_GAME: Path = _root_game
ROOT_STEAM: Path = _root_steam
ROOT_USER_DOCS: Path = _root_user_docs

# From config (optional)
ROOT_VSCODE: Path | None = _config.paths.root_vscode

# =============================================================================
# LOG WARNINGS FOR MISSING CONFIG
# =============================================================================

if _config.options.warn_on_default_paths and _config.paths.using_defaults:
    for path_name in _config.paths.using_defaults:
        logger.warning(f"Using OS-default for {path_name} - configure in ~/.ck3raven/config/workspace.toml")

if _config.errors:
    for issue in _config.errors:
        logger.warning(f"Config invalid: {issue}")
