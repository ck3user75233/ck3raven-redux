from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_MCP_ROOT = _REPO_ROOT / "tools" / "ck3lens_mcp"

if str(_MCP_ROOT) not in sys.path:
    sys.path.insert(0, str(_MCP_ROOT))

from ck3raven.core.reply import TraceInfo
from safety import ReplyBuilder


def _make_rb() -> ReplyBuilder:
    trace = TraceInfo(trace_id="test-trace", session_id="test-session")
    return ReplyBuilder(trace, tool="ck3_file")


class DummyTrace:
    def log(self, *_args, **_kwargs):
        return None


def test_file_edit_validate_syntax_accepts_utf8_bom(tmp_path: Path):
    from ck3lens.policy.executors import file_edit

    file_path = tmp_path / "bom_file.txt"
    file_path.write_text("\ufefftitle = {\n\tcolor = red\n}\n", encoding="utf-8")

    reply = file_edit(
        _make_rb(),
        file_path,
        old_content="color = red",
        new_content="color = blue",
        validate_syntax=True,
        trace=DummyTrace(),
    )

    assert reply.reply_type == "S"
    assert reply.code == "EN-WRITE-S-001"
    assert "color = blue" in file_path.read_text(encoding="utf-8")
    assert file_path.read_text(encoding="utf-8").startswith("\ufeff")


def test_file_edit_validation_failure_returns_invalid_and_does_not_write(
    monkeypatch, tmp_path: Path,
):
    from ck3lens.policy.executors import file_edit

    file_path = tmp_path / "invalid_edit.txt"
    original = "title = {\n\tcolor = red\n}\n"
    file_path.write_text(original, encoding="utf-8")

    monkeypatch.setattr(
        "ck3lens.validate.parse_content",
        lambda *_args, **_kwargs: {
            "success": False,
            "ast": None,
            "errors": [
                {
                    "line": 1,
                    "column": 2,
                    "message": "Unexpected character '»'",
                    "code": "PARSE_ERROR",
                    "severity": "error",
                }
            ],
        },
    )

    reply = file_edit(
        _make_rb(),
        file_path,
        old_content="color = red",
        new_content="color = blue",
        validate_syntax=True,
        trace=DummyTrace(),
    )

    assert reply.reply_type == "I"
    assert reply.code == "EN-PARSE-I-001"
    assert reply.data["write_performed"] is False
    assert reply.data["path"] == str(file_path.resolve())
    assert reply.data["parse_errors"][0]["message"] == "Unexpected character '»'"
    assert file_path.read_text(encoding="utf-8") == original


def test_file_write_validation_failure_returns_invalid_and_does_not_write(
    monkeypatch, tmp_path: Path,
):
    from ck3lens.policy.executors import file_write

    file_path = tmp_path / "invalid_write.txt"
    original = "title = {\n\tcolor = red\n}\n"
    file_path.write_text(original, encoding="utf-8")

    monkeypatch.setattr(
        "ck3lens.validate.parse_content",
        lambda *_args, **_kwargs: {
            "success": False,
            "ast": None,
            "errors": [
                {
                    "line": 1,
                    "column": 1,
                    "message": "Syntax validation failed",
                    "code": "PARSE_ERROR",
                    "severity": "error",
                }
            ],
        },
    )

    reply = file_write(
        _make_rb(),
        file_path,
        content="bad = {",
        validate_syntax=True,
        trace=DummyTrace(),
    )

    assert reply.reply_type == "I"
    assert reply.code == "EN-PARSE-I-001"
    assert reply.data["write_performed"] is False
    assert reply.data["bytes_blocked"] == len("bad = {".encode("utf-8"))
    assert file_path.read_text(encoding="utf-8") == original
