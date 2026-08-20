from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import tarfile
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from atm_swarm import MoneyBoard, ResourceGovernor

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / ".atm"
CLOUD_STATE_OBJECT = "atm-cloud-state.tgz"
AUTHORITY_OBJECT = "atm-authority.json"
CLOUD_CONTROL_FILE = STATE_DIR / "cloud-control.json"
COMMAND_STATE_FILE = STATE_DIR / "cloud-command-state.json"
BOARD_FILE = STATE_DIR / "money-board.sqlite3"
STATE_ALLOWLIST = {
    "state.json", "state.json.lkg", "validated-payment-proofs.jsonl", "monthly-targets.json",
    "human-gates.jsonl", "degraded-opportunities.json", "discovery-cache.json",
    "cloud-control.json", "cloud-command-state.json", "money-board.sqlite3", "oci-capacity.json",
}
COMMAND_RE = re.compile(
    r"\AATM_CMD_V1\nCOMMAND=(STATUS|DOCTOR|RUN_ONCE|START|STOP|PAUSE|RESUME)\nCOMMAND_ID=([A-Za-z0-9._:-]{1,128})\nARGS=(\{.*\})\s*\Z",
    re.S,
)


class CloudSafetyError(RuntimeError):
    pass


class CasConflict(CloudSafetyError):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    return (dt or utcnow()).astimezone(timezone.utc).isoformat()


def parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(timezone.utc)


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    tmp.write_text(json.dumps(payload, sort_keys=True, indent=2, default=str) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_source_sha() -> str:
    value = os.getenv("ATM_SOURCE_SHA") or os.getenv("GITHUB_SHA") or ""
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise CloudSafetyError("exact 40-char source SHA required")
    return value


class ObjectBackend:
    def get(self, name: str) -> tuple[bytes | None, str | None]:
        raise NotImplementedError

    def put(self, name: str, data: bytes, *, etag: str | None, create_only: bool = False) -> str:
        raise NotImplementedError


class OCIBackend(ObjectBackend):
    def __init__(self, config_file: str, profile: str):
        try:
            import oci  # type: ignore
        except Exception as exc:
            raise CloudSafetyError("OCI Python SDK unavailable") from exc
        self.oci = oci
        cfg = oci.config.from_file(config_file, profile)
        self.cfg = cfg
        tenancy = str(cfg["tenancy"])
        self.region = str(cfg["region"])
        self.bucket = os.getenv("ATM_STATE_BUCKET") or ("atm-state-" + hashlib.sha256(tenancy.encode()).hexdigest()[:12])
        self.client = oci.object_storage.ObjectStorageClient(cfg)
        self.namespace = str(self.client.get_namespace().data)
        try:
            self.client.get_bucket(self.namespace, self.bucket)
        except Exception as exc:
            raise CloudSafetyError(f"existing zero-cost state bucket unavailable: {self.bucket}") from exc

    def get(self, name: str) -> tuple[bytes | None, str | None]:
        try:
            response = self.client.get_object(self.namespace, self.bucket, name)
        except self.oci.exceptions.ServiceError as exc:  # type: ignore[attr-defined]
            if int(getattr(exc, "status", 0) or 0) == 404:
                return None, None
            raise
        data = response.data.content if hasattr(response.data, "content") else response.data.raw.read()
        etag = response.headers.get("etag") or response.headers.get("ETag")
        return bytes(data), str(etag) if etag else None

    def put(self, name: str, data: bytes, *, etag: str | None, create_only: bool = False) -> str:
        kwargs: dict[str, Any] = {}
        if create_only:
            kwargs["if_none_match"] = "*"
        elif etag:
            kwargs["if_match"] = etag
        else:
            raise CloudSafetyError("CAS update requires etag or create_only")
        try:
            response = self.client.put_object(self.namespace, self.bucket, name, data, **kwargs)
        except self.oci.exceptions.ServiceError as exc:  # type: ignore[attr-defined]
            if int(getattr(exc, "status", 0) or 0) in {409, 412}:
                raise CasConflict(name) from exc
            raise
        new_etag = response.headers.get("etag") or response.headers.get("ETag")
        if not new_etag:
            _, new_etag = self.get(name)
        if not new_etag:
            raise CloudSafetyError(f"object write returned no ETag: {name}")
        return str(new_etag)


class MemoryBackend(ObjectBackend):
    """Tests only."""
    def __init__(self):
        self.data: dict[str, tuple[bytes, str]] = {}
        self.n = 0

    def get(self, name: str) -> tuple[bytes | None, str | None]:
        row = self.data.get(name)
        return row if row else (None, None)

    def put(self, name: str, data: bytes, *, etag: str | None, create_only: bool = False) -> str:
        old = self.data.get(name)
        if create_only and old is not None:
            raise CasConflict(name)
        if not create_only and (old is None or etag != old[1]):
            raise CasConflict(name)
        self.n += 1
        new = f"e{self.n}"
        self.data[name] = (bytes(data), new)
        return new


def _tar_bytes(state_dir: Path, source_sha: str, board: MoneyBoard | None = None) -> bytes:
    if board:
        board.checkpoint()
    files: dict[str, bytes] = {}
    for name in sorted(STATE_ALLOWLIST):
        path = state_dir / name
        if path.exists() and path.is_file():
            files[name] = path.read_bytes()
    manifest = {
        "schema": "ATM_CLOUD_STATE_V1", "source_sha": source_sha, "created_at": iso(),
        "files": {name: {"size": len(data), "sha256": sha256(data)} for name, data in files.items()},
    }
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.PAX_FORMAT) as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(name=name); info.size = len(data); info.mode = 0o600; info.mtime = int(time.time())
            tf.addfile(info, io.BytesIO(data))
        raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        info = tarfile.TarInfo(name="manifest.json"); info.size = len(raw); info.mode = 0o600; info.mtime = int(time.time())
        tf.addfile(info, io.BytesIO(raw))
    return buf.getvalue()


