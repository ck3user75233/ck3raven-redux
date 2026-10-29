# Sprint 2.1 — Python Indexer Implementation Plan

> **Created:** February 25, 2026  
> **Status:** Planning complete — ready for implementation  
> **Parent:** `ck3_file_Refactor_Design.md` §10, §12.2  
> **Dependencies:** `libcst>=1.0`

---

## 1. Overview

Task 2.1 builds the Python indexer infrastructure with two output modes:

| Output | Format | Size | Purpose |
|--------|--------|------|---------|
| **INDEX** | Compact JSON | ~5KB | Navigation map — names, types, signatures, line ranges |
| **CST** | Full JSON tree | ~50KB | Complete structure — enables future CST editing, deep analysis |

Both are JSON (human/agent readable). The CST output is optional (`parse=True`).

---

## 2. File Structure

```
tools/ck3lens_mcp/ck3lens/indexing/
    __init__.py           # detect_file_type(), get_index(), index_file()
    python_indexer.py     # libcst-based Python indexer
    cst_serializer.py     # CSTVisitor that converts libcst Module → JSON dict
    
    # Future (Task 2.2):
    markdown_indexer.py
    ck3_indexer.py  
    typescript_indexer.py
    fallback_indexer.py
    
    # Task 2.3:
    cst_cache.py          # mtime-based cache layer
```

---

## 3. API Design

### 3.1 MCP Tool Interface

```python
ck3_file(
    command="index",
    path="root:repo/tools/ck3lens_mcp/server.py",
    output_path="root:ck3raven_data/wip/indexes/",  # optional, defaults to cache dir
    parse=False,  # if True, also output full CST JSON
)
```

**Returns:**
```json
{
  "reply_type": "S",
  "code": "EN-INDEX-S-001",
  "data": {
    "source_path": "root:repo/tools/ck3lens_mcp/server.py",
    "index_path": "root:ck3raven_data/wip/indexes/INDEX-server.py.json",
    "cst_path": "root:ck3raven_data/wip/indexes/CST-server.py.json",  // only if parse=True
    "file_type": "python",
    "element_count": 62,
    "index_bytes": 4340,
    "cst_bytes": 52000  // only if parse=True
  }
}
```

### 3.2 Internal Python API

```python
# indexing/__init__.py

def detect_file_type(path: Path) -> Literal["python", "typescript", "markdown", "ck3", "unknown"]:
    """Detect file type from extension."""
    ...

def index_file(
    host_path: Path,
    canonical_path: str,
    *,
    include_cst: bool = False,
) -> IndexResult:
    """
    Index a file and optionally produce full CST.
    
    Returns:
        IndexResult with .index (compact) and optionally .cst (full tree)
    """
    ...

@dataclass
class IndexResult:
    index: dict          # Compact index (names, lines, signatures)
    cst: dict | None     # Full CST JSON tree (if include_cst=True)
    file_type: str
    parser: str
    parser_version: str
```

```python
# indexing/python_indexer.py

def index_python_file(
    host_path: Path,
    canonical_path: str,
    *,
    include_cst: bool = False,
) -> IndexResult:
    """
    Parse Python file with libcst, extract index + optional full CST.
    """
    ...
```

```python
# indexing/cst_serializer.py

def cst_to_json(module: cst.Module) -> dict:
    """
    Convert libcst Module to JSON-serializable dict.
    Preserves full tree structure with node types, values, positions.
    """
    ...
```

---

## 4. Output Schemas

### 4.1 INDEX Schema (Compact)

