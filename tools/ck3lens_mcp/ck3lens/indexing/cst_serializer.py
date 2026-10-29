"""
CST Serializer - converts libcst Module to JSON.

Produces a complete tree structure that preserves the full AST,
enabling round-trip editing in future tasks.
"""

from __future__ import annotations

import importlib.metadata
from datetime import datetime, timezone
from typing import Any

import libcst as cst
from libcst.metadata import PositionProvider, MetadataWrapper


# Schema version for CST output
CST_SCHEMA_VERSION = "1.0.0"

# Maximum string literal length before truncation
MAX_STRING_LENGTH = 200


class CSTToJSON(cst.CSTVisitor):
    """
    Visitor that converts a libcst Module to JSON-serializable structure.
    
    Uses a stack-based approach to build nested dictionaries representing
    the full tree structure.
    """
    
    METADATA_DEPENDENCIES = (PositionProvider,)
    
    def __init__(self, include_whitespace: bool = False):
        super().__init__()
        self.include_whitespace = include_whitespace
        self._stack: list[dict] = []
        self._current: dict = {}
    
    def _get_line(self, node: cst.CSTNode) -> int | None:
        """Get start line for a node if available."""
        try:
            pos = self.get_metadata(PositionProvider, node)
            return pos.start.line
        except KeyError:
            return None


