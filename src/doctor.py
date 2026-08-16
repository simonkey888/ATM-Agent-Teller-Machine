from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parent
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from atm_core.payments import HttpJsonClient
from atm_core.runtime import ProcessLock

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "atm.json"
EXAMPLE = ROOT / "config" / "atm.example.json"


def out(ok: bool, label: str, detail: str = "") -> None:
    prefix = "[OK]" if ok else "[FAIL]"
    print(f"{prefix} {label}" + (f": {detail}" if detail else ""))


def hermes_env_has(name: str) -> bool:
    candidates = []
    local = os.getenv("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "hermes" / ".env")
    candidates.append(Path.home() / ".hermes" / ".env")
    for path in candidates:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.strip().startswith(name + "=") and line.split("=", 1)[1].strip():
                return True
    return False


def main() -> int:
    if not CONFIG.exists():
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(EXAMPLE, CONFIG)
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    failures = 0

    hermes = shutil.which("hermes")
    out(bool(hermes), "Hermes executable", hermes or "missing")
    failures += 0 if hermes else 1

    gh = shutil.which("gh")
    out(bool(gh), "GitHub CLI", gh or "missing")
    failures += 0 if gh else 1

    if gh:
        cp = subprocess.run([gh, "auth", "status"], text=True, capture_output=True)
        out(cp.returncode == 0, "GitHub auth", "authenticated" if cp.returncode == 0 else "not authenticated")
        failures += 0 if cp.returncode == 0 else 1
        read = subprocess.run(
            [gh, "api", "repos/simonkey888/ATM-Agent-Teller-Machine", "--jq", ".full_name"],
            text=True,
            capture_output=True,
        )
        ok = read.returncode == 0 and read.stdout.strip() == "simonkey888/ATM-Agent-Teller-Machine"
        out(ok, "GitHub read", read.stdout.strip() or read.stderr.strip()[-200:])
        failures += 0 if ok else 1

    provider = config.get("provider", {}).get("primary", {})
    required_env = provider.get("requires_env")
    if required_env:
        present = bool(os.getenv(required_env)) or hermes_env_has(required_env)
        out(present, f"Provider credential {required_env}", "present (value not printed)" if present else "missing")
        failures += 0 if present else 1

    with tempfile.NamedTemporaryFile(dir=ROOT, prefix=".atm-doctor-", delete=True) as fh:
        fh.write(b"ok")
        fh.flush()
    out(True, "Repository/state filesystem writable")

    lock_path = ROOT / ".atm" / "atm.lock"
    if lock_path.exists():
        try:
            lock_data = json.loads(lock_path.read_text(encoding="utf-8"))
            active = ProcessLock._process_start(int(lock_data.get("pid", -1))) is not None
        except Exception:
            active = False
        out(True, "Singleton status", "ATM instance active" if active else "stale lock present; runtime will quarantine it")
    else:
        out(True, "Singleton status", "no active lock observed")

    http = HttpJsonClient(timeout=15)
    for label, url in [
        ("WorkProtocol read-only health", "https://workprotocol.ai/api/health"),
        ("Taskmarket read-only health", "https://api.taskmarket.dev/api/health"),
    ]:
        try:
            data = http.get(url)
            out(True, label, str(data)[:160])
        except Exception as exc:
            out(False, label, str(exc)[:200])
            failures += 1

    if hermes:
        model = provider.get("model")
        cmd = [hermes, "chat", "--quiet", "--provider", str(provider.get("provider"))]
        if model:
            cmd += ["--model", str(model)]
        prompt = (
            'Use the terminal tool to run "python --version" and use the web tool to read '
            'https://workprotocol.ai/api/health. Return exactly JSON: '
            '{"status":"PASS","terminal":true,"web":true}. No markdown.'
        )
        cmd += ["--max-turns", "20", "--toolsets", "terminal,web", "-q", prompt]
        cp = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=180, encoding="utf-8", errors="replace")
        try:
            parsed = json.loads((cp.stdout or "").strip()) if cp.returncode == 0 else {}
        except json.JSONDecodeError:
            parsed = {}
        ok = parsed == {"status": "PASS", "terminal": True, "web": True}
        out(ok, "Provider inference + strict JSON + terminal + web", "PASS" if ok else (cp.stderr or cp.stdout)[-300:])
        failures += 0 if ok else 1

    if failures:
        print(f"ATM_DOCTOR=FAIL failures={failures}")
        return 1
    print("ATM_DOCTOR=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
