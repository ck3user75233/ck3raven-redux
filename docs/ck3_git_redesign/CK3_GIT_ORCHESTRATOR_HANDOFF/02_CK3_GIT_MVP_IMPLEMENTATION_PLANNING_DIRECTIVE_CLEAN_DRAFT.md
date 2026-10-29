# Agent Directive - Plan `ck3_git` MVP Stage 0 and Stage 1

**Status:** Owner-review draft, revised by owner direction on 24 September 2026  
**Assignment:** Source inspection, architecture verification, and implementation planning only.

## 1. Authority and deliverable boundary

Use [the canonical architecture and policy](01_CK3_GIT_CANONICAL_ARCHITECTURE_AND_POLICY_CLEAN_DRAFT.md) as the sole normative `ck3_git` source. Current source establishes implementation facts and exact touchpoints; it does not override product policy.

The legacy `ck3_git` implementation and ordinary raw Git through `ck3_exec` are already retired. Plan from that state. This assignment produces documentation and WIP review artifacts only. Production runtime, policy, extension, and test files remain unchanged until separately authorized candidate and apply stages.

The WIP binding synthesis is `05_DECLARATIVE_RULE_DESIGN.md` and `06_TEST_AND_EXIT_GATE_MATRIX.md`. Preserve their content unless a cross-reference requires adjustment. `07_CANDIDATE_REVIEW_WORKFLOW.md` governs candidate, diff, apply, and rollback review.

## 2. Required architecture

Git command policy classification is independent of agent mode and target geography. Plan one `GIT_COMMAND_POLICIES` table that maps each mechanical command or mode to one Git command policy entry. Do not key it by mode, root, subdirectory, or geography and do not treat it as a second geography table.

A policy floor is exactly one of:

```text
DIAGNOSTIC
LOCAL_MUTATION
REMOTE_OR_DESTRUCTIVE
EXCLUDED
```

A floor is a classification, not a record. A Git command policy entry assigns one floor and contains only applicable Owner-approved Condition predicates and concrete argument constraints. Argument constraints are not Condition predicates.

The active route is:

```text
canonical repository target
    -> handler schema and argument validation
    -> WA2 repository and operand resolution
    -> GIT_COMMAND_POLICIES classification
    -> handler supplies resolved facts and approved predicates
    -> canonical enforcement_v2.enforce()
        -> DENY: terminal canonical result
        -> ALLOW: handler-owned controlled direct argv
    -> canonical Reply and logging
```

WA2 owns visibility. Existing canonical enforcement evaluates applicable capability and geography predicates for resolved targets. The handler validates and orchestrates but does not authorize. Enforcement authorizes or denies but never executes Git.

The current `ck3_file` `execute()` divergence is a separate project. Do not copy or repair it.

## 3. Floor semantics

### `DIAGNOSTIC`

No intended persistent mutation. No WRITE or HAT requirement. Enforcement evaluates only predicates explicitly approved for the command entry.

### `LOCAL_MUTATION`

Enforcement evaluates WRITE for every complete affected path plus only other Owner-approved predicates. One unresolved or failed path denies the request. Parent WRITE permits child creation; deletion uses WRITE; rename checks old and new paths.

### `REMOTE_OR_DESTRUCTIVE`

All applicable hard-policy and WRITE predicates pass before HAT. Do not infer or propose command-specific predicates without explicit Owner approval.

### `EXCLUDED`

Canonical unsupported result. No execution or HAT.

Future command-specific predicates and preview mechanics require explicit Owner approval before addition to `GIT_COMMAND_POLICIES`.

## 4. Stage 0 - dormant data and reply preparation

Stage 0 has exactly two proposed production destinations:

| Destination | Planned scope |
|---|---|
| `tools/ck3lens_mcp/ck3lens/capability_matrix_v2.py` | Add the exact four-floor enum and an empty dormant `GIT_COMMAND_POLICIES` table. Add no command entries, handler, process route, or runtime consumer. |
| `tools/ck3lens_mcp/ck3lens/reply_codes.py` | Add the approved Git S/I/D replies and correct the stale EN-layer header as required. |

Stage 0 does not change `enforcement_v2.py`, `execute()`, server registration, WA2, process helpers, active command rows, extension code, or HAT behavior.

Stage 0 validation is visual Owner review of:

- the Owner-approved pre-mutation manifest;
- full WIP baseline and candidate files;
- exact production-versus-candidate diffs;
- the exact four-floor enum;
- the empty dormant `GIT_COMMAND_POLICIES` table;
- approved reply entries and header correction;
- production baseline hashes and changed-file audit; and
- the reviewed but unexecuted backup/apply/rollback artifact.

Do not construct policy entries, synthetic policy records, or a synthetic policy harness for Stage 0. Do not test affected paths in Stage 0 or Diagnostic Stage 1.

## 5. Stage 1 - approved Diagnostic delivery

Stage 1 may activate exactly:

| Command or mode | Policy floor |
|---|---|
| `status` | `DIAGNOSTIC` |
| `diff` | `DIAGNOSTIC` |
| `log` | `DIAGNOSTIC` |
| `show` | `DIAGNOSTIC` |
| `branch:list` | `DIAGNOSTIC` |

