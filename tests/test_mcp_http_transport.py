"""Owned HTTP transport regressions. Never start the production MCP or QBuilder."""

import asyncio
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
from collections import deque

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.fastmcp import FastMCP
from starlette.testclient import TestClient

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools" / "ck3lens_mcp"))

from ck3lens.http_transport import READY_PREFIX, create_http_app


def test_http_requires_nonempty_token() -> None:
    with pytest.raises(ValueError, match="token"):
        create_http_app(FastMCP("test"), "", "test-instance")


def test_http_authentication_applies_to_health_and_tools() -> None:
    app = create_http_app(FastMCP("test"), "secret", "test-instance")
    with TestClient(app) as client:
        for path in ("/health", "/mcp"):
            assert client.get(path).status_code == 401
            assert client.get(path, headers={"x-ck3lens-token": "wrong"}).status_code == 401
        response = client.get("/health", headers={"x-ck3lens-token": "secret"})
        assert response.json() == {"instance_id": "test-instance", "pid": os.getpid()}


@pytest.fixture
def owned_http():
    processes = []

    def start(instance: str, token: str):
        env = {
            **os.environ,
            "PYTHONPATH": str(REPO / "tools" / "ck3lens_mcp"),
            "CK3LENS_INSTANCE_ID": instance,
            "CK3LENS_HTTP_TOKEN": token,
        }
        process = subprocess.Popen(
            [sys.executable, str(REPO / "tests" / "fixtures" / "mcp_http_fixture.py")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=env,
        )
        processes.append(process)
        messages = queue.Queue()
        errors: deque[str] = deque(maxlen=30)

        def drain_errors():
            for line in process.stderr:
                errors.append(line.rstrip())

        def read_ready():
            for line in process.stdout:
                if line.startswith(READY_PREFIX):
                    messages.put(json.loads(line.removeprefix(READY_PREFIX)))
                    return

        threading.Thread(target=read_ready, daemon=True).start()
        threading.Thread(target=drain_errors, daemon=True).start()
        try:
            ready = messages.get(timeout=30)
        except queue.Empty:
            process.kill()
            process.wait(timeout=10)
            pytest.fail(f"HTTP fixture did not become ready: {list(errors)}")
        return process, ready

    yield start
    for process in processes:
        if process.stdin and not process.stdin.closed:
            process.stdin.close()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
            pytest.fail("HTTP fixture did not exit after owner EOF")
        for stream in (process.stdout, process.stderr):
            stream.close()


async def call_counter(url: str, token: str) -> dict:
    async with httpx.AsyncClient(headers={"x-ck3lens-token": token}) as http:
        async with streamable_http_client(url, http_client=http) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("count")
                assert not result.isError
                return json.loads(result.content[0].text)


def test_client_disconnect_and_reconnect_preserves_process_and_state(owned_http) -> None:
    process, ready = owned_http("window-one", "one-token")
    first = asyncio.run(call_counter(ready["url"], "one-token"))
    assert process.poll() is None
    second = asyncio.run(call_counter(ready["url"], "one-token"))
    assert first == {"pid": ready["pid"], "count": 1}
    assert second == {"pid": ready["pid"], "count": 2}
    assert process.poll() is None
    process.stdin.close()
    assert process.wait(timeout=10) == 0


def test_windows_are_isolated_and_cross_window_token_is_rejected(owned_http) -> None:
    _, first = owned_http("window-one", "one-token")
    _, second = owned_http("window-two", "two-token")
    assert first["url"] != second["url"]
    assert first["pid"] != second["pid"]
    health_url = second["url"].removesuffix("/mcp") + "/health"
    response = httpx.get(health_url, headers={"x-ck3lens-token": "one-token"})
    assert response.status_code == 401
    assert asyncio.run(call_counter(first["url"], "one-token"))["count"] == 1
    assert asyncio.run(call_counter(second["url"], "two-token"))["count"] == 1


def test_disconnect_during_tool_execution_does_not_end_owned_server(owned_http) -> None:
    process, ready = owned_http("window-one", "one-token")

    async def cancel_request():
        async with httpx.AsyncClient(headers={"x-ck3lens-token": "one-token"}) as http:
            async with streamable_http_client(ready["url"], http_client=http) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    with pytest.raises(TimeoutError):
                        await asyncio.wait_for(session.call_tool("slow_count"), timeout=0.1)
        await asyncio.sleep(0.6)

    asyncio.run(cancel_request())
    assert process.poll() is None
    result = asyncio.run(call_counter(ready["url"], "one-token"))
    assert result["pid"] == ready["pid"]


def test_http_rejects_foreign_host_and_origin(owned_http) -> None:
    _, ready = owned_http("window-one", "one-token")
    for headers in (
        {"Host": "attacker.invalid"},
        {"Origin": "https://attacker.invalid"},
    ):
        response = httpx.post(
            ready["url"], headers={"x-ck3lens-token": "one-token", **headers}, json={},
        )
        assert response.status_code in (400, 403, 421)


def test_extension_lifecycle_and_definition_regressions(tmp_path: Path) -> None:
    extension = REPO / "tools" / "ck3lens-explorer"
    esbuild = extension / "node_modules" / "esbuild" / "bin" / "esbuild"
    entries = [
        extension / "src" / "mcp" / name
        for name in ("mcpLifecycle.ts", "mcpProcess.ts", "mcpServerProvider.ts")
    ]
    entries.append(extension / "src" / "ipc" / "diagnosticsServer.ts")
    subprocess.run(
        ["node", str(esbuild), *map(str, entries), "--bundle", "--platform=node",
         "--format=cjs", "--external:vscode", f"--outdir={tmp_path}"],
        check=True, capture_output=True, text=True,
    )
    env = {
        **os.environ,
        "MCP_TEST_BUILD": str(tmp_path),
        "MCP_TEST_PYTHON": sys.executable,
        "MCP_TEST_REPO": str(REPO),
    }
    result = subprocess.run(
        ["node", "--test", str(extension / "tests" / "mcpLifecycle.test.cjs")],
        env=env, capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
