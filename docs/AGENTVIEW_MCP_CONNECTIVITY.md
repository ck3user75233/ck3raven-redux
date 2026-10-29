# AgentView MCP Connectivity

This document describes how the CK3 Lens Explorer extension detects whether the MCP subprocess is connected or disconnected, and how that state is reflected in the Agent sidebar tree view.

---

## Overview

The Agent tree view (`ck3lens.agentView`) displays an MCP connection status badge. The status transitions between `connected` and `disconnected` based on signals from two sources:

| Source | Role | Latency |
|--------|------|---------|
| IPC TCP socket lifecycle | **Authoritative** — detects all subprocess death scenarios | Immediate (OS socket close) |
| `vscode.lm.tools` poll (30s) | Belt-and-suspenders — can confirm connected if tools appear | Up to 30 seconds |

**IPC socket-close is the primary mechanism.** The `vscode.lm.tools` poll can only _confirm_ connected; it must never _set_ disconnected (see [Why vscode.lm.tools is not authoritative](#why-vscodelm-tools-is-not-authoritative)).

---

## IPC TCP Socket Lifecycle

The Python MCP subprocess connects to the extension's `DiagnosticsServer` TCP socket on startup and holds that connection open for its entire lifetime. This makes the TCP socket a reliable process-lifetime proxy.

### Subprocess startup → connected

When the Python subprocess starts, it sends a `mcpStarted` JSON-RPC message over IPC:

```json
{ "method": "mcpStarted", "params": { "instanceId": "32m8-a69be8", "pid": 12345 } }
```

`DiagnosticsServer.handleMcpStarted()` receives this, fires `onMcpSubprocessStarted`, and registers a **one-time `socket.once('close')` handler** on the IPC socket.

`extension.ts` subscribes to `onMcpSubprocessStarted` and on first start (not a restart) calls:

```ts
agentProvider.setMcpServerStatus('connected', reportedId);
```

### Subprocess death → disconnected

When the subprocess stops — for any reason — the OS closes the TCP socket. The `socket.once('close')` handler fires, which fires `onMcpSubprocessStopped`.

`extension.ts` subscribes and calls:

```ts
agentProvider.setMcpServerStatus('disconnected', `subprocess stopped (${stoppedId})`);
```

**All death scenarios are covered:**

| Scenario | Python atexit fires? | TCP socket closes? |
|----------|----------------------|--------------------|
| Clean `sys.exit()` | Yes | Yes |
| Unhandled exception | No | Yes |
| `kill -9` / SIGKILL | No | Yes |
| `TerminateProcess` (Windows) | No | Yes |
| VS Code kills subprocess (MCP shutdown) | No | Yes |

`atexit` is unreliable for hard-kill scenarios. TCP socket close is not.

---

## Subprocess Restart Flow

When a restart is detected (PID changed for same instanceId), `extension.ts` calls `mcpServerProvider.rotateInstanceId()` instead of setting connected. The rotation:

1. Generates a new instanceId
2. Fires `_onDidChangeDefinitions` → VS Code spawns a new MCP subprocess
3. Updates `DiagnosticsServer.instanceId` (swaps the IPC port file)
4. Calls `agentProvider.onInstanceIdRotated(newId)` → resets sidebar state to `disconnected`

The new subprocess then sends `mcpStarted`, going through the first-start path above.

---

## `checkMcpConfiguration()` — 30-second poll

`AgentViewProvider.checkMcpConfiguration()` polls `vscode.lm.tools` every 30 seconds. Its role changed in VS Code 1.123:

### Why `vscode.lm.tools` is not authoritative

In VS Code 1.123+, `vscode.lm.tools` called from the extension host does **not** expose MCP tools. The tools are available to chat/agent requests but not to extension-side code. This means the poll returns 0 tools even when MCP is fully working.

**Previous behavior (broken):** `0 tools found → markMcpDisconnected('no tools registered')` — this fired every 30 seconds even when MCP was running fine, spamming false disconnected state.

**Current behavior (correct):** `0 tools found → debug log only` (IPC socket-close handles disconnected). If tools _are_ visible (belt-and-suspenders path, may occur in future VS Code versions), they confirm connected.

### Tool prefix format

The expected prefix for CK3 Lens tools:

```
mcp_ck3_lens_{instanceId}_
```

Where `instanceId` is the current window instance ID (e.g., `32m8-a69be8`). This is derived from VS Code slugifying the `McpStdioServerDefinition` label (`"CK3 Lens"`) and appending the FastMCP `serverInfo.name` suffix (which embeds the instanceId). Do not use `mcp_ck3lens-` — that is an incorrect older format.

---

## `setMcpServerStatus()` — the single mutation point

All connected/disconnected transitions go through `AgentViewProvider.setMcpServerStatus(status, serverName?)`:

```ts
public setMcpServerStatus(status: ConnectionStatus, serverName?: string): void {
    this.state.mcpServer = { status, serverName };
    this.persistState();
    this.refresh();
}
```

This updates `state.mcpServer`, persists to `globalState`, and refreshes the tree view.

**Callers:**
- `extension.ts` → `onMcpSubprocessStarted` (first start): `'connected'`
- `extension.ts` → `onMcpSubprocessStopped`: `'disconnected'`
- `extension.ts` → `onInstanceIdRotated`: indirectly via `agentProvider.onInstanceIdRotated()`
- `checkMcpConfiguration()`: `'connected'` only (when tools visible — belt-and-suspenders)

---

## Sequence Diagram

```
Python subprocess          DiagnosticsServer         extension.ts         AgentViewProvider
      |                          |                        |                       |
      |--- mcpStarted IPC ------>|                        |                       |
      |                          |-- onMcpSubprocessStarted (first start) ------->|
      |                          |   socket.once('close') registered               | setMcpServerStatus('connected')
      |                          |                        |                       |
      |  [subprocess dies]       |                        |                       |
      |                          |<-- socket close (OS)   |                       |
      |                          |-- onMcpSubprocessStopped ---------------------->|
      |                          |                        | setMcpServerStatus('disconnected')
```

---

## Files Involved

| File | Role |
|------|------|
| `tools/ck3lens-explorer/src/ipc/diagnosticsServer.ts` | TCP IPC server; emits `onMcpSubprocessStarted` / `onMcpSubprocessStopped`; registers socket close handler in `handleMcpStarted` |
| `tools/ck3lens-explorer/src/extension.ts` | Wires IPC events to `agentProvider.setMcpServerStatus()` |
| `tools/ck3lens-explorer/src/views/agentView.ts` | Owns `state.mcpServer`; renders status badge; `checkMcpConfiguration()` is belt-and-suspenders only |
| `tools/ck3lens_mcp/server.py` | Python MCP subprocess; sends `mcpStarted` IPC on startup; holds TCP connection open |
