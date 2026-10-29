# MCP stabilization and Git baseline work, 2026-10-24

## Installed-window and late-response evidence, 2026-10-26/27

After both ordinary windows reloaded, new instances `1afs-2e147a` (PID `45356`,
dev) and `zm4g-f7ffe5` (PID `75488`, Lens) completed startup handshakes.
This window passed ping, live mode, database, daemon and playset queries;
the owner confirmed AgentView dev mode. The other window's audit includes
successful signing and contract execution. Old PIDs `54876` and `43976` were
absent at inspection.

Following an explicit readiness Y, one real signing request for the existing
timeout-inspection script exercised the late-response path. The script was
not executed. At `2026-10-27T00:22:37Z`, the client cancelled request `19`.
At `00:23:09Z`, the original handler finished with `CT-SIGN-I-004`; the server
logged `duplicate_response_discarded` rather than terminating. Subsequent ping
and live-mode queries succeeded on the unchanged PID `45356`. This establishes
late timeout-response survival for this request, not late approved-signature
delivery. No retry or synthetic test was performed.

Total signing-handler time was about 212 seconds. The 180-second polling
deadline must not be described as a verified whole-operation bound.
The owner's mode-instructions truncation report is also recorded: the handler
returns complete text, but the precise downstream display/context truncation
boundary remains unresolved. Neither issue is concealed as a passed check.

## Owner disposition and deployment, 2026-10-26

The owner reports that the retired agent-session picker entry disappeared
without intervention and has deferred further investigation. The observed
stale display did not establish continuing agent registration or associated
tool-call failures; do not describe it as a proven agent-facing zombie.
Local definition removal, process/publication cleanup and replacement launch
were observed separately.

At the owner's request, the extension passed TypeScript checking, was rebuilt
in production mode, packaged as version `0.1.0`, and reinstalled through the
VS Code CLI. The built, VSIX-contained and installed extension bundles have
identical SHA-256 hashes. Nine relevant Python modules passed syntax-only
compilation. No synthetic tests, signing submissions or process termination
were performed during this deployment.

The owner will reload the window. Check the new endpoint, fresh legitimate
initialization and AgentView agreement after reload before proceeding with
the Git/new-clone transition. Installation does not prove the current window
has loaded the new bundle. Actual live late-signing-response survival remains
unverified and must not be represented as a passed check.

## Owner correction, 2026-10-26

The ten stabilization test files, their extension mock/runner, and the WIP
validation script have been deleted at the owner's direction, not archived.
Historical passing results below are not acceptance evidence. No synthetic
tests may be created or run without prior owner approval.

The Extension Host snapshot's read-on-activation classification is not MCP
shutdown detection and is not a demonstrated owner requirement. Its ordinary
and structured error calls duplicate the same classification in the output
channel; the separate event journal repeats that classification on disk.
Do not repair its cross-activation lookup or use the snapshot as lifecycle
authority. The necessary lifecycle distinction comes from whether common
cleanup was already underway when the current MCP connection was lost, with
actual PID verification separate from socket loss.

### Incident review procedure, owner clarification 2026-10-26

Unexpected current-MCP connection loss while window shutdown/common cleanup is
not underway is the recovery trigger. Do not require a snapshot diagnosis or
an established crash cause before common cleanup/restart. Retained-PID exit
verification serves teardown correctness, not a second incident classifier.

Review the whole incident timeline, not only the extension/MCP logs:

1. Identify the affected window, instance IDs, announced PIDs, incident UTC time,
   and installed versus Development Host build. Include the last relevant tool
   submission and follow the sequence through cleanup and the actual next launch.
2. Read extension and Python structured logs, including rotated files covering
   that interval. Distinguish definition publication, process announcement,
   connection loss, verified exit and completed cleanup.
3. Read that VS Code window's MCP server output, Extension Host log and Copilot
   Chat log. Also inspect the corresponding Copilot runtime/native-connector log
   for actual launches, client timeouts, transport closure and process outcomes.
   Record empty/unavailable sources explicitly.
4. Include per-instance tool-call traces from `initialize_window_trace`, the
   policy audit trace selected by `_get_trace_path`, `mcp.tool` completion records
   and available protocol request/response metadata. Correlate by UTC timestamp,
   instance/PID, tool, trace ID and protocol request ID where present; these IDs
   are not interchangeable and are not all recorded by every source.
