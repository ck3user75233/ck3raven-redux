"""Shared discovery record for the single QBuilder daemon endpoint."""
from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from ck3lens.paths import ROOT_CK3RAVEN_DATA

PROTOCOL_VERSION = 1
ENDPOINT_PATH = ROOT_CK3RAVEN_DATA / "qbuilder_endpoint.json"


@dataclass(frozen=True)
class EndpointInfo:
    host: str
    port: int
    pid: int
    run_id: str
    db_path: str
    protocol: int = PROTOCOL_VERSION
    published_at: float = 0.0


def _endpoint_path(path: Path | None) -> Path:
    return Path(path) if path is not None else ENDPOINT_PATH


def publish_endpoint(info: EndpointInfo, path: Path | None = None) -> None:
    """Atomically publish the endpoint selected by the lock-owning daemon."""
    endpoint_path = _endpoint_path(path)
    endpoint_path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(info)
    if not payload["published_at"]:
        payload["published_at"] = time.time()

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=endpoint_path.parent,
            prefix=f".{endpoint_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            json.dump(payload, temp_file, separators=(",", ":"))
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, endpoint_path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink(missing_ok=True)


def load_endpoint(path: Path | None = None) -> EndpointInfo | None:
    """Load a valid localhost endpoint, tolerating missing or partial records."""
    endpoint_path = _endpoint_path(path)
    try:
        data = json.loads(endpoint_path.read_text(encoding="utf-8"))
        endpoint = EndpointInfo(
            host=str(data["host"]),
            port=int(data["port"]),
            pid=int(data["pid"]),
            run_id=str(data["run_id"]),
            db_path=str(data["db_path"]),
            protocol=int(data.get("protocol", PROTOCOL_VERSION)),
            published_at=float(data.get("published_at", 0.0)),
        )
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None

    if (
        endpoint.host != "127.0.0.1"
        or not 1 <= endpoint.port <= 65535
        or endpoint.pid <= 0
        or not endpoint.run_id
        or not endpoint.db_path
        or endpoint.protocol != PROTOCOL_VERSION
    ):
        return None
    return endpoint


def clear_endpoint(*, run_id: str, pid: int, path: Path | None = None) -> bool:
    """Remove the endpoint only when it still belongs to this daemon run."""
    endpoint_path = _endpoint_path(path)
    endpoint = load_endpoint(endpoint_path)
    if endpoint is None or endpoint.run_id != run_id or endpoint.pid != pid:
        return False
    try:
        endpoint_path.unlink()
    except OSError:
        return False
    return True
