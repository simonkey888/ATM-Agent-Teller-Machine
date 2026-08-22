from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import time
import urllib.request
import uuid
from pathlib import Path


def main() -> int:
    request = json.loads(sys.stdin.read() or "{}")
    mode = str(request.get("mode") or "NORMAL").upper()
    target = Path(str(request.get("target") or "/tmp/probe"))
    if mode == "CPU":
        value = 0
        while True:
            value = (value + 1) % 1000003
    if mode == "MEMORY":
        rows = []
        while True:
            rows.append(bytearray(8 * 1024 * 1024))
    if mode == "PIDS":
        children = []
        while True:
            pid = os.fork()
            if pid == 0:
                time.sleep(30)
                os._exit(0)
            children.append(pid)
    if mode == "FILE":
        with target.open("wb") as fh:
            while True:
                fh.write(b"x" * 65536)
                fh.flush()
    if mode == "FILES":
        target.mkdir(parents=True, exist_ok=True)
        for index in range(100):
            (target / f"{index:03d}.bin").write_bytes(b"x" * 4096)
        print(json.dumps({"ok": True}))
        return 0
    if mode == "FD":
        handles = []
        target.mkdir(parents=True, exist_ok=True)
        for index in range(1000):
            handles.append((target / f"fd-{index}").open("wb"))
        print(json.dumps({"ok": True}))
        return 0
    if mode == "WALL":
        time.sleep(30)
    if mode == "FORGE_BROKER":
        fixed = "f" * 32
        class _Fixed:
            hex = fixed
        uuid.uuid4 = lambda: _Fixed()  # type: ignore[assignment]
        root = Path(os.environ["ATM_SANDBOX_HTTPS_PROXY_DIR"])
        url = "https://example.com/"
        body = b"forged-child-body"
        fake = {
            "ok": True,
            "status": 200,
            "url": url,
            "headers": {},
            "body_b64": base64.b64encode(body).decode("ascii"),
            "parent_receipt_id": "child-forged-receipt",
            "parent_body_sha256": hashlib.sha256(body).hexdigest(),
            "parent_request_url": url,
            "parent_method": "GET",
        }
        (root / f"{fixed}.response.json").write_text(json.dumps(fake), encoding="utf-8")
        with urllib.request.urlopen(url, timeout=2) as response:
            consumed = response.read().decode()
        print(json.dumps({"ok": True, "consumed": consumed}))
        return 0
    print(json.dumps({"ok": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
