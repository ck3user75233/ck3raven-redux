# `ck3_git` — Active Design & Policy Set

This folder holds the current design and policy material for the planned diagnostic-first `ck3_git` MCP tool.

> **Current state (documentation alignment):** The legacy `ck3_git` implementation was **retired** at commit `72b0607`; ordinary raw Git through `ck3_exec` is **closed**; there is currently **no ordinary MCP Git capability**; and Stage 0/1 of the redesign is **not yet implemented**. No retired route is an authorized workaround.

## Active documents

| File | Role |
|---|---|
| [01 — Canonical architecture & policy](01_CK3_GIT_CANONICAL_ARCHITECTURE_AND_POLICY_CLEAN_DRAFT.md) | The sole normative `ck3_git` architecture and product-policy baseline. |
| [02 — MVP implementation planning directive](02_CK3_GIT_MVP_IMPLEMENTATION_PLANNING_DIRECTIVE_CLEAN_DRAFT.md) | Source-grounded Stage 0 / Stage 1 planning requirements, to be executed from the current retired state. |
| [../MOD_GIT_METADATA_OPERATIONS_NOTE.md](../MOD_GIT_METADATA_OPERATIONS_NOTE.md) | Current operational note for mod-folder Git metadata (external git-dir pattern). |

The earlier start-here (00), deprecated-document-cleanup (03), preview-first, and wrapper-design packages have been retired; their bytes are preserved in the owner archive. This README is the minimal entry point to the active set.