def _node_to_dict(node: cst.CSTNode, wrapper: MetadataWrapper | None = None) -> dict[str, Any]:
    """
    Recursively convert a CST node to dictionary.
    
    Args:
        node: The libcst node to convert
        wrapper: Optional metadata wrapper for line numbers
        
    Returns:
        Dictionary representation of the node
    """
    result: dict[str, Any] = {
        "type": type(node).__name__
    }
    
    # Try to get position info
    if wrapper:
        try:
            pos = wrapper.resolve(PositionProvider).get(node)
            if pos:
                result["line_start"] = pos.start.line
                result["line_end"] = pos.end.line
        except (KeyError, AttributeError):
            pass
    
    # Handle different node types
    if isinstance(node, cst.Module):
        result["body"] = [_node_to_dict(child, wrapper) for child in node.body]
        
    elif isinstance(node, cst.SimpleStatementLine):
        result["body"] = [_node_to_dict(child, wrapper) for child in node.body]
        
    elif isinstance(node, cst.FunctionDef):
        result["name"] = node.name.value
        result["params"] = _params_to_dict(node.params)
        if node.returns:
            result["returns"] = _node_to_dict(node.returns.annotation, wrapper)
        result["decorators"] = [_decorator_to_dict(d, wrapper) for d in node.decorators]
        result["body"] = _body_to_dict(node.body, wrapper)
        result["async"] = isinstance(node.asynchronous, cst.Asynchronous)
        
    elif isinstance(node, cst.ClassDef):
        result["name"] = node.name.value
        result["bases"] = [_arg_to_dict(arg, wrapper) for arg in node.bases]
        result["decorators"] = [_decorator_to_dict(d, wrapper) for d in node.decorators]
        result["body"] = _body_to_dict(node.body, wrapper)
        
    elif isinstance(node, cst.If):
        result["test"] = _node_to_dict(node.test, wrapper)
        result["body"] = _body_to_dict(node.body, wrapper)
        if node.orelse:
            result["orelse"] = _node_to_dict(node.orelse, wrapper)
            
    elif isinstance(node, cst.For):
        result["target"] = _node_to_dict(node.target, wrapper)
        result["iter"] = _node_to_dict(node.iter, wrapper)
        result["body"] = _body_to_dict(node.body, wrapper)
        result["async"] = isinstance(node.asynchronous, cst.Asynchronous)
        
    elif isinstance(node, cst.While):
        result["test"] = _node_to_dict(node.test, wrapper)
        result["body"] = _body_to_dict(node.body, wrapper)
        
    elif isinstance(node, cst.Try):
        result["body"] = _body_to_dict(node.body, wrapper)
        result["handlers"] = [_handler_to_dict(h, wrapper) for h in node.handlers]
        if node.finalbody:
            result["finalbody"] = _body_to_dict(node.finalbody, wrapper)
            
    elif isinstance(node, cst.With):
        result["items"] = [_with_item_to_dict(item, wrapper) for item in node.items]
        result["body"] = _body_to_dict(node.body, wrapper)
        result["async"] = isinstance(node.asynchronous, cst.Asynchronous)
        
    elif isinstance(node, cst.Return):
        if node.value:
            result["value"] = _node_to_dict(node.value, wrapper)
            
    elif isinstance(node, cst.Assign):
        result["targets"] = [_target_to_dict(t, wrapper) for t in node.targets]
        result["value"] = _node_to_dict(node.value, wrapper)
        
    elif isinstance(node, cst.AnnAssign):
        result["target"] = _node_to_dict(node.target, wrapper)
        result["annotation"] = _node_to_dict(node.annotation.annotation, wrapper)
        if node.value:
            result["value"] = _node_to_dict(node.value, wrapper)
            
    elif isinstance(node, cst.AugAssign):
        result["target"] = _node_to_dict(node.target, wrapper)
        result["operator"] = type(node.operator).__name__
        result["value"] = _node_to_dict(node.value, wrapper)
        
    elif isinstance(node, cst.Expr):
        result["value"] = _node_to_dict(node.value, wrapper)
        
    elif isinstance(node, cst.Call):
        result["func"] = _node_to_dict(node.func, wrapper)
        result["args"] = [_arg_to_dict(arg, wrapper) for arg in node.args]
        
    elif isinstance(node, cst.Attribute):
        result["value"] = _node_to_dict(node.value, wrapper)
        result["attr"] = node.attr.value
        
    elif isinstance(node, cst.Subscript):
        result["value"] = _node_to_dict(node.value, wrapper)
        result["slice"] = [_slice_to_dict(s, wrapper) for s in node.slice]
        
    elif isinstance(node, cst.Index):
        # Subscript index - unwrap the value
        return _node_to_dict(node.value, wrapper)
        
    elif isinstance(node, cst.Slice):
        # Slice like [a:b:c]
        result["lower"] = _node_to_dict(node.lower, wrapper) if node.lower else None
        result["upper"] = _node_to_dict(node.upper, wrapper) if node.upper else None
        result["step"] = _node_to_dict(node.step, wrapper) if node.step else None
        
    elif isinstance(node, cst.Name):
        result["value"] = node.value
        
    elif isinstance(node, cst.Integer):
        result["value"] = node.value
        
    elif isinstance(node, cst.Float):
        result["value"] = node.value
        
    elif isinstance(node, cst.SimpleString):
        value = node.value
        if len(value) > MAX_STRING_LENGTH:
            value = value[:MAX_STRING_LENGTH] + "...[truncated]"
        result["value"] = value
        
    elif isinstance(node, cst.FormattedString):
        result["parts"] = [_fstring_part_to_dict(p, wrapper) for p in node.parts]
        
    elif isinstance(node, cst.ConcatenatedString):
        result["parts"] = [_node_to_dict(p, wrapper) for p in (node.left, node.right)]
        
    elif isinstance(node, cst.List):
        result["elements"] = [_element_to_dict(e, wrapper) for e in node.elements]
        
    elif isinstance(node, cst.Dict):
        result["elements"] = [_dict_element_to_dict(e, wrapper) for e in node.elements]
        
    elif isinstance(node, cst.Tuple):
        result["elements"] = [_element_to_dict(e, wrapper) for e in node.elements]
        
    elif isinstance(node, cst.Set):
        result["elements"] = [_element_to_dict(e, wrapper) for e in node.elements]
        
    elif isinstance(node, cst.BinaryOperation):
        result["left"] = _node_to_dict(node.left, wrapper)
        result["operator"] = type(node.operator).__name__
        result["right"] = _node_to_dict(node.right, wrapper)
        
    elif isinstance(node, cst.UnaryOperation):
        result["operator"] = type(node.operator).__name__
        result["expression"] = _node_to_dict(node.expression, wrapper)
        
    elif isinstance(node, cst.BooleanOperation):
        result["left"] = _node_to_dict(node.left, wrapper)
        result["operator"] = type(node.operator).__name__
        result["right"] = _node_to_dict(node.right, wrapper)
        
    elif isinstance(node, cst.Comparison):
        result["left"] = _node_to_dict(node.left, wrapper)
        result["comparisons"] = [_comparison_target_to_dict(c, wrapper) for c in node.comparisons]
        
    elif isinstance(node, cst.IfExp):
        result["test"] = _node_to_dict(node.test, wrapper)
        result["body"] = _node_to_dict(node.body, wrapper)
        result["orelse"] = _node_to_dict(node.orelse, wrapper)
        
    elif isinstance(node, cst.Lambda):
        result["params"] = _params_to_dict(node.params)
        result["body"] = _node_to_dict(node.body, wrapper)
        
    elif isinstance(node, cst.Yield):
        if node.value:
            result["value"] = _node_to_dict(node.value, wrapper)
            
    elif isinstance(node, cst.Await):
        result["expression"] = _node_to_dict(node.expression, wrapper)
        
    elif isinstance(node, cst.Import):
        result["names"] = [_import_alias_to_dict(n) for n in node.names] if isinstance(node.names, tuple) else "*"
        
    elif isinstance(node, cst.ImportFrom):
        if node.module:
            result["module"] = _node_to_dict(node.module, wrapper)
        if isinstance(node.names, cst.ImportStar):
            result["names"] = "*"
        else:
            result["names"] = [_import_alias_to_dict(n) for n in node.names]
        result["relative"] = len(node.relative) if node.relative else 0
        
    elif isinstance(node, cst.Pass):
        pass  # Just the type is enough
        
    elif isinstance(node, cst.Break):
        pass
        
    elif isinstance(node, cst.Continue):
        pass
        
    elif isinstance(node, cst.Raise):
        if node.exc:
            result["exc"] = _node_to_dict(node.exc, wrapper)
            
    elif isinstance(node, cst.Assert):
        result["test"] = _node_to_dict(node.test, wrapper)
        if node.msg:
            result["msg"] = _node_to_dict(node.msg, wrapper)
            
    elif isinstance(node, cst.Global):
        result["names"] = [n.name.value for n in node.names]
        
    elif isinstance(node, cst.Nonlocal):
        result["names"] = [n.name.value for n in node.names]
        
    elif isinstance(node, cst.Else):
        result["body"] = _body_to_dict(node.body, wrapper)
        
    elif isinstance(node, cst.Match):
        result["subject"] = _node_to_dict(node.subject, wrapper)
        result["cases"] = [_match_case_to_dict(c, wrapper) for c in node.cases]
        
    else:
        # Fallback: just record the type
        result["_unhandled"] = True
    
    return result


