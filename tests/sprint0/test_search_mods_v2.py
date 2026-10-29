"""
SMODS-* — ck3_search(command="mods") tests.

Tests the full pipeline: server.py → unified_tools.py → enforcement_v2.execute_db()
→ capability_matrix_v2 → executors.search_mods

Layers tested:
  - CM2: find_database_rule for "mods" command × 2 modes
  - Executor: search_mods with name/workshop_id/fuzzy/abbreviation
  - Routing: ck3_search_impl routes "mods" to _ck3_search_db
  - Edge cases: empty query, no DB, no session

Naming convention: SMODS-XX where XX is the test number.
"""
from __future__ import annotations

import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

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


def _make_rb(tool: str = "ck3_search") -> ReplyBuilder:
    trace = TraceInfo(trace_id="test-trace", session_id="test-session")
    return ReplyBuilder(trace, tool=tool)


def _session_with_mods(*specs: tuple[str, int, int]) -> FakeSession:
    return FakeSession([FakeMod(name=n, cvid=c, load_order=lo) for n, c, lo in specs])


STANDARD_SESSION = _session_with_mods(
    ("CK3 Game Files", 1, 0),
    ("ModA", 2, 1),
    ("ModB", 3, 2),
)


class FakeDB:
    """Minimal DB mock with an in-memory content_versions table."""

    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("""
            CREATE TABLE content_versions (
                content_version_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                workshop_id TEXT,
                source_path TEXT,
                file_count INTEGER DEFAULT 0,
                ingested_at TEXT DEFAULT '2026-01-01'
            )
        """)
        # Seed test data
        self.conn.executemany(
            "INSERT INTO content_versions VALUES (?, ?, ?, ?, ?, ?)",
            [
                (1, "CK3 Game Files", None, "C:/game", 5000, "2026-01-01"),
                (2, "Unofficial Patch", "2871648329", "C:/steam/2871648329", 100, "2026-01-02"),
                (3, "Ethnicities and Portraits Expanded", "2507209632", "C:/steam/2507209632", 200, "2026-01-03"),
                (4, "Community Flavor Pack", "2220098919", "C:/steam/2220098919", 150, "2026-01-04"),
                (5, "More Traditions v2", "2893793966", "C:/steam/2893793966", 80, "2026-01-05"),
            ],
        )
        self.conn.commit()


@pytest.fixture
def fake_db():
    return FakeDB()


# =============================================================================
# SMODS-01: CM2 — find_database_rule for mods command
# =============================================================================

class TestSMODS01RuleDiscovery:
    """find_database_rule returns rules for mods command in both modes."""

    @pytest.mark.parametrize("mode", ["ck3lens", "ck3raven-dev"])
    def test_rule_found(self, mode: str):
        from ck3lens.capability_matrix_v2 import find_database_rule
        rule = find_database_rule(mode, "ck3_search", "mods")
        assert rule is not None, f"No rule found for ck3_search/mods in mode={mode}"

    def test_mods_in_db_search_commands(self):
        from ck3lens.capability_matrix_v2 import DB_SEARCH_COMMANDS
        assert ("ck3_search", "mods") in DB_SEARCH_COMMANDS


# =============================================================================
# SMODS-02: Executor — search_mods basic name search
# =============================================================================

class TestSMODS02NameSearch:
    """search_mods finds mods by name (contains match)."""

    def test_contains_match(self, fake_db: FakeDB):
        from ck3lens.policy.executors import search_mods
        rb = _make_rb()
        reply = search_mods(
            rb, policy_ctx={}, query="Unofficial", session=STANDARD_SESSION, db=fake_db,
        )
        assert reply.reply_type == "S"
        results = reply.data["results"]
        assert len(results) >= 1
        assert any(r["name"] == "Unofficial Patch" for r in results)

    def test_case_insensitive(self, fake_db: FakeDB):
        from ck3lens.policy.executors import search_mods
        rb = _make_rb()
        reply = search_mods(
            rb, policy_ctx={}, query="unofficial patch", session=STANDARD_SESSION, db=fake_db,
        )
        assert reply.reply_type == "S"
        assert any(r["name"] == "Unofficial Patch" for r in reply.data["results"])


# =============================================================================
# SMODS-03: Executor — search_mods workshop_id search
# =============================================================================

class TestSMODS03WorkshopIdSearch:
    """search_mods finds mods by exact workshop ID."""

    def test_exact_id(self, fake_db: FakeDB):
        from ck3lens.policy.executors import search_mods
        rb = _make_rb()
        reply = search_mods(
            rb, policy_ctx={}, query="2507209632", search_by="workshop_id",
            session=STANDARD_SESSION, db=fake_db,
        )
        assert reply.reply_type == "S"
        results = reply.data["results"]
        assert len(results) == 1
        assert results[0]["name"] == "Ethnicities and Portraits Expanded"
        assert results[0]["match_type"] == "exact_id"

    def test_id_via_any(self, fake_db: FakeDB):
        from ck3lens.policy.executors import search_mods
        rb = _make_rb()
        reply = search_mods(
            rb, policy_ctx={}, query="2871648329", search_by="any",
            session=STANDARD_SESSION, db=fake_db,
        )
        assert reply.reply_type == "S"
        assert any(r["name"] == "Unofficial Patch" for r in reply.data["results"])


