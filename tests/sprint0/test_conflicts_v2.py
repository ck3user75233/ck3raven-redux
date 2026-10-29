"""
CONF-* — ck3_conflicts v2 stress tests.

Tests the full pipeline: server.py → unified_tools.py → enforcement_v2.execute_db()
→ capability_matrix_v2 → executors.py

Layers tested:
  - CM2: find_database_rule for all 3 commands × 2 modes
  - CM2: scope_to_session_mods condition gating
  - EN2: execute_db routing and denial
  - Executors: conflict_symbols, conflict_files, conflict_summary
  - Routing: ck3_conflicts_impl thin entry point
  - Edge cases: empty mods, None db, None session, bad commands, SQL injection

Naming convention: CONF-XX where XX is the test number.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_MCP_ROOT = _REPO_ROOT / "tools" / "ck3lens_mcp"

if str(_MCP_ROOT) not in sys.path:
    sys.path.insert(0, str(_MCP_ROOT))

from ck3raven.core.reply import Reply, TraceInfo, MetaInfo
from safety import ReplyBuilder


# =============================================================================
# Fixtures
# =============================================================================

@dataclass
class FakeMod:
    name: str
    cvid: int | None
    load_order: int
    path: Path = Path(".")


class FakeSession:
    def __init__(self, mods: list[FakeMod]):
        self.mods = mods

    def get_mod(self, name: str) -> FakeMod | None:
        return next((m for m in self.mods if m.name == name), None)


def _make_rb(tool: str = "ck3_conflicts") -> ReplyBuilder:
    trace = TraceInfo(trace_id="test-trace", session_id="test-session")
    return ReplyBuilder(trace, tool=tool)


def _session_with_mods(*specs: tuple[str, int, int]) -> FakeSession:
    """Create a FakeSession with mods: (name, cvid, load_order)."""
    return FakeSession([FakeMod(name=n, cvid=c, load_order=lo) for n, c, lo in specs])


STANDARD_SESSION = _session_with_mods(
    ("ModA", 1, 0),
    ("ModB", 2, 1),
    ("ModC", 3, 2),
)


# =============================================================================
# CONF-01 through CONF-06: CM2 — find_database_rule
# =============================================================================

class TestCONF01CM2RuleDiscovery:
    """find_database_rule returns rules for all 3 conflict commands in both modes."""

    @pytest.mark.parametrize("command", ["symbols", "files", "summary"])
    @pytest.mark.parametrize("mode", ["ck3lens", "ck3raven-dev"])
    def test_rule_exists(self, mode: str, command: str):
        from ck3lens.capability_matrix_v2 import find_database_rule
        rule = find_database_rule(mode, "ck3_conflicts", command)
        assert rule is not None, f"No rule for ck3_conflicts/{command} in {mode}"


class TestCONF02CM2UnknownCommand:
    """find_database_rule returns None for unknown commands."""

    @pytest.mark.parametrize("command", ["bogus", "delete", "write", "", "SYMBOLS"])
    def test_no_rule(self, command: str):
        from ck3lens.capability_matrix_v2 import find_database_rule
        rule = find_database_rule("ck3lens", "ck3_conflicts", command)
        assert rule is None


class TestCONF03CM2UnknownTool:
    """find_database_rule returns None for unknown tools."""

    @pytest.mark.parametrize("tool", ["ck3_bogus", "ck3_file", "ck3_exec"])
    def test_no_rule(self, tool: str):
        from ck3lens.capability_matrix_v2 import find_database_rule
        rule = find_database_rule("ck3lens", tool, "symbols")
        assert rule is None


class TestCONF04CM2UnknownMode:
    """find_database_rule returns None for unknown modes."""

    @pytest.mark.parametrize("mode", ["", "invalid", "ck3lens-dev"])
    def test_no_rule(self, mode: str):
        from ck3lens.capability_matrix_v2 import find_database_rule
        rule = find_database_rule(mode, "ck3_conflicts", "symbols")
        assert rule is None


class TestCONF05CM2ScopeConditionCk3lens:
    """ck3lens conflict rules carry scope_to_session_mods condition."""

    @pytest.mark.parametrize("command", ["symbols", "files", "summary"])
    def test_has_scope_condition(self, command: str):
        from ck3lens.capability_matrix_v2 import find_database_rule
        rule = find_database_rule("ck3lens", "ck3_conflicts", command)
        assert rule is not None
        assert len(rule.conditions) == 1
        assert rule.conditions[0].name == "scope_to_session_mods"


class TestCONF06CM2NoConditionDev:
    """ck3raven-dev conflict rules have no conditions."""

    @pytest.mark.parametrize("command", ["symbols", "files", "summary"])
    def test_no_conditions(self, command: str):
        from ck3lens.capability_matrix_v2 import find_database_rule
        rule = find_database_rule("ck3raven-dev", "ck3_conflicts", command)
        assert rule is not None
        assert len(rule.conditions) == 0


# =============================================================================
# CONF-07 through CONF-10: scope_to_session_mods condition
# =============================================================================

class TestCONF07ScopeConditionPasses:
    """scope_to_session_mods returns True when session has indexed mods."""

    def test_passes_with_cvids(self):
        from ck3lens.capability_matrix_v2 import scope_to_session_mods
        cond = scope_to_session_mods()
        assert cond.check(session=STANDARD_SESSION) is True


class TestCONF08ScopeConditionFailsNoSession:
    """scope_to_session_mods returns False with no session."""

    def test_fails_no_session(self):
        from ck3lens.capability_matrix_v2 import scope_to_session_mods
        cond = scope_to_session_mods()
        assert cond.check(session=None) is False


class TestCONF09ScopeConditionFailsEmptyMods:
    """scope_to_session_mods returns False with empty mods list."""

    def test_fails_empty_mods(self):
        from ck3lens.capability_matrix_v2 import scope_to_session_mods
        cond = scope_to_session_mods()
        session = FakeSession([])
        assert cond.check(session=session) is False


class TestCONF10ScopeConditionFailsNullCvids:
    """scope_to_session_mods returns False when all cvids are None."""

    def test_fails_null_cvids(self):
        from ck3lens.capability_matrix_v2 import scope_to_session_mods
        cond = scope_to_session_mods()
        session = FakeSession([FakeMod(name="X", cvid=None, load_order=0)])
        assert cond.check(session=session) is False


# =============================================================================
# CONF-11 through CONF-16: execute_db routing
# =============================================================================

class TestCONF11ExecuteDbDeniesUnknownCommand:
    """execute_db returns D for unknown command."""

    def test_denied(self):
        from ck3lens.policy.enforcement_v2 import execute_db
        rb = _make_rb()
        with patch("ck3lens.agent_mode.get_agent_mode", return_value="ck3raven-dev"):
            reply = execute_db(
                rb, mode="ck3raven-dev", tool="ck3_conflicts", command="bogus",
                params={}, session=STANDARD_SESSION,
            )
        assert reply.reply_type == "D"
        assert reply.code == "EN-GATE-D-001"


class TestCONF12ExecuteDbDeniesUnknownMode:
    """execute_db returns D for unknown mode."""

    def test_denied(self):
        from ck3lens.policy.enforcement_v2 import execute_db
        rb = _make_rb()
        reply = execute_db(
            rb, mode="invalid_mode", tool="ck3_conflicts", command="symbols",
            params={"session": STANDARD_SESSION, "db": MagicMock()},
            session=STANDARD_SESSION,
        )
        assert reply.reply_type == "D"


class TestCONF13ExecuteDbDeniesNoSessionCk3lens:
    """ck3lens mode: execute_db denies when session has no indexed mods."""

    def test_denied_empty_session(self):
        from ck3lens.policy.enforcement_v2 import execute_db
        rb = _make_rb()
        empty_session = FakeSession([])
        reply = execute_db(
            rb, mode="ck3lens", tool="ck3_conflicts", command="symbols",
            params={"session": empty_session, "db": MagicMock()},
            session=empty_session,
        )
        assert reply.reply_type == "D"

    def test_denied_null_cvid_session(self):
        from ck3lens.policy.enforcement_v2 import execute_db
        rb = _make_rb()
        null_session = FakeSession([FakeMod(name="X", cvid=None, load_order=0)])
        reply = execute_db(
            rb, mode="ck3lens", tool="ck3_conflicts", command="files",
            params={"session": null_session, "db": MagicMock()},
            session=null_session,
        )
        assert reply.reply_type == "D"


class TestCONF14ExecuteDbAllowsDevNoConditions:
    """ck3raven-dev mode: execute_db allows without scoping condition."""

    @pytest.mark.parametrize("command", ["symbols", "files", "summary"])
    def test_allowed(self, command: str):
        from ck3lens.policy.enforcement_v2 import execute_db

        rb = _make_rb()
        mock_db = MagicMock()
        mock_db._get_symbol_conflicts_internal.return_value = {
            "conflict_count": 0, "conflicts": [],
        }
        mock_db.conn.execute.return_value.fetchall.return_value = []

        with patch("ck3lens.impl.conflict_ops.detect_primary_file_conflicts",
                    return_value={"conflicts": [], "summary": {
                        "total_originals_with_conflicts": 0,
                        "full_overrides": 0, "partial_overrides": 0,
                        "failed_patch_warnings": 0,
                    }}):
            reply = execute_db(
                rb, mode="ck3raven-dev", tool="ck3_conflicts", command=command,
                params={"session": STANDARD_SESSION, "db": mock_db},
                session=STANDARD_SESSION,
            )
        assert reply.reply_type == "S", f"Expected S, got {reply.reply_type}: {reply.message}"


class TestCONF15ExecuteDbDispatchesToCorrectExecutor:
    """execute_db dispatches to the registered executor function."""

    @pytest.mark.parametrize("command,executor_name", [
        ("symbols", "conflict_symbols"),
        ("files", "conflict_files"),
        ("summary", "conflict_summary"),
    ])
    def test_dispatches(self, command: str, executor_name: str):
        from ck3lens.policy.executors import EXECUTORS
        executor = EXECUTORS.get(("ck3_conflicts", command))
        assert executor is not None
        assert executor.__name__ == executor_name


class TestCONF16ExecuteDbReturnsExecutorErrorCode:
    """execute_db returns E when no executor registered (hypothetical)."""

    def test_no_executor(self):
        from ck3lens.policy.enforcement_v2 import execute_db
        from ck3lens.policy import executors
        rb = _make_rb()

        # Temporarily remove executor
        original = executors.EXECUTORS.pop(("ck3_conflicts", "symbols"), None)
        try:
            reply = execute_db(
                rb, mode="ck3raven-dev", tool="ck3_conflicts", command="symbols",
                params={"session": STANDARD_SESSION, "db": MagicMock()},
                session=STANDARD_SESSION,
            )
            assert reply.reply_type == "E"
            assert reply.code == "EN-EXEC-E-001"
        finally:
            if original:
                executors.EXECUTORS[("ck3_conflicts", "symbols")] = original


# =============================================================================
# CONF-17 through CONF-27: Executor stress tests
# =============================================================================

class TestCONF17SymbolsExecutorNoDb:
    """conflict_symbols returns I when db is None."""

    def test_no_db(self):
        from ck3lens.policy.executors import conflict_symbols
        rb = _make_rb()
        reply = conflict_symbols(rb, policy_ctx={}, db=None, session=STANDARD_SESSION)
        assert reply.reply_type == "I"


class TestCONF18SymbolsExecutorNoSession:
    """conflict_symbols returns I when session is None."""

    def test_no_session(self):
        from ck3lens.policy.executors import conflict_symbols
        rb = _make_rb()
        reply = conflict_symbols(rb, policy_ctx={}, db=MagicMock(), session=None)
        assert reply.reply_type == "I"


class TestCONF19SymbolsExecutorEmptyMods:
    """conflict_symbols returns I with empty session.mods."""

    def test_empty_mods(self):
        from ck3lens.policy.executors import conflict_symbols
        rb = _make_rb()
        reply = conflict_symbols(rb, policy_ctx={}, db=MagicMock(),
                                 session=FakeSession([]))
        assert reply.reply_type == "I"


class TestCONF20SymbolsExecutorSuccess:
    """conflict_symbols returns S with valid data."""

    def test_success(self):
        from ck3lens.policy.executors import conflict_symbols
        rb = _make_rb()
        mock_db = MagicMock()
        mock_db._get_symbol_conflicts_internal.return_value = {
            "conflict_count": 2,
            "conflicts": [
                {"name": "brave", "symbol_type": "trait", "source_count": 2,
                 "sources": [{"mod": "A", "load_order": 0}, {"mod": "B", "load_order": 1}]},
                {"name": "craven", "symbol_type": "trait", "source_count": 2,
                 "sources": [{"mod": "A", "load_order": 0}, {"mod": "C", "load_order": 2}]},
            ],
        }
        reply = conflict_symbols(rb, policy_ctx={}, db=mock_db, session=STANDARD_SESSION)
        assert reply.reply_type == "S"
        assert reply.data["conflict_count"] == 2


class TestCONF21SymbolsExecutorNameFilter:
    """conflict_symbols filters by symbol_names (case-insensitive)."""

    def test_name_filter(self):
        from ck3lens.policy.executors import conflict_symbols
        rb = _make_rb()
        mock_db = MagicMock()
        mock_db._get_symbol_conflicts_internal.return_value = {
            "conflict_count": 3,
            "conflicts": [
                {"name": "brave", "symbol_type": "trait"},
                {"name": "craven", "symbol_type": "trait"},
                {"name": "generous", "symbol_type": "trait"},
            ],
        }
        reply = conflict_symbols(
            rb, policy_ctx={}, db=mock_db, session=STANDARD_SESSION,
            symbol_names=["Brave", "CRAVEN"],
        )
        assert reply.reply_type == "S"
        assert reply.data["conflict_count"] == 2
        names = {c["name"] for c in reply.data["conflicts"]}
        assert names == {"brave", "craven"}

    def test_name_filter_no_match(self):
        from ck3lens.policy.executors import conflict_symbols
        rb = _make_rb()
        mock_db = MagicMock()
        mock_db._get_symbol_conflicts_internal.return_value = {
            "conflict_count": 1,
            "conflicts": [{"name": "brave", "symbol_type": "trait"}],
        }
        reply = conflict_symbols(
            rb, policy_ctx={}, db=mock_db, session=STANDARD_SESSION,
            symbol_names=["nonexistent"],
        )
        assert reply.reply_type == "S"
        assert reply.data["conflict_count"] == 0
        assert reply.data["conflicts"] == []


class TestCONF22SymbolsExecutorPassesCvidsCorrectly:
    """conflict_symbols derives cvids inline from session.mods and forwards them."""

    def test_cvids_match_session(self):
        from ck3lens.policy.executors import conflict_symbols
        rb = _make_rb()
        mock_db = MagicMock()
        mock_db._get_symbol_conflicts_internal.return_value = {
            "conflict_count": 0, "conflicts": [],
        }
        session = _session_with_mods(("A", 10, 0), ("B", 20, 1), ("C", None, 2))
        conflict_symbols(rb, policy_ctx={}, db=mock_db, session=session)

        call_kwargs = mock_db._get_symbol_conflicts_internal.call_args[1]
        assert call_kwargs["cvids"] == frozenset({10, 20})  # None filtered out


class TestCONF23SymbolsExecutorPassesPathAsGameFolder:
    """conflict_symbols passes path parameter as game_folder to internal function."""

    def test_path_forwarded(self):
        from ck3lens.policy.executors import conflict_symbols
        rb = _make_rb()
        mock_db = MagicMock()
        mock_db._get_symbol_conflicts_internal.return_value = {
            "conflict_count": 0, "conflicts": [],
        }
        conflict_symbols(
            rb, policy_ctx={}, db=mock_db, session=STANDARD_SESSION,
            path="common/traits",
        )
        call_kwargs = mock_db._get_symbol_conflicts_internal.call_args[1]
        assert call_kwargs["game_folder"] == "common/traits"


class TestCONF24FilesExecutorNoDb:
    """conflict_files returns I when db is None."""

    def test_no_db(self):
        from ck3lens.policy.executors import conflict_files
        rb = _make_rb()
        reply = conflict_files(rb, policy_ctx={}, db=None, session=STANDARD_SESSION)
        assert reply.reply_type == "I"


class TestCONF25FilesExecutorSuccess:
    """conflict_files returns S with valid data."""

    def test_success(self):
        from ck3lens.policy.executors import conflict_files
        rb = _make_rb()
        mock_db = MagicMock()

        with patch("ck3lens.impl.conflict_ops.detect_primary_file_conflicts",
                    return_value={
                        "conflicts": [{"directory": "common/traits", "original_file": "00_traits.txt"}],
                        "summary": {
                            "total_originals_with_conflicts": 1,
                            "full_overrides": 1,
                            "partial_overrides": 0,
                            "failed_patch_warnings": 0,
                        },
                    }) as mock_detect:
            reply = conflict_files(
                rb, policy_ctx={}, db=mock_db, session=STANDARD_SESSION,
                path="common/traits", limit=50,
            )
            assert reply.reply_type == "S"
            assert reply.data["summary"]["total_originals_with_conflicts"] == 1

            # Verify correct params forwarded
            call_kwargs = mock_detect.call_args[1]
            assert call_kwargs["conn"] is mock_db.conn
            assert call_kwargs["game_folder"] == "common/traits"
            assert call_kwargs["limit"] == 50
            assert call_kwargs["cvids"] == frozenset({1, 2, 3})


class TestCONF26SummaryExecutorNoDb:
    """conflict_summary returns I when db is None."""

    def test_no_db(self):
        from ck3lens.policy.executors import conflict_summary
        rb = _make_rb()
        reply = conflict_summary(rb, policy_ctx={}, db=None, session=STANDARD_SESSION)
        assert reply.reply_type == "I"


class TestCONF27SummaryExecutorSuccess:
    """conflict_summary returns S with both symbol and file summaries."""

    def test_success(self):
        from ck3lens.policy.executors import conflict_summary
        rb = _make_rb()
        mock_db = MagicMock()
        # Mock the SQL query for symbol type breakdown
        mock_db.conn.execute.return_value.fetchall.return_value = [
            {"symbol_type": "trait", "conflict_count": 5},
            {"symbol_type": "event", "conflict_count": 3},
        ]

        with patch("ck3lens.impl.conflict_ops.detect_primary_file_conflicts",
                    return_value={
                        "conflicts": [],
                        "summary": {
                            "total_originals_with_conflicts": 10,
                            "full_overrides": 7,
                            "partial_overrides": 2,
                            "failed_patch_warnings": 1,
                        },
                    }):
            reply = conflict_summary(
                rb, policy_ctx={}, db=mock_db, session=STANDARD_SESSION,
            )
        assert reply.reply_type == "S"
        assert reply.data["total_symbol_conflicts"] == 8
        assert reply.data["by_type"] == {"trait": 5, "event": 3}
        assert reply.data["file_conflict_summary"]["total_originals_with_conflicts"] == 10


# =============================================================================
# CONF-28 through CONF-32: Routing layer (ck3_conflicts_impl)
# =============================================================================

class TestCONF28ImplNoMode:
    """ck3_conflicts_impl returns I when mode not initialized."""

    def test_no_mode(self):
        from ck3lens.unified_tools import ck3_conflicts_impl
        rb = _make_rb()
        with patch("ck3lens.agent_mode.get_agent_mode", return_value=None):
            reply = ck3_conflicts_impl(
                command="symbols", session=STANDARD_SESSION,
                db=MagicMock(), rb=rb,
            )
        assert reply.reply_type == "I"
        assert reply.code == "WA-CFG-I-001"


class TestCONF29ImplNoSession:
    """ck3_conflicts_impl returns I when session is None."""

    def test_no_session(self):
        from ck3lens.unified_tools import ck3_conflicts_impl
        rb = _make_rb()
        with patch("ck3lens.agent_mode.get_agent_mode", return_value="ck3raven-dev"):
            reply = ck3_conflicts_impl(
                command="symbols", session=None,
                db=MagicMock(), rb=rb,
            )
        assert reply.reply_type == "I"
        assert reply.code == "MCP-SYS-I-001"


class TestCONF30ImplForwardsToExecuteDb:
    """ck3_conflicts_impl calls execute_db with correct params."""

    def test_forwards(self):
        from ck3lens.unified_tools import ck3_conflicts_impl
        rb = _make_rb()
        mock_db = MagicMock()

        with patch("ck3lens.agent_mode.get_agent_mode", return_value="ck3raven-dev"), \
             patch("ck3lens.policy.enforcement_v2.execute_db") as mock_exec_db:
            mock_exec_db.return_value = Reply(
                reply_type="S", code="WA-READ-S-001",
                message="ok", data={"conflict_count": 0},
                trace=TraceInfo(trace_id="t", session_id="s"),
                meta=MetaInfo(layer="WA", tool="ck3_conflicts"),
            )
            reply = ck3_conflicts_impl(
                command="files", symbol_type="trait",
                path="common/traits", limit=200,
                session=STANDARD_SESSION, db=mock_db, rb=rb,
            )

        mock_exec_db.assert_called_once()
        call_args = mock_exec_db.call_args
        assert call_args[1]["tool"] == "ck3_conflicts"
        assert call_args[1]["command"] == "files"
        assert call_args[1]["params"]["symbol_type"] == "trait"
        assert call_args[1]["params"]["path"] == "common/traits"
        assert call_args[1]["params"]["limit"] == 200


class TestCONF31ImplAllCommandsRouteToDb:
    """All 3 commands route through execute_db (not geographic execute)."""

    @pytest.mark.parametrize("command", ["symbols", "files", "summary"])
    def test_all_route_db(self, command: str):
        from ck3lens.unified_tools import ck3_conflicts_impl
        rb = _make_rb()

        with patch("ck3lens.agent_mode.get_agent_mode", return_value="ck3raven-dev"), \
             patch("ck3lens.policy.enforcement_v2.execute_db") as mock_exec_db:
            mock_exec_db.return_value = Reply(
                reply_type="S", code="WA-READ-S-001",
                message="ok", data={},
                trace=TraceInfo(trace_id="t", session_id="s"),
                meta=MetaInfo(layer="WA", tool="ck3_conflicts"),
            )
            ck3_conflicts_impl(
                command=command, session=STANDARD_SESSION,
                db=MagicMock(), rb=rb,
            )
        mock_exec_db.assert_called_once()
        assert mock_exec_db.call_args[1]["command"] == command


# =============================================================================
# CONF-32 through CONF-35: Edge cases and stress
# =============================================================================

class TestCONF32MixedCvidsMods:
    """Mods with mixed None/valid cvids: only valid ones used."""

    def test_mixed_cvids(self):
        from ck3lens.policy.executors import conflict_symbols
        rb = _make_rb()
        mock_db = MagicMock()
        mock_db._get_symbol_conflicts_internal.return_value = {
            "conflict_count": 0, "conflicts": [],
        }
        session = FakeSession([
            FakeMod(name="A", cvid=1, load_order=0),
            FakeMod(name="B", cvid=None, load_order=1),
            FakeMod(name="C", cvid=3, load_order=2),
            FakeMod(name="D", cvid=None, load_order=3),
        ])
        conflict_symbols(rb, policy_ctx={}, db=mock_db, session=session)

        call_kwargs = mock_db._get_symbol_conflicts_internal.call_args[1]
        assert call_kwargs["cvids"] == frozenset({1, 3})
        assert call_kwargs["load_order_map"] == {1: 0, 3: 2}


class TestCONF33LargeModCount:
    """Stress: 200 mods all with cvids."""

    def test_200_mods(self):
        from ck3lens.policy.executors import conflict_symbols
        rb = _make_rb()
        mock_db = MagicMock()
        mock_db._get_symbol_conflicts_internal.return_value = {
            "conflict_count": 0, "conflicts": [],
        }
        session = FakeSession([
            FakeMod(name=f"Mod{i}", cvid=i, load_order=i)
            for i in range(200)
        ])
        reply = conflict_symbols(rb, policy_ctx={}, db=mock_db, session=session)
        assert reply.reply_type == "S"

        call_kwargs = mock_db._get_symbol_conflicts_internal.call_args[1]
        assert len(call_kwargs["cvids"]) == 200


class TestCONF34LargeConflictResult:
    """Stress: executor handles 10,000 conflicts without issues."""

    def test_large_result(self):
        from ck3lens.policy.executors import conflict_symbols
        rb = _make_rb()
        mock_db = MagicMock()
        conflicts = [
            {"name": f"symbol_{i}", "symbol_type": "trait", "source_count": 2}
            for i in range(10_000)
        ]
        mock_db._get_symbol_conflicts_internal.return_value = {
            "conflict_count": 10_000,
            "conflicts": conflicts,
        }
        reply = conflict_symbols(rb, policy_ctx={}, db=mock_db, session=STANDARD_SESSION)
        assert reply.reply_type == "S"
        assert reply.data["conflict_count"] == 10_000


class TestCONF35NameFilterOnLargeResult:
    """Stress: symbol_names filter on 10,000 results picks correct subset."""

    def test_filter_large(self):
        from ck3lens.policy.executors import conflict_symbols
        rb = _make_rb()
        mock_db = MagicMock()
        conflicts = [
            {"name": f"symbol_{i}", "symbol_type": "trait", "source_count": 2}
            for i in range(10_000)
        ]
        mock_db._get_symbol_conflicts_internal.return_value = {
            "conflict_count": 10_000,
            "conflicts": conflicts,
        }
        reply = conflict_symbols(
            rb, policy_ctx={}, db=mock_db, session=STANDARD_SESSION,
            symbol_names=["symbol_0", "symbol_9999", "symbol_5000"],
        )
        assert reply.reply_type == "S"
        assert reply.data["conflict_count"] == 3
        names = {c["name"] for c in reply.data["conflicts"]}
        assert names == {"symbol_0", "symbol_5000", "symbol_9999"}


# =============================================================================
# CONF-36 through CONF-39: SQL injection / malicious input
# =============================================================================

class TestCONF36SymbolTypeInjection:
    """Malicious symbol_type passes through safely (parameterized internally)."""

    def test_injection_attempt(self):
        from ck3lens.policy.executors import conflict_symbols
        rb = _make_rb()
        mock_db = MagicMock()
        mock_db._get_symbol_conflicts_internal.return_value = {
            "conflict_count": 0, "conflicts": [],
        }
        # This should not cause SQL injection — it's passed as a parameter
        reply = conflict_symbols(
            rb, policy_ctx={}, db=mock_db, session=STANDARD_SESSION,
            symbol_type="'; DROP TABLE symbols; --",
        )
        assert reply.reply_type == "S"
        # Verify the string was passed through, not injected
        call_kwargs = mock_db._get_symbol_conflicts_internal.call_args[1]
        assert call_kwargs["symbol_type"] == "'; DROP TABLE symbols; --"


class TestCONF37PathInjection:
    """Malicious path passes through safely."""

    def test_injection_attempt(self):
        from ck3lens.policy.executors import conflict_symbols
        rb = _make_rb()
        mock_db = MagicMock()
        mock_db._get_symbol_conflicts_internal.return_value = {
            "conflict_count": 0, "conflicts": [],
        }
        reply = conflict_symbols(
            rb, policy_ctx={}, db=mock_db, session=STANDARD_SESSION,
            path="../../etc/passwd",
        )
        assert reply.reply_type == "S"


class TestCONF38SymbolNamesInjection:
    """Malicious symbol_names filter works safely."""

    def test_injection_names(self):
        from ck3lens.policy.executors import conflict_symbols
        rb = _make_rb()
        mock_db = MagicMock()
        mock_db._get_symbol_conflicts_internal.return_value = {
            "conflict_count": 1,
            "conflicts": [{"name": "brave", "symbol_type": "trait"}],
        }
        reply = conflict_symbols(
            rb, policy_ctx={}, db=mock_db, session=STANDARD_SESSION,
            symbol_names=["'; DROP TABLE symbols; --", "<script>alert(1)</script>"],
        )
        assert reply.reply_type == "S"
        # None of the malicious names match "brave", so filter removes all
        assert reply.data["conflict_count"] == 0


class TestCONF39NegativeLimit:
    """Negative limit is passed through (DB/function handles it)."""

    def test_negative_limit(self):
        from ck3lens.policy.executors import conflict_symbols
        rb = _make_rb()
        mock_db = MagicMock()
        mock_db._get_symbol_conflicts_internal.return_value = {
            "conflict_count": 0, "conflicts": [],
        }
        reply = conflict_symbols(
            rb, policy_ctx={}, db=mock_db, session=STANDARD_SESSION,
            limit=-1,
        )
        assert reply.reply_type == "S"


# =============================================================================
# CONF-40 through CONF-43: DB_CONFLICT_COMMANDS frozen set integrity
# =============================================================================

class TestCONF40CommandSetContents:
    """DB_CONFLICT_COMMANDS contains exactly the 3 expected tuples."""

    def test_contents(self):
        from ck3lens.capability_matrix_v2 import DB_CONFLICT_COMMANDS
        expected = frozenset({
            ("ck3_conflicts", "symbols"),
            ("ck3_conflicts", "files"),
            ("ck3_conflicts", "summary"),
        })
        assert DB_CONFLICT_COMMANDS == expected


class TestCONF41CommandSetIsFrozen:
    """DB_CONFLICT_COMMANDS is truly immutable."""

    def test_immutable(self):
        from ck3lens.capability_matrix_v2 import DB_CONFLICT_COMMANDS
        with pytest.raises(AttributeError):
            DB_CONFLICT_COMMANDS.add(("ck3_conflicts", "bogus"))


class TestCONF42NoOverlapWithSearchCommands:
    """DB_CONFLICT_COMMANDS has no overlap with DB_SEARCH_COMMANDS."""

    def test_no_overlap(self):
        from ck3lens.capability_matrix_v2 import DB_CONFLICT_COMMANDS, DB_SEARCH_COMMANDS
        assert DB_CONFLICT_COMMANDS & DB_SEARCH_COMMANDS == frozenset()


class TestCONF43ExecutorsRegistryComplete:
    """All 3 conflict command tuples have registered executors."""

    def test_all_registered(self):
        from ck3lens.policy.executors import EXECUTORS
        from ck3lens.capability_matrix_v2 import DB_CONFLICT_COMMANDS
        for cmd_tuple in DB_CONFLICT_COMMANDS:
            assert cmd_tuple in EXECUTORS, f"No executor for {cmd_tuple}"


# =============================================================================
# CONF-44: Full pipeline integration (execute_db → executor → Reply)
# =============================================================================

class TestCONF44FullPipelineSymbols:
    """Full pipeline: execute_db → conflict_symbols → Reply with correct data."""

    def test_full_pipeline(self):
        from ck3lens.policy.enforcement_v2 import execute_db
        rb = _make_rb()
        mock_db = MagicMock()
        mock_db._get_symbol_conflicts_internal.return_value = {
            "conflict_count": 1,
            "conflicts": [
                {"name": "brave", "symbol_type": "trait", "source_count": 2,
                 "sources": [{"mod": "ModA", "load_order": 0}, {"mod": "ModB", "load_order": 1}]},
            ],
        }
        reply = execute_db(
            rb, mode="ck3raven-dev", tool="ck3_conflicts", command="symbols",
            params={
                "session": STANDARD_SESSION, "db": mock_db,
                "symbol_type": "trait", "path": "common/traits",
                "limit": 50,
            },
            session=STANDARD_SESSION,
        )
        assert reply.reply_type == "S"
        assert reply.data["conflict_count"] == 1
        assert reply.data["conflicts"][0]["name"] == "brave"


class TestCONF45FullPipelineFiles:
    """Full pipeline for files command."""

    def test_full_pipeline(self):
        from ck3lens.policy.enforcement_v2 import execute_db
        rb = _make_rb()
        mock_db = MagicMock()

        with patch("ck3lens.impl.conflict_ops.detect_primary_file_conflicts",
                    return_value={
                        "conflicts": [
                            {"directory": "common/traits", "original_file": "x.txt",
                             "overrides": [{"type": "full_override"}]},
                        ],
                        "summary": {
                            "total_originals_with_conflicts": 1,
                            "full_overrides": 1,
                            "partial_overrides": 0,
                            "failed_patch_warnings": 0,
                        },
                    }):
            reply = execute_db(
                rb, mode="ck3raven-dev", tool="ck3_conflicts", command="files",
                params={"session": STANDARD_SESSION, "db": mock_db, "limit": 50},
                session=STANDARD_SESSION,
            )
        assert reply.reply_type == "S"
        assert reply.data["summary"]["total_originals_with_conflicts"] == 1


class TestCONF46FullPipelineSummary:
    """Full pipeline for summary command."""

    def test_full_pipeline(self):
        from ck3lens.policy.enforcement_v2 import execute_db
        rb = _make_rb()
        mock_db = MagicMock()
        mock_db.conn.execute.return_value.fetchall.return_value = [
            {"symbol_type": "trait", "conflict_count": 42},
        ]

        with patch("ck3lens.impl.conflict_ops.detect_primary_file_conflicts",
                    return_value={
                        "conflicts": [], "summary": {
                            "total_originals_with_conflicts": 5,
                            "full_overrides": 3,
                            "partial_overrides": 1,
                            "failed_patch_warnings": 1,
                        },
                    }):
            reply = execute_db(
                rb, mode="ck3raven-dev", tool="ck3_conflicts", command="summary",
                params={"session": STANDARD_SESSION, "db": mock_db},
                session=STANDARD_SESSION,
            )
        assert reply.reply_type == "S"
        assert reply.data["total_symbol_conflicts"] == 42
        assert reply.data["by_type"]["trait"] == 42
        assert reply.data["file_conflict_summary"]["full_overrides"] == 3
