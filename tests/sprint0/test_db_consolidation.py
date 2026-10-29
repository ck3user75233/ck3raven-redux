"""
DB2-* — ck3_db consolidation tests (Phase B2).

Verifies the merge of 4 DB tools into unified ck3_db:
  - ck3_close_db → ck3_db(command="disable")
  - ck3_db_delete → ck3_db(command="delete", ...)
  - ck3_db_query → ck3_db(command="query", ...)
  - (new) ck3_db(command="help")

NOTE: server.py uses relative imports (`from .safety import ...`) and cannot
be imported directly in test context. The internal functions
(`_ck3_db_delete_internal`, `_ck3_db_query_internal`, `_DB_QUERY_SCHEMA`) are
integration-tested by the full MCP test suite. This file tests the TASL
registry and structural invariants that ARE importable.

Naming convention: DB2-XX where XX is the test number.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_MCP_ROOT = _REPO_ROOT / "tools" / "ck3lens_mcp"
_COMPLIANCE_ROOT = _REPO_ROOT / "tools" / "compliance"

if str(_MCP_ROOT) not in sys.path:
    sys.path.insert(0, str(_MCP_ROOT))
if str(_COMPLIANCE_ROOT) not in sys.path:
    sys.path.insert(0, str(_COMPLIANCE_ROOT))


# =============================================================================
# DB2-01: TASL registry — unified ck3_db present
# =============================================================================

class TestDB201TaslRegistry:
    """Verify TASL has ck3_db and NOT the 3 deleted tools."""

    def test_ck3_db_in_tasl(self):
        from tasl import TOOL_AREA_MAP
        assert "ck3_db" in TOOL_AREA_MAP
        assert "DB" in TOOL_AREA_MAP["ck3_db"]

    def test_ck3_close_db_not_in_tasl(self):
        from tasl import TOOL_AREA_MAP
        assert "ck3_close_db" not in TOOL_AREA_MAP, \
            "ck3_close_db should be merged into ck3_db"

    def test_ck3_db_delete_not_in_tasl(self):
        from tasl import TOOL_AREA_MAP
        assert "ck3_db_delete" not in TOOL_AREA_MAP, \
            "ck3_db_delete should be merged into ck3_db"

    def test_ck3_db_query_not_in_tasl(self):
        from tasl import TOOL_AREA_MAP
        assert "ck3_db_query" not in TOOL_AREA_MAP, \
            "ck3_db_query should be merged into ck3_db"


# =============================================================================
# DB2-02: Server source — deleted handlers don't exist as @mcp.tool()
# =============================================================================

class TestDB202ServerSource:
    """Verify via source text that deleted handlers are gone and ck3_db is expanded."""

    @pytest.fixture(autouse=True)
    def _load_server_source(self):
        server_path = _MCP_ROOT / "server.py"
        self.source = server_path.read_text(encoding="utf-8")

    def test_no_ck3_close_db_decorator(self):
        """ck3_close_db should not appear as a decorated tool function."""
        # Look for the function definition pattern
        assert "def ck3_close_db(" not in self.source, \
            "ck3_close_db function still exists in server.py"

    def test_no_ck3_db_delete_decorator(self):
        assert "def ck3_db_delete(" not in self.source, \
            "ck3_db_delete function still exists in server.py"

    def test_no_ck3_db_query_decorator(self):
        assert "def ck3_db_query(" not in self.source, \
            "ck3_db_query function still exists in server.py"

    def test_ck3_db_handler_exists(self):
        assert "def ck3_db(" in self.source, \
            "ck3_db handler must exist in server.py"

    def test_ck3_db_has_delete_command(self):
        """The expanded ck3_db should handle delete command."""
        assert '"delete"' in self.source or "'delete'" in self.source

    def test_ck3_db_has_query_command(self):
        """The expanded ck3_db should handle query command."""
        assert '"query"' in self.source or "'query'" in self.source

    def test_ck3_db_has_help_command(self):
        """The expanded ck3_db should handle help command."""
        assert '"help"' in self.source or "'help'" in self.source

    def test_internal_delete_preserved(self):
        """_ck3_db_delete_internal should still exist as internal function."""
        assert "def _ck3_db_delete_internal(" in self.source

    def test_internal_query_preserved(self):
        """_ck3_db_query_internal should still exist as internal function."""
        assert "def _ck3_db_query_internal(" in self.source

    def test_db_query_schema_preserved(self):
        """_DB_QUERY_SCHEMA should still exist."""
        assert "_DB_QUERY_SCHEMA" in self.source


# =============================================================================
# DB2-03: No stale @mcp.tool() registrations for deleted tools
# =============================================================================

class TestDB203NoStaleRegistrations:
    """Verify no @mcp.tool() decorator immediately before deleted function names."""

    @pytest.fixture(autouse=True)
    def _load_server_lines(self):
        server_path = _MCP_ROOT / "server.py"
        self.lines = server_path.read_text(encoding="utf-8").splitlines()

    def _find_decorated_tools(self, func_name: str) -> list[int]:
        """Find lines where func_name appears as a def after @mcp.tool()."""
        hits = []
        for i, line in enumerate(self.lines):
            if f"def {func_name}(" in line:
                # Check the 5 lines above for @mcp.tool()
                for j in range(max(0, i - 5), i):
                    if "@mcp.tool()" in self.lines[j]:
                        hits.append(i + 1)  # 1-indexed
                        break
        return hits

    def test_ck3_close_db_no_registration(self):
        assert self._find_decorated_tools("ck3_close_db") == []

    def test_ck3_db_delete_no_registration(self):
        assert self._find_decorated_tools("ck3_db_delete") == []

    def test_ck3_db_query_no_registration(self):
        assert self._find_decorated_tools("ck3_db_query") == []

    def test_ck3_db_is_registered(self):
        hits = self._find_decorated_tools("ck3_db")
        assert len(hits) == 1, f"ck3_db should have exactly 1 @mcp.tool() registration, found {len(hits)}"
