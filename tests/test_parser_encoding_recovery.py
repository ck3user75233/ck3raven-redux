"""Regression tests for parser recovery from common encoding damage."""

from ck3raven.parser import BlockNode, parse_file, parse_source


def test_parse_source_strips_leading_bom_artifacts():
    ast = parse_source("\ufeffï»¿foo = { bar = baz }")

    assert len(ast.children) == 1
    assert isinstance(ast.children[0], BlockNode)
    assert ast.children[0].name == "foo"
    assert ast.children[0].children[0].key == "bar"


def test_parse_file_strips_mojibake_bom_prefix(tmp_path):
    path = tmp_path / "mojibake_bom.txt"
    path.write_text("ï»¿foo = { bar = baz }", encoding="utf-8")

    ast = parse_file(str(path))

    assert len(ast.children) == 1
    assert ast.children[0].name == "foo"


def test_parse_source_tolerates_replacement_characters_in_identifiers():
    ast = parse_source("foo = { ba\ufffdr = baz }\nna\ufffdme = yes")

    assert len(ast.children) == 2
    assert ast.children[0].children[0].key == "ba\ufffdr"
    assert ast.children[1].key == "na\ufffdme"


def test_parse_source_tolerates_curly_apostrophes_in_identifiers():
    ast = parse_source("b_mansa’l-kharaz = { enabled = yes }")

    assert len(ast.children) == 1
    assert ast.children[0].name == "b_mansa’l-kharaz"
    assert ast.children[0].children[0].key == "enabled"
