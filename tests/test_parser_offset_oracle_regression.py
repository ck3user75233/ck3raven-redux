"""Permanent offset regression tests for CK3 parser spans.

Includes:
1) Oracle fixture promoted from Phase B answer sheet (29 targets)
2) Secondary fixture covering CRLF, comments/trailing comments,
   quoted strings, and non-ASCII characters.
"""

from __future__ import annotations

from ck3raven.parser import parse_source, BlockNode, AssignmentNode, ValueNode


def _collect_target_map(ast):
    """Collect target IDs matching Phase A/B probe selectors."""
    targets = {}

    def add(tid, node):
        targets[tid] = node

    add("root", ast)
    for i, child in enumerate(ast.children):
        add(f"top[{i}]", child)
        if isinstance(child, AssignmentNode) and isinstance(child.value, ValueNode):
            add(f"top[{i}].value", child.value)
        elif isinstance(child, BlockNode):
            for j, grandchild in enumerate(child.children):
                add(f"top[{i}].ch[{j}]", grandchild)
                if isinstance(grandchild, AssignmentNode) and isinstance(grandchild.value, ValueNode):
                    add(f"top[{i}].ch[{j}].val", grandchild.value)
                elif isinstance(grandchild, BlockNode):
                    for k, ggc in enumerate(grandchild.children):
                        add(f"top[{i}].ch[{j}].ch[{k}]", ggc)
                        if isinstance(ggc, AssignmentNode) and isinstance(ggc.value, ValueNode):
                            add(f"top[{i}].ch[{j}].ch[{k}].val", ggc.value)
    return targets


ORACLE_SOURCE = "\n".join(
    [
        "foo = bar",
        "num = 123",
        "flag = yes",
        "param_value = $PARAM$",
        "negative_num = -5",
        "negative_identifier = -feudal_elective",
        "scripted = @my_value",
        "expr_assign = @[1 + 2]",
        "@global_value = 30",
        "outer = { baz = qux; list = { a b c }; nested = { x = 1 } }",
    ]
)

# Promoted from Phase B oracle answer sheet.
ORACLE_EXPECTED = [
    ("root", 0, 232, "foo = bar\nnum = 123\nflag = yes\nparam_value = $PARAM$\nnegative_num = -5\nnegative_identifier = -feudal_elective\nscripted = @my_value\nexpr_assign = @[1 + 2]\n@global_value = 30\nouter = { baz = qux; list = { a b c }; nested = { x = 1 } }"),
    ("top[0]", 0, 9, "foo = bar"),
    ("top[0].value", 6, 9, "bar"),
    ("top[1]", 10, 19, "num = 123"),
    ("top[1].value", 16, 19, "123"),
    ("top[2]", 20, 30, "flag = yes"),
    ("top[2].value", 27, 30, "yes"),
    ("top[3]", 31, 52, "param_value = $PARAM$"),
    ("top[3].value", 45, 52, "$PARAM$"),
    ("top[4]", 53, 70, "negative_num = -5"),
    ("top[4].value", 68, 70, "-5"),
    ("top[5]", 71, 109, "negative_identifier = -feudal_elective"),
    ("top[5].value", 93, 109, "-feudal_elective"),
    ("top[6]", 110, 130, "scripted = @my_value"),
    ("top[6].value", 121, 130, "@my_value"),
    ("top[7]", 131, 153, "expr_assign = @[1 + 2]"),
    ("top[7].value", 145, 153, "@[1 + 2]"),
    ("top[8]", 154, 172, "@global_value = 30"),
    ("top[8].value", 170, 172, "30"),
    ("top[9]", 173, 232, "outer = { baz = qux; list = { a b c }; nested = { x = 1 } }"),
    ("top[9].ch[0]", 183, 192, "baz = qux"),
    ("top[9].ch[0].val", 189, 192, "qux"),
    ("top[9].ch[1]", 194, 210, "list = { a b c }"),
    ("top[9].ch[1].ch[0]", 203, 204, "a"),
    ("top[9].ch[1].ch[1]", 205, 206, "b"),
    ("top[9].ch[1].ch[2]", 207, 208, "c"),
    ("top[9].ch[2]", 212, 230, "nested = { x = 1 }"),
    ("top[9].ch[2].ch[0]", 223, 228, "x = 1"),
    ("top[9].ch[2].ch[0].val", 227, 228, "1"),
]


