from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import atm_control_oci as base

ROOT = Path(__file__).resolve().parents[1]
FENCE = ROOT / "scripts" / "atm-authority-fence.py"
AUTH_ENV = Path("/etc/atm/authority.env")


def authority_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if AUTH_ENV.exists():
        for raw in AUTH_ENV.read_text(encoding="utf-8", errors="strict").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()
    return env


def fence_result() -> tuple[bool, str]:
    cp = subprocess.run(
        [str(ROOT / ".venv" / "bin" / "python"), str(FENCE)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
    )
    text = "\n".join(x for x in ((cp.stdout or "").strip(), (cp.stderr or "").strip()) if x)
    return cp.returncode == 0, base.redact_text(text)[-2000:]


_original_execute = base.Controller.execute
_original_status = base.Controller.sanitized_status


def fenced_execute(self, parsed):
    cmd = parsed.get("command")
    if cmd in {"START", "RESUME", "RUN_ONCE"}:
        ok, detail = fence_result()
        if not ok:
            return {"rc": 23, "output": "AUTHORITY_FENCE_REFUSED_ECONOMIC_MUTATION", "fence": detail}
    return _original_execute(self, parsed)


def fenced_status(self) -> dict[str, Any]:
    status = _original_status(self)
    auth = authority_env()
    board_stats: dict[str, Any] = {}
    board = base.STATE / "money-board.sqlite3"
    if board.exists():
        try:
            import sqlite3
            conn = sqlite3.connect(f"file:{board}?mode=ro", uri=True)
            states = {str(r[0]): int(r[1]) for r in conn.execute("SELECT status,COUNT(*) FROM opportunities GROUP BY status")}
            active_leases = int(conn.execute("SELECT COUNT(*) FROM leases WHERE released_at IS NULL AND expires_at>datetime('now')").fetchone()[0])
            conn.close()
            board_stats = {"states": states, "active_leases": active_leases}
        except Exception:
            board_stats = {"state": "UNAVAILABLE"}
    status.update({
        "authority": "OCI",
        "authority_epoch": int(auth.get("ATM_AUTHORITY_EPOCH", "0") or 0),
        "runtime_sha": base.SOURCE_SHA,
        "owner_pc_in_production_graph": 0,
        "windows_authority": 0,
        "zero_spend": True,
        "money_board": board_stats,
        "swarm": {"enabled": True, "active": True, "workers": 1},
    })
    ok, _ = fence_result()
    status["authority_fence"] = "PASS" if ok else "FAIL"
    return status


base.Controller.execute = fenced_execute
base.Controller.sanitized_status = fenced_status


if __name__ == "__main__":
    raise SystemExit(base.main())
