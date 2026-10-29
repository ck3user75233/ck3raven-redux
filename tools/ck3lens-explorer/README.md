# CK3 Lens Explorer

CK3 Lens Explorer is the VS Code extension that hosts the CK3 Raven agent integration and owns its editor-side process lifecycle.

## Implemented Features

- An extension-owned Python MCP process and dynamic authenticated HTTP definition, unique to each VS Code window.
- Diagnostics IPC lifecycle and ownership tracking for the MCP subprocess.
- Shared QBuilder supervision with per-window and per-MCP leases.
- A Tools view for agent initialization, mode selection, MCP status, instance IDs, and HAT approval.
- A Contracts & Operations view for active contracts, operation history, and bug reports.
- The `@ck3raven` chat participant plus health, status, and doctor commands.
- A status bar item for the active agent and MCP state.
- CK3/Paradox script and localization language registration with TextMate syntax highlighting.

The extension does not provide file browsing, playset editing, conflict or issue views, parser linting, language intelligence, AST inspection, Studio authoring, patch generation, or Git controls.

## Requirements

- VS Code 1.100 or newer.
- A ck3raven checkout with a valid `pyproject.toml`.
- A configured CK3 Raven Python environment that contains the MCP server and QBuilder dependencies.
- Node.js and npm for extension development or packaging.

The repository path and Python executable are resolved from CK3 Raven runtime configuration. The extension does not use path-discovery fallbacks or a bare system `python` command.

## Agent Initialization

Click **Initialize Agent**, select a mode, and send the generated two-step prompt:

1. Ping the instance exposed by VS Code. If that fails, refresh tool discovery and try the instance supplied in the prompt.
2. After a successful ping, call `ck3_get_mode_instructions` with the supplied mode and HAT.

## Lifecycle Logging

The **CK3 Lens** output records one handshake, a disconnect with its socket error when available, and publication of a replacement definition. Each event reports only its own identity/PID, without repeating the previous event. A disconnect does not establish that the OS process exited.

`McpLifecycle` owns generation identity and the signing key. The provider projects that generation into the MCP definition; AgentView reads it for display and initialization prompts without storing or rotating another identity. The view stays alive across MCP disconnects, while its connection/initialization display is cleared.

Initial launch and recovery use the same start procedure. The extension spawns Python with the generation identity, signing secret, diagnostics port and a separate random HTTP transport token. Python binds an OS-assigned port on `127.0.0.1`. The extension verifies its authenticated health response (identity and actual Python PID) before publishing the `/mcp` HTTP definition. The definition contains only the URL, transport header and generation version: no executable command, environment or signing key. An SDK session refresh can reconnect an HTTP client without ending the Python process or resetting its initialized mode.

Normal, failed-start and unexpected shutdown share cleanup: withdraw the definition, close the extension-owned lifetime pipe, await child exit (with a specific-launcher-PID process-tree termination fallback), confirm the announced Python PID exited, release the MCP QBuilder lease and remove instance publications. Only then may another start publish a fresh generation. The launcher PID and actual Python PID can differ on Windows; both are recorded. Window shutdown also stops the extension's QBuilder supervisor and disposes its provider registration.

The announced PID is retained after socket loss. Even before a Python announcement, the extension retains its child-process handle for cleanup. Diagnostics startup must produce a listening socket and successfully publish its binding. A startup announcement must carry the current generation's owner token, and a second process cannot take over an existing generation's lease. HTTP health and MCP requests require the transport token; the SDK's DNS-rebinding protection remains enabled. Tokens and signing keys are not included in lifecycle logs.

Tool-list discovery never establishes or clears a lifecycle connection: cached tool definitions are not a health check. The connection indicator describes the lifecycle handshake, not whether a tool request is responsive.

Contract approval waits run in an MCP worker thread so human review does not block the protocol event loop. The approval deadline is ten minutes; the caller can impose a shorter request deadline. A caller timeout does not prove process exit or cancel an approval already executing.

Python requires an affirmative startup acknowledgement before serving tools. Startup attempts during cleanup and foreign identities are reported and declined; a duplicate generation owner is an error. Unexpected request-socket errors remain errors with peer and last-request method details; these diagnostics do not establish the cause of a reset.

The explicit standalone `--transport stdio` path retains its wire diagnostics. Extension-owned HTTP uses the existing tool start/completion audit and child exit evidence, not stdin/stdout protocol lifetime. Python exits if its extension lifetime pipe or diagnostics lease closes, but not when an HTTP client disconnects.

A repository-owned adapter on this server instance catches only the SDK's exact `Request already responded to` assertion originating in that request's `RequestResponder.respond()` method after completion. It logs `duplicate_response_discarded` as a warning with the actual request ID and method, then discards that duplicate reply. Unrelated assertions and transport errors retain their existing behavior. The adapter uses the SDK's internal request-dispatch method because this SDK has no public response-error hook; its compatibility tests must accompany SDK upgrades. This contains the observed failure without claiming to know what originally completed the historical request. Live installed-host acceptance remains outstanding.

Script review/approval/rejection messages appear in the separate **CK3 Lens Approvals** output. CodeLens rendering and binding/watcher/provider bookkeeping are debug-only. MCP tool auditing remains in the existing Python trace logs, separate from extension lifecycle output; the approvals channel is not a tool-call audit stream.

## Development

From the repository root:

```powershell
cd tools/ck3lens-explorer
npm install
npm run compile
```

Then open the repository root in VS Code, select **Run CK3 Lens Extension** in Run and Debug, and press `F5`. The launch configuration compiles the extension and opens a separate Extension Development Host using the workspace source.

After an explicit compile, **Run CK3 Lens Extension (No Compile)** starts the Development Host without rerunning the build task. For continuous rebuilding, run:

```powershell
npm run watch
```

Use the Development Host to verify activation, the per-window MCP instance, QBuilder health and leases, the retained views and commands, and clean shutdown behavior.

## Packaged Installation

Build a VSIX from `tools/ck3lens-explorer`:

```powershell
npm run package
code --install-extension ck3lens-explorer-0.1.0.vsix --force
```

A packaged installation runs the JavaScript bundled into the installed VSIX. Compiling workspace source does not update an installed extension. Rebuild and reinstall the VSIX whenever workspace source changes must be tested in the packaged installation.

When migrating from the old stdio build, reload both windows after installation so each host replaces its cached executable definition with the HTTP definition. Historical cached stdio definitions are not rewritten or deleted by the extension. A stale HTTP definition cannot launch any process; an endpoint belonging to a stopped generation is unavailable, or rejects a different generation's transport token.

Focused ownership regressions (from the repository root):

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_mcp_http_transport.py -q
```

These use isolated MCP fixture servers, not the live MCP or QBuilder. They cover real SDK disconnect/reconnect, retained process state, per-window authentication/isolation, owner-EOF shutdown, DNS-rebinding rejection, HTTP-only definitions, readiness ordering and failed-launch cleanup. The Python runner also builds and runs the extension lifecycle tests with the existing esbuild and Node test runner.

## Configuration

- `ck3lens.traceLevel`: extension logging level.
- `ck3lens.ipcPort`: diagnostics IPC port; `0` uses an OS-assigned per-window port and is the recommended setting.

Do not add a static MCP configuration file. The extension registers its MCP server dynamically for each VS Code window.

## License

MIT
