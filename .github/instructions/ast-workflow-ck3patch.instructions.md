---
name: CK3 AST Workflow (Patch Outputs)
description: Apply CK3-safe patch output standards for generated script/localization artifacts.
applyTo: "common/**/*.txt,events/**/*.txt,history/**/*.txt,localization/**/*.yml,map_data/**/*.txt"
---
# CK3 patch output requirements

When generating or editing CK3 patch artifacts:

## Patch safety and scope

- Patch only requested targets and symbols.
- Preserve file structure and unrelated blocks.
- Prefer partial, minimal edits unless a full replacement is explicitly required.

## Merge-aware behavior

- Respect CK3 override semantics: load order between mods is dominant.
- Do not claim cross-mod win conditions from filename sorting alone.
- Use filename conventions to signal patch intent when operating within one mod.

## Output discipline

- Produce a clear run report for each patch batch, including:
  - source files
  - target symbols/IDs
  - before/after validation status
  - any fallback matching that was required
- Keep generated artifacts in a dedicated WIP project folder before final apply.

## Validation before apply

- Parse/validate modified CK3 script content before writing final files.
- Re-index final files and fail fast on parser errors.
- If validation fails, stop and return diagnostics instead of writing uncertain output.

Reference guide:
`C:/Users/nateb/.ck3raven/wip/ast_patch_workflow_guide.md`