def _params_to_dict(params: cst.Parameters) -> dict[str, Any]:
    """Convert Parameters node to dict."""
    result: dict[str, Any] = {"type": "Parameters"}
    result["params"] = [_param_to_dict(p) for p in params.params]
    
    if params.star_arg and isinstance(params.star_arg, cst.Param):
        result["star_arg"] = _param_to_dict(params.star_arg)
    
    if params.kwonly_params:
        result["kwonly_params"] = [_param_to_dict(p) for p in params.kwonly_params]
    
    if params.star_kwarg:
        result["star_kwarg"] = _param_to_dict(params.star_kwarg)
    
    return result


def _param_to_dict(param: cst.Param) -> dict[str, Any]:
    """Convert Param node to dict."""
    result: dict[str, Any] = {
        "type": "Param",
        "name": param.name.value,
    }
    if param.annotation:
        result["annotation"] = cst.parse_module("").code_for_node(param.annotation.annotation)
    if param.default:
        result["default"] = cst.parse_module("").code_for_node(param.default)
    return result


def _body_to_dict(body: cst.BaseSuite, wrapper: MetadataWrapper | None) -> list[dict]:
    """Convert function/class body to dict."""
    if isinstance(body, cst.IndentedBlock):
        return [_node_to_dict(stmt, wrapper) for stmt in body.body]
    elif isinstance(body, cst.SimpleStatementSuite):
        return [_node_to_dict(stmt, wrapper) for stmt in body.body]
    return []


def _decorator_to_dict(dec: cst.Decorator, wrapper: MetadataWrapper | None) -> dict[str, Any]:
    """Convert Decorator to dict."""
    return {
        "type": "Decorator",
        "decorator": _node_to_dict(dec.decorator, wrapper),
    }


def _arg_to_dict(arg: cst.Arg, wrapper: MetadataWrapper | None) -> dict[str, Any]:
    """Convert Arg to dict."""
    result: dict[str, Any] = {
        "type": "Arg",
        "value": _node_to_dict(arg.value, wrapper),
    }
    if arg.keyword:
        result["keyword"] = arg.keyword.value
    if isinstance(arg.star, str) and arg.star:
        result["star"] = arg.star
    return result


def _target_to_dict(target: cst.AssignTarget, wrapper: MetadataWrapper | None) -> dict[str, Any]:
    """Convert AssignTarget to dict."""
    return {
        "type": "AssignTarget",
        "target": _node_to_dict(target.target, wrapper),
    }


def _handler_to_dict(handler: cst.ExceptHandler, wrapper: MetadataWrapper | None) -> dict[str, Any]:
    """Convert ExceptHandler to dict."""
    result: dict[str, Any] = {
        "type": "ExceptHandler",
        "body": _body_to_dict(handler.body, wrapper),
    }
    if handler.type:
        result["exc_type"] = _node_to_dict(handler.type, wrapper)
    if handler.name:
        # handler.name is an AsName object, need to access .name.value
        if isinstance(handler.name, cst.AsName):
            result["name"] = handler.name.name.value if isinstance(handler.name.name, cst.Name) else str(handler.name.name)
        else:
            result["name"] = str(handler.name)
    return result


