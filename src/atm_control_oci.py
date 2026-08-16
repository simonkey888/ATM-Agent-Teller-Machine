from __future__ import annotations

import hashlib
import json
import os
import pwd
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SRC = Path(__file__).resolve().parent
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from atm_core.runtime import ProcessLock, SingletonLockError
from atm_core.security import redact_text

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".atm"
CONFIG_PATH = ROOT / "config" / "control.json"
LOCK = STATE / "controller-oci.lock"
STATE_FILE = STATE / "controller-state-oci.json"
ATM_LOCK = STATE / "atm.lock"
PAUSE_FILE = STATE / "paused"
LOG_DIR = STATE / "logs"
RESULT_PREFIX = "ATM_RESULT_V1"
COMMAND_PREFIX = "ATM_CMD_V1"
HOST_CLASS = os.getenv("ATM_HOST_CLASS", "OCI").upper()
SOURCE_SHA = os.getenv("ATM_SOURCE_SHA", "UNKNOWN")
ALLOWED = {"STATUS", "DOCTOR", "START", "STOP", "PAUSE", "RESUME", "RUN_ONCE", "TAIL_LOGS", "EXECUTE_ORDER", "RELOAD_CONFIG", "SYNC"}


class CommandRejected(ValueError):
    pass


class Transient(RuntimeError):
    def __init__(self, message: str, retry_after: int = 30):
        super().__init__(message); self.retry_after = max(1, retry_after)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{uuid.uuid4().hex}")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8")); return raw if isinstance(raw, dict) else dict(default)
    except Exception:
        return dict(default)


def body_hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def service_active(name: str) -> bool:
    cp = subprocess.run(["systemctl", "is-active", "--quiet", name], timeout=15)
    return cp.returncode == 0


def atm_lock_summary() -> dict[str, Any]:
    if not ATM_LOCK.exists():
        return {"active": False}
    try:
        raw = json.loads(ATM_LOCK.read_text(encoding="utf-8")); active = ProcessLock(ATM_LOCK)._is_active(raw)
        return {"active": active, "pid": raw.get("pid") if active else None, "instance_id": raw.get("instance_id") if active else None}
    except Exception:
        return {"active": False, "stale_or_unreadable": True}


def runtime_env() -> dict[str, str]:
    env = {"HOME": "/var/lib/atm", "HERMES_HOME": "/var/lib/atm/.hermes", "PATH": "/var/lib/atm/.local/bin:/usr/local/bin:/usr/bin:/bin", "ATM_HOST_CLASS": HOST_CLASS, "ATM_SOURCE_SHA": SOURCE_SHA, "PYTHONUNBUFFERED": "1"}
    path = Path("/etc/atm/runtime.env")
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1); env[key.strip()] = value.strip()
    return env


def run_as_atm(script: str, *args: str, timeout: int = 180) -> tuple[int, str]:
    acct = pwd.getpwnam("atm")
    def demote() -> None:
        os.setgid(acct.pw_gid); os.initgroups(acct.pw_name, acct.pw_gid); os.setuid(acct.pw_uid)
    cp = subprocess.run([str(ROOT / ".venv" / "bin" / "python"), str(ROOT / "src" / script), *args], cwd=ROOT, env=runtime_env(), preexec_fn=demote, text=True, capture_output=True, encoding="utf-8", errors="replace", timeout=timeout)
    text = "\n".join(x for x in ((cp.stdout or "").strip(), (cp.stderr or "").strip()) if x)
    return cp.returncode, redact_text(text)[-8000:]


