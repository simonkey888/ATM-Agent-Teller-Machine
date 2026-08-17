from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

SRC = Path(__file__).resolve().parent
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from atm_core.payments import HttpJsonClient
from atm_core.runtime import ProcessLock
from atm_core.security import redact_text

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "atm.json"
EXAMPLE = ROOT / "config" / "atm.example.json"


def emit(ok: bool, label: str, detail: str = "") -> None:
    print(f"[{'OK' if ok else 'FAIL'}] {label}" + (f": {detail}" if detail else ""))


def lock_status() -> tuple[bool, str]:
    path = ROOT / ".atm" / "atm.lock"
    if not path.exists():
        return False, "no active lock observed"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        active = ProcessLock(path)._is_active(raw)
        return active, f"pid={raw.get('pid')} instance={raw.get('instance_id')}" if active else "stale lock; runtime will quarantine"
    except Exception as exc:
        return False, f"unreadable/stale lock: {type(exc).__name__}"


def github_read(token: str) -> bool:
    req = urllib.request.Request(
        "https://api.github.com/repos/simonkey888/ATM-Agent-Teller-Machine",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "User-Agent": "atm-doctor-oci/1"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("full_name") == "simonkey888/ATM-Agent-Teller-Machine"


def provider_probe(hermes: str, provider: dict) -> tuple[bool, str]:
    cmd = [hermes, "chat", "--quiet", "--provider", str(provider.get("provider") or "gemini")]
    if provider.get("model"):
        cmd += ["--model", str(provider["model"])]
    cmd += ["--max-turns", "2", "-q", "Reply with exactly ATM_PROVIDER_OK and nothing else."]
    try:
        cp = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=120, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return False, "timeout"
    output = (cp.stdout or "").strip()
    if cp.returncode == 0 and output == "ATM_PROVIDER_OK":
        return True, "PASS"
    diagnostic = redact_text("\n".join([cp.stderr or "", cp.stdout or ""]))[-300:].replace("\n", " ")
    return False, diagnostic or f"exit={cp.returncode}"


def main() -> int:
    failures = 0
    host = os.getenv("ATM_HOST_CLASS", "OCI").upper()
    source_sha = os.getenv("ATM_SOURCE_SHA", "UNKNOWN")
    print(f"ATM_HOST_CLASS={host}")
    print(f"ATM_SOURCE_SHA={source_sha}")

    hermes = shutil.which("hermes")
    emit(bool(hermes), "Hermes executable", hermes or "missing")
    failures += 0 if hermes else 1

    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") or ""
    emit(bool(token), "GitHub token", "present (value not printed)" if token else "missing")
    failures += 0 if token else 1
    if token:
        try:
            ok = github_read(token)
        except Exception:
            ok = False
        emit(ok, "GitHub API read", "authenticated" if ok else "failed")
        failures += 0 if ok else 1

    if not CONFIG.exists() and EXAMPLE.exists():
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(EXAMPLE, CONFIG)
    cfg = json.loads(CONFIG.read_text(encoding="utf-8")) if CONFIG.exists() else {}
    provider = cfg.get("provider", {}).get("primary", {})
    required = str(provider.get("requires_env") or "")
    if required:
        present = bool(os.getenv(required))
        emit(present, f"Provider credential {required}", "present (value not printed)" if present else "missing")
        failures += 0 if present else 1

    state_dir = ROOT / ".atm"
    state_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=state_dir, prefix="doctor-", delete=True) as fh:
        fh.write(b"ok"); fh.flush()
    emit(True, "Persistent state writable")

    active, detail = lock_status()
    emit(True, "Singleton status", detail)
    print(f"ATM_RUNTIME_ACTIVE={'YES' if active else 'NO'}")
    print("HERMES_LEGACY_WINDOWS_SESSION_DIAG=NOT_APPLICABLE_ON_OCI")

    http = HttpJsonClient(timeout=15)
    for label, url in (
        ("WorkProtocol", "https://workprotocol.ai/api/jobs?status=open"),
        ("Taskmarket", "https://api.taskmarket.dev/api/tasks?status=open&limit=1"),
    ):
        try:
            http.get(url); emit(True, f"{label} outbound HTTPS")
        except Exception as exc:
            emit(False, f"{label} outbound HTTPS", type(exc).__name__); failures += 1

    if hermes and (not required or os.getenv(required)):
        ok, detail = provider_probe(hermes, provider)
        emit(ok, "Hermes provider inference", detail)
        failures += 0 if ok else 1

    print(f"ATM_DOCTOR={'PASS' if failures == 0 else 'FAIL'} failures={failures}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
