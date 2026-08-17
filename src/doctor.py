from __future__ import annotations

import argparse
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
from atm_core.runtime import ProcessLock, classify_provider_failure
from atm_core.security import redact_text

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "atm.json"
EXAMPLE = ROOT / "config" / "atm.example.json"
KNOWN_FAILED_SESSIONS = ("20260815_204535_7c8637", "20260815_204812_997674")


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


def lock_status() -> tuple[bool, str]:
    lock_path = ROOT / ".atm" / "atm.lock"
    if not lock_path.exists():
        return False, "no active lock observed"
    try:
        lock_data = json.loads(lock_path.read_text(encoding="utf-8"))
        pid = int(lock_data.get("pid", -1))
        active = ProcessLock._process_start(pid) is not None
        if active:
            return True, f"ATM instance active pid={pid} instance={lock_data.get('instance_id', 'unknown')}"
        return False, "stale lock present; runtime will quarantine it"
    except Exception as exc:
        return False, f"unreadable/stale lock: {type(exc).__name__}"


def diagnostic_roots() -> list[Path]:
    roots = [ROOT / ".atm" / "logs"]
    local = os.getenv("LOCALAPPDATA")
    if local:
        roots.extend([Path(local) / "hermes", Path(local) / "Hermes"])
    roots.extend([Path.home() / ".hermes", Path.home() / ".config" / "hermes"])
    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root).lower()
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def _bounded_text(path: Path) -> str:
    try:
        if not path.is_file() or path.stat().st_size > 2_000_000:
            return ""
        return path.read_text(encoding="utf-8", errors="ignore")
    except (OSError, UnicodeError):
        return ""


def find_session_evidence(session_id: str) -> list[tuple[Path, str]]:
    hits: list[tuple[Path, str]] = []
    scanned = 0
    for root in diagnostic_roots():
        if not root.exists():
            continue
        try:
            paths = root.rglob("*")
        except OSError:
            continue
        for path in paths:
            scanned += 1
            if scanned > 5000:
                return hits
            if not path.is_file():
                continue
            filename_hit = session_id.lower() in path.name.lower()
            text = _bounded_text(path)
            if not filename_hit and session_id not in text:
                continue
            if session_id in text:
                idx = text.find(session_id)
                excerpt = text[max(0, idx - 900) : idx + 2600]
            else:
                excerpt = text[-3000:]
            hits.append((path, excerpt))
            if len(hits) >= 8:
                return hits
    return hits


def diagnose_sessions(provider: dict) -> bool:
    model = provider.get("model")
    complete = True
    state_path = ROOT / ".atm" / "state.json"
    state_text = _bounded_text(state_path) if state_path.exists() else ""
    for session_id in KNOWN_FAILED_SESSIONS:
        evidence = find_session_evidence(session_id)
        if not evidence and session_id in state_text:
            idx = state_text.find(session_id)
            evidence = [(state_path, state_text[max(0, idx - 900) : idx + 2600])]
        if not evidence:
            print(f"HERMES_SESSION_DIAG session_id={session_id} status=EVIDENCE_NOT_FOUND")
            complete = False
            continue
        combined = "\n".join(excerpt for _, excerpt in evidence)
        failure = classify_provider_failure(str(provider.get("provider") or "gemini"), model, combined)
        paths = ",".join(str(path) for path, _ in evidence[:3])
        safe_message = redact_text(failure.message).replace("\n", " ")[-500:]
        print(
            "HERMES_SESSION_DIAG "
            f"session_id={session_id} status=FOUND class={failure.error_class.value} "
            f"http_status={failure.http_status} retry_after={failure.retry_after_seconds} paths={paths}"
        )
        print(f"HERMES_SESSION_ERROR_TAIL session_id={session_id} text={safe_message}")
    print(f"HERMES_DIAGNOSIS={'COMPLETE' if complete else 'INCOMPLETE'}")
    return complete


def run_provider_probe(hermes: str, provider: dict, active_atm: bool) -> bool:
    if active_atm:
        out(False, "Provider probe", "refused because ATM singleton is active")
        return False
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
    out(ok, "Provider inference + strict JSON + terminal + web", "PASS" if ok else redact_text((cp.stderr or cp.stdout)[-300:]))
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="ATM non-destructive runtime doctor")
    parser.add_argument("--probe-provider", action="store_true", help="Launch one Hermes probe only after session diagnosis and only if ATM is stopped")
    parser.add_argument("--require-stopped", action="store_true", help="Fail if an ATM singleton is currently active")
    args = parser.parse_args()

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

    active_atm, detail = lock_status()
    out(True, "Singleton status", detail)
    print(f"ATM_RUNTIME_ACTIVE={'YES' if active_atm else 'NO'}")

    diagnosis_complete = diagnose_sessions(provider)

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

    if args.require_stopped and active_atm:
        print("ATM_REQUIRE_STOPPED=FAIL")
        return 3

    if args.probe_provider:
        if not diagnosis_complete:
            out(False, "Provider probe", "refused until the two known failed sessions are diagnosed")
            return 4
        if not hermes or not run_provider_probe(hermes, provider, active_atm):
            failures += 1
    else:
        print("PROVIDER_PROBE=SKIPPED_BY_DEFAULT")

    if failures:
        print(f"ATM_DOCTOR=FAIL failures={failures}")
        return 1
    print("ATM_DOCTOR=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())