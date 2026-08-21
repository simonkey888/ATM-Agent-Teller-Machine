from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .autonomy_fabric import CapabilityGap, CapabilityGapLedger, DemandClass
from .capability_registry import get_capability
from .capability_registry_v3 import CapabilityRegistryV3Store
from .episodes import EconomicEpisode, EpisodeState, EpisodeStore
from .skill_forge import DemandDrivenSkillForge, SandboxFixtureEvidence


FLYWHEEL_SCHEMA = "ATM_CAPABILITY_FLYWHEEL_RUNTIME_V1"
SANDBOX_REQUEST_SCHEMA = "ATM_FORGE_SANDBOX_REQUEST_V1"
ROOT = Path(__file__).resolve().parents[2]
SANDBOX_WORKER = ROOT / "src" / "atm_forge_sandbox_worker.py"
SECRET_MARKERS = ("TOKEN", "KEY", "SECRET", "PASSWORD", "CREDENTIAL", "MNEMONIC", "SEED", "WALLET")
SAFE_ENV_KEYS = ("SYSTEMROOT", "WINDIR", "SSL_CERT_FILE", "SSL_CERT_DIR")
SYNTHETIC_CAPABILITY = "SYNTHETIC_TEXT_KV_NORMALIZE_V1"


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_env() -> dict[str, str]:
    env = {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
    for key in SAFE_ENV_KEYS:
        value = os.environ.get(key)
        if value:
            env[key] = value
    if any(any(marker in key.upper() for marker in SECRET_MARKERS) for key in env):
        raise RuntimeError("FORGE_ENV_SECRET_FILTER_FAILED")
    return env


def sandbox_worker_sha256() -> str:
    return hashlib.sha256(SANDBOX_WORKER.read_bytes()).hexdigest()


class CapabilityFlywheelStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS forge_queue(
               request_id TEXT PRIMARY KEY,
               gap_fingerprint TEXT NOT NULL UNIQUE,
               episode_id TEXT NOT NULL,
               capability_id TEXT NOT NULL,
               work_class TEXT NOT NULL,
               synthetic INTEGER NOT NULL,
               economic_authority INTEGER NOT NULL DEFAULT 0,
               state TEXT NOT NULL,
               sandbox_receipt_json TEXT,
               sandbox_receipt_digest TEXT,
               promotion_json TEXT,
               created_at TEXT NOT NULL,
               updated_at TEXT NOT NULL)"""
        )

    def close(self) -> None:
        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.conn.close()

    def enqueue(self, *, gap_fingerprint: str, episode_id: str, capability_id: str, work_class: str, synthetic: bool) -> bool:
        request_id = "forge_" + hashlib.sha256(f"{gap_fingerprint}|{capability_id}".encode()).hexdigest()[:40]
        now = utcnow_iso()
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO forge_queue(request_id,gap_fingerprint,episode_id,capability_id,work_class,synthetic,economic_authority,state,created_at,updated_at) VALUES(?,?,?,?,?,?,0,'PENDING',?,?)",
            (request_id, gap_fingerprint, episode_id, capability_id, work_class, int(bool(synthetic)), now, now),
        )
        return cur.rowcount == 1

    def next_pending(self) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM forge_queue WHERE state='PENDING' ORDER BY synthetic ASC,created_at ASC LIMIT 1").fetchone()
        return dict(row) if row else None

    def update(self, request_id: str, *, state: str, receipt: dict[str, Any] | None = None, receipt_digest: str | None = None, promotion: dict[str, Any] | None = None) -> None:
        self.conn.execute(
            "UPDATE forge_queue SET state=?,sandbox_receipt_json=COALESCE(?,sandbox_receipt_json),sandbox_receipt_digest=COALESCE(?,sandbox_receipt_digest),promotion_json=COALESCE(?,promotion_json),updated_at=? WHERE request_id=?",
            (state, json.dumps(receipt, sort_keys=True) if receipt is not None else None, receipt_digest, json.dumps(promotion, sort_keys=True) if promotion is not None else None, utcnow_iso(), request_id),
        )

    def summary(self) -> dict[str, Any]:
        counts = {str(row[0]): int(row[1]) for row in self.conn.execute("SELECT state,COUNT(*) FROM forge_queue GROUP BY state")}
        last = self.conn.execute("SELECT * FROM forge_queue ORDER BY updated_at DESC LIMIT 1").fetchone()
        return {
            "schema": FLYWHEEL_SCHEMA,
            "queue_counts": counts,
            "last_request": dict(last) if last else None,
            "economic_authority": False,
            "synthetic_is_non_economic": True,
        }


def _episode_for_gap(gap: CapabilityGap, episodes_path: Path) -> EconomicEpisode:
    store = EpisodeStore(episodes_path)
    try:
        acceptance_hash = hashlib.sha256(f"gap|{gap.fingerprint()}|{gap.work_class_candidate}".encode()).hexdigest()
        episode_id = "episode_" + hashlib.sha256(f"{gap.canonical_opportunity_id}|{acceptance_hash}".encode()).hexdigest()[:40]
        try:
            episode = store.get(episode_id)
        except KeyError:
            episode = store.create_option(
                canonical_opportunity_id=gap.canonical_opportunity_id,
                source=gap.source,
                opportunity_type=f"CAPABILITY_GAP:{gap.work_class_candidate}",
                acceptance_contract_hash=acceptance_hash,
                funding_evidence_refs=list(gap.evidence_refs),
                max_time_budget_seconds=900,
                kill_condition="FORGE_FAIL_CLOSED_OR_DEMAND_STALE",
                book="OPTION_BOOK",
                option_value={"demand_class": gap.demand_class.value, "synthetic": gap.synthetic, "economic_authority": False},
            )
        progress = dict(episode.progress_summary)
        progress["capability_gap_fingerprint"] = gap.fingerprint()
        progress["capability_gap_reason"] = gap.capability_gap_reason
        progress["synthetic"] = gap.synthetic
        return store.put(episode.model_copy(update={"progress_summary": progress, "economic_state": EpisodeState.OPTION}))
    finally:
        store.close()


def enqueue_gap(gap: CapabilityGap, *, flywheel_path: Path, episodes_path: Path) -> tuple[bool, str]:
    episode = _episode_for_gap(gap, episodes_path)
    capability_id = SYNTHETIC_CAPABILITY if gap.synthetic else f"FORGE_{gap.work_class_candidate}"
    queue = CapabilityFlywheelStore(flywheel_path)
    try:
        inserted = queue.enqueue(
            gap_fingerprint=gap.fingerprint(), episode_id=episode.episode_id,
            capability_id=capability_id, work_class=gap.work_class_candidate, synthetic=gap.synthetic,
        )
        return inserted, episode.episode_id
    finally:
        queue.close()


def ensure_synthetic_plumbing_if_no_real_gap(*, ledger_path: Path, flywheel_path: Path, episodes_path: Path) -> bool:
    ledger = CapabilityGapLedger(ledger_path)
    try:
        real = ledger.conn.execute("SELECT COUNT(*) FROM capability_gaps WHERE synthetic=0").fetchone()[0]
        synthetic = ledger.conn.execute("SELECT COUNT(*) FROM capability_gaps WHERE synthetic=1").fetchone()[0]
        if int(real) > 0 or int(synthetic) > 0:
            return False
        gap = CapabilityGap(
            canonical_opportunity_id="synthetic:order016-forge-plumbing",
            source="synthetic-fixture",
            external_id="order016-forge-plumbing",
            observed_at=utcnow_iso(),
            fresh_object_hash=hashlib.sha256(b"ORDER016_SYNTHETIC_FORGE_PLUMBING_V1").hexdigest(),
            work_class_candidate="SYNTHETIC_TEXT_KV_NORMALIZE",
            requirements=("text/plain",),
            net_reward_if_verified=None,
            funding_verification_state="SYNTHETIC_NON_ECONOMIC",
            competition_if_known=None,
            rejection_reason="SYNTHETIC_PLUMBING_ONLY",
            capability_gap_reason="SYNTHETIC_PLUMBING_ONLY",
            evidence_refs=("synthetic:ORDER016",),
            currentness_state="SYNTHETIC",
            demand_class=DemandClass.SYNTHETIC_FIXTURE,
            synthetic=True,
        )
        ledger.record(gap)
    finally:
        ledger.close()
    enqueue_gap(gap, flywheel_path=flywheel_path, episodes_path=episodes_path)
    return True


def _run_sandbox(capability_id: str, *, synthetic: bool, gap_fingerprint: str, episode_id: str) -> tuple[dict[str, Any], str]:
    worker_sha = sandbox_worker_sha256()
    request = {
        "schema": SANDBOX_REQUEST_SCHEMA,
        "capability_id": capability_id,
        "synthetic": bool(synthetic),
        "gap_fingerprint": gap_fingerprint,
        "episode_id": episode_id,
        "network_policy": "DENY",
        "tool_policy": "FIXED_PATTERN_EXECUTOR_CHECKER_ONLY",
        "parent_pid": os.getpid(),
        "worker_code_sha256": worker_sha,
    }
    raw = json.dumps(request, sort_keys=True, separators=(",", ":"))
    cp = subprocess.run(
        [sys.executable, "-I", str(SANDBOX_WORKER)], input=raw, text=True, capture_output=True,
        cwd=str(ROOT / "src"), env=_clean_env(), timeout=60, check=False,
    )
    if cp.returncode != 0:
        raise RuntimeError("FORGE_SANDBOX_FAILED:" + (cp.stderr or cp.stdout or "")[-500:])
    envelope = json.loads(cp.stdout)
    result = envelope.get("result")
    provenance = envelope.get("provenance")
    digest = str(envelope.get("receipt_digest") or "")
    if not isinstance(result, dict) or not isinstance(provenance, dict):
        raise RuntimeError("FORGE_SANDBOX_RECEIPT_INVALID")
    expected_request = hashlib.sha256(raw.encode()).hexdigest()
    expected_result = hashlib.sha256(json.dumps(result, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    expected_digest = hashlib.sha256(json.dumps(provenance, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    checks = (
        provenance.get("worker_code_sha256") == worker_sha,
        provenance.get("request_sha256") == expected_request,
        provenance.get("result_sha256") == expected_result,
        digest == expected_digest,
        provenance.get("process_isolated") is True,
        int(provenance.get("environment_secret_count", -1)) == 0,
        provenance.get("network_policy") == "DENY",
        provenance.get("tool_policy") == "FIXED_PATTERN_EXECUTOR_CHECKER_ONLY",
    )
    if not all(checks):
        raise RuntimeError("FORGE_SANDBOX_PROVENANCE_VERIFICATION_FAILED")
    return envelope, digest


def run_flywheel_once(*, flywheel_path: Path, registry_path: Path) -> dict[str, Any]:
    queue = CapabilityFlywheelStore(flywheel_path)
    try:
        request = queue.next_pending()
        if not request:
            return {"schema": FLYWHEEL_SCHEMA, "state": "IDLE", "economic_authority": False}
        request_id = str(request["request_id"])
        try:
            envelope, receipt_digest = _run_sandbox(
                str(request["capability_id"]), synthetic=bool(request["synthetic"]),
                gap_fingerprint=str(request["gap_fingerprint"]), episode_id=str(request["episode_id"]),
            )
        except Exception as exc:
            queue.update(request_id, state="SANDBOX_FAILED", promotion={"error_class": type(exc).__name__})
            return {"schema": FLYWHEEL_SCHEMA, "state": "SANDBOX_FAILED", "error_class": type(exc).__name__, "economic_authority": False}
        result = dict(envelope["result"])
        if result.get("state") != "SANDBOX_COMPLETE":
            queue.update(request_id, state="NO_SAFE_PATTERN", receipt=envelope, receipt_digest=receipt_digest)
            return {"schema": FLYWHEEL_SCHEMA, "state": "NO_SAFE_PATTERN", "request_id": request_id, "economic_authority": False}
        evidence_rows = []
        for raw in result.get("fixture_evidence") or []:
            evidence_rows.append(SandboxFixtureEvidence(**{key: raw[key] for key in (
                "fixture_class", "sandbox_id", "process_isolated", "environment_secret_count", "network_policy",
                "tool_allowlist_enforced", "executor_pass", "checker_pass", "artifact_contract_pass",
            )}))
        capability_id = str(request["capability_id"])
        decision = DemandDrivenSkillForge().evaluate(
            capability_id, evidence_rows,
            commercial_use_ok=True,
            supply_chain_pinned=True,
            license_verified=True,
            existing_duplicate=get_capability(capability_id) is not None,
            falsification_pass=bool(result.get("falsification_pass")),
            benchmark_pass=bool(result.get("benchmark_pass")),
            owner_cost_usd="0",
        )
        decision_payload = {"schema": decision.schema, "capability_id": decision.capability_id, "promoted": decision.promoted, "failures": list(decision.failures)}
        if not decision.promoted:
            queue.update(request_id, state="PROMOTION_REJECTED", receipt=envelope, receipt_digest=receipt_digest, promotion=decision_payload)
            return {"schema": FLYWHEEL_SCHEMA, "state": "PROMOTION_REJECTED", "decision": decision_payload, "economic_authority": False}
        evidence_digest = hashlib.sha256(json.dumps(result, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
        registry = CapabilityRegistryV3Store(registry_path)
        try:
            persisted = registry.persist(
                decision, evidence_digest=evidence_digest, sandbox_receipt_digest=receipt_digest,
                synthetic=bool(request["synthetic"]), episode_id=str(request["episode_id"]),
                gap_fingerprint=str(request["gap_fingerprint"]), supervisor_authority="ATM_SUPERVISOR_ORDER016_V1",
            )
        finally:
            registry.close()
        state = "SYNTHETIC_PROMOTION_PROOF" if bool(request["synthetic"]) else "PROMOTED"
        queue.update(request_id, state=state, receipt=envelope, receipt_digest=receipt_digest, promotion=persisted)
        return {
            "schema": FLYWHEEL_SCHEMA, "state": state, "request_id": request_id,
            "capability_id": capability_id, "synthetic_non_economic": bool(request["synthetic"]),
            "activation_enabled": bool(persisted["activation_enabled"]), "sandbox_receipt_digest": receipt_digest,
            "evidence_digest": evidence_digest, "economic_authority": False,
        }
    finally:
        queue.close()


def public_flywheel_status(path: Path) -> dict[str, Any]:
    if not Path(path).exists():
        return {"schema": FLYWHEEL_SCHEMA, "state": "UNOBSERVED", "economic_authority": False}
    store = CapabilityFlywheelStore(path)
    try:
        return store.summary()
    finally:
        store.close()
