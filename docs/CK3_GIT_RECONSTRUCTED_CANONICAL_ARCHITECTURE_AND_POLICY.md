# `ck3_git` — Canonical Architecture and Policy

**Status:** Reconstructed canonical owner baseline  
**Current state:** Legacy `ck3_git` and ordinary raw Git through `ck3_exec` were retired at commit `72b0607`. There is currently no ordinary MCP Git capability. Stage 0 / Stage 1 replacement work has not yet been implemented.

## 1. Purpose and authority

`ck3_git` is the future single controlled route for ordinary agent-initiated Git operations in `ck3raven` and CK3 Lens development.

This document is the normative product-policy and architecture source for that replacement.

Implementation plans and source inspection may determine how the policy fits the current source tree, but they do not change the policy.

Historical Wave 1A material, retired implementation code, preview-first packages, old request schemas, and earlier wrapper designs are not implementation authorities.

The replacement starts from the clean retired state created by `72b0607`. No compatibility bridge, fallback, legacy handler, or raw-Git route exists during the gap before Stage 1.

---

## 2. Core architectural invariants

1. **WA2 is the sole path-resolution and visibility authority.**

   WA2 owns canonical `root:` / `mod:` addressing, active mode/playset visibility, containment, host-path conversion, canonical output addresses, and opaque resolved references.

   `ck3_git` must not duplicate containment or visibility logic, independently inspect host paths for permission, or expand visibility.

   A terminal WA2 result is returned unchanged.

2. **`enforcement_v2.enforce()` is the sole authorization and denial authority.**

   Supported-operation policy, argument policy, applicable Conditions, existing-WRITE authorization, policy floor, escalation, and HAT eligibility are evaluated through the existing declarative enforcement architecture.

   Mechanical matching, preflight adapters, orchestration helpers, and subprocess helpers do not independently authorize.

3. **Git operation policy is declarative.**

   Actual Git command/mode rows carry their applicable:

   - operation profile;
   - policy floor mapping;
   - argument profile;
   - internal preflight;
   - WRITE selector/source;
   - Git-specific Conditions;
   - escalation rules;
   - HAT treatment;
   - execution/reply/logging treatment.

4. **Git reuses existing WRITE authority.**

   Git does not introduce a separate WRITE, DELETE, repository, or path-permission system.

   For Git-managed content, create, modify, delete, and rename are authorized using the existing WRITE path.

   Git does not query DELETE as a separate capability.

5. **Authorization is atomic for the submitted request.**

   If any required path, target, branch, remote, state, fact-sufficiency, or authorization check fails, the whole request stops.

   `ck3_git` never silently removes a denied path/ref or constructs a permitted subset.

6. **Preflight gathers facts; it does not authorize.**

   Git-native dry-run, porcelain, check, or other calculations may gather facts required by declarative policy.

   The resulting facts are supplied to enforcement.

   Missing facts required for hard policy block execution.

7. **HAT follows hard policy.**

   Human approval cannot supply missing WA2 visibility, WRITE authority, branch ownership, remote authorization, state validity, or missing hard-policy facts.

8. **Execution occurs only after authorization.**

   Final Git execution uses direct argv under the controlled process envelope after all required policy decisions permit execution and, where applicable, HAT has been validated.

9. **The old request schema is retired.**

   There is one request interpretation path.

   No compatibility adapter, dual request form, alias, fallback translation, or legacy-field interpreter survives.

10. **Ordinary raw Git remains closed.**

    Git through ordinary generic `ck3_exec`, shell aliases, wrappers, or equivalent ordinary agent routes is not an alternative Git interface.

    Explicitly human-approved arbitrary signed code remains a separate break-glass capability and is not ordinary `ck3_git` availability.

---

## 3. Git operation profiles versus policy floors

These are deliberately separate concepts.

### 3.1 Git operation profiles

An **operation profile** describes the material effect class of a Git operation under the controlled execution envelope.

There are exactly four:

