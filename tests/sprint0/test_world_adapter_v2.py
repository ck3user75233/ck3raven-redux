"""
WA2-* — WorldAdapterV2 resolution tests.

Tests: WA2-01 through WA2-10, including the non-truncation guarantee.
"""
from __future__ import annotations

import pytest

from ck3lens.world_adapter_v2 import WorldAdapterV2


# =========================================================================
# WA2-01: Root Address Resolution
# =========================================================================

class TestWA201RootResolution:
    """Canonical root:<key>/<path> addresses resolve correctly."""

    @pytest.mark.parametrize(
        "input_str, expected_abs",
        [
            ("root:repo/src/server.py", "root:repo/src/server.py"),
            ("root:repo/src", "root:repo/src"),
            ("root:ck3raven_data/wip/analysis.py", "root:ck3raven_data/wip/analysis.py"),
            ("root:game/common/traits/00_traits.txt", "root:game/common/traits/00_traits.txt"),
        ],
    )
    def test_resolves_ok(self, wa2: WorldAdapterV2, input_str: str, expected_abs: str) -> None:
        reply, ref = wa2.resolve(input_str)
        assert reply.reply_type == "S", f"Expected success for {input_str}, got: {reply.message}"
        assert ref is not None
        assert reply.data["resolved"] == expected_abs


# =========================================================================
# WA2-02: Mod Address Resolution
# =========================================================================

class TestWA202ModResolution:
    """Canonical mod:<Name>/<path> addresses resolve correctly."""

    @pytest.mark.parametrize(
        "input_str, expected_abs",
        [
            ("mod:TestModA/common/traits/zzz_patch.txt", "mod:TestModA/common/traits/zzz_patch.txt"),
            ("mod:TestModA/common", "mod:TestModA/common"),
        ],
    )
    def test_resolves_ok(self, wa2: WorldAdapterV2, input_str: str, expected_abs: str) -> None:
        reply, ref = wa2.resolve(input_str)
        assert reply.reply_type == "S", f"Expected success for {input_str}, got: {reply.message}"
        assert ref is not None
        assert reply.data["resolved"] == expected_abs


# =========================================================================
# WA2-03: Unknown Root Key → Invalid
# =========================================================================

class TestWA203UnknownRootKey:
    """Using an invalid root key fails."""

    def test_bogus_key(self, wa2: WorldAdapterV2) -> None:
        reply, ref = wa2.resolve("root:bogus/foo")
        assert reply.reply_type != "S"
        assert ref is None
        assert reply.message is not None


# =========================================================================
# WA2-04: Unknown Mod → Invalid
# =========================================================================

class TestWA204UnknownMod:
    """Referencing a mod not in session.mods fails."""

    def test_nonexistent_mod(self, wa2: WorldAdapterV2) -> None:
        reply, ref = wa2.resolve("mod:NonexistentMod/foo")
        assert reply.reply_type != "S"
        assert ref is None

    def test_near_match_suggested_for_punctuation_difference(self, wa2: WorldAdapterV2) -> None:
        reply, ref = wa2.resolve("mod:EC724 LSC Submod/common/traits/submod_patch.txt")
        assert reply.reply_type != "S"
        assert ref is None
        assert reply.data["not_found_kind"] == "mod"
        assert reply.data["mod_name"] == "EC724 LSC Submod"
        assert "mod:EC724-LSC Submod/common/traits/submod_patch.txt" in reply.data["near_matches"]


# =========================================================================
# WA2-05: Host-Absolute Path → Invalid (STRICT)
# =========================================================================

class TestWA205HostAbsoluteRejected:
    """Raw host paths are ALWAYS rejected, even if they fall under a known root (Invariant A)."""

    @pytest.mark.parametrize(
        "input_str",
        [
            # Paths that don't map to any root
            r"C:\Users\test\file.txt",
            "/home/test/file.txt",
            "/Users/nate/Documents/foo",
            # UNC paths
            r"\\server\share\file.txt",
        ],
    )
    def test_host_path_rejected(self, wa2: WorldAdapterV2, input_str: str) -> None:
        reply, ref = wa2.resolve(input_str)
        assert reply.reply_type != "S", f"Should reject host-absolute path: {input_str}"
        assert ref is None

    def test_host_path_under_known_root_still_rejected(
        self, wa2: WorldAdapterV2, tmp_roots: dict
    ) -> None:
        """
        CRITICAL: host-absolute paths under known roots must NOT reverse-resolve.
        This was the bug: C:\\...\\ck3raven_data\\wip\\... was being converted to root:ck3raven_data/wip/...
        Canonical paths are the ONLY accepted input format.
        """
        # Build an absolute path that IS under a known root
        wip_path = tmp_roots["ck3raven_data"] / "wip" / "analysis.py"
        assert wip_path.exists(), "Test file must exist"

        # Try Windows-style path
        reply, ref = wa2.resolve(str(wip_path))
        assert reply.reply_type != "S", f"Should reject: {wip_path}"
        assert ref is None

        # Try with forward slashes
        reply, ref = wa2.resolve(str(wip_path).replace("\\", "/"))
        assert reply.reply_type != "S"
        assert ref is None

    def test_host_path_under_repo_rejected(
        self, wa2: WorldAdapterV2, tmp_roots: dict
    ) -> None:
        """Host-absolute path under repo root must also be rejected."""
        server_path = tmp_roots["repo"] / "src" / "server.py"
        assert server_path.exists()

        reply, ref = wa2.resolve(str(server_path))
        assert reply.reply_type != "S"
        assert ref is None


