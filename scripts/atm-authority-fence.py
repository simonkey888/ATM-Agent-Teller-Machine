#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ENV_FILE = Path(os.getenv("ATM_AUTHORITY_ENV_FILE", "/etc/atm/authority.env"))


def load_env() -> dict[str, str]:
    out = dict(os.environ)
    if ENV_FILE.exists():
        for raw in ENV_FILE.read_text(encoding="utf-8", errors="strict").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            out.setdefault(key.strip(), value.strip())
    return out


def main() -> int:
    env = load_env()
    url = env.get("ATM_AUTHORITY_READ_URL", "")
    instance = env.get("ATM_CANONICAL_OCI_INSTANCE_OCID", "")
    epoch_raw = env.get("ATM_AUTHORITY_EPOCH", "")
    sha = env.get("ATM_SOURCE_SHA", "")
    if not url.startswith("https://"):
        print("AUTHORITY_FENCE=FAIL reason=READ_URL_MISSING", file=sys.stderr); return 21
    if not instance.startswith("ocid1.instance."):
        print("AUTHORITY_FENCE=FAIL reason=INSTANCE_ID_INVALID", file=sys.stderr); return 21
    if not epoch_raw.isdigit() or int(epoch_raw) <= 0:
        print("AUTHORITY_FENCE=FAIL reason=EPOCH_INVALID", file=sys.stderr); return 21
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        print("AUTHORITY_FENCE=FAIL reason=SOURCE_SHA_INVALID", file=sys.stderr); return 21
    req = urllib.request.Request(url, headers={"User-Agent": "ATM-Authority-Fence/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
    except Exception as exc:
        print(f"AUTHORITY_FENCE=FAIL reason=READ_ERROR class={type(exc).__name__}", file=sys.stderr); return 22
    try:
        rec = json.loads(raw)
    except Exception:
        print("AUTHORITY_FENCE=FAIL reason=RECORD_JSON_INVALID", file=sys.stderr); return 22
    checks = {
        "schema": rec.get("schema") == "ATM_AUTHORITY_V1",
        "holder": rec.get("holder") == "OCI",
        "instance": rec.get("oci_instance_ocid") == instance,
        "epoch": int(rec.get("epoch") or 0) == int(epoch_raw),
        "source_sha": rec.get("source_sha") == sha,
    }
    try:
        expiry = datetime.fromisoformat(str(rec.get("lease_expires_at") or "").replace("Z", "+00:00"))
        checks["lease_live"] = expiry.astimezone(timezone.utc) > datetime.now(timezone.utc)
    except ValueError:
        checks["lease_live"] = False
    repo = Path(__file__).resolve().parents[1]
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, text=True, capture_output=True, timeout=5)
    dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=repo, text=True, capture_output=True, timeout=5)
    checks["actual_head"] = head.returncode == 0 and head.stdout.strip() == sha
    checks["tracked_tree_clean"] = dirty.returncode == 0 and not dirty.stdout.strip()
    bad = [k for k, ok in checks.items() if not ok]
    if bad:
        print("AUTHORITY_FENCE=FAIL reason=MISMATCH fields=" + ",".join(bad), file=sys.stderr); return 23
    print(f"AUTHORITY_FENCE=PASS holder=OCI epoch={epoch_raw} source_sha={sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