def parse_command(body: str) -> dict[str, Any]:
    lines = body.replace("\r\n", "\n").strip().split("\n")
    if len(lines) != 4 or lines[0].strip() != COMMAND_PREFIX:
        raise CommandRejected("invalid envelope")
    pairs: dict[str, str] = {}
    for line in lines[1:]:
        if "=" not in line: raise CommandRejected("malformed command line")
        key, value = line.split("=", 1); pairs[key.strip()] = value.strip()
    if set(pairs) != {"COMMAND", "COMMAND_ID", "ARGS"}: raise CommandRejected("invalid fields")
    command = pairs["COMMAND"].upper()
    if command not in ALLOWED: raise CommandRejected("unknown command")
    cid = pairs["COMMAND_ID"]
    if not cid or len(cid) > 128 or not all(c.isalnum() or c in "._:-" for c in cid): raise CommandRejected("invalid command id")
    try: args = json.loads(pairs["ARGS"])
    except json.JSONDecodeError as exc: raise CommandRejected("ARGS invalid") from exc
    if not isinstance(args, dict): raise CommandRejected("ARGS must object")
    target = str(args.pop("target_host", "")).upper()
    if target and target != HOST_CLASS: raise CommandRejected("target host mismatch")
    if command == "TAIL_LOGS":
        if set(args) - {"lines"}: raise CommandRejected("TAIL_LOGS args")
        lines_n = int(args.get("lines", 40))
        if not 1 <= lines_n <= 200: raise CommandRejected("TAIL_LOGS bounds")
    elif command == "EXECUTE_ORDER":
        if set(args) - {"issue", "verify_only"} or int(args.get("issue", 0)) <= 0 or args.get("verify_only") is not True: raise CommandRejected("EXECUTE_ORDER verify-only")
    elif args:
        raise CommandRejected(f"{command} args not allowed")
    return {"command": command, "command_id": cid, "args": args}