```json
{
  "schema_version": "1.0.0",
  "file_path": "root:repo/tools/ck3lens_mcp/server.py",
  "file_bytes": 265440,
  "file_lines": 6636,
  "parser": "libcst",
  "parser_version": "1.5.1",
  "indexer_version": "1.0.0",
  "indexed_at": "2026-02-25T14:30:00Z",
  "elements": [
    {
      "kind": "function",
      "name": "_log_exception",
      "signature": "(trace_id: str, tool_name: str, exc: Exception) -> None",
      "line_start": 578,
      "line_end": 596,
      "depth": 0,
      "decorators": [],
      "docstring": "Log exception with trace context.",
      "children": []
    },
    {
      "kind": "class",
      "name": "ReplyBuilder",
      "signature": null,
      "line_start": 598,
      "line_end": 767,
      "depth": 0,
      "decorators": [],
      "docstring": "Builder for MCP Reply objects.",
      "children": [
        {
          "kind": "method",
          "name": "__init__",
          "signature": "(self, tool: str, trace: Trace, meta: dict)",
          "line_start": 605,
          "line_end": 620,
          "depth": 1,
          "decorators": [],
          "docstring": null,
          "children": []
        },
        {
          "kind": "method", 
          "name": "success",
          "signature": "(self, code: str, data: dict) -> Reply",
          "line_start": 622,
          "line_end": 640,
          "depth": 1,
          "decorators": [],
          "docstring": "Build success reply.",
          "children": []
        }
      ]
    },
    {
      "kind": "assignment",
      "name": "AVG_LINE_BYTES",
      "signature": "45",
      "line_start": 227,
      "line_end": 227,
      "depth": 0,
      "decorators": [],
      "docstring": null,
      "children": []
    }
  ]
}
```

**Element kinds:** `function`, `class`, `method`, `assignment`, `import_group`

**Size estimate:** ~70 bytes/function, ~100 bytes/class + ~60 bytes/method

### 4.2 CST Schema (Full Tree)

```json
{
  "schema_version": "1.0.0",
  "file_path": "root:repo/tools/ck3lens_mcp/server.py",
  "parser": "libcst",
  "parser_version": "1.5.1",
  "tree": {
    "type": "Module",
    "body": [
      {
        "type": "SimpleStatementLine",
        "line": 1,
        "body": [
          {
            "type": "ImportFrom",
            "module": "pathlib",
            "names": [
              {"type": "ImportAlias", "name": "Path", "asname": null}
            ]
          }
        ]
      },
      {
        "type": "FunctionDef",
        "name": "_log_exception",
        "line_start": 578,
        "line_end": 596,
        "params": {
          "type": "Parameters",
          "params": [
            {
              "type": "Param",
              "name": "trace_id",
              "annotation": {"type": "Name", "value": "str"},
              "default": null
            },
            {
              "type": "Param",
              "name": "tool_name",
              "annotation": {"type": "Name", "value": "str"},
              "default": null
            }
          ]
        },
        "returns": {"type": "Name", "value": "None"},
        "decorators": [],
        "body": [
          {
            "type": "SimpleStatementLine",
            "line": 579,
            "body": [
              {
                "type": "Expr",
                "value": {
                  "type": "Call",
                  "func": {"type": "Attribute", "value": {"type": "Name", "value": "logger"}, "attr": "error"},
                  "args": ["..."]
                }
              }
            ]
          }
        ]
      }
    ]
  }
}
```

**Key properties:**
- Every node has `type` matching libcst class name
- Line numbers included on statements
- Full nesting preserved
- String literals may be truncated for size (configurable)

---

## 5. Call Chain

### 5.1 Explicit `command="index"` (agent-initiated)

```
ck3_file(command="index", path="root:repo/server.py", parse=True)
    │
    ├─ WA2.resolve(path) → host_path, ref
    │
    ├─ indexing.index_file(host_path, canonical_path, include_cst=True)
    │   ├─ detect_file_type() → "python"
    │   ├─ python_indexer.index_python_file()
    │   │   ├─ libcst.parse_module(source)
    │   │   ├─ IndexExtractor(CSTVisitor).walk() → elements[]
    │   │   ├─ if include_cst: cst_serializer.cst_to_json(module) → tree{}
    │   │   └─ return IndexResult(index=..., cst=...)
    │   └─ return IndexResult
    │
    ├─ cst_cache.write_index(host_path, result.index) → index_path
    ├─ if parse: cst_cache.write_cst(host_path, result.cst) → cst_path
    │
    └─ Reply.data = {index_path, cst_path, element_count, ...}
```

