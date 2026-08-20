from __future__ import annotations

import json
import socket
from pathlib import Path

SOCKET_PATH = Path("/run/atm-publisher/publisher.sock")


def publish_workspace(opportunity_id: str, workspace: Path, timeout: int = 120) -> str:
    payload = json.dumps({"opportunity_id": opportunity_id, "workspace": str(workspace.resolve())}, separators=(",", ":")) + "\n"
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(str(SOCKET_PATH))
        sock.sendall(payload.encode("utf-8"))
        sock.shutdown(socket.SHUT_WR)
        raw = b""
        while len(raw) < 65536:
            chunk = sock.recv(8192)
            if not chunk:
                break
            raw += chunk
    finally:
        sock.close()
    response = json.loads(raw.decode("utf-8"))
    if not response.get("ok") or not response.get("url"):
        raise RuntimeError("publisher failed: " + str(response.get("error") or "unknown"))
    return str(response["url"])
