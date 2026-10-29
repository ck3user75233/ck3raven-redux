"""Small real MCP server for transport tests; does not load project data."""

import os

import anyio
from mcp.server.fastmcp import FastMCP

from ck3lens.http_transport import run_http_server, start_owner_monitor
from ck3lens.stdio_transport import guard_completed_responses

mcp = FastMCP("http-lifecycle-test")
counter = 0


@mcp.tool()
def count() -> dict[str, int]:
    global counter
    counter += 1
    return {"pid": os.getpid(), "count": counter}


@mcp.tool()
async def slow_count() -> dict[str, int]:
    await anyio.sleep(0.5)
    return count()


if __name__ == "__main__":
    start_owner_monitor()
    guard_completed_responses(mcp._mcp_server, lambda _event, _data: None)
    anyio.run(
        run_http_server, mcp, os.environ["CK3LENS_HTTP_TOKEN"],
        os.environ["CK3LENS_INSTANCE_ID"],
    )
