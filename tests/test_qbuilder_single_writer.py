"""QBuilder daemon mutation-lane regression tests."""
from __future__ import annotations

import json
import socket
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

_MCP_ROOT = Path(__file__).resolve().parent.parent / "tools" / "ck3lens_mcp"
if str(_MCP_ROOT) not in sys.path:
    sys.path.insert(0, str(_MCP_ROOT))


def test_new_cvid_is_compact_opaque_identity() -> None:
    from qbuilder.identity import new_cvid

    prefix, *groups = new_cvid().split("-")
    alphabet = set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")

    assert prefix == "CVID"
    assert len(groups) == 2
    assert all(len(group) == 4 for group in groups)
    assert all(set(group) <= alphabet for group in groups)


def test_discovery_runs_before_build_claim(monkeypatch) -> None:
    from qbuilder import worker as worker_module

    order: list[str] = []
    lock_held = False

    class RecordingLock:
        def __enter__(self):
            nonlocal lock_held
            lock_held = True
            order.append("lock_enter")

        def __exit__(self, exc_type, exc_value, traceback):
            nonlocal lock_held
            lock_held = False
            order.append("lock_exit")

    class FakeBuildWorker:
        def __init__(self, conn) -> None:
            self.conn = conn

        def claim_work(self):
            assert lock_held
            order.append("claim")
            return None

    def process_discovery() -> int:
        assert lock_held
        order.append("discovery")
        return 4

    monkeypatch.setattr(worker_module, "BuildWorker", FakeBuildWorker)

    result = worker_module.run_build_worker(
        object(),
        continuous=False,
        discovery_callback=process_discovery,
        mutation_lock=RecordingLock(),
    )

    assert order == ["lock_enter", "discovery", "claim", "lock_exit"]
    assert result == {"items_processed": 0, "completed": 0, "errors": 0}


def test_ipc_start_creates_only_accept_thread(monkeypatch) -> None:
    from qbuilder import ipc_server as ipc_module

    targets: list[object] = []

    class FakeThread:
        def __init__(self, *, target, args=(), daemon=None, name=None) -> None:
            targets.append(target)

        def start(self) -> None:
            pass

        def join(self, timeout=None) -> None:
            pass

    monkeypatch.setattr(ipc_module.threading, "Thread", FakeThread)

    conn = sqlite3.connect(":memory:")
    server = ipc_module.DaemonIPCServer(conn, port=0)
    try:
        server.start()
        assert targets == [server._accept_loop]
    finally:
        server.stop()
        conn.close()


def test_ipc_server_reads_back_os_assigned_port() -> None:
    from qbuilder.ipc_server import DaemonIPCServer

    conn = sqlite3.connect(":memory:")
    server = DaemonIPCServer(conn, port=0)
    try:
        server.start()
        assert server.port > 0
        with socket.create_connection(("127.0.0.1", server.port), timeout=1.0):
            pass
    finally:
        server.stop()
        conn.close()


def test_endpoint_round_trip_and_owner_matched_cleanup(tmp_path: Path) -> None:
    from qbuilder.endpoint import EndpointInfo, clear_endpoint, load_endpoint, publish_endpoint

    endpoint_path = tmp_path / "qbuilder_endpoint.json"
    endpoint = EndpointInfo(
        host="127.0.0.1",
        port=43210,
        pid=1234,
        run_id="daemon-test",
        db_path=str(tmp_path / "test.db"),
    )

    publish_endpoint(endpoint, endpoint_path)
    loaded = load_endpoint(endpoint_path)
    assert loaded is not None
    assert loaded.host == endpoint.host
    assert loaded.port == endpoint.port
    assert loaded.pid == endpoint.pid
    assert loaded.run_id == endpoint.run_id
    assert loaded.db_path == endpoint.db_path
    assert loaded.published_at > 0

    assert not clear_endpoint(run_id="wrong", pid=endpoint.pid, path=endpoint_path)
    assert not clear_endpoint(run_id=endpoint.run_id, pid=9999, path=endpoint_path)
    assert endpoint_path.exists()
    assert clear_endpoint(run_id=endpoint.run_id, pid=endpoint.pid, path=endpoint_path)
    assert not endpoint_path.exists()


def test_daemon_client_refreshes_shared_endpoint(tmp_path: Path, monkeypatch) -> None:
    import qbuilder.endpoint as endpoint_module
    from ck3lens.daemon_client import DaemonClient
    from qbuilder.endpoint import EndpointInfo, publish_endpoint

    endpoint_path = tmp_path / "qbuilder_endpoint.json"
    monkeypatch.setattr(endpoint_module, "ENDPOINT_PATH", endpoint_path)
    monkeypatch.delenv("CK3RAVEN_IPC_PORT", raising=False)
    original_instance = DaemonClient._instance
    try:
        DaemonClient._instance = None
        client = DaemonClient()
        first = EndpointInfo(
            host="127.0.0.1",
            port=41001,
            pid=1001,
            run_id="daemon-first",
            db_path=str(tmp_path / "test.db"),
        )
        second = EndpointInfo(
            host="127.0.0.1",
            port=41002,
            pid=1002,
            run_id="daemon-second",
            db_path=str(tmp_path / "test.db"),
        )

        publish_endpoint(first, endpoint_path)
        first_host, first_port, loaded_first = client._resolve_address()
        assert (first_host, first_port) == (first.host, first.port)
        assert loaded_first is not None and loaded_first.run_id == first.run_id

        publish_endpoint(second, endpoint_path)
        _, cached_port, cached_endpoint = client._resolve_address()
        assert cached_port == first.port
        assert cached_endpoint is not None and cached_endpoint.run_id == first.run_id
        second_host, second_port, loaded_second = client._resolve_address(force_refresh=True)
        assert (second_host, second_port) == (second.host, second.port)
        assert loaded_second is not None and loaded_second.run_id == second.run_id
    finally:
        DaemonClient._instance = original_instance


