"""Loopback MCP transport owned by the extension, not by an MCP client."""

import hmac
import json
import os
import socket
import sys
import threading

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

READY_PREFIX = "CK3LENS_HTTP_READY "
TOKEN_HEADER = "x-ck3lens-token"


def start_owner_monitor() -> None:
    def monitor() -> None:
        while sys.stdin.buffer.read(1):
            pass
        os._exit(0)

    threading.Thread(target=monitor, daemon=True, name="mcp-owner-pipe").start()


class TransportAuthentication:
    def __init__(self, app: ASGIApp, token: str):
        if not token:
            raise ValueError("An HTTP transport token is required")
        self.app = app
        self.token = token.encode("ascii")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = dict(scope["headers"])
            supplied = headers.get(TOKEN_HEADER.encode("ascii"), b"")
            if not hmac.compare_digest(supplied, self.token):
                await Response(status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def create_http_app(mcp: FastMCP, token: str, instance_id: str) -> ASGIApp:
    async def health(_request: Request) -> JSONResponse:
        return JSONResponse({"instance_id": instance_id, "pid": os.getpid()})

    app = mcp.streamable_http_app()
    app.add_route("/health", health, methods=["GET"])
    return TransportAuthentication(app, token)


async def run_http_server(mcp: FastMCP, token: str, instance_id: str) -> None:
    app = create_http_app(mcp, token, instance_id)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        if os.name == "nt":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]

        class OwnedServer(uvicorn.Server):
            async def startup(self, sockets: list[socket.socket] | None = None) -> None:
                await super().startup(sockets=sockets)
                if self.started:
                    print(
                        READY_PREFIX + json.dumps({
                            "instance_id": instance_id,
                            "pid": os.getpid(),
                            "url": f"http://127.0.0.1:{port}/mcp",
                        }),
                        flush=True,
                    )

        server = OwnedServer(uvicorn.Config(
            app, log_level="warning", access_log=False, timeout_graceful_shutdown=5,
        ))
        await server.serve(sockets=[listener])
