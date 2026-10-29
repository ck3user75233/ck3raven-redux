"""
QBuilder CLI — Canonical Phase 1

Commands:
    daemon    Start the single-writer daemon (IPC server + build worker)
    status    Show queue status through a read-only connection
    stop      Request graceful daemon shutdown through IPC

SINGLE-WRITER ARCHITECTURE (January 2026):
    The `daemon` command is the ONLY process that writes to the database.
    MCP servers are read-only and communicate with the daemon via IPC.
    See docs/SINGLE_WRITER_ARCHITECTURE.md for details.
"""

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path
from ck3lens.paths import ROOT_CK3RAVEN_DATA

from .schema import init_qbuilder_schema, get_queue_counts
from ck3raven.db.schema import (
    DATABASE_VERSION,
    IncompatibleDatabaseError,
    cleanup_expired_sessions,
    get_schema_version,
    init_database,
)
from .discovery import playset_scan, run_discovery
from .worker import run_build_worker


def get_db_path() -> Path:
    """Get database path."""
    return ROOT_CK3RAVEN_DATA / 'ck3raven.db'


def get_connection(auto_init: bool = True) -> sqlite3.Connection:
    """
    Get database connection, auto-initializing schema if needed.
    
    Args:
        auto_init: If True, create database and schema if missing
    
    Returns:
        Database connection with row_factory set
    """
    db_path = get_db_path()

    from .writer_lock import require_current_process_writer
    require_current_process_writer(db_path)
    
    # Check if database exists
    db_exists = db_path.exists()
    
    if not db_exists:
        if not auto_init:
            print(f"Error: Database not found at {db_path}")
            sys.exit(1)
        
        # Create directory if needed
        db_path.parent.mkdir(parents=True, exist_ok=True)
        print(f"Creating database at {db_path}")
    
    # Connect
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # QBuilder doesn't use file_contents FK — disable to prevent crash
    # when discovery inserts file hashes without file_contents rows.
    # Proper fix: remove stale FK from schema (tracked for DB refactor).
    conn.execute("PRAGMA foreign_keys = OFF")
    
    # Check if schema exists and is current version
    if auto_init:
        schema_version = get_schema_version(conn)
        
        if schema_version is None:
            # No schema at all - initialize
            print("Initializing database schema...")
            conn = init_database(db_path, force=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = OFF")  # Re-disable after init_database
            print(f"[OK] Database initialized (schema v{DATABASE_VERSION})")
        elif schema_version != DATABASE_VERSION:
            raise IncompatibleDatabaseError(
                f"Database schema v{schema_version} is incompatible with v{DATABASE_VERSION}; "
                "restart QBuilder with --fresh"
            )
    
    return conn


def get_readonly_connection() -> sqlite3.Connection:
    """Open the existing database without creating schema or write access."""
    db_path = get_db_path()
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found at {db_path}")
    db_uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(db_uri, uri=True, timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn


def _process_discovery_if_pending(conn: sqlite3.Connection) -> int:
    """Process discovery queue if there are pending items.
    
    Called by the build worker during idle periods to check for
    new discovery tasks that were enqueued via IPC.
    
    Returns:
        Number of files discovered (0 if no discovery tasks)
    """
    counts = get_queue_counts(conn)
    discovery_pending = counts.get('discovery', {}).get('pending', 0)
    
    if discovery_pending > 0:
        result = run_discovery(conn)
        return result.get('files_discovered', 0)
    return 0


def cmd_daemon(args: argparse.Namespace) -> int:
    """
    Start the single-writer daemon.
    
    This is the ONLY process that writes to the database.
    It runs both the IPC server (for client requests) and the build worker.
    
    The daemon:
    1. Acquires the writer lock (fails if another daemon running)
    2. Opens database in read-write mode
    3. Starts IPC server for client requests
    4. Runs build worker loop
    """
    import uuid
    import signal
    
    from .endpoint import EndpointInfo, clear_endpoint, publish_endpoint
    from .writer_lock import WriterLock, WriterLockError
    from .ipc_server import DaemonIPCServer, get_requested_ipc_port
    from .logging import QBuilderLogger
    
    # Reconfigure stdout/stderr to UTF-8 so mod names with non-ASCII characters
    # (e.g. Korean/Chinese/Japanese) don't crash the daemon via cp1252 encoding.
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    
    db_path = get_db_path()
    run_id = f"daemon-{uuid.uuid4().hex[:8]}"
    logger = QBuilderLogger(run_id=run_id)
    
    # Fresh build mode
    if args.fresh:
        print("Fresh build mode: will reset all data")
    
    # Acquire writer lock
    print(f"Acquiring writer lock for {db_path}...")
    lock = WriterLock(db_path)
    
    if not lock.acquire(wait_timeout=5.0, poll_interval=0.1):
        holder_info = lock.get_holder_info()
        if holder_info:
            print(f"[ERROR] Another daemon is already running (PID {holder_info.pid})")
        else:
            print("[ERROR] Could not acquire writer lock after waiting for handoff")
        return 1
    
    daemon_pid = __import__('os').getpid()
    print(f"[OK] Writer lock acquired (PID {daemon_pid})")
    endpoint_published = False
    
    try:
        # Open the database, recreating the schema only on explicit --fresh.
        if args.fresh:
            conn = init_database(db_path, force=True)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = OFF")
        else:
            conn = get_connection()
        init_qbuilder_schema(conn)
        expired_sessions_cleaned = cleanup_expired_sessions(conn)
        if expired_sessions_cleaned:
            print(f"[OK] Cleaned up {expired_sessions_cleaned} expired builder sessions")
        
        # Enqueue discovery for the active playset after schema recreation.
        if args.fresh:
            print("[OK] Schema recreated for fresh build")

            print("Enqueuing discovery for active playset...")
            scan_result = playset_scan(conn)
            print(
                f"[OK] Prepared {scan_result['mods_ready']}/{scan_result['mods_total']} "
                "content identities"
            )
        
        # Start IPC server with shared activity tracker
        port = get_requested_ipc_port()
        
        # Shared shutdown event for coordinated shutdown
        import threading
        shutdown_event = threading.Event()
        mutation_lock = threading.RLock()
        
        def shutdown_callback():
            """Called by IPC server when shutdown command received."""
            shutdown_event.set()
        
        # Create shared activity tracker for IPC status queries
        from qbuilder.ipc_server import RunActivity
        run_activity = RunActivity()
        run_activity.set_run_id(run_id)
        
        ipc_server = DaemonIPCServer(
            conn,
            port=port,
            db_path=db_path,
            shutdown_callback=shutdown_callback,
            run_activity=run_activity,
            mutation_lock=mutation_lock,
        )
        ipc_server.start()
        try:
            publish_endpoint(EndpointInfo(
                host=ipc_server.host,
                port=ipc_server.port,
                pid=daemon_pid,
                run_id=run_id,
                db_path=str(db_path),
            ))
            endpoint_published = True
        except Exception:
            ipc_server.stop()
            raise
        print(f"[OK] IPC server listening on port {ipc_server.port}")
        
        # Thread 3: File System Watcher
        from .watcher import WatcherService
        watcher = WatcherService(conn, mutation_lock=mutation_lock)
        watch_count = watcher.start()
        print(f"[OK] File watcher watching {watch_count} directories")
        
        # Handle shutdown signals
        def handle_signal(signum, frame):
            print(f"\nReceived signal {signum}, shutting down...")
            shutdown_event.set()
        
        signal.signal(signal.SIGINT, handle_signal)
        signal.signal(signal.SIGTERM, handle_signal)
        
        # Log startup
        counts = get_queue_counts(conn)
        pending = counts.get('build', {}).get('pending', 0)
        print(f"\n=== Daemon Started ===")
        print(f"  run_id: {run_id}")
        print(f"  db: {db_path}")
        print(f"  ipc_port: {ipc_server.port}")
        print(f"  pending: {pending}")
        print(f"  poll_interval: {args.poll_interval}s")
        print(f"  log: {logger.log_file}")
        print(f"\nPress Ctrl+C to stop\n")
        
        logger.run_start(total_items=pending)
        start_time = time.time()
        
        # Run discovery and build work sequentially on the daemon's main
        # connection. IPC handlers only enqueue discovery requests.
        result = run_build_worker(
            conn,
            max_items=args.max_items,
            logger=logger,
            continuous=True,
            poll_interval=args.poll_interval,
            shutdown_event=shutdown_event,
            run_activity=run_activity,
            discovery_callback=lambda: _process_discovery_if_pending(conn),
            mutation_lock=mutation_lock,
        )
        
        elapsed = time.time() - start_time
        
        # Shutdown
        print("\nShutting down...")
        watcher.stop()
        ipc_server.stop()
        
        logger.run_complete(
            processed=result['items_processed'],
            errors=result['errors'],
            duration_ms=elapsed * 1000
        )
        
        print(f"\n[OK] Daemon shutdown complete:")
        print(f"  Processed: {result['items_processed']}")
        print(f"  Completed: {result['completed']}")
        print(f"  Errors: {result['errors']}")
        print(f"  Uptime: {elapsed:.1f}s")
        
        return 0
        
    finally:
        try:
            if endpoint_published:
                clear_endpoint(run_id=run_id, pid=daemon_pid)
        finally:
            lock.release()
            print("[OK] Writer lock released")


def cmd_status(args: argparse.Namespace) -> int:
    """Show queue status without acquiring write access."""
    conn = get_readonly_connection()
    
    try:
        counts = get_queue_counts(conn)
        
        print("=== QBuilder Status ===\n")
        
        # Discovery queue
        d = counts.get('discovery', {})
        print("Discovery Queue:")
        print(f"  Pending:    {d.get('pending', 0)}")
        print(f"  Processing: {d.get('processing', 0)}")
        print(f"  Completed:  {d.get('completed', 0)}")
        print(f"  Errors:     {d.get('error', 0)}")
        print(f"  Total:      {counts['discovery_total']}")
        
        # Build queue
        b = counts.get('build', {})
        print("\nBuild Queue:")
        print(f"  Pending:    {b.get('pending', 0)}")
        print(f"  Processing: {b.get('processing', 0)}")
        print(f"  Completed:  {b.get('completed', 0)}")
        print(f"  Errors:     {b.get('error', 0)}")
        print(f"  Total:      {counts['build_total']}")
        
        # Database counts
        print("\nDatabase:")
        for table in ['files', 'asts', 'symbols', 'refs']:
            try:
                row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
                print(f"  {table}: {row[0]:,}")
            except sqlite3.OperationalError:
                print(f"  {table}: (table not found)")
        
        return 0
    finally:
        conn.close()


def cmd_stop(args: argparse.Namespace) -> int:
    """Send shutdown command to running daemon via IPC."""
    import socket
    import json
    from .endpoint import load_endpoint
    from .ipc_server import get_ipc_port
    
    endpoint = load_endpoint()
    port = endpoint.port if endpoint is not None else get_ipc_port()
    timeout = getattr(args, 'timeout', 10)
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5.0)
    
    try:
        sock.connect(("127.0.0.1", port))
        request = {
            "v": 1, 
            "id": "cli-stop-1", 
            "method": "shutdown", 
            "params": {"graceful": True, "timeout": timeout}
        }
        sock.sendall((json.dumps(request) + "\n").encode())
        
        response = sock.recv(4096).decode().strip()
        result = json.loads(response)
        
        if result.get("ok"):
            shutdown_result = result.get("result", {})
            if not shutdown_result.get("acknowledged", False):
                reason = shutdown_result.get("denied_reason", "shutdown_denied")
                print(f"[INFO] Shutdown denied: {reason}")
                return 1
            print("[OK] Shutdown signal sent to daemon")
            if args.wait:
                print("Waiting for daemon to exit...")
                _wait_for_daemon_exit(port, timeout)
        else:
            error = result.get("error", {})
            print(f"[ERROR] {error.get('message', 'Unknown error')}")
            return 1
            
    except ConnectionRefusedError:
        print("[INFO] Daemon not running (connection refused)")
        return 0
    except socket.timeout:
        print("[WARN] Daemon did not respond - may need to kill manually")
        return 1
    finally:
        sock.close()
    
    return 0


def _wait_for_daemon_exit(port: int, timeout: int = 10):
    """Wait for daemon to actually exit."""
    import socket
    import time
    
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1.0)
            sock.connect(("127.0.0.1", port))
            sock.close()
            time.sleep(0.5)  # Still running, wait
        except ConnectionRefusedError:
            print("[OK] Daemon stopped")
            return
        except socket.timeout:
            pass
    print("[WARN] Daemon still running after timeout")