#### `DIAGNOSTIC`

No intended persistent mutation of the selected repository, worktree, external filesystem, or remote state.

A Diagnostic operation may still:

- require WA2 resolution;
- require operation/argument admission;
- require remote read policy;
- have output/redaction controls.

#### `LOCAL_MUTATION`

May mutate the selected repository's index, objects, local refs, configuration, or worktree, but does not by default intend remote mutation or destructive/history-rewriting effects.

#### `REMOTE_OR_DESTRUCTIVE`

Mutates remote/shared state, or may delete, overwrite, discard, rewrite, force-move, or otherwise destructively affect recoverable state.

#### `EXCLUDED`

Outside the active supported `ck3_git` surface or prohibited by policy.

No execution path exists for the excluded form.

### 3.2 Policy enforcement floors

The operation profile maps to a default enforcement floor:

| Operation profile | Policy floor | Required treatment |
|---|---|---|
| `DIAGNOSTIC` | `UNGATED` | Resolve through WA2, match an activated declarative operation/argument profile, evaluate any applicable read/remote Conditions through the common policy path, then execute without WRITE authorization or HAT. |
| `LOCAL_MUTATION` | `POLICY_CHECKED` | Applicable declarative operation policy and all required existing-WRITE checks must pass through `enforce()`. No HAT is required merely because the operation is a local mutation. |
| `REMOTE_OR_DESTRUCTIVE` | `HAT_REQUIRED` | All applicable hard policy, Conditions, and existing-WRITE checks must pass through `enforce()` first. The exact request then additionally requires valid single-use HAT. |
| `EXCLUDED` | `EXCLUDED_UNSUPPORTED` | Return the canonical not-currently-supported result. Do not execute and do not offer HAT. |

`UNGATED` does **not** mean “outside enforcement.” It means that an admitted Diagnostic operation has no WRITE gate and no HAT gate.

### 3.3 Entry and effective treatment

A declarative row has an **entry operation profile**.

Preflight may:

- preserve it;
- expose facts that require escalation to a higher profile/floor;
- establish a complete no-op;
- establish hard denial.

It may never lower required treatment.

For example:

```text
fetch
entry profile = LOCAL_MUTATION
default floor = POLICY_CHECKED

preflight finds ordinary create/fast-forward remote-tracking update
    → remain POLICY_CHECKED

preflight finds forced local-ref movement or prune/delete
    → effective profile REMOTE_OR_DESTRUCTIVE
    → effective floor HAT_REQUIRED
```

---

## 4. Agent-facing request model

The agent-facing form uses ordinary Git vocabulary:

```json
{
  "target": "mod:Example Mod",
  "command": "push",
  "argv": ["origin", "HEAD:refs/heads/dev/example"],
  "rationale": "Publish completed development work."
}
```

The request does not accept:

- shell command strings;
- agent-selected host working directories;
- arbitrary environment overrides;
- `-C`;
- `-c`;
- `--git-dir`;
- `--work-tree`;
- alternate index/object stores;
- namespace/configuration redirection;
- arbitrary external program selection.

The trusted implementation supplies WA2-derived execution geography and controlled process parameters.

The retired typed schema is not accepted or translated.

---

## 5. Declarative operation rows

Every admitted command or mechanically distinct mode has a declarative row.

Applicable fields include:

```text
actual Git command/mode
entry operation profile
default policy floor
argument profile
internal preflight, if any
required preflight facts
WRITE selector and source, if any
Git-specific Conditions
profile/floor escalation rules
HAT rule
execution profile
canonical reply/logging profile
```

Nonapplicable fields need not be populated merely to satisfy a universal schema.

A Diagnostic row therefore does not need mutation preflight, WRITE, escalation, or HAT data.

The existing geographic key remains:

```text
(mode, root_key, subdirectory)
```

No second Git visibility/mode/geography matrix is created.

A structural reorganization or new separate policy matrix/module is not implicitly authorized merely for convenience; any material reorganization of the existing capability/operations architecture requires explicit owner approval.