# =============================================================================
# SMODS-04: Executor — fuzzy abbreviation matching
# =============================================================================

class TestSMODS04FuzzyAbbreviation:
    """search_mods handles uppercase abbreviations like EPE."""

    def test_abbreviation_epe(self, fake_db: FakeDB):
        from ck3lens.policy.executors import search_mods
        rb = _make_rb()
        reply = search_mods(
            rb, policy_ctx={}, query="EPE", fuzzy=True,
            session=STANDARD_SESSION, db=fake_db,
        )
        assert reply.reply_type == "S"
        results = reply.data["results"]
        assert any(r["name"] == "Ethnicities and Portraits Expanded" for r in results)

    def test_abbreviation_cfp(self, fake_db: FakeDB):
        from ck3lens.policy.executors import search_mods
        rb = _make_rb()
        reply = search_mods(
            rb, policy_ctx={}, query="CFP", fuzzy=True,
            session=STANDARD_SESSION, db=fake_db,
        )
        assert reply.reply_type == "S"
        results = reply.data["results"]
        assert any(r["name"] == "Community Flavor Pack" for r in results)


# =============================================================================
# SMODS-05: Executor — fuzzy disabled
# =============================================================================

class TestSMODS05FuzzyDisabled:
    """With fuzzy=False, abbreviations should not match."""

    def test_no_abbreviation_match(self, fake_db: FakeDB):
        from ck3lens.policy.executors import search_mods
        rb = _make_rb()
        reply = search_mods(
            rb, policy_ctx={}, query="EPE", fuzzy=False,
            session=STANDARD_SESSION, db=fake_db,
        )
        assert reply.reply_type == "S"
        # Should not find EPE via abbreviation, only via contains
        # "EPE" doesn't appear as substring in any mod name, so 0 results
        results = reply.data["results"]
        assert not any(r["match_type"] == "abbreviation" for r in results)


# =============================================================================
# SMODS-06: Edge cases
# =============================================================================

class TestSMODS06EdgeCases:
    """Edge cases: empty query, no DB, no session."""

    def test_empty_query(self, fake_db: FakeDB):
        from ck3lens.policy.executors import search_mods
        rb = _make_rb()
        reply = search_mods(
            rb, policy_ctx={}, query="", session=STANDARD_SESSION, db=fake_db,
        )
        assert reply.reply_type == "I"

    def test_no_db(self):
        from ck3lens.policy.executors import search_mods
        rb = _make_rb()
        reply = search_mods(
            rb, policy_ctx={}, query="test", session=STANDARD_SESSION, db=None,
        )
        assert reply.reply_type == "I"

    def test_no_results(self, fake_db: FakeDB):
        from ck3lens.policy.executors import search_mods
        rb = _make_rb()
        reply = search_mods(
            rb, policy_ctx={}, query="zzz_nonexistent_mod_xyz", session=STANDARD_SESSION, db=fake_db,
        )
        assert reply.reply_type == "S"
        assert reply.data["count"] == 0

    def test_limit_respected(self, fake_db: FakeDB):
        from ck3lens.policy.executors import search_mods
        rb = _make_rb()
        reply = search_mods(
            rb, policy_ctx={}, query="%", limit=2, session=STANDARD_SESSION, db=fake_db,
        )
        assert reply.reply_type == "S"
        assert len(reply.data["results"]) <= 2


# =============================================================================
# SMODS-07: Routing — ck3_search_impl routes "mods" to DB path
# =============================================================================

class TestSMODS07Routing:
    """ck3_search_impl routes command='mods' through _ck3_search_db."""

    def test_mods_routes_to_db(self, fake_db: FakeDB):
        from unittest.mock import patch
        from ck3lens.unified_tools import ck3_search_impl

        rb = _make_rb()
        with patch("ck3lens.agent_mode.get_agent_mode", return_value="ck3raven-dev"):
            reply = ck3_search_impl(
                command="mods",
                query="Unofficial",
                session=STANDARD_SESSION,
                db=fake_db,
                rb=rb,
            )
        assert reply.reply_type == "S"
        results = reply.data["results"]
        assert any(r["name"] == "Unofficial Patch" for r in results)

    def test_mods_with_search_by(self, fake_db: FakeDB):
        from unittest.mock import patch
        from ck3lens.unified_tools import ck3_search_impl

        rb = _make_rb()
        with patch("ck3lens.agent_mode.get_agent_mode", return_value="ck3raven-dev"):
            reply = ck3_search_impl(
                command="mods",
                query="2507209632",
                search_by="workshop_id",
                session=STANDARD_SESSION,
                db=fake_db,
                rb=rb,
            )
        assert reply.reply_type == "S"
        assert any(r["name"] == "Ethnicities and Portraits Expanded" for r in reply.data["results"])


