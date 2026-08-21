from __future__ import annotations

import json
import os
import sys
import time
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
    print(json.dumps({"ok": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