For each entry, specify only its identity, floor, concrete read-only argument constraints, and controlled-process requirements. Do not add WRITE, mutation inspection, escalation, or HAT data.

The handler validates request shape and arguments, resolves targets through WA2, classifies the command through `GIT_COMMAND_POLICIES`, supplies facts to canonical `enforce()`, and executes direct argv only after `ALLOW`. Unknown commands, unsupported modes, excluded commands, and argument mismatches return the canonical unsupported result.

Stage 1 evidence uses actual isolated MCP calls at approved representative canonical roots. Capture successful and failing WA2 paths, command-table classification, argument rejection, canonical enforcement ordering, absence of process launch on denial, direct argv controls, bounded and sanitized output, replies, logging, and raw-Git closure.

## 6. First mutation stage

The first separately approved stage that proposes an actual mutation command owns `AFFECTED_PATHS` testing. It must:

1. use actual Git commands in isolated repositories;
2. cover every applicable canonical root or geography;
3. log the complete affected-path list;
4. log each WA2 resolution and WRITE decision in order;
5. deny the whole request on one unresolved or failed path; and
6. obtain prior Owner approval before adding any synthetic supplemental test.

If Git-native inspection cannot establish a complete affected-path set, report the command and recommend `EXCLUDED` or unsupported status before proposing an entry.

Middle and mutation command research may describe factual Git behavior. It must not assign Conditions, imply approval, or activate a command. Future command-specific predicates and preview mechanics require explicit Owner approval.

## 7. Current-source inspection

Inspect live source before naming exact files or symbols. Record repository-relative paths, relevant symbols, current behavior, immutable baselines or hashes, and evidence supporting each proposed action.

Inspect:

- MCP handler conventions and safe-tool wrappers;
- WA2 repository and operand resolution;
- `capability_matrix_v2.py` and canonical `enforce()` interfaces;
- controlled process mechanics;
- raw-Git whitelist closure;
- reply registry and `ReplyBuilder` conventions;
- canonical logging and trace propagation;
- relevant existing tests; and
- extension code only if an approved stage actually touches it.

Distinguish live code from retired code and documentation-only proposals. Do not treat an unimplemented idea as a migration target.

## 8. Replies, logging, and execution controls

Use registry-owned canonical replies. WA2 failures pass through unchanged. Enforcement denials remain terminal. Missing executable and nonzero Git results follow the approved controlled-attempt result mapping; timeout follows the framework error path.

Use trusted executable discovery, direct argv with no shell, WA2-derived working directory, controlled environment and locale, disabled pager/color/prompts, disabled external helpers where required, bounded stdout/stderr, timeout, process-tree cleanup, and sanitization.

Logging records resolution handoff, command-table classification, argument rejection, enforcement outcome, process start/end after `ALLOW`, and final result. Omit host paths, credentials, sensitive URLs, commit messages, raw patches, hidden output, and signing material.

## 9. Candidate and Owner-review workflow

Use stage-specific WIP directories beneath:

```text
root:ck3raven_data/wip/ck3_git_mvp/stage-0/
root:ck3raven_data/wip/ck3_git_mvp/stage-1/
```

Before generating or modifying any candidate, prepare a pre-mutation manifest with exact production destinations, named definitions, precise planned edits, raw-byte baseline hashes, baseline-copy paths, and planned candidate paths. Obtain Owner approval before candidate generation.

After approval:

1. generate full candidate files only under WIP;
2. create exact production-versus-candidate diffs;
3. compare every hunk with the approved manifest;
4. document and separately approve every deviation or remove it;
5. record final candidate hashes, destinations, candidate paths, and diff artifacts; and
6. prove production remains unchanged with baseline hashes and a changed-file audit.

Application is separately approved. The reviewed apply artifact verifies production hashes, creates byte-for-byte backups, copies only approved candidates, verifies candidate hashes, and invokes rollback on failure. Rollback restores and verifies every baseline.

## 10. Required planning package

Produce and reconcile every Markdown file under:

```text
root:ck3raven_data/wip/ck3_git_mvp/implementation_plan/
```

The package must include the source map, Stage 0 and Stage 1 plans, file manifest, binding command-policy design, test/exit matrix, candidate review workflow, QC synthesis, owner-decision summary, package README, and historical subagent reports.

Historical reports retain their original research context. Add a concise banner stating that Owner decisions supersede stale conclusions and that the current binding synthesis is 05/06 plus the governing documents. Remove stale prescriptive conclusions that could mislead implementation, and label reconciliation clearly.

## 11. Acceptance gate

The planning assignment is complete when:

- the two governing documents and WIP package use the approved terminology and authority boundaries;
- Stage 0 contains no constructed command entries or synthetic policy harness;
- Stage 1 contains only the five approved Diagnostic entries and concrete argument constraints;
- no unapproved command-specific predicate or preview mechanism is presented as policy;
- first-mutation affected-path evidence follows the actual-Git isolated-repository workflow;
- candidate generation remains gated by the Owner-approved pre-mutation manifest;
- production remains unchanged; and
- exact searches confirm no stale prohibited terminology remains.