5. Separate client timeout, backend completion, response transmission and process
   termination. Backend completion is not proof of client receipt. Per-instance
   tool-call audit entries are written on completion, so their absence does not
   establish that no request was active when the process ended.
6. Report the observed sequence, correlations, proven cause (if any), missing
   evidence and recovery outcome. Do not generalize one incident's cause to
   another or use logs to restore runtime identity/authorization.

Current extension logging is duplicated: `Logger` writes only to an output
channel; `StructuredLogger` writes JSONL and mirrors to that same channel.
One event should be emitted once and routed to file/display, rather than callers
using both implementations for the same event. Keep Python and extension log
files separate and preserve the dedicated approvals output.

### Authorized source corrections, 2026-10-26

- Tool auditing now writes `tool_start` before the handler and retains the
  existing reply-based `tool_call` completion. Both include timestamp, PID,
  instance/window ID and the same trace ID. Start records contain parameter
  names and, for contracts, the command, not argument payloads. Completion
  parameters redact token, signature, password, secret and key fields.
- Removed snapshot reading, previous-host classification and the parallel host
  event journal. The monitor only writes diagnostic snapshots. Nothing reads
  those snapshots to control activation, cleanup or restart.
- Common cleanup unregisters the old VS Code provider registration instead of
  only firing a changed-definitions event. Each start registers a fresh,
  generation-bound provider. The supported start-time resolver is enabled and
  resolves only that provider's active definition; the definition version also
  changes with the generation. No old identity is relabeled and no private
  retirement acknowledgement or extra restart path was added. Deactivation
  no longer has a competing provider-disposal sequence.
- Cleanup attempts process teardown even when withdrawal fails. Display
  failures are reported separately; they cannot skip teardown. Lease release
  and publication removal are both attempted after confirmed process teardown.
  Essential failures remain explicit and prevent replacement start. Completion
  notification cannot reverse the recorded teardown outcome.
- Lifecycle socket-close tracking is installed before notifying UI subscribers.
  Activation publication failure enters the same deactivation/common cleanup.
  Extension logging failures fall back explicitly to the Extension Host console
  instead of throwing through lifecycle operations.
- Initial mode publication uses the shared filename helper. Python publication
  uses atomic replacement; AgentView reconciles at startup, connection,
  refresh, its existing monitoring interval and before timeout display rollback.
  It validates the publication identity/mode and no longer claims to reset
  backend authorization on an unconfirmed display.
- Removed the uncalled age-based mode cleanup helper, including its undefined
  `MODE_DIR` reference, rather than retaining a second cleanup mechanism.
- Relevant logging/rotation/viewer, tool trace/reply spill, signing and QBuilder
  endpoint/database/supervisor paths now use the configured data-root constant.
  QBuilder subprocess launch explicitly receives that root.
- A supplied HAT cannot initialize when the signing secret is unavailable.
- Owner selected 180 seconds for the existing synchronous signing wait. Both
  returned mode instruction files and the contract tool description require a
  VS Code workstation-readiness Y/N question before every `sign_script`, an
  explicit Y before submission, and no automatic timeout retry.
- The worker wrapper now explicitly imports `anyio.to_thread.run_sync` rather
  than relying on a top-level attribute not exported by the installed AnyIO.

Validation is static only: extension TypeScript checking and compilation, Python
syntax checks and focused Pylance diagnostics. No synthetic tests, live signing,
process termination, package installation, reload or Git history operations were performed.
Pylance still reports an existing `ContractV1.get` attribute error in
`ReplyBuilder._meta`; this untouched issue is not a clean-Python-diagnostics claim.
Actual server-list retirement, replacement launch, normal close and late-response
survival remain subject to the agreed live acceptance, not established by builds.

### Development Host baseline received, 2026-10-26 02:48 +08

The continuous Development Host report records instance `mvo2-fb0b9c`,
PID `50952`, successful ping, startup/handshake identity agreement and operator-
confirmed AgentView/live-mode agreement. Initial observation began after
legitimate `ck3lens` initialization, so that baseline did not perform a
fake-HAT attempt on an uninitialized process.

At owner direction the agent subsequently attempted a mode change with fake
authorization: rejected at `2026-10-25T18:45:59.986Z` with
`MCP-SYS-I-001`, and the mode remained `ck3lens`. A fresh legitimate HAT
then changed the same instance/PID to `ck3raven-dev`, completed at
`2026-10-25T18:46:41.855Z`, with operator-confirmed AgentView agreement.
This establishes the reported mode-change authorization behavior, not an
uninitialized-process exercise or lifecycle recovery.

