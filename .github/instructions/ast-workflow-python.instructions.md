---
name: CK3 AST Workflow (Python)
description: AST-first, offset-safe, two-phase workflow for Python scripts used to create or edit CK3 mod patch artifacts.
---
# CK3 AST patch scripting workflow (Python)

This file is intentionally task-scoped and not globally auto-applied to all Python work.
Use it when creating or editing Python scripts that parse CK3 script files and generate CK3 patch artifacts.

## Core workflow

1. Bootstrap parser imports with sys.path.insert(...) for local SDK access when needed. Do not rely on fragile dynamic import tricks for this workflow.
2. Parse first, then traverse AST nodes to locate targets. Avoid raw line-based editing as a primary strategy.
3. Compute byte/character offsets from AST node spans.
4. Build edits as explicit replacement ops and apply them in reverse-sorted offset order.
5. Use two-phase execution:
   - Phase 1: write candidate outputs and reports under a timestamped WIP project folder.
   - Phase 2: apply approved artifacts to destination paths.

## Reliability requirements

- Treat BlockNode spans as authoritative where available.
- If an AssignmentNode has missing or degenerate spans (for example [0,0)), use deterministic text-search fallback with strict guard checks.
- Require full-string diff assertions before apply (expected full file content equality checks).
- Prefer explicit, deterministic transforms over heuristic mutation.

## Encoding and platform rules

- Preserve CK3 text expectations: use UTF-8 with BOM (utf-8-sig) for .txt and .yml outputs unless file-specific constraints require otherwise.
- Keep console output Windows-safe (avoid unnecessary non-ASCII in status logging).

## Validation checklist

Before considering the task complete:

- Syntax-validate produced CK3 script where applicable.
- Re-index changed files (ck3_file command=index parse=true) to ensure parser acceptance.
- Emit a run report that lists inputs, edited symbols, fallback usages, and validation results.

Reference guide:
C:/Users/nateb/.ck3raven/wip/ast_patch_workflow_guide.md
