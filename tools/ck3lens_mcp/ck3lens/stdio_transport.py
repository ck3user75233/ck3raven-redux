"""CK3 Lens stdio transport diagnostics.

This mirrors the MCP SDK stdio transport while handling receiver-close races
that the SDK currently lets escape from its task group.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from contextlib import asynccontextmanager
from functools import wraps
from io import TextIOWrapper
from typing import Any, TypeVar

import anyio
import anyio.lowlevel
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.session import ServerSession
from mcp.shared.message import SessionMessage
from mcp.shared.session import RequestResponder

TransportLogger = Callable[[str, dict[str, object]], None]
LifespanT = TypeVar("LifespanT")
RequestT = TypeVar("RequestT")


def guard_completed_responses(
    server: Server[LifespanT, RequestT],
    log_warning: TransportLogger,
) -> None:
    """Contain the SDK's completed-responder assertion on this server instance."""
    handle_request = server._handle_request
    respond_code = RequestResponder.respond.__code__

    @wraps(handle_request)
    async def handle(
        message: RequestResponder[types.ClientRequest, types.ServerResult],
        req: types.ClientRequestType,
        session: ServerSession,
        lifespan_context: LifespanT,
        raise_exceptions: bool,
    ) -> None:
        try:
            await handle_request(message, req, session, lifespan_context, raise_exceptions)
        except AssertionError as error:
            origin = error.__traceback__
            while origin is not None and origin.tb_next is not None:
                origin = origin.tb_next
            if (
                str(error) != "Request already responded to"
                or origin is None
                or origin.tb_frame.f_code is not respond_code
                or origin.tb_frame.f_locals.get("self") is not message
                or message.in_flight
            ):
                raise
            log_warning(
                "duplicate_response_discarded",
                {"request_id": str(message.request_id), "method": req.method},
            )

    # FastMCP has no public response-error hook; do not modify SDK classes globally.
    server._handle_request = handle


def _emit(log_event: TransportLogger | None, event: str, data: dict[str, object]) -> None:
    if log_event is None:
        return
    try:
        log_event(event, data)
    except Exception:
        pass


def _message_summary(message: types.JSONRPCMessage) -> dict[str, object]:
    root = message.root
    summary: dict[str, object] = {"message_type": type(root).__name__}
    method = getattr(root, "method", None)
    request_id = getattr(root, "id", None)
    if method is not None:
        summary["method"] = str(method)
    if request_id is not None:
        summary["request_id"] = str(request_id)
    if isinstance(root, (types.JSONRPCRequest, types.JSONRPCNotification)):
        params = root.params or {}
        related_request_id = params.get("requestId")
        if isinstance(related_request_id, (str, int)):
            summary["related_request_id"] = str(related_request_id)
        if method == "tools/call" and isinstance(params.get("name"), str):
            summary["tool_name"] = params["name"]
    if isinstance(root, types.JSONRPCError):
        summary["error_code"] = root.error.code
    return summary


@asynccontextmanager
async def diagnostic_stdio_server(
    stdin: Any | None = None,
    stdout: Any | None = None,
    *,
    log_event: TransportLogger | None = None,
):
    """Provide SDK-compatible stdio streams with closure-race diagnostics."""
    if stdin is None:
        stdin = anyio.wrap_file(TextIOWrapper(sys.stdin.buffer, encoding="utf-8"))
    if stdout is None:
        stdout = anyio.wrap_file(TextIOWrapper(sys.stdout.buffer, encoding="utf-8"))

    read_stream_writer: MemoryObjectSendStream[SessionMessage | Exception]
    read_stream: MemoryObjectReceiveStream[SessionMessage | Exception]
    write_stream: MemoryObjectSendStream[SessionMessage]
    write_stream_reader: MemoryObjectReceiveStream[SessionMessage]

    read_stream_writer, read_stream = anyio.create_memory_object_stream(0)
    write_stream, write_stream_reader = anyio.create_memory_object_stream(0)

    async def stdin_reader() -> None:
        try:
            async with read_stream_writer:
                async for line in stdin:
                    try:
                        message = types.JSONRPCMessage.model_validate_json(line)
                    except Exception as exc:
                        await read_stream_writer.send(exc)
                        continue

                    _emit(log_event, "message_received", _message_summary(message))
                    try:
                        await read_stream_writer.send(SessionMessage(message))
                    except anyio.BrokenResourceError:
                        _emit(
                            log_event,
                            "input_receiver_closed",
                            _message_summary(message),
                        )
                        await anyio.lowlevel.checkpoint()
                        return
            _emit(log_event, "stdin_eof", {})
        except (anyio.ClosedResourceError, anyio.BrokenResourceError):
            _emit(log_event, "input_stream_closed", {})
            await anyio.lowlevel.checkpoint()

    async def stdout_writer() -> None:
        try:
            async with write_stream_reader:
                async for session_message in write_stream_reader:
                    payload = session_message.message.model_dump_json(
                        by_alias=True,
                        exclude_none=True,
                    )
                    await stdout.write(payload + "\n")
                    await stdout.flush()
                    _emit(
                        log_event,
                        "message_sent",
                        _message_summary(session_message.message),
                    )
        except (anyio.ClosedResourceError, anyio.BrokenResourceError):
            _emit(log_event, "output_stream_closed", {})
            await anyio.lowlevel.checkpoint()

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(stdin_reader)
        task_group.start_soon(stdout_writer)
        yield read_stream, write_stream
