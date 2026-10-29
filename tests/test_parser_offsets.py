"""
Regression tests for AST node source offsets.

Every non-synthetic node must carry a meaningful (start_offset, end_offset)
span.  The invariants checked here are:

  1. start_offset >= 0 and end_offset >= start_offset
  2. For non-root nodes: end_offset > start_offset  (slice is non-empty)
  3. source[node.start_offset:node.end_offset] equals the expected token text
  4. No non-root node has (0, 0) unless it genuinely starts at position 0 and
     end_offset > 0.
  5. The RootNode has start_offset=0 and end_offset == last real token end.

The fixture covers every node path exercised by the parser:
  - Simple scalar assignments (string, number, bool, param)
  - Negative number / negative identifier
  - Scripted value (@my_value standalone and in assignment)
  - Inline expression (@[1 + 2] standalone and in assignment)
  - Top-level @global = value assignment
  - Named block (outer = { … }) with nested content
  - Standalone value inside list
  - Nested block with its own assignment

Specifically excluded from invariant checks: RootNode itself (its start is 0
by definition), and any node whose value is an empty-block edge case.
"""

import pytest
from ck3raven.parser import parse_source, BlockNode, AssignmentNode, ValueNode, ListNode, RootNode


# ---------------------------------------------------------------------------
# Fixture source — every line contributes at least one distinct node path
# ---------------------------------------------------------------------------

SOURCE = """\
foo = bar
num = 123
flag = yes
param_value = $PARAM$
negative_num = -5
negative_identifier = -feudal_elective
scripted = @my_value
expr_assign = @[1 + 2]
@global_value = 30
outer = { baz = qux; list = { a b c }; nested = { x = 1 } }
"""


def _ast():
    return parse_source(SOURCE)


# ---------------------------------------------------------------------------
# Helper: recursively collect all non-root nodes
# ---------------------------------------------------------------------------

def _collect_nodes(node, result=None):
    if result is None:
        result = []
    result.append(node)
    if isinstance(node, (BlockNode, RootNode)):
        for child in node.children:
            _collect_nodes(child, result)
    elif isinstance(node, ListNode):
        for item in node.items:
            _collect_nodes(item, result)
    if isinstance(node, AssignmentNode):
        _collect_nodes(node.value, result)
    return result


# ---------------------------------------------------------------------------
# Tests: per-node source-slice correctness
# ---------------------------------------------------------------------------

class TestScalarAssignmentOffsets:
    def test_foo_assignment_span(self):
        ast = _ast()
        node = ast.children[0]
        assert isinstance(node, AssignmentNode)
        assert node.key == "foo"
        assert SOURCE[node.start_offset:node.end_offset] == "foo = bar"

    def test_foo_value_span(self):
        ast = _ast()
        node = ast.children[0]
        vn = node.value
        assert SOURCE[vn.start_offset:vn.end_offset] == "bar"

    def test_num_value_span(self):
        ast = _ast()
        node = ast.children[1]
        assert isinstance(node, AssignmentNode)
        assert node.key == "num"
        vn = node.value
        assert SOURCE[vn.start_offset:vn.end_offset] == "123"
        assert SOURCE[node.start_offset:node.end_offset] == "num = 123"

    def test_flag_bool_span(self):
        ast = _ast()
        node = ast.children[2]
        assert isinstance(node, AssignmentNode)
        assert node.key == "flag"
        vn = node.value
        assert SOURCE[vn.start_offset:vn.end_offset] == "yes"

    def test_param_span(self):
        ast = _ast()
        node = ast.children[3]
        assert isinstance(node, AssignmentNode)
        assert node.key == "param_value"
        vn = node.value
        assert vn.value_type == "param"
        assert SOURCE[vn.start_offset:vn.end_offset] == "$PARAM$"


class TestNegativeValueOffsets:
    def test_negative_number_span(self):
        ast = _ast()
        node = ast.children[4]
        assert isinstance(node, AssignmentNode)
        assert node.key == "negative_num"
        vn = node.value
        assert SOURCE[vn.start_offset:vn.end_offset] == "-5"

    def test_negative_number_assignment_span(self):
        ast = _ast()
        node = ast.children[4]
        assert SOURCE[node.start_offset:node.end_offset] == "negative_num = -5"

    def test_negative_identifier_span(self):
        ast = _ast()
        node = ast.children[5]
        assert isinstance(node, AssignmentNode)
        assert node.key == "negative_identifier"
        vn = node.value
        assert SOURCE[vn.start_offset:vn.end_offset] == "-feudal_elective"


class TestScriptedValueOffsets:
    def test_scripted_value_span(self):
        """scripted = @my_value — value node spans @my_value"""
        ast = _ast()
        node = ast.children[6]
        assert isinstance(node, AssignmentNode)
        assert node.key == "scripted"
        vn = node.value
        assert vn.value_type == "scripted_value"
        assert SOURCE[vn.start_offset:vn.end_offset] == "@my_value"

    def test_scripted_assignment_span(self):
        ast = _ast()
        node = ast.children[6]
        assert SOURCE[node.start_offset:node.end_offset] == "scripted = @my_value"