def _with_item_to_dict(item: cst.WithItem, wrapper: MetadataWrapper | None) -> dict[str, Any]:
    """Convert WithItem to dict."""
    result: dict[str, Any] = {
        "type": "WithItem",
        "item": _node_to_dict(item.item, wrapper),
    }
    if item.asname:
        result["asname"] = _node_to_dict(item.asname.name, wrapper)
    return result


def _slice_to_dict(slc: cst.BaseSlice, wrapper: MetadataWrapper | None) -> dict[str, Any]:
    """Convert slice to dict."""
    if isinstance(slc, cst.SubscriptElement):
        return _node_to_dict(slc.slice, wrapper)
    return {"type": "UnknownSlice"}


def _element_to_dict(elem: cst.BaseElement, wrapper: MetadataWrapper | None) -> dict[str, Any]:
    """Convert list/tuple/set element to dict."""
    if isinstance(elem, cst.Element):
        return _node_to_dict(elem.value, wrapper)
    elif isinstance(elem, cst.StarredElement):
        return {"type": "Starred", "value": _node_to_dict(elem.value, wrapper)}
    return {"type": "UnknownElement"}


def _dict_element_to_dict(elem: cst.BaseDictElement, wrapper: MetadataWrapper | None) -> dict[str, Any]:
    """Convert dict element to dict."""
    if isinstance(elem, cst.DictElement):
        return {
            "type": "DictElement",
            "key": _node_to_dict(elem.key, wrapper),
            "value": _node_to_dict(elem.value, wrapper),
        }
    elif isinstance(elem, cst.StarredDictElement):
        return {"type": "StarredDictElement", "value": _node_to_dict(elem.value, wrapper)}
    return {"type": "UnknownDictElement"}


def _comparison_target_to_dict(comp: cst.ComparisonTarget, wrapper: MetadataWrapper | None) -> dict[str, Any]:
    """Convert ComparisonTarget to dict."""
    return {
        "type": "ComparisonTarget",
        "operator": type(comp.operator).__name__,
        "comparator": _node_to_dict(comp.comparator, wrapper),
    }


def _import_alias_to_dict(alias: cst.ImportAlias) -> dict[str, Any]:
    """Convert ImportAlias to dict."""
    result: dict[str, Any] = {
        "type": "ImportAlias",
        "name": cst.parse_module("").code_for_node(alias.name),
    }
    if alias.asname:
        if isinstance(alias.asname, cst.AsName):
            result["asname"] = alias.asname.name.value if isinstance(alias.asname.name, cst.Name) else str(alias.asname.name)
    return result


def _fstring_part_to_dict(part: cst.BaseFormattedStringContent, wrapper: MetadataWrapper | None) -> dict[str, Any]:
    """Convert f-string part to dict."""
    if isinstance(part, cst.FormattedStringText):
        return {"type": "FormattedStringText", "value": part.value}
    elif isinstance(part, cst.FormattedStringExpression):
        return {"type": "FormattedStringExpression", "value": _node_to_dict(part.expression, wrapper)}
    return {"type": "UnknownFStringPart"}


def _match_case_to_dict(case: cst.MatchCase, wrapper: MetadataWrapper | None) -> dict[str, Any]:
    """Convert MatchCase to dict."""
    result: dict[str, Any] = {
        "type": "MatchCase",
        "pattern": _node_to_dict(case.pattern, wrapper),
        "body": _body_to_dict(case.body, wrapper),
    }
    if case.guard:
        result["guard"] = _node_to_dict(case.guard, wrapper)
    return result


def cst_to_json(module: cst.Module, canonical_path: str) -> dict[str, Any]:
    """
    Convert libcst Module to JSON-serializable dict.
    
    Args:
        module: Parsed libcst Module
        canonical_path: Canonical path for metadata
        
    Returns:
        Full CST as JSON dict
    """
    # Wrap with metadata for position info
    wrapper = MetadataWrapper(module)
    
    tree = _node_to_dict(module, wrapper)
    
    return {
        "schema_version": CST_SCHEMA_VERSION,
        "file_path": canonical_path,
        "parser": "libcst",
        "parser_version": importlib.metadata.version("libcst"),
        "serialized_at": datetime.now(timezone.utc).isoformat(),
        "tree": tree,
    }


__all__ = [
    "cst_to_json",
    "CST_SCHEMA_VERSION",
]