---

## 6. Existing-WRITE selectors

There are three currently required WRITE-selection forms.

### `NONE`

No existing-WRITE check applies.

Used by Diagnostic operations.

Explicit path operands still pass through WA2 for resolution/visibility.

### `AFFECTED_PATHS`

The operation supplies the complete finite set of content paths that may be created, modified, deleted, renamed, staged, committed, or changed in the worktree.

Every required path is resolved through WA2 and submitted through the existing WRITE authorization path.

Rules:

- deletion uses WRITE;
- rename/move checks old and new content paths;
- all required paths must pass;
- no denied path is filtered out;
- an incomplete or unenumerable set blocks support for that request.

The source may be:

```text
REQUEST
PREFLIGHT
REQUEST_AND_PREFLIGHT
```

### `REPOSITORY_TARGET`

The existing-WRITE check applies to the original WA2-resolved content-associated repository target for repository-metadata/ref or remote-related operations that do not themselves require an unbounded worktree-content authorization.

Typical uses:

```text
fetch
branch:create
tag:create
push
```

This does not imply blanket WRITE authority over every descendant content path.

### Former `WHOLE_TARGET`

`WHOLE_TARGET` is no longer a separate selector.

Where an operation may change a broad working-tree scope, the later owner direction is to use the same actual-path route as `AFFECTED_PATHS`.

Therefore:

```text
branch:switch
pull:ff_only
```

must establish the complete affected content-path set and obtain existing WRITE authorization for those paths.

The exact Git-native mechanism used to calculate that complete set is an implementation-planning question, not separately fixed product policy.

If a complete required path set cannot be established, the operation remains unsupported.

---

## 7. Git-specific Conditions

Only genuinely Git-specific policy belongs in new Conditions.

### `branch_policy()`

Owns:

- protected branch restrictions;
- approved development naming;
- approved base;
- branch ownership/session association;
- one-development-work-item-at-a-time rule;
- permitted source/target transitions.

`stable` is human-controlled and unavailable as an agent push target.

New development branches originate from the exact approved `pre-release` tip and use the approved development naming convention.

### `remote_policy()`

Owns:

- approved configured remotes;
- permitted read contact;
- target namespace rules;
- protected/foreign remote refs;
- tag-publication restrictions.

### `repository_state_policy()`

Owns state compatibility for an admitted operation, including unsupported unmerged or sequencer state and any row-specific clean-state requirement.

### `preview_sufficient()`

Confirms that all facts required by that row for hard policy are complete and interpretable.

### `hat_proof_valid()`

Validates the exact, live, unexpired, unused single-request approval proof.

These Conditions do not replace WA2 or existing WRITE authorization.

---

## 8. Canonical Diagnostic command inventory

The following operations belong to operation profile `DIAGNOSTIC` and default policy floor `UNGATED`.

Their presence here does **not** mean all are activated in Stage 1.

| Git operation/mode | Supported diagnostic form / important controls |
|---|---|
| `status` | Repository status and branch summary; bounded output. |
| `diff` | Repository-contained worktree/staged/commit/range diff; no `--no-index`, external diff/textconv, or output-file writing. |
| `log` | Bounded history inspection; no external output/helper modes. |
| `shortlog` | Bounded history summary. |
| `show` | Bounded object/commit display; external diff/textconv prohibited. |
| `range-diff` | Compare commit series under controlled diff settings. |
| `describe` | Read-only object description. |
| `blame` | Repository-contained file blame; external `--contents` / external ignore-revision files excluded. |
| `rev-parse:object_or_ref` | Object/ref interpretation. |
| `rev-parse:path_or_discovery` | Repository/path discovery; host paths canonicalized or withheld. |
| `cat-file:type_size_content` | Bounded object type/size/content; filters/textconv excluded. |
| `ls-files` | Index/worktree listing; external exclude/pathspec files excluded. |
| `ls-tree` | Tree inspection. |
| `merge-base` | Ancestry calculation. |
| `count-objects` | Bounded object-count diagnostics. |
| `ls-remote` | Read refs from one approved configured remote; remote read policy applies. |
| `branch:list` | Branch listing. |
| `branch:show_current` | Current-branch inspection. |
| `tag:list` | Tag listing only. |
| `remote:read` | Remote list / verbose list / get-url; sensitive URLs sanitized. |
| `worktree:list` | Worktree listing; external host paths canonicalized or withheld. |
| `stash:list` | List stash entries. |
| `stash:show` | Inspect one stash entry. |
| `reflog:show` | Recovery-history inspection. |
| `config:read` | Local repository get/list forms; sensitive values redacted. |
| `symbolic-ref:read` | Read one symbolic ref. |
| `fsck:check` | Consistency check; `--lost-found` excluded; bounded output/time. |