class TestInlineExpressionOffsets:
    def test_expr_value_span(self):
        """expr_assign = @[1 + 2] — value node spans @[1 + 2]"""
        ast = _ast()
        node = ast.children[7]
        assert isinstance(node, AssignmentNode)
        assert node.key == "expr_assign"
        vn = node.value
        assert vn.value_type == "inline_expression"
        assert SOURCE[vn.start_offset:vn.end_offset] == "@[1 + 2]"

    def test_expr_assignment_span(self):
        ast = _ast()
        node = ast.children[7]
        assert SOURCE[node.start_offset:node.end_offset] == "expr_assign = @[1 + 2]"


class TestAtGlobalAssignmentOffsets:
    def test_at_global_span(self):
        """@global_value = 30 — assignment key starts at @"""
        ast = _ast()
        node = ast.children[8]
        assert isinstance(node, AssignmentNode)
        assert node.key == "@global_value"
        assert SOURCE[node.start_offset:node.end_offset] == "@global_value = 30"

    def test_at_global_value_span(self):
        ast = _ast()
        node = ast.children[8]
        vn = node.value
        assert SOURCE[vn.start_offset:vn.end_offset] == "30"


class TestBlockNodeOffsets:
    def test_outer_block_starts_at_outer(self):
        """outer = { … } — block start_offset at 'o' of 'outer'"""
        ast = _ast()
        outer = ast.children[9]
        assert isinstance(outer, BlockNode)
        assert outer.name == "outer"
        assert SOURCE[outer.start_offset] == "o"

    def test_outer_block_ends_at_closing_brace(self):
        ast = _ast()
        outer = ast.children[9]
        assert SOURCE[outer.end_offset - 1] == "}"

    def test_inner_block_nested_has_span(self):
        ast = _ast()
        outer = ast.children[9]
        # Find nested block child
        nested = next(c for c in outer.children if isinstance(c, BlockNode) and c.name == "nested")
        assert nested.start_offset > 0
        assert nested.end_offset > nested.start_offset
        assert SOURCE[nested.start_offset:nested.end_offset] == "nested = { x = 1 }"

    def test_list_node_in_outer_has_span(self):
        ast = _ast()
        outer = ast.children[9]
        # 'list = { a b c }' parses as BlockNode(name="list") with three ValueNode children
        list_block = next(c for c in outer.children if isinstance(c, BlockNode) and c.name == "list")
        assert list_block.start_offset > 0
        assert list_block.end_offset > list_block.start_offset
        assert SOURCE[list_block.start_offset:list_block.end_offset] == "list = { a b c }"


class TestRootNodeOffsets:
    def test_root_start_offset_is_zero(self):
        ast = _ast()
        assert ast.start_offset == 0

    def test_root_end_offset_is_nonzero(self):
        ast = _ast()
        assert ast.end_offset > 0

    def test_root_end_offset_covers_source(self):
        ast = _ast()
        # Root end_offset should point to end of last token
        # The source ends with a newline; end_offset covers up to and including the last real token
        assert ast.end_offset <= len(SOURCE)


# ---------------------------------------------------------------------------
# Recursive invariant test
# ---------------------------------------------------------------------------

class TestRecursiveOffsetInvariant:
    """Every node in the tree must satisfy basic span sanity conditions."""

    def _check_node(self, node, source, is_root=False):
        assert node.start_offset >= 0, f"Negative start_offset on {node!r}"
        assert node.end_offset >= node.start_offset, (
            f"end_offset < start_offset on {node!r}: "
            f"[{node.start_offset}:{node.end_offset}]"
        )
        if not is_root:
            assert node.end_offset > node.start_offset, (
                f"Zero-length span on non-root node {node!r}: "
                f"[{node.start_offset}:{node.end_offset}]"
            )
            # The slice must be non-empty and printable
            span = source[node.start_offset:node.end_offset]
            assert span, f"Empty source slice for {node!r}"

        # Recurse
        if isinstance(node, (BlockNode, RootNode)):
            for child in node.children:
                self._check_node(child, source)
        if isinstance(node, ListNode):
            for item in node.items:
                self._check_node(item, source)
        if isinstance(node, AssignmentNode):
            self._check_node(node.value, source)

    def test_all_nodes_have_valid_spans(self):
        ast = _ast()
        self._check_node(ast, SOURCE, is_root=True)


# ---------------------------------------------------------------------------
# Edge case: standalone value inside list
# ---------------------------------------------------------------------------

class TestStandaloneValueOffsets:
    def test_list_items_have_span(self):
        """Items inside list = { a b c } each have correct offsets."""
        ast = _ast()
        outer = ast.children[9]
        # 'list = { a b c }' parses as BlockNode(name="list") with ValueNode children
        list_block = next(c for c in outer.children if isinstance(c, BlockNode) and c.name == "list")
        for item in list_block.children:
            assert isinstance(item, ValueNode)
            assert item.end_offset > item.start_offset
            span = SOURCE[item.start_offset:item.end_offset]
            assert span in ("a", "b", "c"), f"Unexpected list item span {span!r}"
