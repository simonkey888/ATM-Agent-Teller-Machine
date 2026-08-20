#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import os
import tarfile
import urllib.request
from pathlib import Path


VERSION = "0.2.0"
ARCHIVE_SHA256 = "d601f4f542319c3b9fa8dca9f5ccfc134a2ca001648da528db5f03c9e6c2599b"
URL = "https://github.com/h4ckf0r0day/obscura/releases/download/v0.2.0/obscura-x86_64-linux.tar.gz"


def install(target: Path) -> Path:
    target = target.resolve()
    target.mkdir(parents=True, exist_ok=True)
    archive = target / "obscura-v0.2.0-x86_64-linux.tar.gz"
    if not archive.exists():
        request = urllib.request.Request(URL, headers={"User-Agent": "ATM-Obscura-Pinned/1.0"})
        with urllib.request.urlopen(request, timeout=120) as response, archive.open("wb") as handle:
            while chunk := response.read(1024 * 1024):
                handle.write(chunk)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != ARCHIVE_SHA256:
        archive.unlink(missing_ok=True)
        raise SystemExit("OBSCURA_ARCHIVE_INTEGRITY_MISMATCH")
    with tarfile.open(archive, "r:gz") as bundle:
        members = bundle.getmembers()
        if any(member.name not in {"obscura", "obscura-worker"} or not member.isfile() for member in members):
            raise SystemExit("OBSCURA_ARCHIVE_SHAPE_REJECTED")
        for member in members:
            source = bundle.extractfile(member)
            if source is None:
                raise SystemExit("OBSCURA_ARCHIVE_MEMBER_UNREADABLE")
            (target / member.name).write_bytes(source.read())
    binary = target / "obscura"
    os.chmod(binary, 0o755)
    os.chmod(target / "obscura-worker", 0o755)
    return binary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=Path, required=True)
    args = parser.parse_args()
    binary = install(args.target)
    print(f"OBSCURA_VERSION={VERSION}")
    print(f"OBSCURA_ARCHIVE_SHA256={ARCHIVE_SHA256}")
    print(f"OBSCURA_BINARY={binary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