All Diagnostic rows use:

- WA2;
- controlled direct-argv Git execution;
- canonical reply/logging;
- their declared argument profile;
- any applicable read-only `remote_policy()`.

They do **not** require WRITE or HAT.

---

## 9. Explicit preview/check operations

An agent-requested preview/check is classified as its **own actual operation**, based on its real effects.

A preview-looking option is not automatically Diagnostic.

| Explicit form | Operation profile | Default policy floor | Current classification |
|---|---|---|---|
| `add --dry-run` | `DIAGNOSTIC` | `UNGATED` | Tested preview form; no material mutation observed under controlled envelope. |
| `mv --dry-run` | `DIAGNOSTIC` | `UNGATED` | Preview only. |
| `rm --dry-run` | `DIAGNOSTIC` | `UNGATED` | Preview only; actual `rm` excluded. |
| `push --dry-run --porcelain` | `DIAGNOSTIC` | `UNGATED` | Approved-remote read policy and controlled helpers still apply. |
| `remote prune --dry-run` | `DIAGNOSTIC` | `UNGATED` | Remote preview/read policy applies. |
| `worktree prune --dry-run` | `DIAGNOSTIC` | `UNGATED` | Preview-only administration report. |
| `commit --dry-run` | `LOCAL_MUTATION` | `POLICY_CHECKED` | Bounded index-byte refresh was observed; do not call it Diagnostic. |
| `fetch --dry-run` | `LOCAL_MUTATION` | `POLICY_CHECKED` | Git-directory mutation/difference was observed; remains local mutation. |
| conflicting `merge-tree` calculation | `LOCAL_MUTATION` | `POLICY_CHECKED` | Object-store writes were observed. |
| Preview-looking forms that actually initiate unsupported mutation | `EXCLUDED` | `EXCLUDED_UNSUPPORTED` | No execution. |

These classifications do not activate the associated mutating family.

---

## 10. Canonical Local-Mutation inventory

These are canonical future MVP operations, but are outside diagnostic-only Stage 1.

### 10.1 `add`

**Operation profile:** `LOCAL_MUTATION`  
**Default policy floor:** `POLICY_CHECKED`  
**WRITE:** `AFFECTED_PATHS`  
**HAT:** none

Supported:

- explicit repository-contained paths/pathspecs;
- `-A`;
- `-u`;
- broad forms only where the complete affected set can be established.

Internal preflight:

```text
add dry-run where needed to obtain the complete affected set
```

Every affected path must resolve through WA2 and pass existing WRITE.

No subsetting.

Excluded forms include interactive/patch/edit modes, external pathspec files, repository redirection, and external helper execution.

### 10.2 `commit`

**Operation profile:** `LOCAL_MUTATION`  
**Default policy floor:** `POLICY_CHECKED`  
**WRITE:** `AFFECTED_PATHS`  
**HAT:** none

Canonical preflight:

```text
git commit --dry-run --porcelain -z <supported corresponding args>
```

The preflight establishes:

- complete prospective path/status set;
- empty versus nonempty result;
- unknown/unmerged status.