class GitHubBus:
    def __init__(self, cfg: dict[str, Any]):
        self.repo = cfg["repository"]; self.owner = cfg["owner_login"]; self.issue = int(cfg["control_issue"]); self.obs = int(cfg["observatory_issue"])
        self.token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") or ""
        if not self.token: raise RuntimeError("GITHUB_TOKEN missing")
    def headers(self, extra=None):
        return {"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "atm-oci-control/1", **(extra or {})}
    @staticmethod
    def retry(exc: urllib.error.HTTPError) -> int:
        ra = exc.headers.get("Retry-After")
        if ra and ra.isdigit(): return int(ra)
        reset = exc.headers.get("X-RateLimit-Reset")
        return max(1, int(reset) - int(time.time()) + 1) if reset and reset.isdigit() else 30
    def request(self, method: str, path: str, payload: Any = None, extra=None) -> tuple[Any, Any]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = self.headers({"Content-Type": "application/json"} if payload is not None else {})
        if extra: headers.update(extra)
        req = urllib.request.Request("https://api.github.com" + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read(); return (json.loads(raw.decode("utf-8")) if raw else {}, resp.headers)
        except urllib.error.HTTPError as exc:
            if exc.code == 304: return None, exc.headers
            if exc.code in {403, 429, 502, 503, 504}: raise Transient(f"GitHub HTTP {exc.code}", self.retry(exc)) from exc
            raise
    def comments(self, etag=None, since=None):
        q = {"per_page": "100"}
        if since: q["since"] = since
        extra = {"If-None-Match": etag} if etag else None
        return self.request("GET", f"/repos/{self.repo}/issues/{self.issue}/comments?{urllib.parse.urlencode(q)}", extra=extra)
    def post_result(self, body: str) -> int:
        data, _ = self.request("POST", f"/repos/{self.repo}/issues/{self.issue}/comments", {"body": body}); return int(data["id"])
    def issue(self, number: int):
        data, _ = self.request("GET", f"/repos/{self.repo}/issues/{number}"); return data
    def publish_observatory(self, status: dict[str, Any]) -> None:
        body = "ATM_OBSERVATORY_V1\nSTATUS_JSON=" + json.dumps(status, ensure_ascii=False, separators=(",", ":")) + "\n\nSanitized machine state. Economic truth remains authoritative only in the local validated-payment ledger."
        self.request("PATCH", f"/repos/{self.repo}/issues/{self.obs}", {"body": body})


class Controller:
    def __init__(self, cfg: dict[str, Any], bus: GitHubBus):
        self.cfg = cfg; self.bus = bus
        floor = int(os.getenv("ATM_IGNORE_COMMENT_ID", cfg.get("ignore_comment_ids_before_or_equal", 0)))
        self.state = load_json(STATE_FILE, {"last_seen_comment_id": floor, "last_seen_updated_at": None, "etag": None, "etag_since": None, "processed_comments": {}, "processed_commands": {}, "last_observatory_publish": 0})
        self.state["last_seen_comment_id"] = max(floor, int(self.state.get("last_seen_comment_id", floor)))
    def save(self): atomic_json(STATE_FILE, self.state)
    def authorize(self, comment):
        cid = int(comment["id"]); body = str(comment.get("body") or "")
        if cid <= int(self.state["last_seen_comment_id"]): raise CommandRejected("old")
        if str((comment.get("user") or {}).get("login") or "") != self.cfg["owner_login"]: raise CommandRejected("author")
        parsed = parse_command(body); parsed.update({"comment_id": cid, "body_hash": body_hash(body)}); return parsed
    def execute(self, parsed):
        cmd, args = parsed["command"], parsed["args"]
        if cmd == "STATUS":
            rc, out = run_as_atm("atm_v2.py", "--status"); return {"rc": rc, "output": out, "service_active": service_active("atm-supervisor.service"), "atm": atm_lock_summary()}
        if cmd == "DOCTOR":
            rc, out = run_as_atm("doctor_oci.py", timeout=240); return {"rc": rc, "output": out, "atm": atm_lock_summary()}
        if cmd == "START":
            subprocess.run(["systemctl", "enable", "atm-supervisor.service"], check=True, timeout=30)
            subprocess.run(["systemctl", "start", "atm-supervisor.service"], check=True, timeout=45)
            time.sleep(2); return {"rc": 0 if service_active("atm-supervisor.service") else 3, "output": "STARTED" if service_active("atm-supervisor.service") else "START_FAILED", "atm": atm_lock_summary()}
        if cmd == "STOP":
            subprocess.run(["systemctl", "disable", "--now", "atm-supervisor.service"], check=False, timeout=45); return {"rc": 0, "output": "STOPPED", "atm": atm_lock_summary()}
        if cmd == "PAUSE":
            PAUSE_FILE.parent.mkdir(parents=True, exist_ok=True); PAUSE_FILE.write_text(now() + "\n", encoding="utf-8"); return {"rc": 0, "output": "PAUSED_MONITOR_ONLY"}
        if cmd == "RESUME":
            PAUSE_FILE.unlink(missing_ok=True); subprocess.run(["systemctl", "enable", "--now", "atm-supervisor.service"], check=True, timeout=45); return {"rc": 0, "output": "RESUMED"}
        if cmd == "RUN_ONCE":
            if service_active("atm-supervisor.service") or atm_lock_summary().get("active"): return {"rc": 3, "output": "RUN_ONCE_REFUSED_SUPERVISOR_ACTIVE"}
            rc, out = run_as_atm("atm_v2.py", "--once", timeout=7200); return {"rc": rc, "output": out}
        if cmd == "TAIL_LOGS":
            n = int(args.get("lines", 40)); paths = sorted(LOG_DIR.glob("*"), key=lambda p: p.stat().st_mtime if p.is_file() else 0, reverse=True)
            text = "NO_LOGS"
            for path in paths:
                if path.is_file() and path.stat().st_size <= 2_000_000:
                    text = "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]); break
            return {"rc": 0, "output": redact_text(text)[-6000:]}
        if cmd == "EXECUTE_ORDER":
            issue = self.bus.issue(int(args["issue"])); return {"rc": 0, "output": "ORDER_VERIFIED_ONLY", "issue": issue.get("number"), "state": issue.get("state"), "title": issue.get("title")}
        if cmd == "RELOAD_CONFIG": return {"rc": 0, "output": "CONFIG_IS_RELOADED_PER_RUNTIME_CYCLE"}
        if cmd == "SYNC": return {"rc": 4, "output": "OCI_EXACT_SHA_PINNED_SYNC_REFUSED_USE_EXACT_SHA_BOOTSTRAP"}
        raise CommandRejected("unknown")
    def result_body(self, parsed, started, ended, result):
        safe = json.loads(redact_text(json.dumps(result, default=str, ensure_ascii=False)))
        return "\n".join([RESULT_PREFIX, f"COMMAND_ID={parsed['command_id']}", f"COMMENT_ID={parsed['comment_id']}", f"STATUS={'SUCCESS' if int(result.get('rc', 1)) == 0 else 'FAILED'}", f"HOST_CLASS={HOST_CLASS}", f"SOURCE_SHA={SOURCE_SHA}", f"STARTED_AT={started}", f"ENDED_AT={ended}", f"ATM_ACTIVE={'YES' if atm_lock_summary().get('active') else 'NO'}", "RESULT=" + json.dumps(safe, ensure_ascii=False, separators=(",", ":"))])
    def process(self, comment):
        cid = int(comment["id"]); body = str(comment.get("body") or ""); updated = str(comment.get("updated_at") or comment.get("created_at") or "")
        if cid <= int(self.state.get("last_seen_comment_id", 0)): return False
        prior = self.state["processed_comments"].get(str(cid))
        if prior:
            if prior.get("body_hash") != body_hash(body): self.state["last_seen_comment_id"] = max(int(self.state["last_seen_comment_id"]), cid); self.save(); return False
            if prior.get("phase") == "RESULT_PENDING":
                rid = self.bus.post_result(prior["result_body"]); prior.update({"phase": "DONE", "result_comment_id": rid, "result_body": None}); self.save(); return True
            return False
        try: parsed = self.authorize(comment)
        except CommandRejected:
            self.state["last_seen_comment_id"] = max(int(self.state["last_seen_comment_id"]), cid); self.save(); return False
        if parsed["command_id"] in self.state["processed_commands"]:
            self.state["last_seen_comment_id"] = max(int(self.state["last_seen_comment_id"]), cid); self.save(); return False
        started = now(); rec = {"phase": "EXECUTING", "body_hash": parsed["body_hash"], "command_id": parsed["command_id"], "started_at": started}
        self.state["processed_comments"][str(cid)] = rec; self.state["processed_commands"][parsed["command_id"]] = rec; self.save()
        try: result = self.execute(parsed)
        except Exception as exc: result = {"rc": 1, "output": redact_text(str(exc))[-3000:]}
        ended = now(); rec = {**rec, "phase": "RESULT_PENDING", "result_body": self.result_body(parsed, started, ended, result)}
        self.state["processed_comments"][str(cid)] = rec; self.state["processed_commands"][parsed["command_id"]] = rec; self.save()
        rid = self.bus.post_result(rec["result_body"]); rec.update({"phase": "DONE", "result_comment_id": rid, "result_body": None})
        self.state["processed_comments"][str(cid)] = rec; self.state["processed_commands"][parsed["command_id"]] = rec
        self.state["last_seen_comment_id"] = max(int(self.state["last_seen_comment_id"]), cid)
        if updated and (not self.state.get("last_seen_updated_at") or updated > self.state["last_seen_updated_at"]): self.state["last_seen_updated_at"] = updated
        self.save(); return True
    def sanitized_status(self) -> dict[str, Any]:
        rc, out = run_as_atm("atm_v2.py", "--status")
        status: dict[str, Any] = {}
        if rc == 0:
            try: status = json.loads(out[out.find("{"):])
            except Exception: status = {}
        active = status.get("active_opportunity") or {}
        ledger = STATE / "validated-payment-proofs.jsonl"; last_payment = None
        if ledger.exists():
            try:
                rows = [json.loads(x) for x in ledger.read_text(encoding="utf-8", errors="ignore").splitlines() if x.strip()]
                if rows:
                    p = rows[-1]; last_payment = {"amount_usd": str(p.get("normalized_usd")), "time": p.get("timestamp"), "rail": p.get("platform")}
            except Exception: pass
        gates = 0
        gate_file = STATE / "human-gates.jsonl"
        if gate_file.exists(): gates = sum(1 for x in gate_file.read_text(encoding="utf-8", errors="ignore").splitlines() if x.strip())
        return {"host_class": HOST_CLASS, "source_sha": SOURCE_SHA, "updated_at": now(), "controller_state": "RUNNING", "supervisor_state": "RUNNING" if service_active("atm-supervisor.service") else "STOPPED", "supervisor_pid": atm_lock_summary().get("pid"), "phase": status.get("phase"), "paused": bool(status.get("paused")), "monthly_target_usd": status.get("MONTHLY_TARGET_USD"), "monthly_realized_withdrawable_usd": status.get("MONTHLY_REALIZED_WITHDRAWABLE_USD"), "monthly_gap_usd": status.get("MONTHLY_GAP_USD"), "required_daily_run_rate_usd": status.get("REQUIRED_DAILY_RUN_RATE_USD"), "observed_7d_run_rate_usd": status.get("OBSERVED_7D_RUN_RATE_USD"), "observed_30d_run_rate_usd": status.get("OBSERVED_30D_RUN_RATE_USD"), "payouts_count": status.get("PAYOUTS_COUNT"), "human_gate_count": gates, "active_task": ({"id": active.get("canonical_opportunity_id"), "source": active.get("source"), "status": active.get("upstream_status"), "reward_gross": active.get("reward_gross")} if active else None), "last_payment": last_payment}
    def maybe_publish(self):
        cadence = int(self.cfg.get("observatory_publish_seconds", 300)); last = float(self.state.get("last_observatory_publish", 0))
        if time.time() - last < cadence: return
        self.bus.publish_observatory(self.sanitized_status()); self.state["last_observatory_publish"] = time.time(); self.save()
    def poll(self):
        self.maybe_publish(); since = self.state.get("last_seen_updated_at"); etag = self.state.get("etag") if self.state.get("etag_since") == since else None
        data, headers = self.bus.comments(etag, since)
        if data is None: self.save(); return
        new_etag = headers.get("ETag") if headers else None
        if new_etag: self.state["etag"] = new_etag; self.state["etag_since"] = since
        for comment in data:
            if str(comment.get("body") or "").startswith(RESULT_PREFIX): continue
            self.process(comment)
        self.save()


def main() -> int:
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if cfg.get("repository") != "simonkey888/ATM-Agent-Teller-Machine" or int(cfg.get("control_issue", 0)) != 4: raise RuntimeError("control identity mismatch")
    lock = ProcessLock(LOCK)
    try: lock.acquire()
    except SingletonLockError: return 3
    try:
        bus = GitHubBus(cfg); controller = Controller(cfg, bus); delay = int(cfg.get("poll_seconds", 30))
        while True:
            try: controller.poll(); delay = int(cfg.get("poll_seconds", 30))
            except Transient as exc: delay = min(int(cfg.get("max_backoff_seconds", 900)), max(delay * 2, exc.retry_after))
            except KeyboardInterrupt: return 130
            except Exception as exc:
                LOG_DIR.mkdir(parents=True, exist_ok=True)
                with (LOG_DIR / "controller-oci.log").open("a", encoding="utf-8") as fh: fh.write(f"{now()} {redact_text(str(exc))}\n")
                delay = min(int(cfg.get("max_backoff_seconds", 900)), max(30, delay * 2))
            time.sleep(delay)
    finally: lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