After this baseline, isolated the post-publication AgentView refresh failure
from startup failure handling: it reports an `ext.ui` error but cannot escape
publication and enter deactivation. Recompile only; do not disturb the running
Development Host. Its loaded extension does not acquire this correction until
a fresh activation. No synthetic tests are approved or required by this note.

## Requested order

1. Finish agreed lifecycle consolidation and safe late-response handling. Keep synchronous signing available. Validate source and actual installed behavior before declaring success.
2. Publish a clean representation of the local repository to GitHub, preserve recovery options, then establish a development branch. Review the exact remote/default-branch replacement before destructive ref updates.
3. Review a comprehensive async-signing touchpoint and rollout plan, then implement only the approved approach.

No async implementation belongs in phase 1.

## Current baseline

- MCP initialization succeeded on `31vy-c8e4dc` after reboot. Historical signatures, contracts, and process IDs must not be reused.
- Local checkout: `recovery/2026-08-22`, HEAD `9e14415`, existing origin `ck3user75233/ck3raven`.
- Local has extensive modified, deleted and untracked source from earlier work; preserve all of it. Current remote-tracking refs are not proof of current GitHub state.
- Existing lifecycle/approval-worker changes are saved but the last batch was not packaged/installed. Prior focused results do not validate forthcoming changes.
- The configuration-inspection WIP script was syntax-validated but not executed. Its previous signing call timed out to the agent despite later user approval/backend success.

## Clarification from user review today

No requirement exists for a host retirement receipt or a private API proving the host will never launch an old definition. Remove that proposed implementation gate.

Validate local process/file teardown and then live intended-instance/tool exposure. Tool registry visibility is not OS-process liveness evidence; retain the actual process check separately.

The earlier [implementation plan](./MCP_LIFECYCLE_IMPLEMENTATION_PLAN.md) remains the lifecycle mapping/reference, except where this document records later user decisions.

## Phase 1 scope

- One start procedure, common cleanup for normal/unexpected shutdown.
- Remove same-identity PID supersession; no user requirement supports it.
- Retain process evidence after socket deletion; do not infer verified exit from missing socket state.
- Remove independent lifecycle controllers and obsolete rotation/rejection bookkeeping, not merely rename logs.
- Preserve per-window isolation, process-local initialization, approvals output separation, and existing execution enforcement.
- Cleanup process/files before a fresh generation; distinguish publication from process launch and handshake from protocol responsiveness.
- Keep unrelated errors visible.

### Late-response investigation approved

User approved the reproduction/producer-fix-first sequence below. Any compatibility
containment still requires separate review.

Until asynchronous signing is implemented, before **every** `sign_script` call use
the VS Code question/answer facility to ask:
"Are you at the workstation and can approve a sign_script request? Y/N"
Wait for Y before submitting. Do not automatically retry a timed-out signing call.

The captured crash is a duplicate-completion assertion in the installed SDK:
`RequestResponder.respond()` asserts before its cancellation guard; the final SDK response call is outside the handler catch. The unhandled task failure unwinds the server session.

Options to investigate in order:

1. Reproduce the failure and trace whether our handler returns despite request expiry/cancellation; fix our producer/integration if possible.
2. Check for a compatible SDK release that fixes completion handling and verify with the same regression.
3. Only if necessary, propose a narrow repository-owned compatibility boundary for this exact completed-request failure, with warning and regression coverage.

Do not silently choose option 3, install dependency changes without a manifest change, edit site-packages as a product fix, swallow arbitrary assertions, or use a server-wide exception catcher as recovery.

Tests must cover late completion followed by another request and continued protocol responsiveness, not merely successful approval or process existence.

### Evidence correction and protocol capture

The synthetic test that explicitly sent `notifications/cancelled` did not establish
that the real client sent that notification during the reported incident. Its
passing result is not acceptance evidence for the actual timeout/late-approval
workflow. The test and the worker checkpoint justified only by that test have been
removed. The nonblocking worker integration remains.

No cancellation feature or cancellation regression is a phase 1 requirement.
The actual cause of duplicate completion remains unresolved.