All prospective content paths must pass existing WRITE.

Applicable Conditions include:

- `branch_policy()`;
- `repository_state_policy()`;
- `preview_sufficient()`.

Unsupported:

- amend;
- fixup;
- squash;
- interactive/patch;
- editor/external message/template files;
- unsupported pathspec-input forms;
- empty-commit creation;
- other non-ordinary commit forms outside the approved profile.

### 10.3 `fetch`

**Entry operation profile:** `LOCAL_MUTATION`  
**Default policy floor:** `POLICY_CHECKED`  
**WRITE:** `REPOSITORY_TARGET`  
**HAT:** only after destructive escalation

Preflight:

```text
git fetch --dry-run --porcelain <approved remote and supported args>
```

Required facts include the complete target-ref/effect record set.

| Effect | Effective treatment |
|---|---|
| Complete no-op | Return informational success; skip actual fetch. |
| New approved remote-tracking ref | Remain `LOCAL_MUTATION` / `POLICY_CHECKED`. |
| Fast-forward approved remote-tracking ref | Remain `LOCAL_MUTATION` / `POLICY_CHECKED`. |
| Forced local-ref movement | Escalate to `REMOTE_OR_DESTRUCTIVE` / `HAT_REQUIRED`. |
| Prune/delete local ref | Escalate to `REMOTE_OR_DESTRUCTIVE` / `HAT_REQUIRED`. |
| Disallowed tag mutation | Hard deny / unsupported under current tag policy. |
| Target outside approved namespace | Hard deny. |
| Unknown/incomplete facts | Unsupported for that request; no execution. |

### 10.4 `branch:create`

**Operation profile:** `LOCAL_MUTATION`  
**Default policy floor:** `POLICY_CHECKED`  
**WRITE:** `REPOSITORY_TARGET`  
**Internal preflight:** none  
**HAT:** none

Creates one approved development branch from the exact approved `pre-release` tip.

`branch_policy()` governs naming, base, ownership, protected targets, and prior-work closure.

Creation does not implicitly switch to the branch.

### 10.5 `branch:switch`

**Operation profile:** `LOCAL_MUTATION`  
**Default policy floor:** `POLICY_CHECKED`  
**WRITE:** `AFFECTED_PATHS`  
**HAT:** none

Supported only when the complete set of content paths that may be changed by the switch can be established before final authorization.

Every affected path must pass existing WRITE.

The precise Git-native calculation used to derive the complete set is an implementation-planning decision.

If the complete set cannot be established, branch switching remains unsupported.

Applicable Conditions:

- `branch_policy()`;
- `repository_state_policy()`.

Excluded:

- force/discard;
- detach;
- orphan;
- path checkout;
- implicit branch creation;
- create-and-switch forms unless separately admitted.

### 10.6 `tag:create`

**Operation profile:** `LOCAL_MUTATION`  
**Default policy floor:** `POLICY_CHECKED`  
**WRITE:** `REPOSITORY_TARGET` on the content-associated repository  
**Internal preflight:** none  
**HAT:** none merely for local creation

Local creation is supported in policy as a distinct row.

The authorization subject is the content-associated repository named by the request, not merely the separately stored Git metadata directory.

Allowed local creation may include the specifically approved lightweight/annotated forms supported by the final argument profile.

Force-moving an existing tag, deletion, and publication remain unsupported.

Activation is later scope; this policy classification does not place `tag:create` into diagnostic-only Stage 1.

---

## 11. Canonical Remote-or-Destructive inventory

### 11.1 `push`

**Operation profile:** `REMOTE_OR_DESTRUCTIVE`  
**Policy floor:** `HAT_REQUIRED`  
**WRITE:** `REPOSITORY_TARGET`  
**Preflight:** `git push --dry-run --porcelain`  
**HAT:** required unless complete no-op causes actual push to be skipped

The preflight must expose the complete concrete source/destination ref set and current per-ref effect.

Hard policy denies:

- `stable`;
- foreign/unowned targets;
- tag publication under the current MVP;
- force authority;
- unknown/incomplete target sets.

Approved create/fast-forward development-ref updates require HAT.

Approved deletion of an owned remote development ref requires HAT with prominent deletion presentation.

### 11.2 `pull:ff_only`

**Operation profile:** `REMOTE_OR_DESTRUCTIVE`  
**Policy floor:** `HAT_REQUIRED`  
**WRITE:** `AFFECTED_PATHS`  
**HAT:** always when the operation actually executes

Only the narrow controlled form is admitted:

```text
git pull --ff-only --no-recurse-submodules <approved-remote> <approved-branch>
```

Requirements:

- approved remote and branch;
- branch/remote/repository-state policy;
- fetch-side factual preflight;
- complete enumeration of content paths that may be affected by the integration;
- existing WRITE authorization for every affected path;
- HAT for the retained exact command.

The exact calculation used to establish the complete affected-path set is an implementation-planning matter.

If that set cannot be established completely, the operation remains unsupported.

A fetch-side no-op does not waive HAT for a pull that would still perform integration.

---

## 12. Excluded / unsupported inventory

The following remain operation profile `EXCLUDED` with policy floor `EXCLUDED_UNSUPPORTED` unless a later owner-approved policy explicitly admits a narrower row.

- ordinary raw Git through `ck3_exec`, shell strings, aliases, interpreter wrappers, or other bypasses;
- agent-supplied repository/configuration redirection (`-C`, `-c`, `--git-dir`, `--work-tree`, alternate index/object/config/namespace);
- direct Git-metadata manipulation through ordinary file routes as a substitute for `ck3_git`;
- general merge, including initial-MVP `merge --ff-only`;
- rebase;
- cherry-pick and revert workflows;
- reset and arbitrary restore;
- actual Git `rm` / `mv`;
- branch delete, force-delete, rename, copy and other lifecycle operations beyond admitted create/switch;
- tag replacement/force-move, deletion and publication;
- stash mutation (`push`, `pop`, `apply`, `drop`, `clear`);
- worktree mutation (`add`, `remove`, `move`, `repair`, lock/unlock, prune execution);
- remote reconfiguration;
- configuration writes;
- destructive cleanup (`clean`, object prune, reflog expire/delete/drop/write);
- history rewriting (`filter-branch`, filter-repo, replace);
- repository administration (`init`, `clone`, `gc`, maintenance);
- submodule mutation, recursive fetch/push and sparse-checkout mutation;
- stateful specialist workflows such as bisect, `am`, general `apply`;
- format-patch, archive, bundle and other currently unsupported export workflows;
- notes mutation;
- signature-verification commands until separately activated, even where Diagnostic in principle;
- write-capable plumbing such as `update-ref`, `read-tree`, `checkout-index`, `commit-tree`, `hash-object -w`, `fast-import`;
- force/mirror push forms, leading-plus refspecs, and force-with-lease under the MVP;
- protected/foreign/tag push targets;
- pull modes other than the admitted narrow ff-only form;
- unsupported commit modes such as amend/fixup/squash and other non-ordinary variants outside the approved profile;
- every other operation/mode lacking an activated matching row.

Excluded means:

- no requested execution;
- no HAT route;
- canonical not-currently-supported outcome.

---

## 13. Evaluation sequence

For one `ck3_git` request:

```text
request
    ↓
schema validation
    ↓
WA2 repository resolution
    ↓
actual operation + argument-profile match
    ↓
entry operation profile
    ↓
default policy floor
    ↓
row-declared preflight, if any
    ↓
complete factual context
    ↓
preflight-derived profile/floor escalation, if any
    ↓
WA2 resolution of required WRITE subjects
    ↓
enforce() operation policy + existing-WRITE checks
    ↓
hard-policy outcome
    ↓
complete no-op treatment, if applicable
    ↓
HAT if effective floor = HAT_REQUIRED
    ↓
direct-argv execution of retained request
    ↓
canonical reply + structured audit
```

