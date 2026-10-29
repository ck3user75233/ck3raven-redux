"""ck3_validate canonical path resolution tests."""
from __future__ import annotations

from unittest.mock import patch

from ck3raven.core.reply import TraceInfo
from safety import ReplyBuilder
from ck3lens.unified_tools import ck3_validate_impl
from ck3lens.world_adapter_v2 import WorldAdapterV2


def _make_rb() -> ReplyBuilder:
    trace = TraceInfo(trace_id="test-trace", session_id="test-session")
    return ReplyBuilder(trace, tool="ck3_validate")


def test_python_file_resolves_canonical_wip_path(wa2: WorldAdapterV2) -> None:
    with (
        patch("ck3lens.agent_mode.get_agent_mode", return_value="ck3raven-dev"),
        patch.object(WorldAdapterV2, "create", return_value=wa2),
    ):
        reply = ck3_validate_impl(
            target="python",
            file_path="root:ck3raven_data/wip/analysis.py",
            session=object(),
            rb=_make_rb(),
        )

    assert reply.reply_type == "S"
    assert reply.code == "WA-VAL-S-001"
    assert reply.data["valid"] is True


def test_python_file_returns_wa2_reply_for_missing_path(wa2: WorldAdapterV2) -> None:
    with (
        patch("ck3lens.agent_mode.get_agent_mode", return_value="ck3raven-dev"),
        patch.object(WorldAdapterV2, "create", return_value=wa2),
    ):
        reply = ck3_validate_impl(
            target="python",
            file_path="root:ck3raven_data/wip/missing.py",
            session=object(),
            rb=_make_rb(),
        )

    assert reply.reply_type == "I"
    assert reply.code == "WA-RES-I-001"


def test_python_inline_content_does_not_resolve_file_path() -> None:
    with (
        patch("ck3lens.agent_mode.get_agent_mode", return_value="ck3raven-dev"),
        patch.object(WorldAdapterV2, "create") as create,
    ):
        reply = ck3_validate_impl(
            target="python",
            content="answer = 42\n",
            file_path="root:ck3raven_data/wip/display_name.py",
            session=object(),
            rb=_make_rb(),
        )

    create.assert_not_called()
    assert reply.reply_type == "S"
    assert reply.code == "WA-VAL-S-001"