The stdio diagnostics now record `message_received` before dispatch and
`message_sent` after stdout flush. Records contain message type, method, request
ID, referenced request ID where supplied, tool name for tool calls, and error
code for protocol errors. Arguments, results, signatures, and arbitrary error
text are not recorded. Existing server logging adds timestamp, instance and PID.
This captures actual traffic without choosing a timeout mechanism in advance.
An output record proves a local flush, not client receipt.

These source changes require a new MCP process before live capture is available.
Do not deliberately request signing or provoke a timeout without the required
availability question. Actual incident capture and a verified late-reply fix
remain outstanding.

Focused validation: `python -m pytest tests\test_stdio_transport.py
tests\test_mcp_worker_tool.py -q` passed all 5 tests, with one existing resolver
deprecation warning. These checks verify metadata logging, payload exclusion,
closure-race handling and nonblocking worker behavior, not the original crash fix.

## Phase 2 safety

Investigate earlier Git recovery history and current refs before writing Git history.
Do not treat dirty local deletions as accidental or automatically restore them.

Recommended review point: publish the local snapshot on a new branch first, verify tree parity and clone usability, preserve existing remote refs, then agree how main/stable/default are replaced and name the new dev branch. No force push or branch deletion is approved by this document.

## Phase 3 scope and rollout review

Review all current signing entry points, IPC calls, signature generation, CodeLens, AgentView, chat prefill, contract-open verification, execution enforcement, tests, docs, packaging and loaded-runtime effects before proposing edits.

User's candidate rollout options:

- WIP candidate files plus production backups, with reversible promotion/build/package/install and rollback.
- A parallel V2 signing route while original signing remains operational; remove/rename only after acceptance.
- Other approaches may be presented, not silently adopted.

Preserve the narrow agreed UX:

- Prompt return from submission, one outstanding request, user reviews when available.
- Dedicated signing chat-prefill action; never reuse initialization/HAT workflow logic.
- Existing signature/content/Sigil validation remains authoritative.
- No invented persistence, explicit invalidation system, general queue, or cancellation feature.
- Scope of one-request limit and exact V2 route/rollout are review decisions, not assumptions.

## Completion evidence

Record implementation, retired code, test commands/results, packaging/install outcome, live host checks and remaining limitations separately. Maintain session todos and restart notes. Do not claim completion based on unit mocks alone or quietly drop unresolved regressions.

### Lifecycle implementation completed in source

- [McpLifecycle](../tools/ck3lens-explorer/src/mcp/mcpLifecycle.ts) owns generation
  identity/key and serializes the single start/common cleanup procedures. Close
  prevents replacement publication; failed cleanup prevents start.
- [Extension wiring](../tools/ck3lens-explorer/src/extension.ts) uses the
  coordinator for initial publication, connection loss and deactivation.
  Duplicate Sigil wiring and QBuilder MCP release outside cleanup were removed.
  Structured logging follows the current generation.
- [Diagnostics](../tools/ck3lens-explorer/src/ipc/diagnosticsServer.ts) retains the
  announced PID after socket removal, verifies that PID before deleting
  publications, and refuses same-generation process takeover. Startup coalesces
  and rejects listener/binding-publication failures; availability requires an
  actual listening socket.
- [Provider](../tools/ck3lens-explorer/src/mcp/mcpServerProvider.ts) projects
  coordinator identity/key. Own identity rotation and independent recovery have
  been retired. Definition errors are surfaced rather than represented as an
  empty successful list. Withdrawal still intentionally returns an empty list.
- QBuilder MCP lease release remains best effort if its daemon is unavailable;
  that condition is now a warning, not a claim of confirmed release.

Validation after these changes:

- Unchanged, previously signed `validate_mcp_lifecycle_20261024.py`: TypeScript
  passed, 32 extension tests passed, 14 Python lifecycle tests passed.
- `python -m pytest tests\test_stdio_transport.py tests\test_mcp_worker_tool.py
  tests\test_ipc_client_lifecycle.py -q`: 17 passed, one existing resolver warning.
- Initial `npm run package` exceeded the execution tool's 120-second limit during
  packaging; the command reported terminating its process tree. No installation
  or window reload was performed. A production bundle was generated, but that
  alone is not a validated package.

### Approved duplicate-response containment