def _restore_tar(raw: bytes, state_dir: Path, *, allow_legacy: bool = False) -> dict[str, Any]:
    if not raw:
        if allow_legacy:
            return {"schema": "ATM_EMPTY_LEGACY_STATE"}
        raise CloudSafetyError("empty cloud state object")
    try:
        tf = tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz")
    except tarfile.TarError as exc:
        raise CloudSafetyError("remote state archive is corrupt") from exc
    with tf:
        members = {m.name: m for m in tf.getmembers() if m.isfile()}
        if any("/" in name or "\\" in name or name.startswith(".") for name in members):
            raise CloudSafetyError("unsafe state archive path")
        if "manifest.json" not in members:
            if not allow_legacy:
                raise CloudSafetyError("cloud state manifest missing")
            unknown = set(members) - STATE_ALLOWLIST
            if unknown:
                raise CloudSafetyError(f"legacy state has unexpected files: {sorted(unknown)}")
            state_dir.mkdir(parents=True, exist_ok=True)
            for name, member in members.items():
                data = tf.extractfile(member).read()  # type: ignore[union-attr]
                (state_dir / name).write_bytes(data)
            return {"schema": "ATM_LEGACY_STATE_IMPORTED", "files": sorted(members)}
        manifest = json.loads(tf.extractfile(members["manifest.json"]).read())  # type: ignore[union-attr]
        if manifest.get("schema") != "ATM_CLOUD_STATE_V1" or not isinstance(manifest.get("files"), dict):
            raise CloudSafetyError("unsupported cloud state manifest")
        if set(manifest["files"]) - STATE_ALLOWLIST:
            raise CloudSafetyError("cloud state contains unexpected files")
        state_dir.mkdir(parents=True, exist_ok=True)
        for name, meta in manifest["files"].items():
            member = members.get(name)
            if not member:
                raise CloudSafetyError(f"manifest file missing: {name}")
            data = tf.extractfile(member).read()  # type: ignore[union-attr]
            if int(meta.get("size", -1)) != len(data) or meta.get("sha256") != sha256(data):
                raise CloudSafetyError(f"checksum mismatch: {name}")
            (state_dir / name).write_bytes(data)
        return manifest


def _validate_restored_state(state_dir: Path) -> None:
    state = state_dir / "state.json"
    if state.exists():
        try:
            payload = json.loads(state.read_text(encoding="utf-8"))
        except Exception as exc:
            raise CloudSafetyError("restored runtime state JSON is corrupt") from exc
        if not isinstance(payload, dict):
            raise CloudSafetyError("restored runtime state must be an object")
    ledger = state_dir / "validated-payment-proofs.jsonl"
    if ledger.exists():
        try:
            for line in ledger.read_text(encoding="utf-8").splitlines():
                if line.strip() and not isinstance(json.loads(line), dict):
                    raise ValueError("ledger row must be object")
        except Exception as exc:
            raise CloudSafetyError("restored payment ledger is corrupt") from exc
    board = state_dir / "money-board.sqlite3"
    if board.exists():
        import sqlite3
        try:
            conn = sqlite3.connect(f"file:{board}?mode=ro", uri=True)
            row = conn.execute("PRAGMA integrity_check").fetchone(); conn.close()
        except Exception as exc:
            raise CloudSafetyError("restored Money Board is corrupt") from exc
        if not row or row[0] != "ok":
            raise CloudSafetyError("restored Money Board integrity check failed")