def test_oracle_offset_sheet_regression():
    ast = parse_source(ORACLE_SOURCE, filename="offset_probe_fixture")
    target_map = _collect_target_map(ast)

    assert len(target_map) >= 29

    for tid, expected_start, expected_end, expected_slice in ORACLE_EXPECTED:
        assert tid in target_map, f"Missing target {tid}"
        node = target_map[tid]
        assert node.start_offset == expected_start, f"{tid} start mismatch"
        assert node.end_offset == expected_end, f"{tid} end mismatch"
        observed = ORACLE_SOURCE[node.start_offset:node.end_offset]
        assert observed == expected_slice, f"{tid} slice mismatch"


def test_oracle_no_zero_zero_on_non_root_nodes():
    ast = parse_source(ORACLE_SOURCE, filename="offset_probe_fixture")
    target_map = _collect_target_map(ast)

    for tid, node in target_map.items():
        if tid == "root":
            continue
        assert not (node.start_offset == 0 and node.end_offset == 0), tid
        assert node.end_offset > node.start_offset, tid


CRLF_SOURCE = (
    'title = "Café"\r\n'
    '# comment before next\r\n'
    'note = "naïve" # trailing comment\r\n'
    'scripted = @my_value\r\n'
    'expr = @[1 + 2] # trailing\r\n'
)


def test_crlf_comment_unicode_fixture_offsets_and_slices():
    ast = parse_source(CRLF_SOURCE, filename="offset_probe_crlf_comments_unicode")

    # Comments are ignored as elements; expect only real assignments.
    assert len(ast.children) == 4

    n0 = ast.children[0]  # title = "Café"
    n1 = ast.children[1]  # note = "naïve"
    n2 = ast.children[2]  # scripted = @my_value
    n3 = ast.children[3]  # expr = @[1 + 2]

    # CRLF-sensitive exact spans.
    assert (n0.start_offset, n0.end_offset) == (0, 14)
    assert CRLF_SOURCE[n0.start_offset:n0.end_offset] == 'title = "Café"'
    assert CRLF_SOURCE[n0.value.start_offset:n0.value.end_offset] == '"Café"'

    assert (n1.start_offset, n1.end_offset) == (39, 53)
    assert CRLF_SOURCE[n1.start_offset:n1.end_offset] == 'note = "naïve"'
    assert CRLF_SOURCE[n1.value.start_offset:n1.value.end_offset] == '"naïve"'

    assert (n2.start_offset, n2.end_offset) == (74, 94)
    assert CRLF_SOURCE[n2.start_offset:n2.end_offset] == 'scripted = @my_value'
    assert CRLF_SOURCE[n2.value.start_offset:n2.value.end_offset] == '@my_value'

    assert (n3.start_offset, n3.end_offset) == (96, 111)
    assert CRLF_SOURCE[n3.start_offset:n3.end_offset] == 'expr = @[1 + 2]'
    assert CRLF_SOURCE[n3.value.start_offset:n3.value.end_offset] == '@[1 + 2]'

    # Ensure trailing comments are not included in assignment spans.
    assert '#' not in CRLF_SOURCE[n1.start_offset:n1.end_offset]
    assert '#' not in CRLF_SOURCE[n3.start_offset:n3.end_offset]

    # Character-offset sanity with non-ASCII code points.
    # Offsets are character-based in decoded source, so these remain stable.
    assert len('"Café"') == 6
    assert len('"naïve"') == 7
    assert n0.value.end_offset - n0.value.start_offset == 6
    assert n1.value.end_offset - n1.value.start_offset == 7
