"""
E2E-* — Integration / end-to-end round-trip tests.

Tests: E2E-01 (resolve → list round-trip), E2E-02 (cd then relative list)

E2E-03 (legacy input normalization) deleted — WA2 rejects legacy ROOT_REPO:/ by design.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ck3lens.world_adapter_v2 import WorldAdapterV2
from ck3lens.impl.dir_ops import (
    ck3_dir_impl,
    set_session_home_root,
)
from ck3lens.leak_detector import check_no_host_paths


@pytest.fixture(autouse=True)
def _reset_home():
    """Reset session home root to default before each test."""
    set_session_home_root("ck3raven_data")
    yield
    set_session_home_root("ck3raven_data")


# =========================================================================
# E2E-01: Full Resolve → list Round-Trip
# =========================================================================

class TestE2E01ResolveListRoundTrip:
    """Resolve a path via WA2, recover host path, list via ck3_dir,
    verify no host paths leak."""

    def test_full_round_trip(self, wa2: WorldAdapterV2) -> None:
        # 1. Resolve
        reply, ref = wa2.resolve("root:repo/src", require_exists=True)
        assert reply.reply_type == "S"
        assert ref is not None

        # 2. Host path recovery
        host_paths = wa2.host_path(ref)
        assert len(host_paths) == 1
        assert isinstance(host_paths[0], Path)

        # 3. List via ck3_dir
        data = ck3_dir_impl("list", path="root:repo/src", wa2=wa2)
        assert "entries" in data

        # 4. Leak detector
        check_no_host_paths(data, context="E2E-01")

        # 5. Entries contain server.py
        names = [e["name"] for e in data["entries"]]
        assert "server.py" in names


# =========================================================================
# E2E-02: cd Then Relative list
# =========================================================================

class TestE2E02CdThenRelativeList:
    """Change home to root:repo, then list with a relative path."""

    def test_cd_then_relative(self, wa2: WorldAdapterV2) -> None:
        # 1. cd to repo
        cd_result = ck3_dir_impl("cd", path="root:repo", wa2=wa2)
        assert cd_result["home"] == "root:repo/"

        # 2. list with relative path "src" (resolves as root:repo/src)
        data = ck3_dir_impl("list", path="src", wa2=wa2)
        assert "entries" in data

        # 3. Entries contain server.py
        names = [e["name"] for e in data["entries"]]
        assert "server.py" in names