class CloudStateSession:
    def __init__(self, backend: ObjectBackend, state_dir: Path, source_sha: str):
        self.backend = backend; self.state_dir = state_dir; self.source_sha = source_sha
        self.etag: str | None = None; self.exists = False

    def restore(self) -> dict[str, Any]:
        raw, etag = self.backend.get(CLOUD_STATE_OBJECT)
        if raw is None:
            legacy, _ = self.backend.get("atm-state.tgz")
            if legacy is not None:
                manifest = _restore_tar(legacy, self.state_dir, allow_legacy=True)
                _validate_restored_state(self.state_dir)
            else:
                manifest = {"schema": "ATM_CLOUD_STATE_NEW"}
            self.exists = False; self.etag = None
            return manifest
        manifest = _restore_tar(raw, self.state_dir)
        _validate_restored_state(self.state_dir)
        self.exists = True; self.etag = etag
        return manifest

    def checkpoint(self, board: MoneyBoard | None = None) -> str:
        raw = _tar_bytes(self.state_dir, self.source_sha, board)
        if self.exists:
            self.etag = self.backend.put(CLOUD_STATE_OBJECT, raw, etag=self.etag)
        else:
            self.etag = self.backend.put(CLOUD_STATE_OBJECT, raw, etag=None, create_only=True); self.exists = True
        return self.etag


@dataclass
class AuthorityLease:
    epoch: int
    holder: str
    lease_id: str
    expires_at: str
    source_sha: str
    etag: str