No Condition launches Git.

No handler invents additional policy after obtaining a generic allow.

No HAT repairs a failed hard-policy or WRITE decision.

---

## 14. Controlled Git execution envelope

Internal preflight and final execution use the same controlled process principles:

- trusted Git executable;
- direct argv, no shell/PowerShell command wrapper;
- WA2-derived execution geography;
- controlled locale;
- pager/color disabled;
- interactive prompts/askpass disabled;
- editor-driven operation excluded unless expressly supported;
- executable hooks/helpers/diff/textconv/filter/merge drivers controlled or disabled as required;
- custom remote programs prohibited;
- uncontrolled submodule recursion prohibited;
- credentials protected and never logged;
- automatic maintenance/auto-gc controlled;
- bounded output;
- timeout;
- canonicalized/withheld host paths;
- sanitized replies/logging.

Execution controls are mechanism, not authorization.

---

## 15. Repository errors and reply semantics

There is no Git-specific repository-existence permission gate.

There is no local `.git` precheck.

There is no separate exact-root repository assertion introduced as a substitute for that precheck.

After WA2 resolution and authorization, Git itself may report:

- malformed Git syntax;
- non-repository target;
- missing ref/branch/remote;
- incompatible repository state;
- other ordinary Git failures.

| Cause | Treatment |
|---|---|
| WA2 cannot resolve/see the address/path | Existing WA2 terminal result unchanged. |
| Malformed tool input / ordinary input invalidity before execution | Existing Invalid (`I`) treatment. |
| Git process starts and exits, regardless of Git exit code | MCP-tool Success (`S`); Git `exit_code`, bounded `stdout`, and bounded `stderr` are reported in `data`. |
| Actual operation policy or WRITE/capability decision fails | Enforcement Denied (`D`). |
| Valid request outside activated supported surface | Canonical unsupported outcome. |
| Genuine process/infrastructure failure before Git starts | Existing appropriate Error treatment. |
| Timeout after Git starts | Existing framework Error treatment with bounded partial output in `data`. |
| Successful/no-op execution | Existing Success treatment as applicable. |

A required pre-execution failure stops the whole request while preserving the originating canonical semantic class. Once the controlled wrapper starts Git, the MCP reply type is `S`; Git's own success or failure is data, not the MCP tool's reply type.

---

## 16. HAT

HAT is:

- time bounded;
- non-forgeable;
- single use;
- bound to one immutable pending request;
- issued/displayed by the trusted extension;
- unavailable as an override for hard-policy failure.

The pending request binds at least:

- exact retained argv;
- canonical repository context;
- rationale;
- operation profile;
- policy floor;
- interpreted path/ref effects;
- relevant preflight limitations;
- expiry and nonce.

Retry or follow-on commands require new authorization and, if applicable, a new HAT.

---

## 17. Separate mod Git metadata

For mods under the user mod area, Git metadata remains stored separately from the mod folder.

The implementation must preserve this arrangement.

It must not place:

- a `.git` directory;
- a `.git` pointer file;

inside the mod folder merely to simplify repository discovery.

The trusted implementation supplies the verified metadata/worktree execution parameters after normal WA2 resolution using existing shared interfaces.

The agent request does not gain arbitrary host-path or repository-redirection authority.

The active operations note owns the verified current location/pattern and specific mod-to-metadata associations.

---

## 18. Stage 0 / Stage 1 boundary

Legacy retirement and documentation disposition are complete prerequisites, not future Stage 0 work.

### Stage 0

Stage 0 plans and prepares the smallest shared enabling infrastructure needed for the replacement, including as source inspection requires:

- declarative Git operation-profile data integrated with the existing operations/capability architecture;
- the explicit profile → policy-floor mapping;
- shared check-only authorization using the existing rule/Condition evaluator without executor dispatch;
- existing-WRITE subject handling;
- the minimum Git-aware mechanical coordinator/orchestration required by the architecture;
- canonical reply preparation;
- canonical structured logging/test foundations;
- controlled-process design needed by Stage 1;
- exact unsupported behavior.

