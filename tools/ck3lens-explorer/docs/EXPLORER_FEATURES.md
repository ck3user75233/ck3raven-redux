# Planned CK3 Lens Explorer Features

## Status

Every feature in this document is planned and not implemented. No historical scaffold, partial implementation, removed command, or removed view is treated as remaining product capability.

The currently implemented extension scope is documented in `../README.md` and `../DESIGN.md`. Any feature below requires a new product decision and must preserve the canonical ownership model.

## Planning Principles

- `Session.mods` is the only authoritative runtime mod list.
- Playset state is consumed from authoritative session snapshots, not reconstructed from files, descriptors, launcher data, or database guesses.
- User-supplied canonical paths resolve through WorldAdapter v2 exactly once.
- Resolution and visibility do not decide permissions. Mutations route through canonical MCP tools and the enforcement boundary.
- QBuilder remains the sole database writer.
- Read-only editor features may use a purpose-built read-only service where direct VS Code APIs are insufficient.
- Explicit file display and opening should use VS Code filesystem and editor APIs where appropriate.
- Parser-backed work may use a narrow parser worker. It must not recreate a generic Python command bridge or an independent session owner.
- No path-discovery fallback, alternate mod list, direct playset JSON mutation, or arbitrary mod write is permitted.

## Playset Visibility

**Status:** Planned, not implemented.

**Intent:** Show the active playset, load order, readiness, and mod identity without providing an independent playset manager.

**Implementation boundary:**

- Read state from an authoritative session snapshot produced by the canonical session lifecycle.
- Render `Session.mods` directly; do not rescan descriptors, launcher databases, or directories to fill gaps.
- Treat unresolved or ingesting entries as explicit states rather than selecting fallback content.
- Route switch, import, add, remove, or reorder actions through canonical MCP and enforcement flows if mutation UI is approved later.
- Never edit playset or session JSON directly.

## Game-State Browsing And Provenance

**Status:** Planned, not implemented.

**Intent:** Browse visible game and mod content with source, load-order, winner, and provenance information.

**Implementation boundary:**

- Scope queries with the authoritative session snapshot and its resolved content identities.
- Use a purpose-built read-only query service or canonical MCP read tools for indexed state.
- Resolve user-entered canonical paths through WorldAdapter v2.
- Use VS Code filesystem APIs and `openTextDocument` for ordinary display of an already identified file.
- Keep provenance read-only; do not infer writability from source type or display metadata.

## Native Symbol Intelligence

**Status:** Planned, not implemented.

**Intent:** Provide completion, hover, definition, reference, and workspace-symbol behavior through native VS Code language APIs.

**Implementation boundary:**

- Implement native VS Code providers with cancellation, document-version checks, and bounded queries.
- Obtain symbols and references from a purpose-built read-only service or canonical read tools scoped by the authoritative session.
- Open definition targets with VS Code editor APIs after canonical resolution.
- Do not add an independent session cache, parallel symbol ownership model, or general-purpose backend command channel.
- Keep all providers read-only.

## Diagnostics And Validation

**Status:** Planned, not implemented.

**Intent:** Publish CK3 syntax, reference, scope, and localization diagnostics through native VS Code diagnostics.

**Implementation boundary:**

- Use `DiagnosticCollection` and document-version-aware scheduling in the extension.
- A narrow parser worker may parse in-memory document text and return structured diagnostics only.
- Reference checks must use authoritative, playset-scoped read models.
- Validation is not a permission oracle and must not write files or mutate indexed state.
- Do not recreate a generic bridge for parsing, file access, or commands.

## Issues And Conflicts

**Status:** Planned, not implemented.

**Intent:** Present parser issues, reference issues, file conflicts, symbol conflicts, and winning definitions.

**Implementation boundary:**

- Treat the UI as a read-only projection of canonical diagnostics and conflict queries.
- Scope all results to the authoritative session snapshot and canonical load order.
- Keep conflict policy computation in the established resolver or canonical MCP layer, not in the view.
- Route any resolution or mutation action through canonical MCP contracts and enforcement.
- Do not create view-local conflict state that competes with the underlying source of truth.

## AST Inspection

**Status:** Planned, not implemented.

**Intent:** Inspect parsed structure, source ranges, parse errors, and optional comparisons without authoring files.

**Implementation boundary:**

- Parse current in-memory text through a narrow parser worker or canonical read-only parser tool.
- Return structured AST and error data only; do not expose a general backend command surface.
- Keep source offsets tied to the exact document version that produced the parse result.
- Use native editors or a bounded webview for presentation, and VS Code file APIs for explicit file display.
- AST inspection remains read-only.

## Studio And File Authoring

**Status:** Planned, not implemented.

**Intent:** Provide guided creation and editing workflows for CK3 files only after a dedicated product decision.

**Implementation boundary:**

- Use native editors and focused forms for authoring rather than owning a second filesystem model.
- Resolve every target through WorldAdapter v2 and preserve its canonical address.
- Send create, edit, rename, and delete operations through canonical MCP and enforcement flows.
- Treat validation as advisory input to the mutation request, never as a substitute for enforcement.
- Do not write directly to arbitrary mod paths, infer editable mods, or maintain a whitelist outside canonical policy.

## Patch And Compatch Workflows

**Status:** Planned, not implemented.

**Intent:** Analyze incompatibilities, prepare reviewed patch proposals, and apply approved compatch artifacts.

**Implementation boundary:**

- Use canonical conflict and diff tools for analysis and preserve source provenance.
- Generate proposals as reviewable artifacts before mutation.
- Apply changes only through canonical MCP contract, token, validation, and enforcement flows.
- Follow CK3 merge policy and AST-safe patch rules; do not implement a direct patch writer in the extension.
- Let QBuilder observe or enqueue resulting files through its established single-writer lifecycle.

## Reports

**Status:** Planned, not implemented.

**Intent:** Present or export summaries for playset readiness, provenance, diagnostics, conflicts, and patch work.

**Implementation boundary:**

- Build reports from authoritative session-scoped read queries.
- Keep report generation read-only unless the user explicitly requests an export.
- Route exported artifact writes through canonical MCP and enforcement flows.
- Do not persist a second playset snapshot or use reports as operational state.

## Acceptance Gates

A planned feature may be reintroduced only when all of these gates are satisfied:

1. **Product decision:** Scope, user workflow, and ownership are explicitly approved.
2. **Canonical ownership:** Session, path resolution, enforcement, database, and editor responsibilities stay with their established owners.
3. **No fallback state:** The design introduces no path-discovery fallback, parallel mod list, reconstructed playset, direct JSON mutation, or alternate adapter.
4. **Focused tests:** Tests cover the feature boundary, lifecycle behavior, error handling, and the relevant canonical invariants.
5. **Development Host acceptance:** The workspace extension is compiled and exercised in an Extension Development Host, including activation, intended workflow, multi-window behavior where relevant, and clean shutdown.

A historical implementation, deleted file, old screenshot, or stale document is not evidence that any gate has been met.