class AuthorityManager:
    def __init__(self, backend: ObjectBackend, source_sha: str, instance_state: Callable[[str], str] | None = None):
        self.backend = backend; self.source_sha = source_sha; self.instance_state = instance_state

    def _load(self) -> tuple[dict[str, Any] | None, str | None]:
        raw, etag = self.backend.get(AUTHORITY_OBJECT)
        if raw is None:
            return None, None
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CloudSafetyError("authority record corrupt") from exc
        required = {"epoch", "holder", "lease_id", "lease_expires_at", "heartbeat_at", "source_sha"}
        if not required.issubset(rec) or rec.get("holder") not in {"GITHUB_ACTIONS", "OCI"}:
            raise CloudSafetyError("authority record invalid")
        return rec, etag

    def acquire_github(self, ttl_seconds: int = 240) -> AuthorityLease | None:
        now = utcnow(); rec, etag = self._load()
        if rec and rec["holder"] == "OCI":
            ocid = str(rec.get("oci_instance_ocid") or "")
            if not ocid:
                raise CloudSafetyError("OCI authority has no canonical instance identity")
            if not self.instance_state:
                return None
            state = self.instance_state(ocid).upper()
            if state not in {"STOPPED", "TERMINATED"}:
                return None
        if rec:
            exp = parse_dt(rec.get("lease_expires_at"))
            if rec["holder"] == "GITHUB_ACTIONS" and exp and exp > now:
                return None
            epoch = int(rec["epoch"]) + 1
        else:
            epoch = 1
        lease_id = uuid.uuid4().hex
        new = {
            "schema": "ATM_AUTHORITY_V1", "epoch": epoch, "holder": "GITHUB_ACTIONS", "lease_id": lease_id,
            "lease_expires_at": iso(now + timedelta(seconds=max(60, ttl_seconds))), "heartbeat_at": iso(now),
            "oci_instance_ocid": None, "source_sha": self.source_sha,
        }
        raw = json.dumps(new, sort_keys=True, separators=(",", ":")).encode()
        new_etag = self.backend.put(AUTHORITY_OBJECT, raw, etag=etag, create_only=rec is None)
        return AuthorityLease(epoch, "GITHUB_ACTIONS", lease_id, new["lease_expires_at"], self.source_sha, new_etag)

    def renew(self, lease: AuthorityLease, ttl_seconds: int = 240) -> AuthorityLease:
        rec, etag = self._load()
        if not rec or rec.get("holder") != lease.holder or rec.get("lease_id") != lease.lease_id or int(rec["epoch"]) != lease.epoch:
            raise CasConflict("authority ownership lost")
        rec["lease_expires_at"] = iso(utcnow() + timedelta(seconds=max(60, ttl_seconds)))
        rec["heartbeat_at"] = iso(); rec["source_sha"] = self.source_sha
        new_etag = self.backend.put(AUTHORITY_OBJECT, json.dumps(rec, sort_keys=True, separators=(",", ":")).encode(), etag=etag)
        return AuthorityLease(lease.epoch, lease.holder, lease.lease_id, rec["lease_expires_at"], self.source_sha, new_etag)

    def release(self, lease: AuthorityLease) -> None:
        rec, etag = self._load()
        if not rec or rec.get("holder") != lease.holder or rec.get("lease_id") != lease.lease_id or int(rec["epoch"]) != lease.epoch:
            return
        rec["lease_expires_at"] = iso(utcnow() - timedelta(seconds=1)); rec["heartbeat_at"] = iso()
        self.backend.put(AUTHORITY_OBJECT, json.dumps(rec, sort_keys=True, separators=(",", ":")).encode(), etag=etag)

    def promote_oci(self, instance_ocid: str, expected_github_lease: AuthorityLease | None = None) -> dict[str, Any]:
        rec, etag = self._load(); now = utcnow()
        if rec and rec["holder"] == "GITHUB_ACTIONS":
            exp = parse_dt(rec.get("lease_expires_at"))
            if exp and exp > now and (expected_github_lease is None or rec.get("lease_id") != expected_github_lease.lease_id):
                raise CloudSafetyError("cannot promote while another GHA lease is live")
        epoch = (int(rec["epoch"]) if rec else 0) + 1
        new = {
            "schema": "ATM_AUTHORITY_V1", "epoch": epoch, "holder": "OCI", "lease_id": uuid.uuid4().hex,
            "lease_expires_at": iso(now + timedelta(days=3650)), "heartbeat_at": iso(now),
            "oci_instance_ocid": instance_ocid, "source_sha": self.source_sha,
        }
        self.backend.put(AUTHORITY_OBJECT, json.dumps(new, sort_keys=True, separators=(",", ":")).encode(), etag=etag, create_only=rec is None)
        return new