# =============================================================================
# SMODS-08: No deduplication between ID and name matches
# =============================================================================

class TestSMODS08NoDuplicates:
    """Workshop ID match + name match for same mod should not duplicate."""

    def test_no_dupes_when_id_matches_name(self, fake_db: FakeDB):
        from ck3lens.policy.executors import search_mods
        rb = _make_rb()
        # Search by "any" with workshop_id that also has a name match
        reply = search_mods(
            rb, policy_ctx={}, query="2871648329", search_by="any",
            session=STANDARD_SESSION, db=fake_db,
        )
        assert reply.reply_type == "S"
        results = reply.data["results"]
        ids = [r["content_version_id"] for r in results]
        # No duplicates
        assert len(ids) == len(set(ids))


class FakeUnifiedSearchDB:
    def __init__(self, unified_result: dict[str, Any]):
        self._unified_result = unified_result

    def _unified_search_internal(self, **_: Any) -> dict[str, Any]:
        return self._unified_result


# =============================================================================
# SMODS-09: Unified search regressions — content results preserved
# =============================================================================

class TestSMODS09UnifiedSearchContent:
    def test_search_auto_keeps_content_results(self):
        from ck3lens.policy.executors import search_auto

        rb = _make_rb()
        db = FakeUnifiedSearchDB({
            "symbols": {"count": 0, "results": [], "adjacencies": [], "references_by_mod": {}},
            "content": {
                "count": 2,
                "results": [
                    {"relpath": "common/landed_titles/00_landed_titles.txt", "match_count": 1, "matches": [{"line": 45052, "snippet": "c_rennes = {"}]},
                    {"relpath": "history/titles/k_brittany.txt", "match_count": 1, "matches": [{"line": 546, "snippet": "c_rennes = {"}]},
                ],
            },
            "files": {"count": 0, "results": []},
        })

        reply = search_auto(
            rb,
            policy_ctx={},
            query="c_rennes",
            session=STANDARD_SESSION,
            db=db,
        )

        assert reply.reply_type == "S"
        assert reply.data["content"]["count"] == 2
        assert len(reply.data["content"]["results"]) == 2
        assert reply.data["files"]["count"] == 0
        assert reply.data["truncated"] is False
        assert "2 content files" in reply.message

    def test_search_symbols_keeps_content_results(self):
        from ck3lens.policy.executors import search_symbols

        rb = _make_rb()
        db = FakeUnifiedSearchDB({
            "symbols": {
                "count": 1,
                "results": [{"symbol_id": 1, "name": "c_rennes", "symbol_type": "title"}],
                "adjacencies": [],
                "references_by_mod": {},
            },
            "content": {
                "count": 1,
                "results": [
                    {"relpath": "history/characters/breton.txt", "match_count": 1, "matches": [{"line": 3842, "snippet": "add_pressed_claim = title:c_rennes"}]},
                ],
            },
            "files": {"count": 0, "results": []},
        })

        reply = search_symbols(
            rb,
            policy_ctx={},
            query="c_rennes",
            session=STANDARD_SESSION,
            db=db,
        )

        assert reply.reply_type == "S"
        assert reply.data["symbols"]["count"] == 1
        assert reply.data["content"]["count"] == 1
        assert "1 content files" in reply.message


class TestSMODS10ContentExecutor:
    def test_search_content_uses_rg_when_available(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        from ck3lens.policy.executors import search_content

        root = tmp_path / "game"
        root.mkdir()
        rb = _make_rb()

        monkeypatch.setattr("shutil.which", lambda name: "rg" if name == "rg" else None)

        def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                args[0],
                0,
                stdout="common/landed_titles/00_landed_titles.txt:45052:c_rennes = {\nhistory/titles/k_brittany.txt:546:c_rennes = {\n",
                stderr="",
            )

        monkeypatch.setattr(subprocess, "run", fake_run)

        reply = search_content(rb, root, query="c_rennes")

        assert reply.reply_type == "S"
        assert reply.data["backend"] == "rg"
        assert reply.data["match_count"] == 2
        assert reply.data["files_scanned"] == 2
        assert reply.data["matches"][0]["path"] == "common/landed_titles/00_landed_titles.txt"
        assert reply.data["matches"][1]["line"] == 546

    def test_search_content_marks_python_fallback_timeout_as_truncated(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ):
        from ck3lens.policy.executors import search_content

        root = tmp_path / "scan"
        root.mkdir()
        for index in range(200):
            (root / f"file_{index:03d}.txt").write_text("no hit here\n", encoding="utf-8")

        rb = _make_rb()
        monkeypatch.setattr("shutil.which", lambda name: None)

        class FakeClock:
            def __init__(self):
                self.calls = 0

            def __call__(self) -> float:
                self.calls += 1
                return 0.0 if self.calls == 1 else 11.0

        clock = FakeClock()
        monkeypatch.setattr("time.monotonic", clock)

        reply = search_content(rb, root, query="c_rennes")

        assert reply.reply_type == "S"
        assert reply.data["backend"] == "python"
        assert reply.data["truncated"] is True
        assert reply.data["match_count"] == 0
