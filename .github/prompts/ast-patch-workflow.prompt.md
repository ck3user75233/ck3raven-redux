---
name: ast-patch-workflow
description: Run the CK3 AST-first patch workflow with two-phase staging and validation.
agent: agent
---
Use this prompt for CK3 patch scripting tasks.

How to invoke (user):
- In Chat, type /ast-patch-workflow
- Optionally append task details, for example: /ast-patch-workflow update holder fixes for selected title history files
- Alternative: run Chat: Run Prompt and pick ast-patch-workflow

Run this task using the CK3 AST-first patch workflow.

## Inputs to collect first

- Target files or directories
- Target symbols or IDs to modify
- Expected behavioral outcome
- Whether this is analysis-only, phase-1 staging, or phase-2 apply

## Required execution model

1. Parse targets and identify node spans.
2. Compute deterministic edits from AST locations.
3. Apply edits in reverse offset order.
4. Write staged outputs into a timestamped WIP project folder plus run_report.txt.
5. Validate syntax and index staged results.
6. If user approved apply: write final targets and re-validate.

## Guardrails

- Do not use heuristic broad replacements when precise AST spans exist.
- If span data is missing for a target node, use strict text-search fallback and record it in the report.
- Stop on validation errors and report exact failing files and symbols.

Use this reference when needed:
C:/Users/nateb/.ck3raven/wip/ast_patch_workflow_guide.md

At the end, return:

- A concise change summary
- Validation results
- Paths to staged or applied outputs and report files