User approved the narrow request-boundary guard after separate review. The
adapter in [stdio_transport.py](../tools/ck3lens_mcp/ck3lens/stdio_transport.py)
wraps only this server instance's internal `_handle_request` method. It handles
only the exact completed-response assertion whose innermost traceback originates
in `RequestResponder.respond()` on that same responder, with the responder no
longer in flight. It logs a warning with request ID/method and returns without
another response. Other assertions and transport errors propagate. SDK classes
and installed SDK files are not changed.

`_run_mcp_stdio` installs the adapter and adds instance/PID to warning logs.
This is an intentionally version-coupled compatibility boundary, not an
identification of the historical timeout mechanism.

Updated focused Python result: 24 passed, one existing resolver warning.
Tests establish the unguarded installed SDK assertion, guarded warning behavior,
continued real SDK session processing of ping and another request, isolation
from another server instance, and propagation of unrelated assertions (including
identical text from a different origin) and output transport failures. No
cancellation notifications are sent by these tests. Pylance reports no errors
in the adapter or transport tests; server entry-point syntax validation passes.

Packaging succeeded with `npm run package -- --no-dependencies`. The extension
bundles dependencies into its output (only `vscode` is external); the flag avoids
dependency enumeration. A package inspection exposed stale generated test
bundles, so `out/test/**` and the test runner scripts are now explicitly excluded
and the final package must be rebuilt with these exclusions.

Part 1 remains open until the final package is verified/installed and the new
process plus actual host lifecycle behavior are checked. No synthetic
cancellation test or removed worker checkpoint counts as evidence that the
historical incident's full sequence was reproduced.

### Installation completed; reload/live acceptance pending

The final VSIX was rebuilt successfully: 14 files, no generated test bundles or
test runners. The user answered Y to the availability question and approved
`install_mcp_lifecycle_20261024.py`. That script verified the package's lifecycle
markers and bundle hash, created a matching rollback VSIX from the installed
production extension, installed the candidate through the official VS Code CLI,
and verified the installed bundle matches the candidate.

Deployment directory:
`C:\Users\nateb\.ck3raven\wip\deployments\ck3lens-explorer\lifecycle-20261024T105540554320Z`

- `deployment.json`: phase `installed_reload_required`, CLI exit code 0.
- `candidate.vsix`: installed candidate.
- `rollback.vsix`: previous installed production extension; restore with the
  official CLI command recorded in `deployment.json`, then reload.
- Installed/candidate bundle SHA-256:
  `5f0f94e8e345293ad17a6eb5a6a9791f6d5d11c67adb126d9be5f3ec14e7b4b0`.

No window was reloaded and no MCP process was intentionally stopped. Existing
windows may still be running their previously loaded extension/backend code.

Next acceptance sequence, after user-directed reload:

1. Discover and ping the fresh registered instance; initialize with fresh user
   authorization. Do not reuse the old identity, signing key or approval tokens.
2. Confirm current-generation handshake, mode display, tool responsiveness and
   actual `message_received`/`message_sent` records with protocol request IDs.
3. Check normal reload cleanup: old announced PID gone, old generation's scoped
   port/mode publications removed, fresh identity/key published, and no duplicate
   entry in the relevant host/session MCP inventory.
4. With explicit coordination, exercise one unexpected owner-connection loss in
   an isolated test window and confirm common cleanup precedes replacement,
   without disturbing another window.
5. If the original late-approval incident recurs, inspect actual protocol records
   and `duplicate_response_discarded` warnings. Do not invent a cancellation
   mechanism or claim a complete real-host reproduction from unit tests.

Only after these live checks is part 1 complete. Git baseline work and async
signing remain untouched and in their requested order.

### Dev Host finding: mode display remains at VS Code default

User reported successful mode initialization but AgentView still displayed
"initialized by VS Code". The Dev Host generation `pi72-e6eb2b` completed its
lifecycle handshake with PID 14392. Its scoped mode file records
`ck3raven-dev`, that same instance/PID and a process-generation identifier,
updated at 19:12:24 +08:00. Protocol records show the initialization call and
response. This supports a projection defect, not independently verified current
in-memory mode; the Dev Host agent should query `ck3_get_detected_mode` to settle
the latter before reload.

The watcher compared `uri.fsPath` directly to an OS-built path. On Windows, VS
Code normalizes URI drive-letter case; an OS-built `C:\...` and watcher `c:\...`
therefore failed strict equality. The existing unit test incorrectly supplied
OS-built paths as watcher events, hiding the mismatch.

