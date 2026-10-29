# MCP lifecycle consolidation plan

**Saved:** 2026-10-23, before the user's planned shutdown.  
**Status:** Lifecycle coordinator and common cleanup implemented in source, with
focused tests passing. The narrow duplicate-response guard was separately
approved and implemented; installation/live-host acceptance remains outstanding.
The detailed proposal below is historical; use
[today's work record](./MCP_SESSION_20261024.md) for current scope and evidence.

**2026-10-24 review:** See [today's work record](./MCP_SESSION_20261024.md). The user rejected a host-retirement-receipt requirement; it is not a prerequisite to lifecycle implementation. Late-response remedy is under review: investigate our producer/integration and a compatible SDK fix before choosing a narrow compatibility catch. Older proposed gates below are historical, not newly approved requirements.

## Agreed design

There is one start procedure. Calling it after a previous generation has been cleaned up does not require a separate restart implementation.

There is one cleanup procedure. Normal shutdown and unexpected shutdown supply different circumstances to that procedure, not separate cleanup implementations.

```text
start -> starting -> running
                       |
                 cleanup(reason)
                       |
                    stopped
                       |
              start, if requested
```

The extension coordinates lifecycle ownership; the actual native/SDK MCP client remains the process launcher. Publishing a definition is not a process launch or a cleanup acknowledgement.

Each new generation receives a fresh MCP identity and signing key and starts uninitialized. AgentView has no identity of its own.

### Signing scope clarified by the user, 2026-10-23 22:56 +08:00

This supersedes earlier speculative approval-queue requirements below. Keep it separate from the lifecycle consolidation.

The narrow change is to make seeking script approval independent of how quickly the user responds:

1. Submit a signing request and return promptly rather than hold a tool call open awaiting human review.
2. Allow at most one outstanding signing request. Do not build a multi-request queue. Cross-window versus per-instance cardinality has not yet been specified.
3. Display the outstanding request in AgentView; the user can open it for review when available. A nonblocking notification may draw attention to it.
4. Retain the existing review/approval interaction and signing format.
5. On approval, use a dedicated signing action to prepare chat text containing the signature object and continuation instructions. The user sends that text.

The chat-prefill approach is analogous to the existing mode/HAT prompt UX, but must NOT call or directly reuse initialization/mode/HAT workflow logic. No automatic background message delivery is requested.

Do not add these requirements:

- Approval-layer content-change detection or re-review enforcement. Existing signature/content validation at execution is responsible for rejecting a mismatched script.
- Explicit approval invalidation/re-review triggered by signing-key changes. Existing Sigil verification already rejects signatures made with an incompatible key.
- Persistence across window reloads. The user accepts having to resubmit a request.
- A new cancellation feature, request-state framework, result-query tool, or durable delivery system.

The earlier two-hour long-stop was a user-suggested bound, not a reason to add a broader request-management subsystem.

Ordinary approval rejection or expiry has not been shown to terminate MCP. The confirmed crash was an SDK duplicate-response assertion after a caller-timeout/late-approval sequence; do not relabel normal review outcomes as fatal. Any separate SDK crash fix must remain separately scoped.

### User clarifications and immediate timeout priority, 2026-10-23 12:25 +08:00

- There is no user requirement for same-identity PID supersession. It is existing behavior to remove, not a feature to preserve.
- Retain process ownership/PID evidence after a socket is removed. A heartbeat answers responsiveness, not whether an old OS process has exited.
- The callers of the current shutdown verifier are extension deactivation and the late-start teardown branch in [extension.ts](../tools/ck3lens-explorer/src/extension.ts). They are not socket-health consumers.
- Lease loss currently terminates Python because `_monitor_lifecycle_disconnect()` explicitly calls `os._exit(0)` after EOF/error. This is an application lifetime policy, not normal TCP or Python behavior. Make that policy explicit in lifecycle cleanup; do not describe every lease loss as a spontaneous process crash.
- The late-result failure should be contained to the expired request. The observed SDK code asserts before checking cancellation, and its final response step is outside its handler exception catch. The assertion escapes a child task; AnyIO's task group cancels siblings and raises an exception group, unwinding the server session. That is why this defect affects the entire server rather than only the request.

**First investigate and align caller/backend deadlines.** Originally the requested review period was 600 seconds; the user subsequently said a shorter period is acceptable in principle and asked how short it needs to be. No replacement duration has been agreed or implemented. Identical caller/backend deadlines can still race; review-opening, polling, cancellation, and response overhead must fit within the caller budget.

The official [Copilot CLI command reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference) documents an MCP `--timeout` default of **30000 ms** for discovery/tool calls. Under that default, a **20-second** review window is a candidate, leaving ten seconds for other work. This is not a measurement of this already-running SDK session's effective timeout, nor a verified total-call bound. A textual `30000` example found in the native binary was part of an unrelated embedded prompt and is not evidence of the runtime default.

Verified integration limitation:

- Official [Copilot SDK types](https://github.com/github/copilot-sdk/blob/main/nodejs/src/types.ts) define `MCPServerConfigBase.timeout` as a tool-call timeout in milliseconds.
- The locally installed VS Code extension API's `McpStdioServerDefinition` exposes label, command, args, env, version, and cwd, but no caller-timeout field.
- The installed agent-host conversion function `LH` constructs SDK configuration explicitly and does not forward a timeout.
- Therefore adding an untyped `timeout` property to our provider, or changing a child-process environment variable, does not establish caller-timeout alignment.
- No caller timeout has been changed, and the effective current caller deadline has not been measured. Do not report alignment as complete.

The user subsequently proposed an asynchronous UI approach; its narrower scope is recorded above. Do not carry the earlier speculative durable pending/result-query design into implementation.

Do not claim that the duplicate-completion crash is fixed merely by choosing a
longer timeout. No cancellation notification has been established for the
reported incident, and synthetic cancellation testing is not an acceptance
requirement. Actual protocol metadata capture is now implemented to establish
the sequence without assuming a mechanism.

## Evidence that constrains this plan

The [lifecycle map and incident chronology](C:/Users/nateb/.copilot/session-state/dd9140f8-2f8f-458d-8306-88a4904da309/files/mcp-lifecycle-map-20261023.md) documents the inspected source, installed host behavior, and actual process ancestry.

- Current source has competing constructions: fresh-identity publication after lease loss and same-identity PID supersession that closes the previous lease.
- The observed application processes were launched by Copilot's runtime.
- VS Code's List Servers command can display either native server objects or session/SDK inventory. Native 500 ms reconciliation does not establish session inventory cleanup time.
- Active socket bookkeeping is erased before some shutdown verification. An absent entry currently produces an unchecked `stopped=true`.
- Replacement publication has no old-process-exit or client-inventory-retirement barrier.
- An unexpected lifecycle socket loss is not necessarily a process crash. Closing the lease can itself terminate Python.

## New incident: late approval followed by another signing request

**Reported by the user at 2026-10-23 10:04 +08:00; independently correlated with the logs.**

Observed user sequence:

1. A `ck3_contract(command="sign_script")` call times out at the caller.
2. Its CodeLens approval button remains available.
3. The user approves after the caller has timed out, but the agent receives no approval result.
4. The agent submits another signing request. The user observes the MCP shutdown at that point.
5. The caller reports:

```text
MCP server 'ck3lens-72zr-2c9c26': McpError: MCP error -32001: Request timed out
```

This is the newly identified reproduction sequence. The caller's timeout text is not the underlying Python exception.

### Correlated backend evidence

Instance `72zr-2c9c26`, application PID 31896, on 2026-10-23:

| UTC | Local (+08:00) | Event |
| --- | --- | --- |
| 02:01:17.250 | 10:01:17.250 | Signing success `CT-SIGN-S-001`, duration 552835.4 ms, trace `377bc9129778416f`. |
| 02:01:17.469 | 10:01:17.469 | Signing rejection `CT-SIGN-I-003`, duration 346599.23 ms, trace `323b75cd9f424297`. Do not relabel this result as a timeout or another success. |
| 02:01:42.431 | 10:01:42.431 | Fatal nested TaskGroup exception with two leaf assertions: `AssertionError: Request already responded to`. |
| 02:01:42.468 | 10:01:42.468 | Main loop ended. |
| 02:01:42.473 | 10:01:42.473 | Extension published replacement definition `qlae-b80825`. Publication does not establish successful replacement startup. |
| 02:01:42.492 | 10:01:42.492 | Old process atexit log. |

The fatal stack reaches the installed MCP SDK low-level `_handle_request()` response step, then `RequestResponder.respond()`, which asserts that the request is not already completed. Both assertions escape through task groups and end the server session.

The installed responder's cancellation method marks a request completed and sends its cancellation response. A subsequent attempt to respond to that responder hits this assertion. This is a concrete cancellation/late-response failure path consistent with the reported trigger, not just a generic transport-framing hypothesis.

The crash stack does not identify the two request IDs or prove which operation first completed each responder. Capture the protocol sequence in a focused reproduction before asserting that both leaf failures were signing responses. Do not use the inherited crash-log trace context to assign request identity.

### Existing fixes versus outstanding work

- The backend signing deadline is 600 seconds; the caller has a separate deadline. Its exact configuration remains unverified. Extending the backend deadline alone does not extend the caller deadline.
- The saved Python worker wrapper moves the blocking contract handler off the protocol event loop. It loads in a new MCP Python process, including one created after a window reload.
- Existing regression coverage exercises simulated approval cancellation and pings during/after the wait. It does not exercise late CodeLens approval followed by re-submitting the actual signing operation.
- Therefore the exact newly observed assertion failure is **not yet verified fixed**.
- Latest extension-side changes are still not packaged/installed. A window reload alone does not install them.

Add a bounded real-stdio regression for caller expiry/cancellation, late approval, and a second signing request. Assert that the process remains alive, the second request has its own responder, and pings work before and after both completions.

The asynchronous UI change above removes the long-running approval tool response rather than introducing a durable result-retrieval system. Preserve the crash evidence and investigate duplicate completion separately; do not swallow the SDK assertion or patch installed dependency files as the product fix.

This crash regression is a concrete diagnostic priority alongside lifecycle ownership step 0. Lifecycle consolidation and renaming/debouncing logs are not substitutes for fixing request completion.

## 1. New code and responsibilities

### One small lifecycle owner

Add [mcpLifecycle.ts](../tools/ck3lens-explorer/src/mcp/mcpLifecycle.ts) as a focused coordinator, not a generic process framework.

It owns:

- Lifecycle phase: stopped, starting, running, cleaning, or failed.
- The current generation record: MCP identity, signing key, observed PID, and termination evidence.
- A retained retiring-generation record until cleanup is actually complete.
- Serialization/coalescing of start and cleanup work.
- A closing flag that prevents pending work from publishing a new generation during deactivation.

Expose one `start()` and one `cleanup(reason)`. Do not add a separate restart API.

Concurrent starts must share the same pending operation and generation. Concurrent cleanup notifications for the same generation must share one cleanup operation. A stale completion must not clear a newer generation's state.

Socket handles remain transport details in diagnostics. The lifecycle owner retains process identity independently of whether a socket is still present.

### Start sequence

1. Require the previous generation's cleanup to be complete. A failed cleanup remains a visible failure; do not silently publish a replacement.
2. Allocate the new generation exactly once.
3. Ensure window-level diagnostics and QBuilder prerequisites are ready. Their one-time bootstrap is not a second MCP start path.
4. Bind the generation to diagnostics, the provider projection, and the mode projection. Install lifecycle observers before publication.
5. Publish one complete definition through the existing provider registration.
6. Observe the launching client's process hello and associate its PID with this generation.
7. Keep lifecycle-connected state distinct from protocol readiness and explicit mode initialization.

Do not introduce ping polling, discovery retries, or a second process launcher to make publication look successful.

### Cleanup sequence

1. Mark the generation cleaning and retain its PID and ownership information.
2. Withdraw the launch definition and request retirement through the actual client's supported lifecycle route.
3. If a process remains alive, request its termination using the existing owned-lease mechanism. Keep specific-PID termination only as the bounded fallback.
4. Verify OS exit independently of socket existence. An unknown PID is unknown, not confirmed stopped.
5. Verify retirement of the relevant client configuration/inventory using the supported owner acknowledgement established in implementation step 0 below.
6. Remove only that generation's port/mode publications and release its MCP QBuilder lease.
7. Mark cleanup complete and project stopped/uninitialized UI state.

These postconditions are independent: a dead process does not prove an entry was removed; a removed entry does not prove a process exited.

On unexpected termination, invoke cleanup and then the same `start()` if recovery is requested. On an intentional stop/deactivation, invoke cleanup and remain stopped. The start decision belongs to the owner, not to diagnostics socket callbacks.

### Abrupt owner death

The dead Extension Host cannot execute cleanup. The next activation must reconcile abandoned ownership records using the same cleanup rules, with a verified dead owner and scoped paths.

Preserve window isolation. Never remove another live window's publications. Keep this reconciliation distinct from proof that native/SDK registrations have been retired.

## 2. Existing code: retain, change, retire

| File / surface | Retain | Change or retire |
| --- | --- | --- |
| [extension.ts](../tools/ck3lens-explorer/src/extension.ts) | Activation wiring, logging channels, view wiring, window services | Replace direct started/stopped lifecycle policy and deactivation's separate teardown orchestration with calls to the lifecycle owner. Retire the local generate/rebind/rotate recovery block and late-start teardown branch as independent controllers. |
| [mcpServerProvider.ts](../tools/ck3lens-explorer/src/mcp/mcpServerProvider.ts) | One provider registration, Python selection, complete definition construction | Project the lifecycle owner's generation. Remove independently mutable identity/key ownership and `rotateInstanceId()`. Withdraw definitions through the common cleanup flow; dispose the registration only when the extension is ending. |
| [diagnosticsServer.ts](../tools/ck3lens-explorer/src/ipc/diagnosticsServer.ts) | TCP transport, request diagnostics, hello parsing, owned socket tracking, approval handling | Report lifecycle observations rather than decide replacement. Retire same-ID PID supersession that destroys an existing process lease. Replace map-absence-as-success teardown verification with explicit retirement evidence. |
| Same diagnostics source | Window/identity binding checks necessary for IPC ownership | Retire provisional `retiredInstanceIds` / `retiredLaunchSockets` recovery bookkeeping and its quiet-decline mechanism as the zombie strategy. Do not replace it with another warning/rejection loop. See ownership prerequisite below before removing a guard. |
| Same diagnostics source | Scoped publication deletion as a low-level operation | Separate publication rebinding from implicit process killing. Retire the unused `cleanupZombieServer()` implementation; if its orphan-file rules are needed, integrate them once into owner reconciliation rather than keep a misleading parallel "zombie" path. |
| [server.py](../tools/ck3lens_mcp/server.py) | One Python entry point, stdio diagnostics, signal reporting, process-local initialization, heartbeats, approval worker wrapper | Route orderly Python finalization through one process-local finalizer. Keep lease-loss emergency exit distinct only where required for bounded termination; document that hard exit cannot run finalizers. Remove obsolete lifecycle comments/assumptions. |
| [ipc_client.py](../tools/ck3lens_mcp/ck3lens/ipc_client.py) | Instance-scoped port discovery, explicit hello acknowledgement, lease transport | Keep transport failure explicit. A negative hello is not a registration-cleanup mechanism. Adapt the handshake only to the verified owner protocol, not to speculative duplicate-launch handling. |
| [agentView.ts](../tools/ck3lens-explorer/src/views/agentView.ts) | Provider/current-generation getter, stable filtered mode watcher, diagnostic-only tool discovery | Subscribe to lifecycle projections. No stored MCP identity, rotation method, process ownership, or automatic start from view refresh. |
| [agent_mode.py](../tools/ck3lens_mcp/ck3lens/agent_mode.py) | Authoritative process-local mode and per-generation display publication | Ensure cleanup cannot delete or restore a newer generation's projection. Disk state must never restore authorization. |
| [qbuilderSupervisor.ts](../tools/ck3lens-explorer/src/qbuilder/qbuilderSupervisor.ts) | Shared-daemon supervision, per-window attachment, bounded readiness checks | Release the retiring MCP lease from common cleanup. Preserve the extension lease until deactivation; do not restart or stop the shared daemon on every MCP replacement. |
| [extensionHostMonitor.ts](../tools/ck3lens-explorer/src/utils/extensionHostMonitor.ts) | Forensic host-generation records | Keep it observational. Do not make it another MCP recovery controller. |
| [README.md](../tools/ck3lens-explorer/README.md) | Accurate channel and approval documentation | Replace lifecycle descriptions with the single-start/common-cleanup model and explicit connection/readiness distinctions. |

Retirement means removing the old behavior and its obsolete state/tests, not renaming log messages while preserving it.

## 3. Ownership prerequisite: resolve before implementing recovery

The extension API currently exposes definition publication/disposal, not an established completion acknowledgement for every consuming SDK client.

**Implementation step 0 is therefore a real dependency, not a solved detail:**

1. Reproduce the lingering entry using a controlled owned test setup; identify native versus session List Servers.
2. Correlate definition publication/withdrawal, the exact client/session consuming it, Python launch/exit, and inventory insertion/removal.
3. Establish the supported client retirement and stop-intent notification route. A host Stop must not be mistaken for an unexpected failure and immediately undone.
4. Demonstrate how that client stops using an old launch snapshot before a fresh generation is published.

Do not claim that calling provider `shutdown()` fulfils this contract. Do not assume a timer, a negative startup acknowledgement, or a hidden/private SDK RPC is an acceptable replacement.

If this cannot be achieved using supported integration, record the precise blocker and get a design decision before changing the transport/identity architecture. Do not silently adopt a stable server label, static MCP configuration, or a new recovery rejection policy.

Ordinary healthy operation should not generate launches that must be rejected. Malformed or foreign ownership data must still fail visibly; removing startup integrity checks is not the fix.

## 4. Ordered implementation work

| Step | Work | Depends on |
| --- | --- | --- |
| 0 | Establish actual client ownership, retirement completion, and intentional-stop semantics | Existing lifecycle map |
| 1 | Add lifecycle owner and deterministic state/serialization tests | Step 0 |
| 2 | Make provider a projection; route diagnostics observations and extension shutdown through the owner; remove old controllers in the same change | Step 1 |
| 3 | Consolidate Python finalization and scoped publication/lease cleanup; preserve bounded hard-exit handling | Steps 0-2 |
| 4 | Add real subprocess and host-level regression checks; update related documentation | Steps 2-3 |
| 5 | Validate the integrated batch, review exact package contents, obtain deployment approval, package/install, then reload only with user direction | Step 4 |

The new incident above provides a fatal duplicate-completion assertion to reproduce and address in parallel. The earlier `hgo6-947b14` receiver-closure incident still lacks its initiating exception. Do not retroactively equate the two or hold out lifecycle consolidation/the approval worker fix as proof that either complete sequence is resolved.

## 5. Validation required before deployment

Add focused [mcpLifecycle.test.ts](../tools/ck3lens-explorer/src/mcp/mcpLifecycle.test.ts) and register it in the [existing runner](../tools/ck3lens-explorer/scripts/run-unit-tests.js).

Required cases:

- Initial start and start after cleanup execute the same procedure.
- Two concurrent starts allocate/publish once.
- Intentional stop cleans up and stays stopped.
- Unexpected termination cleans up once before a new start.
- Simultaneous socket loss, process exit, and deactivation do not duplicate cleanup or publish during shutdown.
- A removed socket with a still-live PID is not reported as stopped.
- Failed or unknown process/client retirement prevents replacement publication and is visible.
- A second launch does not silently kill the current process as a normal same-ID supersession path.
- Stale close/finalizer/file events cannot clear the current generation.
- Per-window publications and QBuilder leases remain isolated.
- AgentView refresh/tool discovery does not start processes or infer protocol responsiveness.
- Caller signing timeout/cancellation, late CodeLens approval, and a second signing request do not terminate the MCP session or double-complete a responder.

Update [diagnostics tests](../tools/ck3lens-explorer/src/ipc/diagnosticsServer.test.ts) to test transport observations and ownership errors, not retired-ID suppression as successful recovery. Preserve [AgentView tests](../tools/ck3lens-explorer/src/views/agentView.test.ts).

Preserve and extend [IPC lifecycle tests](../tests/test_ipc_client_lifecycle.py), [worker responsiveness tests](../tests/test_mcp_worker_tool.py), [stdio tests](../tests/test_stdio_transport.py), and [process mode tests](../tests/test_agent_mode_lifecycle.py) as relevant.

Host acceptance must exercise both native and SDK List Servers, collect the inventory-retirement evidence, and show that a removed identity cannot be relaunched from stale configuration during ordinary recovery. Unit mocks alone do not prove this.

Run the existing focused extension suite, TypeScript check, compile, and relevant Python selectors. Do not quietly exclude newly failing tests. Previously recorded baseline: 16 extension tests/typecheck/compile passed; 28 Python tests passed with one unrelated legacy spill-exemption test deselected. Those results predate this proposed consolidation.

## 6. Shutdown and resumption handoff

- No lifecycle consolidation is implemented yet. Existing working-tree fixes from the investigation remain present.
- No new package was installed in this planning step. Installation and loaded-window state remain separate.
- The previously opened deployment contract was cancelled without executing packaging/install.
- Leave the [deployment script](C:/Users/nateb/.ck3raven/wip/deploy_logging_prompt_20261022.py) unchanged and unexecuted. Its previous approval is not approval of future lifecycle changes.
- Preserve all unrelated dirty worktree changes. Do not reset, clean, or commit them as part of this plan.
- After reopening, discover and ping the then-current registered MCP instance and initialize with a fresh user-provided token if required. Do not assume historical instance IDs/PIDs are still valid.
- Resume with the newly captured duplicate-response crash regression and implementation step 0, not packaging or another rejection/log-level patch.
- Outstanding separate questions: initiating exception for the earlier receiver closure, caller approval deadline/result loss, exact request IDs in the new assertion failure, original uncorrelated ECONNRESET, and the exact inventory event behind the reported minutes-long entry.