def main():
    parser = argparse.ArgumentParser(
        prog='qbuilder',
        description='Crash-safe queue-based builder for CK3 mod processing'
    )
    subparsers = parser.add_subparsers(dest='command', required=True)
    
    # daemon (the primary command for single-writer architecture)
    daemon_parser = subparsers.add_parser('daemon', 
        help='Start the single-writer daemon (recommended)')
    daemon_parser.add_argument('--fresh', action='store_true',
                               help='Reset ALL data and start fresh')
    daemon_parser.add_argument('--max-items', type=int, default=None,
                               help='Maximum build items to process (for testing)')
    daemon_parser.add_argument('--poll-interval', type=float, default=5.0,
                               help='Seconds between polls when queue empty (default: 5)')
    daemon_parser.set_defaults(func=cmd_daemon)
    
    # status
    status_parser = subparsers.add_parser('status', help='Show queue status')
    status_parser.set_defaults(func=cmd_status)
    
    # stop (send shutdown signal to daemon)
    stop_parser = subparsers.add_parser('stop', help='Stop running daemon')
    stop_parser.add_argument('--timeout', type=int, default=10, 
                             help='Shutdown timeout seconds (default: 10)')
    stop_parser.add_argument('--wait', action='store_true',
                             help='Wait for daemon to exit')
    stop_parser.set_defaults(func=cmd_stop)
    
    args = parser.parse_args()
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
