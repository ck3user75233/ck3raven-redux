import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from ck3raven.db.schema import init_database
from qbuilder.lookup_extractors import (
    extract_characters,
    extract_provinces,
    extract_names,
    extract_holy_sites,
    extract_dynasties,
)


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def test_lookup_schema_uses_content_version_identity(tmp_path):
    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = _connect(db_path)

    province_info = conn.execute("PRAGMA table_info(province_lookup)").fetchall()
    province_pk = [row["name"] for row in province_info if row["pk"]]
    assert province_pk == ["province_id", "content_version_id"]

    character_info = conn.execute("PRAGMA table_info(character_lookup)").fetchall()
    character_pk = [row["name"] for row in character_info if row["pk"]]
    assert character_pk == ["character_id", "content_version_id"]

    dynasty_info = conn.execute("PRAGMA table_info(dynasty_lookup)").fetchall()
    dynasty_pk = [row["name"] for row in dynasty_info if row["pk"]]
    assert dynasty_pk == ["dynasty_id", "content_version_id"]

    title_info = conn.execute("PRAGMA table_info(title_lookup)").fetchall()
    title_pk = [row["name"] for row in title_info if row["pk"]]
    assert title_pk == ["title_key", "content_version_id"]

    holy_site_info = conn.execute("PRAGMA table_info(holy_site_lookup)").fetchall()
    holy_site_pk = [row["name"] for row in holy_site_info if row["pk"]]
    assert holy_site_pk == ["holy_site_key", "content_version_id"]

    unique_indexes = conn.execute("PRAGMA index_list(name_lookup)").fetchall()
    unique_index_names = [row["name"] for row in unique_indexes if row["unique"]]
    assert unique_index_names
    index_cols = []
    for index_name in unique_index_names:
        cols = conn.execute(f"PRAGMA index_info({index_name!r})").fetchall()
        index_cols.append([row["name"] for row in cols])
    assert ["name_list_id", "name", "gender", "content_version_id"] in index_cols

    conn.close()


def test_character_extractor_preserves_rows_per_content_version(tmp_path):
    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = _connect(db_path)

    base = '123 = {\n    name = "Base"\n    culture = anglo_saxon\n}\n'
    modded = '123 = {\n    name = "Modded"\n    culture = norman\n}\n'
    updated = '123 = {\n    name = "Updated"\n    culture = english\n}\n'

    extract_characters(base, 1, 1, conn)
    extract_characters(modded, 2, 2, conn)
    extract_characters(updated, 3, 2, conn)
    conn.commit()

    rows = conn.execute(
        "SELECT character_id, content_version_id, name, culture FROM character_lookup ORDER BY content_version_id"
    ).fetchall()

    assert len(rows) == 2
    assert (rows[0]["character_id"], rows[0]["content_version_id"], rows[0]["name"], rows[0]["culture"]) == (123, 1, "Base", "anglo_saxon")
    assert (rows[1]["character_id"], rows[1]["content_version_id"], rows[1]["name"], rows[1]["culture"]) == (123, 2, "Updated", "english")

    conn.close()


def test_other_lookup_extractors_preserve_rows_per_content_version(tmp_path):
    db_path = tmp_path / "test.db"
    init_database(db_path)
    conn = _connect(db_path)

    extract_provinces('9472 = {\n    culture = tuyuhun\n}\n', 1, 1, conn)
    extract_provinces('9472 = {\n    culture = han\n}\n', 2, 2, conn)

    extract_names(
        'name_list_test = {\n    male_names = { John }\n    female_names = { Jane }\n}\n',
        3,
        1,
        conn,
    )
    extract_names(
        'name_list_test = {\n    male_names = { John }\n    female_names = { Jane }\n}\n',
        4,
        2,
        conn,
    )

    extract_holy_sites('jerusalem = {\n    county = c_jerusalem\n    flag = base_flag\n}\n', 5, 1, conn)
    extract_holy_sites('jerusalem = {\n    county = c_jerusalem\n    flag = mod_flag\n}\n', 6, 2, conn)

    extract_dynasties('25061 = {\n    name = dynn_Karling\n    culture = frankish\n}\n', 7, 1, conn)
    extract_dynasties('25061 = {\n    name = dynn_Karling_mod\n    culture = french\n}\n', 8, 2, conn)
    conn.commit()

    province_rows = conn.execute(
        "SELECT province_id, content_version_id, culture FROM province_lookup WHERE province_id = 9472 ORDER BY content_version_id"
    ).fetchall()
    assert len(province_rows) == 2
    assert [row["culture"] for row in province_rows] == ["tuyuhun", "han"]

    name_rows = conn.execute(
        "SELECT name_list_id, name, gender, content_version_id FROM name_lookup WHERE name = 'John' ORDER BY content_version_id"
    ).fetchall()
    assert len(name_rows) == 2
    assert [row["content_version_id"] for row in name_rows] == [1, 2]

    holy_rows = conn.execute(
        "SELECT holy_site_key, content_version_id, flag FROM holy_site_lookup WHERE holy_site_key = 'jerusalem' ORDER BY content_version_id"
    ).fetchall()
    assert len(holy_rows) == 2
    assert [row["flag"] for row in holy_rows] == ["base_flag", "mod_flag"]

    dynasty_rows = conn.execute(
        "SELECT dynasty_id, content_version_id, name_key, culture FROM dynasty_lookup WHERE dynasty_id = 25061 ORDER BY content_version_id"
    ).fetchall()
    assert len(dynasty_rows) == 2
    assert [row["culture"] for row in dynasty_rows] == ["frankish", "french"]

    conn.close()
