# CK3 Raven Dev Mode — Agent Instructions

**Mode:** `ck3raven-dev` | **Purpose:** Develop and maintain the ck3raven toolchain — a Python-based parser, resolver, SQLite database, MCP server, and VS Code extension that together power CK3 Lens for AI-assisted CK3 modding.

---

## Your Role

Autonomous development agent. When given a task: Analyze → Plan → **Write code** → Validate → Commit.

---

## Write Access (Canonical Domains)

Mutations require a contract (`ck3_contract`), except writes to WIP (`root:ck3raven_data/wip/`).

| Domain | Root Key | Access | Notes |
|--------|----------|--------|-------|
| ck3raven source | `repo` | Read/Write/Delete | All directories |
| Data directory | `ck3raven_data` | Mixed | `wip/`, `config/`, `playsets/`, `logs/`, `journal/`, `cache/`, `artifacts/` are writable; `db/` and `daemon/` are read-only (owned by QBuilder daemon) |
| Vanilla CK3 | `game` | Read-only | For testing/reference |
| Steam Workshop | `steam` | Read-only | For testing/reference |
| User mod folder | `user_docs` | Read-only | **No writes in dev mode** |

> **ROOT_* vs root: — these are different things.**
> The internal constants (`ROOT_REPO`, `ROOT_CK3RAVEN_DATA`, etc.) are **host-absolute path constants** loaded from `workspace.toml` config at import time (see `ck3lens/paths.py`). `ROOT_REPO` is the workspace directory ck3raven runs from — it is NOT a Python package path (it's config-based because `__file__` computation breaks in pip-installed venvs). These constants are used internally by `capability_matrix.py` and `world_adapter.py`. They are NOT MCP tool parameters.
> 
> Canonical addresses (`root:repo/`, `mod:MyMod/`) are **session-absolute schemes** for MCP tool parameters. They are resolved to host-absolute paths by WA2 internally. All tools now support canonical addressing.

### Root Key Reference

| Key | Resolves to | Example canonical address |
|-----|-------------|--------------------------|
| `repo` | ck3raven workspace root | `root:repo/tools/ck3lens_mcp/server.py` |
| `ck3raven_data` | `~/.ck3raven/` | `root:ck3raven_data/wip/my_script.py` |
| `game` | Vanilla CK3 install | `root:game/common/defines/00_defines.txt` |
| `steam` | Steam Workshop | `root:steam/2820384801/...` |
| `user_docs` | User mod folder | `root:user_docs/mod/My Mod/descriptor.mod` |

📖 Full matrix: `tools/ck3lens_mcp/ck3lens/capability_matrix.py`

---

## Tools

| Tool | Purpose |
|------|---------|
| `ck3_file` | Read/write/edit source files |
| `ck3_exec` | Run shell commands (policy-enforced) |
| `ck3_search` | Search mod database (symbols, content, files) |
| `ck3_db` | Database management (status, query, delete, help) |
| `ck3_qbuilder` | Build daemon control (status, build, discover, stop) |
| `ck3_validate` | Syntax validation (CK3 script and Python) |
| `ck3_vscode` | VS Code/Pylance diagnostics |
| `ck3_contract` | Open/close work contracts for mutations |
| `ck3_logs` | ck3raven component logs (MCP, extension, daemon) |
| `ck3_parse_content` | Parse CK3 script → AST |

### Script Execution (ck3_exec)

Two paths:

**Whitelisted commands** (no contract needed): `python -m pytest tests/`, etc. Raw Git through `ck3_exec` is **closed** (`git ...` is no longer whitelisted; the legacy `ck3_git` tool was retired at commit `72b0607`).

**Custom scripts** (requires script signing):
1. Write script to WIP via `ck3_file`
2. Before **every** `sign_script` request, use the VS Code question tool to ask:
   "Are you at the workstation and ready to review and approve a sign_script request? Y/N"
   Wait for an explicit Y. If N or unanswered, do not submit the request.
3. `ck3_contract(command="sign_script", script_path="root:ck3raven_data/wip/my_script.py")`
4. Human reviews + approves in VS Code (CodeLens button). The synchronous approval
   wait is at most 180 seconds; the client may time out sooner. Do not automatically
   retry a timed-out request or submit another while the first may still be pending.
5. `ck3_contract(command="open", script_signature=..., script_path=...)`
6. `ck3_exec("python <path>/my_script.py")`

---

## Validation

Code is **not complete** until:
1. Pylance diagnostics clean (`ck3_vscode(command="diagnostics")` or VS Code's `get_errors`)
2. Changes are committed through your normal local Git workflow (the `ck3_git` tool was retired at commit `72b0607`; no ordinary MCP Git capability currently exists)

---

## Architecture

```
src/ck3raven/
├── parser/      # Lexer + parser (100% regex-free, error-recovering)
├── resolver/    # Conflict resolution and merge policy engine
├── db/          # SQLite schema, FTS5 search, symbol/ref storage
├── analyzers/   # Error log parsing, log analysis
├── core/        # Shared utilities
├── logs/        # Log management
├── emulator/    # (Future) Game state building
└── tools/       # Internal tooling

qbuilder/              # Build daemon (single-writer architecture)
tools/ck3lens_mcp/     # MCP server (28+ tools exposed to Copilot)
tools/ck3lens-explorer/ # VS Code extension (sidebar UI, MCP provider)
tools/compliance/      # Contracts, tokens, semantic validator
```

**When writing code that imports ck3raven internals:**
```python
from ck3raven.parser import parse_file, parse_source
from ck3raven.db.schema import get_connection
from ck3raven.db.search import search_symbols
```

---

## Key Documents

| Document | Purpose |
|----------|---------|
| `docs/CANONICAL_ARCHITECTURE.md` | **5 rules every agent must follow** |
| `docs/SINGLE_WRITER_ARCHITECTURE.md` | QBuilder daemon design |
| `docs/05_ACCURATE_MERGE_OVERRIDE_RULES.md` | CK3 engine merge behavior reference |
| `docs/06_CONTAINER_MERGE_OVERRIDE_TABLE.md` | Per-content-type merge policy |

---

## Wrong Mode?

If editing CK3 mod files (.txt, .yml) → switch to `ck3lens` mode.

---

## After a Window Reload

If `ck3_ping` fails with "tool does not exist", the instance ID has rotated. **Do not start a new conversation.** Instead:
1. If the user's message contains an instance ID (e.g. `[zlah-b28716]`), use it directly — construct the prefix using only the first 4-char hex segment: `mcp_ck3lens-{first4}-_` (e.g., `[zlah-b28716]` → `mcp_ck3lens-zlah-_`, `[mh68-4f1adb]` → `mcp_ck3lens-mh68-_`). Call `ck3_ping` with that prefix. **User-provided IDs take priority.**
2. If no ID was provided (or the user-provided one fails twice), call `tool_search("ck3 ping mode instructions")` as a last resort to rediscover the live registry.
3. Call `ck3_get_mode_instructions(mode="ck3raven-dev")` with a fresh hat_token from the sidebar.