Stage 0 activates no Git operation and performs no mutating Git workflow.

The canonical policy does **not** pre-decide that Stage 0 must touch exactly two production files. The implementation-planning agent must inspect current source and propose the minimum evidence-backed file set consistent with these requirements.

Stage 0 must not:

- create a parallel enforcement authority;
- introduce a compatibility bridge;
- restore raw Git;
- activate mutation/HAT workflows;
- undertake linter/TASL/CI improvement work.

### Stage 1

Stage 1 delivers a cohesive working **Diagnostic-only** `ck3_git`.

The planning agent must assess at least:

```text
status
diff
log
show
branch --list
```

and recommend one exact first subset.

Every nonactivated Git operation returns the canonical not-currently-supported result.

Stage 1:

- uses WA2;
- uses the declarative operation/profile model;
- routes applicable policy through `enforce()`;
- uses controlled direct-argv execution;
- uses canonical replies/logging;
- preserves raw-Git closure.

Stage 1 does not activate:

- Local-Mutation operations;
- Remote-or-Destructive operations;
- mutation preflight machinery beyond what a selected Diagnostic itself intrinsically requires;
- HAT;
- mutating branch/remote workflows.

---

## 19. Canonical command summary

| Operation | Entry operation profile | Default policy floor | WRITE | Internal fact gathering | HAT |
|---|---|---|---|---|---|
| Diagnostic matrix in §8 | `DIAGNOSTIC` | `UNGATED` | `NONE` | None unless intrinsic read/remote facts required | No |
| `add` | `LOCAL_MUTATION` | `POLICY_CHECKED` | `AFFECTED_PATHS` | Dry-run where needed for complete affected set | No |
| `commit` | `LOCAL_MUTATION` | `POLICY_CHECKED` | `AFFECTED_PATHS` | `commit --dry-run --porcelain -z` | No |
| `fetch` | `LOCAL_MUTATION` | `POLICY_CHECKED` | `REPOSITORY_TARGET` | `fetch --dry-run --porcelain` | Only if destructive escalation |
| `branch:create` | `LOCAL_MUTATION` | `POLICY_CHECKED` | `REPOSITORY_TARGET` | None | No |
| `branch:switch` | `LOCAL_MUTATION` | `POLICY_CHECKED` | `AFFECTED_PATHS` | Complete affected-path set required; exact mechanism implementation-defined | No |
| `tag:create` | `LOCAL_MUTATION` | `POLICY_CHECKED` | `REPOSITORY_TARGET` | None | No |
| `push` | `REMOTE_OR_DESTRUCTIVE` | `HAT_REQUIRED` | `REPOSITORY_TARGET` | `push --dry-run --porcelain` | Yes unless complete no-op skips push |
| `pull:ff_only` | `REMOTE_OR_DESTRUCTIVE` | `HAT_REQUIRED` | `AFFECTED_PATHS` | Fetch-side facts + complete affected-path set; exact path derivation implementation-defined | Yes |
| All excluded rows | `EXCLUDED` | `EXCLUDED_UNSUPPORTED` | none | none | No |

This table is the controlling compact statement of the intended command treatment.

---

## 20. Conformance rule

An implementation conforms only if:

- operation profile and enforcement floor remain separate concepts;
- every activated operation has one declarative row;
- profile-to-floor routing is explicit;
- WA2 remains sole path/visibility authority;
- `enforce()` remains sole authorization/denial authority;
- existing WRITE is reused rather than recreated;
- required affected-path sets are complete;
- no request is silently subsetted;
- preflight gathers facts rather than authorizing;
- HAT follows hard policy and never overrides it;
- compatibility/fallback paths remain absent;
- raw Git remains closed;
- unsupported operations fail closed;
- canonical replies/logging are used end to end.