The regression now supplies URI-normalized watcher paths and failed before the
fix: displayed mode was `none`, expected `ck3raven-dev` (31 passed / 1 failed).
The `modeFilePath` getter now normalizes through `vscode.Uri.file(...).fsPath`,
matching the API used by the watcher. After correction: TypeScript clean,
32 extension tests and 14 Python lifecycle tests pass. `npm run compile`
succeeded, preparing the corrected workspace bundle for a Dev Host reload.

No window was reloaded. The previously installed candidate VSIX does NOT yet
contain this correction. First retest the Dev Host's current live mode query,
then reload only the Dev Host, initialize its fresh generation and verify
AgentView, live mode and scoped publication agree. Rebuild/reinstall the final
production package only after the Dev Host retest is satisfactory.

### Dev Host report received and persistent recovery logging corrected

The report for `dv9i-5e6fed` / PID 70992 confirms initialized process mode,
publication and actual AgentView agree. Controlled termination produced
`j39b-9ea718` / PID 2508, fresh uninitialized state, successful initialization
and matching display; old PID/publications were absent.

Two reported failures concern absent persistent disconnect/cleanup evidence.
Source inspection found that disconnect was output-channel-only, while successful
cleanup had no completion record. The extension now logs the single disconnect
event through StructuredLogger and the coordinator reports cleanup completion
after successful teardown/publication removal and before replacement. No
recovery semantics were changed.

Validation: TypeScript clean, 34 extension tests and 14 Python lifecycle tests
passed; workspace compile succeeded. Original report results are preserved,
with an engineering follow-up appended. Instructions now request only the narrow
logging/recovery retest, not a repeat of the full registry inventory.

No reload, termination, production repackaging or reinstallation was performed
for this correction. Live retest and normal post-close verification remain open.

### Resumed review, shutdown evidence and snapshot preservation

The focused live retest in the report passes: `mmhe-1387a5` / PID 836
disconnected at `2026-10-25T00:26:25.376Z`, cleanup completed at `.391Z`,
and replacement `7rao-e6f784` was published at `.400Z`. Replacement PID 46764
responded, initialized afresh and agreed with AgentView and its mode publication.

Post-close inspection does NOT establish correct historical normal shutdown.
Although the host snapshot recorded clean shutdown, the cleanup record at
`2026-10-25T01:03:58.620Z` reported `pid:null` / no announced PID for
`7rao-e6f784`, which had announced PID 46764. Current absence after reboot is
not evidence of the required exit-before-removal ordering.

VS Code disposes context subscriptions before awaiting asynchronous deactivate.
Diagnostics was subscription-owned as well as explicitly disposed after common
cleanup; early disposal erased retained PID evidence. Diagnostics now has explicit
deactivation ownership, with disposal on early startup failures. Disposal no longer
erases announced PID evidence; verified publication removal remains its release
point. The new owned-child regression failed with an undefined PID before the fix.

Snapshot replacement no longer deletes the destination after rename failure.
The exact temporary file is cleaned in finally; cleanup failure is logged without
masking the original persistence error. A later heartbeat/state write retries
naturally. Regressions first reproduced loss of the last snapshot on rename EPERM
and a leaked temporary file on fsync EPERM; both now pass and verify subsequent
write recovery. The actual cause of the earlier production EPERM remains unknown.

Validation: TypeScript clean, 37 extension tests and 14 Python lifecycle tests
passed (one existing resolver deprecation warning); workspace compile succeeded.
No production install, reload or process termination was performed.

The reported `b2il-331c81` instance has a `ck3lens` publication for PID 73828,
set at `2026-10-25T17:18:57.809679`. Installed bundle SHA256 still matches
the older `5f0f94e8...e7b4b0` package, which predates the AgentView watcher fix.
This supports the reported stale display; it is not a live in-memory mode query
against that instance.

Remaining live acceptance: launch the freshly compiled Development Host,
initialize once and record its announced MCP PID. Close it normally, then inspect
from outside the closed host: cleanup must identify that same PID, verify exit,
remove scoped publications and precede clean shutdown. Do not repeat the already
passed controlled-recovery or full registry inventory. Then rebuild/install the
final VSIX and validate the installed build after a user-directed reload.

### Configured runtime-root correction

User clarified that every mode-publication caller must use the configured global
data root, not independently reconstruct a home-directory path. The canonical
address remains `root:ck3raven_data/agent_mode_<instance_id>.json`; no files
are moved under the repository.