def test_daemon_client_port_override_beats_shared_endpoint(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import qbuilder.endpoint as endpoint_module
    from ck3lens.daemon_client import DaemonClient
    from qbuilder.endpoint import EndpointInfo, publish_endpoint

    endpoint_path = tmp_path / "qbuilder_endpoint.json"
    monkeypatch.setattr(endpoint_module, "ENDPOINT_PATH", endpoint_path)
    publish_endpoint(EndpointInfo(
        host="127.0.0.1",
        port=41001,
        pid=1001,
        run_id="daemon-shared",
        db_path=str(tmp_path / "test.db"),
    ), endpoint_path)
    monkeypatch.setenv("CK3RAVEN_IPC_PORT", "42000")
    original_instance = DaemonClient._instance
    try:
        DaemonClient._instance = None
        client = DaemonClient()
        assert client._resolve_address(force_refresh=True) == ("127.0.0.1", 42000, None)
    finally:
        DaemonClient._instance = original_instance


def test_queue_status_preserves_validated_daemon_identity(monkeypatch) -> None:
    from ck3lens.daemon_client import DaemonClient, DaemonStatus

    client = object.__new__(DaemonClient)
    monkeypatch.setattr(client, "health", lambda: DaemonStatus(
        daemon_pid=1234,
        db_path="test.db",
        state="idle",
        queue_pending=0,
        queue_leased=0,
        queue_failed=2,
        run_id="daemon-test",
        ipc_port=43210,
        recent_activity={"state": "idle"},
        active_clients=[{"client_id": "test-window"}],
    ))

    status = client.get_queue_status()

    assert status["daemon_pid"] == 1234
    assert status["run_id"] == "daemon-test"
    assert status["ipc_port"] == 43210


def test_failed_child_is_killed_and_reaped_after_terminate_timeout() -> None:
    from ck3lens.daemon_lifecycle import terminate_and_reap

    events: list[object] = []

    class FakeProcess:
        returncode = None

        def poll(self):
            events.append("poll")
            return None

        def terminate(self) -> None:
            events.append("terminate")

        def kill(self) -> None:
            events.append("kill")

        def wait(self, timeout=None) -> int:
            events.append(("wait", timeout))
            if timeout == 0.25:
                raise subprocess.TimeoutExpired(cmd="qbuilder", timeout=timeout)
            return 9

    returncode = terminate_and_reap(
        FakeProcess(),
        terminate_timeout=0.25,
        kill_timeout=0.5,
    )

    assert returncode == 9
    assert events == [
        "poll",
        "terminate",
        ("wait", 0.25),
        "kill",
        ("wait", 0.5),
    ]


def test_database_mutation_requires_current_process_writer(tmp_path: Path) -> None:
    from qbuilder.writer_lock import (
        WriterLock,
        WriterLockError,
        require_current_process_writer,
    )

    db_path = tmp_path / "test.db"

    with pytest.raises(WriterLockError):
        require_current_process_writer(db_path)

    with WriterLock(db_path):
        require_current_process_writer(db_path)

    with pytest.raises(WriterLockError):
        require_current_process_writer(db_path)


def test_rw_connection_factories_require_writer_lock(tmp_path: Path) -> None:
    from qbuilder import api as api_module
    from qbuilder.ipc_server import DaemonIPCServer
    from qbuilder.writer_lock import WriterLock, WriterLockError

    db_path = tmp_path / "test.db"

    with pytest.raises(WriterLockError):
        api_module.get_connection(db_path)

    server = DaemonIPCServer(sqlite3.connect(":memory:"), db_path=db_path)
    try:
        with pytest.raises(WriterLockError):
            server._get_handler_write_conn()
    finally:
        server.conn.close()

    with WriterLock(db_path):
        api_conn = api_module.get_connection(db_path)
        api_conn.close()
        ipc_conn = server._get_handler_write_conn()
        ipc_conn.close()


def test_build_processing_uses_mutation_lock(monkeypatch) -> None:
    from qbuilder import worker as worker_module

    order: list[str] = []
    lock_held = False

    class RecordingLock:
        def __enter__(self):
            nonlocal lock_held
            lock_held = True
            order.append("lock_enter")

        def __exit__(self, exc_type, exc_value, traceback):
            nonlocal lock_held
            lock_held = False
            order.append("lock_exit")

    class FakeExecutor:
        envelope_steps: dict[str, list[str]] = {}

    class FakeBuildWorker:
        def __init__(self, conn) -> None:
            self.executor = FakeExecutor()

        def claim_work(self):
            assert lock_held
            order.append("claim")
            return {"relpath": "common/test.txt", "envelope": "TEST", "file_id": 1}

        def process_item(self, item):
            assert lock_held
            order.append("process")
            return {"status": "completed"}

    monkeypatch.setattr(worker_module, "BuildWorker", FakeBuildWorker)

    result = worker_module.run_build_worker(
        object(),
        max_items=1,
        continuous=False,
        mutation_lock=RecordingLock(),
    )

    assert order == [
        "lock_enter", "claim", "lock_exit",
        "lock_enter", "process", "lock_exit",
    ]
    assert result == {"items_processed": 1, "completed": 1, "errors": 0}


def test_ipc_mutation_dispatch_uses_mutation_lock() -> None:
    from qbuilder.ipc_server import DaemonIPCServer

    lock_held = False

    class RecordingLock:
        def __enter__(self):
            nonlocal lock_held
            lock_held = True

        def __exit__(self, exc_type, exc_value, traceback):
            nonlocal lock_held
            lock_held = False

    conn = sqlite3.connect(":memory:")
    server = DaemonIPCServer(conn, mutation_lock=RecordingLock())

    def handler(request):
        assert lock_held
        return {"locked": True}

    server._handlers["enqueue_files"] = handler
    response = server._process_request(
        '{"v":1,"id":"test","method":"enqueue_files","params":{}}'
    )

    assert '"ok": true' in response
    assert '"locked": true' in response
    conn.close()


def test_readonly_connection_rejects_writes(tmp_path: Path) -> None:
    from qbuilder import api as api_module

    db_path = tmp_path / "test.db"
    sqlite3.connect(db_path).close()

    conn = api_module.get_readonly_connection(db_path)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("CREATE TABLE forbidden (id INTEGER)")
    finally:
        conn.close()


def test_background_builder_launcher_is_retired() -> None:
    from qbuilder import api as api_module

    with pytest.raises(RuntimeError, match="retired"):
        api_module.start_background_build()


def test_legacy_mutating_cli_command_is_not_registered(monkeypatch) -> None:
    from qbuilder import cli as cli_module

    monkeypatch.setattr(sys, "argv", ["qbuilder", "build"])
    with pytest.raises(SystemExit) as exc_info:
        cli_module.main()

    assert exc_info.value.code == 2


def test_shutdown_waits_for_last_extension_client(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from qbuilder import schema as schema_module
    from qbuilder.ipc_server import DaemonIPCServer

    monkeypatch.setattr(schema_module, "get_queue_counts", lambda conn: {
        "build": {"pending": 0, "processing": 0, "completed": 0, "error": 0},
        "discovery": {"pending": 0, "processing": 0, "completed": 0, "error": 0},
    })
    shutdowns: list[str] = []
    db_path = tmp_path / "test.db"
    sqlite3.connect(db_path).close()
    conn = sqlite3.connect(":memory:")
    server = DaemonIPCServer(
        conn,
        db_path=db_path,
        shutdown_callback=lambda: shutdowns.append("shutdown"),
    )

    def request(method: str, client_id: str) -> dict:
        response = server._process_request(json.dumps({
            "v": 1,
            "id": f"{method}-{client_id}",
            "method": method,
            "params": {
                "client_id": client_id,
                "client_pid": 1234,
                "client_name": f"ck3lens-{client_id}",
            },
        }))
        return json.loads(response)

    try:
        request("unknown", "extension:first")
        request("unknown", "extension:second")

        health = request("health", "extension:first")
        assert health["result"]["clients"]["count"] == 2
        assert {
            client["client_id"] for client in health["result"]["clients"]["active"]
        } == {"extension:first", "extension:second"}

        unregistered_first = request("unregister_client", "extension:first")
        assert unregistered_first["result"]["unregistered"] is True
        assert unregistered_first["result"]["remaining_clients"] == 1

        denied = request("shutdown", "extension:first")
        assert denied["ok"] is True
        assert denied["result"]["acknowledged"] is False
        assert denied["result"]["denied_reason"] == "other_clients_active"
        assert [
            client["client_id"] for client in denied["result"]["active_clients"]
        ] == ["extension:second"]
        assert shutdowns == []

        health_after_denial = request("health", "extension:second")
        assert {
            client["client_id"]
            for client in health_after_denial["result"]["clients"]["active"]
        } == {"extension:second"}

        unregistered = request("unregister_client", "extension:second")
        assert unregistered["result"]["unregistered"] is True
        assert unregistered["result"]["remaining_clients"] == 0

        acknowledged = request("shutdown", "extension:first")
        assert acknowledged["result"]["acknowledged"] is True
        assert acknowledged["result"]["active_clients"] == []
        assert shutdowns == ["shutdown"]
    finally:
        conn.close()
