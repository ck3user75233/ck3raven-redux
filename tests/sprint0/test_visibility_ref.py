"""
VR-* — VisibilityRef tests.

Tests: VR-01 (Immutability), VR-02 (No Host Path), VR-03 (UUID4 Token),
       VR-04 (Unique Tokens per Resolve)
"""
from __future__ import annotations

import uuid

import pytest

from ck3lens.world_adapter_v2 import VisibilityRef, WorldAdapterV2


# =========================================================================
# VR-01: Immutability
# =========================================================================

class TestVR01Immutability:
    """VisibilityRef is frozen — attributes cannot be set or deleted."""

    def test_token_readable(self, wa2: WorldAdapterV2) -> None:
        reply, ref = wa2.resolve("root:repo/src/server.py")
        assert reply.reply_type == "S"
        assert ref is not None
        assert isinstance(ref.token, str)

    def test_ref_is_opaque(self, wa2: WorldAdapterV2) -> None:
        """VisibilityRef has only token — no session_abs field."""
        reply, ref = wa2.resolve("root:repo/src/server.py")
        assert ref is not None
        assert not hasattr(ref, "session_abs")

    def test_frozen_token(self, wa2: WorldAdapterV2) -> None:
        reply, ref = wa2.resolve("root:repo/src/server.py")
        assert ref is not None
        with pytest.raises((AttributeError, TypeError)):
            ref.token = "x"  # type: ignore[misc]

    def test_str_format(self, wa2: WorldAdapterV2) -> None:
        reply, ref = wa2.resolve("root:repo/src/server.py")
        assert ref is not None
        assert str(ref) == f"VisibilityRef({ref.token})"

    def test_repr_format(self, wa2: WorldAdapterV2) -> None:
        reply, ref = wa2.resolve("root:repo/src/server.py")
        assert ref is not None
        assert repr(ref) == f"VisibilityRef(token={ref.token!r})"


# =========================================================================
# VR-02: No Host Path on Object
# =========================================================================

class TestVR02NoHostPath:
    """VisibilityRef must contain no host-absolute path in any attribute."""

    _HOST_PREFIXES = ("C:\\", "D:\\", "/home/", "/Users/", "/mnt/", "\\\\")

    def test_token_is_not_path(self, wa2: WorldAdapterV2) -> None:
        reply, ref = wa2.resolve("root:repo/src/server.py")
        assert ref is not None
        for prefix in self._HOST_PREFIXES:
            assert not ref.token.startswith(prefix)

    def test_canonical_path_in_reply_not_ref(self, wa2: WorldAdapterV2) -> None:
        """Canonical session path lives in Reply.data["resolved"], not on ref."""
        reply, ref = wa2.resolve("root:repo/src/server.py")
        assert ref is not None
        assert reply.data["resolved"] == "root:repo/src/server.py"


# =========================================================================
# VR-03: Token is UUID4
# =========================================================================

class TestVR03TokenUUID4:
    """Each VisibilityRef has a valid UUID4 token."""

    def test_valid_uuid4(self, wa2: WorldAdapterV2) -> None:
        reply, ref = wa2.resolve("root:repo/src/server.py")
        assert ref is not None
        parsed = uuid.UUID(ref.token, version=4)
        assert parsed.version == 4


# =========================================================================
# VR-04: Unique Tokens per Resolve
# =========================================================================

class TestVR04UniqueTokens:
    """Every resolve() call mints a fresh token, even for the same input."""

    def test_two_resolves_different_tokens(self, wa2: WorldAdapterV2) -> None:
        reply1, ref1 = wa2.resolve("root:repo/src")
        reply2, ref2 = wa2.resolve("root:repo/src")
        assert ref1 is not None and ref2 is not None
        assert ref1.token != ref2.token

    def test_same_resolved_path(self, wa2: WorldAdapterV2) -> None:
        reply1, ref1 = wa2.resolve("root:repo/src")
        reply2, ref2 = wa2.resolve("root:repo/src")
        assert ref1 is not None and ref2 is not None
        assert reply1.data["resolved"] == reply2.data["resolved"]
