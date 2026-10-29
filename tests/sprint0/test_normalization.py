"""
NORM-* — Input normalization tests.

Tests: NORM-02 (Legacy mod:Name:/), NORM-03 (Emitter never produces legacy forms)

NORM-01 (Legacy ROOT_X:/) was deleted — WA2 intentionally rejects
legacy ROOT_ syntax. No fallbacks, no legacy formats.
"""
from __future__ import annotations

import re

import pytest

from ck3lens.world_adapter_v2 import WorldAdapterV2


# =========================================================================
# NORM-02: Legacy mod:Name:/ Accepted
# =========================================================================

class TestNORM02LegacyModAccepted:
    """Sprint 0 accepts mod:Name:/path (colon-slash separator) and normalizes."""

    def test_legacy_mod_colon_slash(self, wa2: WorldAdapterV2) -> None:
        reply, ref = wa2.resolve("mod:TestModA:/common/traits")
        assert reply.reply_type == "S"
        assert ref is not None
        assert reply.data["resolved"] == "mod:TestModA/common/traits"


# =========================================================================
# NORM-03: Emitter Never Produces Legacy Forms
# =========================================================================

class TestNORM03NeverEmitsLegacy:
    """No VisibilityRef contains ROOT_ prefix or :/ separator."""

    _LEGACY_ROOT_RE = re.compile(r"ROOT_")
    _LEGACY_MOD_COLON_SLASH_RE = re.compile(r"mod:.*:/")

    @pytest.mark.parametrize(
        "input_str",
        [
            "root:repo/src/server.py",
            "mod:TestModA/common",
            "mod:TestModA:/common",
            "root:game/common/traits/00_traits.txt",
        ],
    )
    def test_session_abs_no_legacy_root(self, wa2: WorldAdapterV2, input_str: str) -> None:
        reply, ref = wa2.resolve(input_str)
        if reply.reply_type == "S" and ref is not None:
            resolved = reply.data["resolved"]
            assert not self._LEGACY_ROOT_RE.search(resolved), (
                f"Legacy ROOT_ found in resolved: {resolved}"
            )

    @pytest.mark.parametrize(
        "input_str",
        [
            "mod:TestModA/common",
            "mod:TestModA:/common",
        ],
    )
    def test_session_abs_no_legacy_mod(self, wa2: WorldAdapterV2, input_str: str) -> None:
        reply, ref = wa2.resolve(input_str)
        if reply.reply_type == "S" and ref is not None:
            resolved = reply.data["resolved"]
            assert not self._LEGACY_MOD_COLON_SLASH_RE.search(resolved), (
                f"Legacy mod colon-slash found in resolved: {resolved}"
            )

    @pytest.mark.parametrize(
        "input_str",
        [
            "root:repo/src/server.py",
        ],
    )
    def test_root_category_lowercase(self, wa2: WorldAdapterV2, input_str: str) -> None:
        reply, ref = wa2.resolve(input_str)
        if reply.reply_type == "S":
            root_key = reply.data.get("root_key")
            if root_key:
                assert root_key == root_key.lower(), (
                    f"root_key not lowercase: {root_key}"
                )
                assert not root_key.startswith("ROOT_"), (
                    f"root_key uses enum form: {root_key}"
                )