Python mode publication and IPC discovery now import `ROOT_CK3RAVEN_DATA`.
That constant reuses the existing bootstrap resolver, which honors
`CK3RAVEN_DATA` and uses the per-user default only when no override is configured.
The extension's `CK3RAVEN_DATA_ROOT` now follows that same configuration and is
explicitly passed to the MCP subprocess. This also selects the workspace config
under the configured root.

Extension mode reading, watching and cleanup share the configured root and
central mode filename/path helpers. IPC publication and deletion share their
path helper. AgentView no longer compares complete watcher-event and expected
host-path strings or normalizes a drive letter: the watcher is scoped to the
configured root and matches the current instance's filename. Retired-instance
events cannot restore its mode. This supersedes the earlier URI-normalized
expected-path workaround.

The new Python regression first failed because mode publication ignored the
supplied non-default global root. After correction, a fresh subprocess with
`CK3RAVEN_DATA` verifies the canonical root, mode writer and IPC client all
receive that same custom root. Extension tests run under an isolated non-default
root and verify mode reading and actual IPC/mode publication removal there.

Validation: TypeScript clean; 38 extension tests, 20 focused Python
mode/initialization/IPC tests, and the unchanged validator's 14 lifecycle tests
passed. Existing resolver deprecation warning remains. Workspace compile
succeeded. No production install, reload, or live initialization was performed.

### New installed-window incident, 2026-10-25 21:13 +08

User reported disconnect of `b2il-331c81` / PID 73828, followed by publication
of `mb51-d178e5` and rejection of old-identity PID 18648. This is the installed
window, not evidence from the newly compiled Development Host. Installed bundle
still hashes to `5f0f94e8e345293ad17a6eb5a6a9791f6d5d11c67adb126d9be5f3ec14e7b4b0`.
Extension Host PID 18484 remained alive with a running forensic snapshot.

Correlated evidence:

- `13:13:16.673Z`: last observed heartbeat from PID 73828.
- `13:13:33.909Z`: Copilot runtime records input stream termination.
- `.910Z`: extension output records lifecycle ECONNRESET for PID 73828.
- `.929Z`: persistent extension log records publication of `mb51-d178e5`.
- `13:13:37.492Z`: Copilot runtime records SDK session attachment.
- `13:13:43.680Z`: Python records a new PID 18648 starting with OLD identity
  `b2il-331c81`; `.687Z` records rejection, `.688Z` refuses unowned startup.
- `13:13:44.053Z`: runtime native_connector identifies the failed server as
  `ck3lens-b2il-331c81`, exit code 1, stderr explicitly naming old identity
  and unavailable lifecycle lease.

Thus the later identity warning is accurate: the runtime launched an old
definition after replacement publication. AgentView's `mb51-d178e5` matches
published state, not a successful live replacement process. Its IPC publication
exists and mode is null; no replacement Python startup was observed in MCP log.
Both old Python PIDs were absent at inspection. Original process exit cause is
NOT established: no fatal Python traceback was found in the inspected logs.
Runtime service/session turnover is correlated, not proof of which side first
caused the original disconnect. Do not reinterpret runtime service shutdown
metadata as evidence of an MCP request-cancellation notification.

Native MCP log files for these identities exist but were empty. Relevant runtime
evidence is in `.copilot/logs/process-1792894752931-61928.log`, around lines
1156-1183. No source change, install, reload, or process termination was performed
for this incident investigation. Cache/session definition reconciliation remains
an unresolved live recovery limitation; do not call the entire phase accepted.

User additionally confirms the retired server remains in VS Code's server list
with Error status. This is an observed stale native inventory entry, not merely
an inferred SDK cache or a live zombie OS process. Recovery acceptance must cover
the native list as well as process/file teardown and replacement tool calls.
The earlier statement that only normal-close testing remained was too narrow.

Corrected misleading startup diagnostic in source: it now says "Rejected MCP
startup announcement", identifies announcedInstanceId, new PID and
currentInstanceId, and explicitly says no lifecycle lease was granted. The
rejection reason also states the identity mismatch rather than ownership.
Regression verifies both identities and that no PID/socket tracking was accepted.
Unchanged validation script passes (TypeScript, extension38, Python lifecycle14);
workspace compiled. This diagnostic correction does NOT fix stale inventory
reconciliation, and no such recovery fix is claimed or deployed.
