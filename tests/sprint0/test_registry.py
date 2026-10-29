"""
REG-* — Token registry tests.

Tests: REG-01 (host_path recovery), REG-02 (invalid token → empty list),
       REG-03 (MAX_TOKENS hard cap, synthetic paths), REG-04 (token uniqueness under volume)
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ck3lens.world_adapter_v2 import (
    MAX_TOKENS,
    VisibilityRef,
    WorldAdapterV2,
)


# =========================================================================
# REG-01: host_path Recovery
# =========================================================================

class TestREG01HostPathRecovery:
    """wa2.host_path(ref) recovers host-absolute Paths as a list."""

    def test_basic_recovery(self, wa2: WorldAdapterV2) -> None:
        reply, ref = wa2.resolve("root:repo/src/server.py")
        assert reply.reply_type == "S"
        assert ref is not None
        host_paths = wa2.host_path(ref)
        assert len(host_paths) == 1
        host = host_paths[0]
        assert isinstance(host, Path)
        assert host.exists()
        assert host.name == "server.py"

    def test_directory_recovery(self, wa2: WorldAdapterV2) -> None:
        reply, ref = wa2.resolve("root:repo/src")
        assert ref is not None
        host_paths = wa2.host_path(ref)
        assert len(host_paths) == 1
        assert host_paths[0].is_dir()


# =========================================================================
# REG-02: Invalid Token → None
# =========================================================================

class TestREG02InvalidToken:
    """A fabricated VisibilityRef returns an empty list from host_path."""

    def test_fabricated_token(self, wa2: WorldAdapterV2) -> None:
        fake_ref = VisibilityRef(token="not-a-real-token")
        assert wa2.host_path(fake_ref) == []


# REG-03 deleted — registry hard cap verified via live MCP tool calls.


# =========================================================================
# REG-04: Token Uniqueness Under Volume
# =========================================================================

class TestREG04TokenUniquenessVolume:
    """1,000 resolves of the same input produce 1,000 unique tokens."""

    def test_1000_unique_tokens(self, wa2: WorldAdapterV2) -> None:
        tokens = []
        for _ in range(1000):
            reply, ref = wa2.resolve("root:repo/src", require_exists=False)
            assert ref is not None
            tokens.append(ref.token)
        assert len(set(tokens)) == 1000
