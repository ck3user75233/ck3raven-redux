# CK3 Lens Explorer Design

## Status

CK3 Lens Explorer is a lifecycle and agent-integration extension. It does not currently implement a game-state explorer, authoring environment, or language intelligence service.

The former generic Python JSON-RPC bridge is retired. The extension now uses a dynamic VS Code MCP provider for agent tools, a focused diagnostics IPC server for editor integration and subprocess ownership, and a shared QBuilder daemon for database mutation.

## Architecture

```text
VS Code window
  |
  +-- Extension activation and command registration
  |
  +-- Diagnostics IPC server
  |     +-- per-window endpoint publication
  |     +-- MCP process-lifetime socket ownership
  |     +-- VS Code diagnostics access
  |     +-- human approval flow for script signing
  |
  +-- QBuilder supervisor
  |     +-- shared daemon discovery or startup
  |     +-- per-window lease and heartbeat
  |     +-- last-window shutdown request
  |
  +-- Owned MCP process
  |     +-- Python child handle, exit code and bounded cleanup
  |     +-- signing secret and diagnostics endpoint in child environment only
  |     +-- authenticated loopback HTTP readiness
  |
  +-- Dynamic HTTP MCP definition provider
  |     +-- unique instance ID and server name per window
  |     +-- loopback URL and separate transport token, no launch command
  |
  +-- Retained user surfaces
        +-- Tools view
        +-- Contracts & Operations view
        +-- CK3 Raven chat participant and diagnostic commands
        +-- status bar
        +-- CK3/Paradox language registration and TextMate grammars
```

## Retained Modules

| Module | Responsibility |
|---|---|
| `src/extension.ts` | Composes activation, retained views and commands, process ownership, and ordered deactivation. |
| `src/mcp/mcpServerProvider.ts` | Registers only an HTTP connection to an already-ready per-window process. |
| `src/mcp/mcpProcess.ts` | Spawns Python, verifies readiness and owns child exit and termination. |
| `src/mcp/mcpLifecycle.ts` | Serializes generation startup, publication, common cleanup and recovery. |
| `src/ipc/diagnosticsServer.ts` | Owns the per-window diagnostics endpoint and the MCP process-lifetime socket. |
| `src/qbuilder/qbuilderSupervisor.ts` | Ensures the shared QBuilder daemon is healthy and maintains this window's lease. |
| `src/views/agentView.ts` | Presents agent initialization, mode, MCP status, instance identity, and approval controls. |
| `src/views/contractsView.ts` | Presents contracts, operation history, and bug-report actions. |
| `src/chat/participant.ts` | Implements the CK3 Raven chat participant. |
| `src/chat/diagnose.ts` and `src/setup/doctor.ts` | Implement health, quick-status, and doctor diagnostics. |
| `src/widget/statusBar.ts` | Presents retained status information in the VS Code status bar. |
| `syntaxes/` and `language-configuration.json` | Provide CK3/Paradox script and localization language registration and syntax highlighting. |

## Ownership And Lifecycle Invariants

1. Each VS Code window receives a fresh MCP instance ID and a unique MCP server definition. Static MCP configuration is not part of the architecture.
2. The diagnostics server starts before the extension spawns Python. The HTTP definition is registered only after an authenticated readiness probe confirms identity and actual Python PID.
3. Python holds an instance-bound diagnostics socket and an extension-owned stdin lifetime pipe. Owner loss ends Python; HTTP client disconnection does not. Socket closure alone is not proof of OS process exit.
4. Fresh generations start only after common cleanup confirms the old process has stopped. Cached HTTP definitions contain no executable and cannot spawn a retired generation.
5. QBuilder is a shared, single-writer daemon. The supervisor discovers or starts it, verifies the published endpoint identity, and maintains an extension lease with a heartbeat.
6. The MCP process and extension window use distinct QBuilder leases. A window releases both during shutdown, and daemon shutdown is requested only after its leases are removed. Other live windows keep the daemon running.
7. Cleanup withdraws and disposes the provider registration, stops the owned child, confirms Python exit, releases the MCP lease and removes publications. Deactivation then releases window services and disposes diagnostics.
8. Repository and Python locations come from canonical CK3 Raven runtime configuration. There are no path-discovery or bare-Python fallbacks.
9. `Session.mods` remains the authoritative runtime mod list. The extension must not reconstruct playsets, maintain another mod list, or mutate playset JSON directly.
10. User-supplied canonical paths resolve through WorldAdapter v2. Resolution identifies a target; enforcement alone decides whether a mutation may execute.
11. Mutations use canonical MCP and enforcement flows. The extension must not add arbitrary direct mod writes or permission prechecks.
12. Ordinary display or opening of an explicitly identified file should use VS Code filesystem and editor APIs when no canonical mutation is involved.

## Data And Process Boundaries

- The extension owns editor UI, VS Code registrations, per-window process identity, diagnostics IPC, and extension-side leases.
- The MCP server owns the agent tool surface and canonical request routing.
- WorldAdapter v2 owns canonical target resolution and visibility.
- The enforcement layer owns mutation decisions at execution time.
- `Session.mods` owns active playset membership and order.
- QBuilder owns all database writes. Extension and MCP readers do not create alternate write paths.

## Retired Bridge

The generic Python JSON-RPC bridge and its extension-side session facade are retired and must not return. New read-only language or parser features may use a purpose-built, narrow service or worker, but they must not recreate a general command bridge, independent session hydration, path fallback, or mutation route.

## Non-Goals

The current extension does not provide:

- Explorer, Playset, Conflicts, or Issues product views.
- Playset mutation UI or direct playset state editing.
- Parser linting, completion, hover, definition, reference, or other language intelligence.
- AST inspection, Studio, a floating widget panel, or file templates.
- Patch writing, compatch generation, arbitrary mod-file editing, or Git integration.
- Database initialization or build controls as an extension product surface.

Possible future surfaces are specified only as plans in `docs/EXPLORER_FEATURES.md` and require explicit acceptance before implementation.
