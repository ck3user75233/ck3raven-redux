import json
import os
import sys
from pathlib import Path

import pytest

MCP_ROOT = Path(__file__).parent.parent / "tools" / "ck3lens_mcp"
sys.path.insert(0, str(MCP_ROOT))

from ck3lens import workspace


@pytest.fixture
def session_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    game_path = tmp_path / "game"
    game_path.mkdir()
    monkeypatch.setattr(workspace, "ROOT_GAME", game_path)
    monkeypatch.setattr(workspace, "ROOT_CK3RAVEN_DATA", tmp_path / "data")
    return tmp_path


def _load_playset(tmp_path: Path, mods: list[dict[str, object]]) -> workspace.Session:
    playset_path = tmp_path / "playset.json"
    playset_path.write_text(
        json.dumps({"playset_name": "Focused", "mods": mods}),
        encoding="utf-8",
    )
    return workspace.load_session(playset_path=playset_path)


def test_hydrates_arbitrarily_named_immediate_child_descriptor(
    session_roots: Path,
) -> None:
    mod_path = session_roots / "arbitrary"
    mod_path.mkdir()
    (mod_path / "custom_launcher_name.mod").write_text(
        'name="Descriptor Name"\nremote_file_id="123456"\n',
        encoding="utf-8",
    )
    (mod_path / "directory.mod").mkdir()
    nested_path = mod_path / "nested"
    nested_path.mkdir()
    (nested_path / "ignored.mod").write_text('name="Nested"\n', encoding="utf-8")

    session = _load_playset(
        session_roots,
        [{"name": "Playset Name", "path": str(mod_path), "enabled": True}],
    )

    assert [(mod.name, mod.load_order) for mod in session.mods] == [
        ("Base Game", 0),
        ("Descriptor Name", 1),
    ]
    assert session.mods[1].workshop_id == "123456"
    assert session.load_errors == []


def test_missing_descriptor_is_omitted_and_reported(session_roots: Path) -> None:
    mod_path = session_roots / "missing"
    mod_path.mkdir()

    session = _load_playset(
        session_roots,
        [{"name": "Missing Mod", "path": str(mod_path), "enabled": True}],
    )

    assert [(mod.name, mod.load_order) for mod in session.mods] == [("Base Game", 0)]
    resolved_mod_path = Path(os.path.normcase(str(mod_path.resolve())))
    assert session.load_errors == [
        {
            "code": "PLAYSET_MOD_UNRESOLVED",
            "name": "Missing Mod",
            "path": str(mod_path),
            "message": (
                "No mod descriptor found; the formal mod name could not be determined: "
                f"{resolved_mod_path}"
            ),
        }
    ]


def test_conflicting_descriptor_names_are_omitted_and_reported(
    session_roots: Path,
) -> None:
    mod_path = session_roots / "ambiguous"
    mod_path.mkdir()
    (mod_path / "zeta.mod").write_text('name="Zeta"\n', encoding="utf-8")
    (mod_path / "alpha.mod").write_text('name="Alpha"\n', encoding="utf-8")

    session = _load_playset(
        session_roots,
        [{"name": "Ambiguous Mod", "path": str(mod_path), "enabled": True}],
    )

    assert [(mod.name, mod.load_order) for mod in session.mods] == [("Base Game", 0)]
    resolved_mod_path = Path(os.path.normcase(str(mod_path.resolve())))
    assert session.load_errors == [
        {
            "code": "PLAYSET_MOD_UNRESOLVED",
            "name": "Ambiguous Mod",
            "path": str(mod_path),
            "message": (
                "Two or more mod descriptor files were found, and the mod names provided are in conflict: "
                f'{resolved_mod_path}: alpha.mod: "Alpha", zeta.mod: "Zeta"'
            ),
        }
    ]


@pytest.mark.parametrize(
    ("name_line", "expected_reason"),
    [
        ('name "Example Mod"', "Mod descriptor name is unreadable"),
        ("name=Example Mod", "Mod descriptor name is unreadable"),
        ('name=""', "No formal mod name found in mod descriptor"),
    ],
    ids=["missing-equals", "missing-quotes", "empty-name"],
)
def test_owner_approved_name_line_variants(
    session_roots: Path, name_line: str, expected_reason: str
) -> None:
    mod_path = session_roots / "name_line"
    mod_path.mkdir()
    (mod_path / "descriptor.mod").write_text(name_line + "\n", encoding="utf-8")

    session = _load_playset(
        session_roots,
        [{"name": "Unvalidated Playset Label", "path": str(mod_path), "enabled": True}],
    )

    assert [(mod.name, mod.load_order) for mod in session.mods] == [("Base Game", 0)]
    assert len(session.load_errors) == 1
    error = session.load_errors[0]
    assert error["code"] == "PLAYSET_MOD_UNRESOLVED"
    assert expected_reason in error["message"]
    report = session.get_load_report()
    assert report["excluded_mods"] == [error]
    assert report["excluded_mod_count"] == 1
    assert "Excluded 1 unresolved mod(s)" in report["exclusion_notice"]