class GitHubBus:
    def __init__(self, token: str, repo: str = "simonkey888/ATM-Agent-Teller-Machine", issue: int = 4, owner: str = "simonkey888"):
        self.token = token; self.repo = repo; self.issue = issue; self.owner = owner; self.api = "https://api.github.com"

    def _request(self, method: str, path: str, payload: Any | None = None) -> Any:
        data = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(self.api + path, data=data, method=method, headers={
            "Authorization": "Bearer " + self.token, "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "ATM-Cloud/1.0", "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read(); return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[-1000:]
            raise CloudSafetyError(f"GitHub API {exc.code}: {body}") from exc

    def comments(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []; page = 1
        while True:
            chunk = self._request("GET", f"/repos/{self.repo}/issues/{self.issue}/comments?per_page=100&page={page}")
            if not isinstance(chunk, list):
                raise CloudSafetyError("GitHub comments response invalid")
            rows.extend(chunk)
            if len(chunk) < 100:
                return rows
            page += 1
            if page > 100:
                raise CloudSafetyError("GitHub comment pagination runaway")

    def bootstrap_floor(self, state: dict[str, Any]) -> bool:
        if "command_floor_comment_id" in state:
            return False
        rows = self.comments(); floor = max([int(r.get("id") or 0) for r in rows] or [0])
        state["command_floor_comment_id"] = floor; state["last_seen_comment_id"] = floor; state["processed_command_ids"] = []
        return True

    def pending(self, state: dict[str, Any]) -> list[tuple[dict[str, Any], str, str, dict[str, Any]]]:
        floor = int(state.get("command_floor_comment_id") or 0); seen = int(state.get("last_seen_comment_id") or floor)
        processed = set(state.get("processed_command_ids") or []); out = []
        for row in self.comments():
            cid = int(row.get("id") or 0)
            if cid <= max(floor, seen): continue
            state["last_seen_comment_id"] = max(int(state.get("last_seen_comment_id") or 0), cid)
            if str((row.get("user") or {}).get("login") or "") != self.owner: continue
            m = COMMAND_RE.fullmatch(str(row.get("body") or ""))
            if not m: continue
            verb, command_id, args_raw = m.groups()
            if command_id in processed: continue
            args = json.loads(args_raw)
            if args != {}: raise CloudSafetyError(f"ARGS must be empty for {verb}")
            out.append((row, verb, command_id, args))
        return out

    def post_result(self, command_id: str, verb: str, result: dict[str, Any], source_sha: str, authority_epoch: int) -> int:
        safe = json.dumps(result, sort_keys=True, default=str)[:5000]
        body = "\n".join(["ATM_RESULT_V1", f"COMMAND={verb}", f"COMMAND_ID={command_id}", "HOST_CLASS=GITHUB_ACTIONS",
                           f"SOURCE_SHA={source_sha}", f"AUTHORITY_EPOCH={authority_epoch}", "RESULT=" + safe])
        row = self._request("POST", f"/repos/{self.repo}/issues/{self.issue}/comments", {"body": body})
        return int(row["id"])

    def publish_observatory(self, status: dict[str, Any], issue: int = 7) -> int:
        safe = sanitize_status(status); marker = "ATM OBSERVATORY STATE"
        comments = self._request("GET", f"/repos/{self.repo}/issues/{issue}/comments?per_page=100&page=1")
        existing = None
        for row in comments if isinstance(comments, list) else []:
            if str(row.get("body") or "").startswith(marker): existing = int(row["id"])
        body = marker + "\n```json\n" + json.dumps(safe, sort_keys=True, indent=2, default=str) + "\n```"
        if existing:
            row = self._request("PATCH", f"/repos/{self.repo}/issues/comments/{existing}", {"body": body})
        else:
            row = self._request("POST", f"/repos/{self.repo}/issues/{issue}/comments", {"body": body})
        return int(row["id"])


def sanitize_status(status: dict[str, Any]) -> dict[str, Any]:
    allowed = {"schema", "authority", "authority_epoch", "cloud_heartbeat_at", "runtime_sha", "phase", "state", "paused",
               "realized_withdrawable_usd", "monthly_target_usd", "monthly_gap_usd", "required_daily_run_rate_usd",
               "payouts_count", "human_gate_count", "human_gate_categories", "active_task", "oci_capacity", "swarm",
               "money_board", "last_result_status", "cycle", "owner_pc_in_production_graph", "windows_authority",
               "zero_spend", "cloud_run_id", "payout_destinations"}
    out = {k: v for k, v in status.items() if k in allowed}
    raw = json.dumps(out, default=str).lower()
    if any(x in raw for x in ["private_key", "seed", "mnemonic", "github_token", "google_api_key", "authorization", "bearer "]):
        raise CloudSafetyError("status sanitizer detected secret-like material")
    return out


def _control() -> dict[str, Any]:
    if CLOUD_CONTROL_FILE.exists():
        raw = json.loads(CLOUD_CONTROL_FILE.read_text())
        if isinstance(raw, dict): return raw
    return {"running": True, "paused": False, "updated_at": iso()}


def _save_control(control: dict[str, Any]) -> None:
    control["updated_at"] = iso(); atomic_json(CLOUD_CONTROL_FILE, control)


def _command_state() -> dict[str, Any]:
    if COMMAND_STATE_FILE.exists():
        raw = json.loads(COMMAND_STATE_FILE.read_text())
        if isinstance(raw, dict): return raw
    return {}


def _save_command_state(state: dict[str, Any]) -> None:
    state["processed_command_ids"] = list(state.get("processed_command_ids") or [])[-1000:]
    atomic_json(COMMAND_STATE_FILE, state)


def _opp_negative(opp: Any) -> list[str]:
    neg = []; status = str(getattr(opp, "upstream_status", "") or "").upper()
    if status not in {"OPEN", "ACTIVE", "AVAILABLE", "FUNDED"}: neg.append("CLOSED")
    if not getattr(opp, "funding_proof", None): neg.append("UNFUNDED")
    if int(getattr(opp, "competition", 0) or 0) >= 8: neg.append("SATURATED")
    if str(getattr(opp, "ai_policy", "UNKNOWN")).upper() in {"FORBIDDEN", "NO_AI"}: neg.append("AI_FORBIDDEN")
    if str(getattr(opp, "payment_method", "UNKNOWN")).upper() in {"UNKNOWN", "NONE"}: neg.append("PAYOUT_UNAVAILABLE")
    return neg


def swarm_shadow(config: dict[str, Any], adapters: dict[str, Any], board: MoneyBoard) -> dict[str, Any]:
    import atm_v2 as core
    gov = ResourceGovernor(); gov.validate()
    enabled = [(name, adapters[name]) for name in config.get("discovery_order", []) if name in adapters][:gov.max_scouts]
    minimum = config.get("min_reward_usd", 1); candidates: dict[str, Any] = {}; errors: dict[str, str] = {}
    def scout(name: str, adapter: Any) -> tuple[str, list[Any]]: return name, list(adapter.discover(minimum))
    with ThreadPoolExecutor(max_workers=max(2, min(gov.max_scouts, len(enabled) or 2))) as pool:
        futures = [pool.submit(scout, n, a) for n, a in enabled]
        for future in as_completed(futures):
            try:
                name, found = future.result()
                for opp in found:
                    payload = opp.model_dump(mode="json"); candidates[opp.canonical_opportunity_id] = opp
                    board.upsert_candidate(payload, scout=name, base_score=float(core.money_velocity(opp)), confidence=0.8,
                                           negative_evidence=_opp_negative(opp), status="NORMALIZED")
            except Exception as exc:
                errors[str(len(errors))] = str(exc)[-500:]
    board.expire_stale(); top = board.top(limit=1)
    if top:
        cid = top[0]["canonical_id"]; opp = candidates.get(cid)
        if opp is not None:
            adapter = adapters.get(str(getattr(opp, "source", "")))
            if adapter is not None:
                try:
                    snap = adapter.fetch_authoritative(opp); adapter.verify_freshness(opp, snap); adapter.verify_funding(opp, snap); adapter.verify_eligibility(opp, snap)
                    board.mark_falsifier(cid, "CONFIRM", confidence=0.9)
                except Exception as exc:
                    board.mark_falsifier(cid, "KILL", reason=type(exc).__name__, confidence=0.9)
    stats = board.stats(); stats["errors"] = errors
    return {"stats": stats, "live_candidates": candidates}


def build_status(core: Any, state: Any, ledger: Any, targets: Any, board: MoneyBoard, lease: AuthorityLease, control: dict[str, Any]) -> dict[str, Any]:
    base = core.status_payload(state, ledger, targets); stats = board.stats(); active = None
    if state.active_opportunity:
        active = {"id": state.active_opportunity.canonical_opportunity_id, "source": state.active_opportunity.source, "url": state.active_opportunity.authoritative_url}
    capacity = "CAPACITY_DEFERRED_NONBLOCKING"
    cap = STATE_DIR / "oci-capacity.json"
    if cap.exists():
        try: capacity = json.loads(cap.read_text()).get("state", capacity)
        except Exception: pass
    return {"schema":"ATM_CLOUD_STATUS_V1", "authority":"GITHUB_ACTIONS", "authority_epoch":lease.epoch,
            "cloud_heartbeat_at":iso(), "runtime_sha":lease.source_sha, "phase":base.get("phase"),
            "state":"PAUSED" if control.get("paused") else ("RUNNING" if control.get("running") else "STOPPED"),
            "paused":bool(control.get("paused")), "cycle":base.get("cycle",0),
            "realized_withdrawable_usd":base.get("MONTHLY_REALIZED_WITHDRAWABLE_USD","0"),
            "monthly_target_usd":base.get("MONTHLY_TARGET_USD","500"), "monthly_gap_usd":base.get("MONTHLY_GAP_USD","500"),
            "required_daily_run_rate_usd":base.get("REQUIRED_DAILY_RUN_RATE_USD","0"), "payouts_count":base.get("PAYOUTS_COUNT",0),
            "human_gate_count":0, "human_gate_categories":[], "active_task":active, "oci_capacity":capacity,
            "payout_destinations":base.get("payout_destinations", []),
            "swarm":{"enabled":True,"active":True,"scouts":stats["scout_count"],"falsifiers":stats["falsified_count"],"workers":1,"watchers":1},
            "money_board":{"states":stats["candidate_counts"],"active_leases":stats["active_leases"]},
            "last_result_status":(state.last_result or {}).get("status") if isinstance(state.last_result,dict) else None,
            "owner_pc_in_production_graph":0, "windows_authority":0, "zero_spend":True, "cloud_run_id":os.getenv("GITHUB_RUN_ID")}


def _instance_state_factory(config_file: str, profile: str) -> Callable[[str], str]:
    def state(ocid: str) -> str:
        import oci  # type: ignore
        cfg = oci.config.from_file(config_file, profile)
        return str(oci.core.ComputeClient(cfg).get_instance(ocid).data.lifecycle_state)
    return state


def service_commands(bus: GitHubBus, command_state: dict[str, Any], control: dict[str, Any], source_sha: str, lease: AuthorityLease,
                     status_cb: Callable[[], dict[str, Any]], run_once_cb: Callable[[], dict[str, Any]]) -> None:
    if bus.bootstrap_floor(command_state):
        _save_command_state(command_state); return
    for row, verb, command_id, _ in bus.pending(command_state):
        if verb == "START": control["running"] = True; result = {"ok":True,"state":"RUNNING"}
        elif verb == "STOP": control["running"] = False; result = {"ok":True,"state":"STOPPED","control_plane_alive":True}
        elif verb == "PAUSE": control["paused"] = True; result = {"ok":True,"state":"PAUSED"}
        elif verb == "RESUME": control["paused"] = False; control["running"] = True; result = {"ok":True,"state":"RUNNING"}
        elif verb in {"STATUS","DOCTOR"}: result = status_cb(); result["doctor"] = "PASS" if verb == "DOCTOR" else None
        elif verb == "RUN_ONCE": result = run_once_cb()
        else: raise CloudSafetyError("unexpected command")
        _save_control(control); result_id = bus.post_result(command_id, verb, result, source_sha, lease.epoch)
        processed = list(command_state.get("processed_command_ids") or []); processed.append(command_id)
        command_state["processed_command_ids"] = processed[-1000:]
        command_state["last_seen_comment_id"] = max(int(command_state.get("last_seen_comment_id") or 0), int(row["id"]))
        command_state["last_result_comment_id"] = result_id; _save_command_state(command_state)


def run_cloud_cycle(backend: ObjectBackend | None = None) -> dict[str, Any]:
    source_sha = safe_source_sha()
    if os.getenv("GITHUB_ACTIONS") != "true" and not os.getenv("ATM_ALLOW_LOCAL_CLOUD_TEST"):
        raise CloudSafetyError("production cloud cycle requires GitHub Actions")
    if os.getenv("ATM_REPO_VISIBILITY", "public") != "public":
        raise CloudSafetyError("zero-spend cloud cycle requires public repository standard runner")
    config_file = os.getenv("OCI_CLI_CONFIG_FILE") or str(Path.home()/".oci"/"config"); profile = os.getenv("OCI_CLI_PROFILE") or "ATM_REMOTE"
    backend = backend or OCIBackend(config_file, profile); STATE_DIR.mkdir(parents=True, exist_ok=True)
    session = CloudStateSession(backend, STATE_DIR, source_sha); restored = session.restore()
    import atm as v1
    import atm_v2 as core
    from atm_core.models import HumanGate, Phase
    from atm_core.opportunities import HumanGateRequired, OpportunityValidationError
    from atm_core.payments import PaymentLedger, PaymentNotFinal, PaymentValidationError
    from atm_core.state import StateStore
    config = v1.load_config(); wallet = os.getenv("ATM_BASE_WALLET_ADDRESS", "")
    if not re.fullmatch(r"0x[0-9a-fA-F]{40}", wallet): raise CloudSafetyError("canonical public payout wallet missing")
    config["payment_recipient_public_identifier"] = wallet; atomic_json(ROOT/"config"/"atm.json", config)
    instance_state = None if isinstance(backend, MemoryBackend) else _instance_state_factory(config_file, profile)
    authority = AuthorityManager(backend, source_sha, instance_state); lease = authority.acquire_github(int(os.getenv("ATM_CLOUD_LEASE_SECONDS","240")))
    if lease is None: return {"status":"ECONOMIC_NOOP_AUTHORITY_OCI_OR_LIVE_GHA","restored":restored}
    board = MoneyBoard(BOARD_FILE); store = StateStore(core.STATE_FILE); state = store.load(max(core.MONTH1_FLOOR,500))
    ledger = PaymentLedger(core.PAYMENT_LEDGER_FILE); targets = core.MonthlyTargetStore(core.TARGET_FILE, core.MONTH1_FLOOR)
    state.target_paid_usd = targets.target_for(core.utcnow(), ledger.load()); adapters = core.build_adapters(config)
    control = _control(); command_state = _command_state(); token = os.getenv("ATM_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN") or ""
    bus = GitHubBus(token) if token else None; deadline = time.monotonic()+max(30,min(210,int(os.getenv("ATM_CLOUD_SLICE_SECONDS","150"))))
    max_phases=max(1,min(8,int(os.getenv("ATM_CLOUD_MAX_PHASES","4")))); phase_count=0
    def checkpoint() -> None:
        store.save(state); board.checkpoint(); session.checkpoint(board); authority.renew(lease)
    def one_phase() -> dict[str, Any]:
        nonlocal phase_count, adapters, config
        if time.monotonic() >= deadline: return {"ok":False,"reason":"SLICE_DEADLINE"}
        config=v1.load_config(); config["payment_recipient_public_identifier"]=wallet; adapters=core.build_adapters(config)
        try:
            if control.get("paused") and state.phase not in {Phase.MONITOR,Phase.PAYMENT_VERIFY}:
                core.refresh_discovery_cache(config,adapters); state.last_result={"status":"PAUSED_SAFE_SCOUTING","held_phase":state.phase.value}; state.cycle+=1
            else:
                prior=state.phase; action_lease=None
                lease_class={Phase.VERIFY:"VERIFY",Phase.CLAIM:"CLAIM",Phase.WORK:"WORK",Phase.SUBMIT:"SUBMIT"}.get(prior)
                if lease_class and state.active_opportunity:
                    action_lease=board.acquire_lease(state.active_opportunity.canonical_opportunity_id,lease_class,f"gha:{lease.lease_id}",300)
                    if action_lease is None: return {"ok":False,"reason":"ACTION_LEASE_BUSY","phase":prior.value}
                try: core.run_cycle(config,state,adapters,ledger)
                finally:
                    if action_lease: board.release_lease(action_lease,state.phase.value)
            if state.human_gate: core.localize_gate(state.human_gate,state,config)
        except HumanGateRequired as exc: core.localize_gate(exc.gate,state,config)
        except (OpportunityValidationError,PaymentNotFinal,PaymentValidationError) as exc:
            failed_id=state.active_opportunity.canonical_opportunity_id if state.active_opportunity else None; failed_phase=state.phase; state.last_error=str(exc)
            if failed_phase in {Phase.VERIFY,Phase.CLAIM}:
                core._degrade_opportunity(failed_id,"VERIFY_ROUTE_INVALID" if failed_phase==Phase.VERIFY else "CLAIM_ROUTE_FAILED",config)
                state.active_opportunity=None; state.phase=Phase.DISCOVER
            elif failed_phase==Phase.PAYMENT_VERIFY: state.phase=Phase.MONITOR
        except Exception as exc:
            state.last_error=str(exc)[-1000:]
            if state.phase in {Phase.WORK,Phase.CHECK}:
                gate=HumanGate(kind="PROVIDER_ROUTE_UNAVAILABLE",reason=type(exc).__name__,exact_human_action="No recurring owner action required; route remains circuit-broken until configured provider is available.",resume_phase=state.phase,opportunity_id=state.active_opportunity.canonical_opportunity_id if state.active_opportunity else None)
                core.localize_gate(gate,state,config)
            else: raise
        phase_count+=1; checkpoint()
        return {"ok":True,"phase":state.phase.value,"cycle":state.cycle,"last_result":(state.last_result or {}).get("status") if isinstance(state.last_result,dict) else None}
    try:
        shadow=swarm_shadow(config,adapters,board); checkpoint()
        if bus:
            service_commands(bus,command_state,control,source_sha,lease,lambda:build_status(core,state,ledger,targets,board,lease,control),one_phase); checkpoint()
        if control.get("running"):
            while phase_count<max_phases and time.monotonic()<deadline:
                one_phase()
                if control.get("paused") and state.phase not in {Phase.MONITOR,Phase.PAYMENT_VERIFY}: break
        status=build_status(core,state,ledger,targets,board,lease,control); status["swarm"]["shadow_errors"]=len(shadow["stats"].get("errors",{}))
        if bus: bus.publish_observatory(status)
        checkpoint(); return status
    finally:
        try: board.close()
        finally: authority.release(lease)


def cli() -> int:
    p=argparse.ArgumentParser(); p.add_argument("command",choices=["run"]); args=p.parse_args()
    if args.command=="run": print(json.dumps(sanitize_status(run_cloud_cycle()),sort_keys=True,default=str)); return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(cli())
