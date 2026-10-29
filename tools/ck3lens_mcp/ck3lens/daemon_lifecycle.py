"""QBuilder subprocess lifecycle helpers."""
from __future__ import annotations

import subprocess
import sys
from typing import Protocol


class ManagedProcess(Protocol):
    pid: int
    returncode: int | None

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...


def _terminate_windows_tree(pid: int) -> None:
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def terminate_and_reap(
    process: ManagedProcess,
    *,
    terminate_timeout: float = 5.0,
    kill_timeout: float = 5.0,
    tree_on_windows: bool = False,
) -> int:
    """Stop a failed daemon launcher and always reap its process handle."""
    if process.poll() is not None:
        return process.wait()

    if tree_on_windows and sys.platform == "win32":
        _terminate_windows_tree(process.pid)
        try:
            return process.wait(timeout=kill_timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            return process.wait(timeout=kill_timeout)

    process.terminate()
    try:
        return process.wait(timeout=terminate_timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        return process.wait(timeout=kill_timeout)