# =========================================================================
# WA2-05b: Legacy ROOT_X Format → Invalid
# =========================================================================

class TestWA205bLegacyFormatRejected:
    """Legacy ROOT_X:/path format is no longer supported."""

    @pytest.mark.parametrize(
        "input_str",
        [
            "ROOT_REPO:/src/server.py",
            "ROOT_CK3RAVEN_DATA:/wip/analysis.py",
            "ROOT_GAME:/common/traits/00_traits.txt",
        ],
    )
    def test_legacy_format_rejected(self, wa2: WorldAdapterV2, input_str: str) -> None:
        reply, ref = wa2.resolve(input_str)
        assert reply.reply_type != "S", f"Should reject legacy format: {input_str}"
        assert ref is None


# =========================================================================
# WA2-05c: Only Canonical Format Accepted
# =========================================================================

class TestWA205cOnlyCanonicalAccepted:
    """Only root: and mod: prefixes are valid input formats."""

    @pytest.mark.parametrize(
        "input_str",
        [
            # Invalid prefixes
            "file:repo/src/server.py",
            "path:repo/src/server.py",
            "local:repo/src/server.py",
            "internal:repo/src/server.py",
            # Missing prefix
            "repo/src/server.py",
            "src/server.py",
            # Wrong prefix format
            "Root:repo/src/server.py",
            "ROOT:repo/src/server.py",
            "Mod:TestModA/common/traits/zzz_patch.txt",
            "MOD:TestModA/common/traits/zzz_patch.txt",
        ],
    )
    def test_invalid_prefix_rejected(self, wa2: WorldAdapterV2, input_str: str) -> None:
        reply, ref = wa2.resolve(input_str)
        assert reply.reply_type != "S", f"Should reject invalid format: {input_str}"
        assert ref is None


# =========================================================================
# WA2-06: Path Traversal Rejected
# =========================================================================

class TestWA206PathTraversal:
    """.. components that escape a root are rejected."""

    @pytest.mark.parametrize(
        "input_str",
        [
            "root:repo/../../../etc/passwd",
            "mod:TestModA/../../secret",
        ],
    )
    def test_traversal_rejected(self, wa2: WorldAdapterV2, input_str: str) -> None:
        reply, ref = wa2.resolve(input_str)
        assert reply.reply_type != "S"
        assert ref is None


# =========================================================================
# WA2-07: require_exists=True, Path Missing → Invalid
# =========================================================================

class TestWA207RequireExistsMissing:
    """When require_exists=True (default), non-existent paths fail."""

    def test_missing_file(self, wa2: WorldAdapterV2) -> None:
        reply, ref = wa2.resolve("root:repo/nonexistent.py")
        assert reply.reply_type != "S"
        assert ref is None

    def test_missing_component_near_match(self, wa2: WorldAdapterV2) -> None:
        reply, ref = wa2.resolve("root:repo/dcs")
        assert reply.reply_type != "S"
        assert ref is None
        assert reply.data["not_found_kind"] == "path"
        assert "root:repo/docs" in reply.data["near_matches"]


# =========================================================================
# WA2-08: require_exists=False, Path Missing → Success
# =========================================================================

class TestWA208RequireExistsFalse:
    """When require_exists=False, structurally valid but missing paths succeed."""

    def test_missing_ok(self, wa2: WorldAdapterV2) -> None:
        reply, ref = wa2.resolve("root:repo/nonexistent.py", require_exists=False)
        assert reply.reply_type == "S"
        assert ref is not None

    def test_host_path_recovery(self, wa2: WorldAdapterV2, tmp_roots: dict) -> None:
        reply, ref = wa2.resolve("root:repo/nonexistent.py", require_exists=False)
        assert ref is not None
        host_paths = wa2.host_path(ref)
        assert len(host_paths) == 1
        assert host_paths[0].parent == tmp_roots["repo"]


# =========================================================================
# WA2-09: require_exists=False, Root Still Validated
# =========================================================================

class TestWA209RequireExistsFalseStillValidated:
    """Even with require_exists=False, invalid root keys or path escapes still fail."""

    def test_bogus_key(self, wa2: WorldAdapterV2) -> None:
        reply, ref = wa2.resolve("root:bogus/foo", require_exists=False)
        assert reply.reply_type != "S"
        assert ref is None

    def test_traversal_escape(self, wa2: WorldAdapterV2) -> None:
        reply, ref = wa2.resolve("root:repo/../../escape", require_exists=False)
        assert reply.reply_type != "S"
        assert ref is None


# =========================================================================
# WA2-10: Non-Truncation Guarantee
# =========================================================================

class TestWA210NonTruncation:
    """require_exists=False must NOT truncate to the last existing ancestor."""

    def test_full_path_preserved(self, wa2: WorldAdapterV2) -> None:
        reply, ref = wa2.resolve("root:repo/src/does_not_exist/file.py", require_exists=False)
        assert reply.reply_type == "S"
        assert ref is not None
        assert reply.data["relative_path"] == "src/does_not_exist/file.py"
        assert reply.data["resolved"] == "root:repo/src/does_not_exist/file.py"