### 5.2 Implicit via `context_limit` denial (automatic)

```
ck3_file(command="read", path="root:repo/server.py", dev_mode=True)
    │
    ├─ enforcement_v2.enforce() → evaluates context_limit
    │   ├─ os.stat() → 265KB > 15KB threshold
    │   ├─ indexing.index_file(host_path, canonical_path, include_cst=False)
    │   │   └─ (only index, not full CST — keep denial lightweight)
    │   ├─ cst_cache.write_index() → index_path
    │   ├─ Deposits into ctx["_denial_meta"]:
    │   │   - index_path, index_type, index_element_count
    │   └─ returns False (deny)
    │
    └─ Reply: DENIAL with index_path in data.next_call
```

---

## 6. Integration Points

| Component | Change | Sprint |
|-----------|--------|--------|
| `unified_tools.py` | Add `command="index"` handler | 2.1 |
| `server.py` | Add `parse: bool = False` parameter to ck3_file | 2.1 |
| `capability_matrix_v2.py` | `context_limit()` calls `get_index()` on denial | 2.4 |
| `pyproject.toml` | Add `libcst>=1.0` | 2.4 |
| `cst_cache.py` | Cache read/write for both INDEX and CST files | 2.3 |

---

## 7. File Naming Convention

```
~/.ck3raven/wip/MCP_tool_replies/ck3_file/indexes/

INDEX-{filename}.json     # Compact index
CST-{filename}.json       # Full CST (if parse=True)

# For files with same name from different paths, append hash:
INDEX-server.py-a1b2c3d4.json
CST-server.py-a1b2c3d4.json
```

Hash = first 8 chars of SHA256(canonical_path)

---

## 8. Deliverables

### 8.1 Files to Create

| File | Lines (est.) | Description |
|------|--------------|-------------|
| `indexing/__init__.py` | ~80 | `detect_file_type()`, `index_file()`, `IndexResult` dataclass |
| `indexing/python_indexer.py` | ~200 | `IndexExtractor(CSTVisitor)`, `index_python_file()` |
| `indexing/cst_serializer.py` | ~150 | `CSTToJSON(CSTVisitor)`, `cst_to_json()` |

### 8.2 Files to Modify

| File | Change |
|------|--------|
| `server.py` | Add `parse: bool` param, route `command="index"` |
| `unified_tools.py` | Add `_ck3_file_index()` handler |

### 8.3 Acceptance Criteria

1. **Index test:** `index_file(safety.py)` returns correct element list
   - All top-level functions found with correct line ranges
   - `SafeToolWrapper` class with all methods listed as children
   - Index JSON < 5KB

2. **CST test:** `index_file(safety.py, include_cst=True)` returns full tree
   - Tree can be loaded as JSON
   - Every function body is present with nested structure
   - CST JSON < 50KB

3. **Integration test:** `ck3_file(command="index", path="root:repo/.../safety.py", parse=True)`
   - Both INDEX-safety.py.json and CST-safety.py.json written to cache
   - Reply.data contains both paths
   - Files are valid JSON

---

## 9. Dependencies

```toml
# pyproject.toml additions (Task 2.4)
[project.optional-dependencies]
indexing = [
    "libcst>=1.0",
]
```

For now, manually install: `pip install libcst>=1.0`

---

## 10. Open Questions

1. **String truncation in CST:** Should long string literals be truncated to save space? (Proposed: truncate at 200 chars with `"...[truncated]"`)

2. **Comment preservation:** Should `cst_to_json` include comments? (libcst preserves them, but adds size)

3. **Import grouping:** Should consecutive imports be grouped into one `import_group` element in the index?

---

## 11. Future Extensions

- **Task 2.2:** Markdown, CK3, TypeScript, fallback indexers (same dual-output pattern)
- **Task 2.3:** Cache layer with mtime invalidation
- **Later:** `command="cst_edit"` using cached CST for round-trip editing
