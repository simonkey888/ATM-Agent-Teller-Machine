from __future__ import annotations

import base64
import hashlib
import re
import threading
import time
import uuid
from decimal import Decimal
from typing import Any

_INSTALLED = False


def install() -> None:
    """Close exact-head ORDER-018 regressions without changing economic authority."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    from . import sandbox_boundary_v2 as sandbox_v2

    # RT8: parent-originated broker receipts. The child may write requests in the
    # shared transport directory, but a response only counts as authoritative if
    # its receipt id/body digest/url/method exactly match the parent in-memory
    # record created by the HTTPS broker during this sandbox invocation.
    _broker_sessions: dict[str, dict[str, dict[str, Any]]] = {}
    _sandbox_local = threading.local()
    _real_clean_env = sandbox_v2._clean_env
    _real_proxy_init = sandbox_v2.ReadOnlyHttpsFileProxy.__init__
    _real_proxy_handle = sandbox_v2.ReadOnlyHttpsFileProxy._handle
    _real_writeback = sandbox_v2._writeback_staging
    _real_structural_run = sandbox_v2.StructuralSandbox.run

    def _clean_env_with_session(policy, owner_home, sentinel):
        env = _real_clean_env(policy, owner_home, sentinel)
        session = uuid.uuid4().hex
        env["ATM_SANDBOX_BROKER_SESSION"] = session
        _broker_sessions[session] = {}
        _sandbox_local.session = session
        return env

    def _proxy_init_with_session(self, *args, **kwargs):
        _real_proxy_init(self, *args, **kwargs)
        self._atm_parent_session = str(getattr(_sandbox_local, "session", ""))

    def _parent_bound_handle(self, request):
        response = _real_proxy_handle(self, request)
        if response.get("ok") is True:
            body = base64.b64decode(str(response.get("body_b64") or ""))
            receipt_id = uuid.uuid4().hex
            record = {
                "receipt_id": receipt_id,
                "body_sha256": hashlib.sha256(body).hexdigest(),
                "url": str(request.get("url") or ""),
                "method": str(request.get("method") or "GET").upper(),
                "status": int(response.get("status") or 200),
                "observed_at_unix_ns": time.time_ns(),
                "allowlist_decision": "ALLOW",
            }
            session = str(getattr(self, "_atm_parent_session", ""))
            if not session or session not in _broker_sessions:
                raise RuntimeError("SANDBOX_BROKER_PARENT_SESSION_MISSING")
            _broker_sessions[session][receipt_id] = record
            response = dict(response)
            response.update({
                "parent_receipt_id": receipt_id,
                "parent_body_sha256": record["body_sha256"],
                "parent_request_url": record["url"],
                "parent_method": record["method"],
            })
        return response

    def _staging_observation(staging):
        max_bytes = int(getattr(_sandbox_local, "aggregate_max_bytes", 0) or 0)
        max_files = int(getattr(_sandbox_local, "aggregate_max_files", 0) or 0)
        total = 0
        count = 0
        for _original, stage in staging:
            for path in stage.rglob("*"):
                if path.is_symlink():
                    raise sandbox_v2.SandboxLimitExceeded("AGGREGATE_OUTPUT_SYMLINK")
                if not path.is_file():
                    continue
                count += 1
                total += int(path.stat().st_size)
                if max_files and count > max_files:
                    raise sandbox_v2.SandboxLimitExceeded("AGGREGATE_OUTPUT_FILES", f"{count}>{max_files}")
                if max_bytes and total > max_bytes:
                    raise sandbox_v2.SandboxLimitExceeded("AGGREGATE_OUTPUT_BYTES", f"{total}>{max_bytes}")
        observation = {"files": count, "bytes": total, "max_files": max_files, "max_bytes": max_bytes}
        _sandbox_local.aggregate_observation = observation
        return observation

    def _bounded_writeback(staging):
        _staging_observation(staging)
        return _real_writeback(staging)

    def _parent_bound_run(self, *, worker_name, request, policy, writable_paths=()):
        normalized = policy.normalized()
        _sandbox_local.aggregate_max_bytes = int(normalized.limits.max_file_bytes)
        _sandbox_local.aggregate_max_files = max(1, min(32, int(normalized.limits.max_open_files) // 2))
        _sandbox_local.aggregate_observation = {"files": 0, "bytes": 0, "max_files": _sandbox_local.aggregate_max_files, "max_bytes": _sandbox_local.aggregate_max_bytes}
        session = ""
        try:
            envelope = _real_structural_run(self, worker_name=worker_name, request=request, policy=normalized, writable_paths=writable_paths)
            session = str(getattr(_sandbox_local, "session", ""))
            parent_rows = _broker_sessions.get(session, {})
            proof = dict(envelope.get("sandbox") or {})
            consumed = proof.get("broker_receipts_consumed") or []
            if not isinstance(consumed, list):
                raise sandbox_v2.SandboxError("SANDBOX_BROKER_CONSUMPTION_RECEIPT_INVALID")
            if len(consumed) != len(parent_rows):
                raise sandbox_v2.SandboxError(f"SANDBOX_BROKER_PARENT_RECEIPT_COUNT_MISMATCH:{len(consumed)}!={len(parent_rows)}")
            for row in consumed:
                if not isinstance(row, dict):
                    raise sandbox_v2.SandboxError("SANDBOX_BROKER_CONSUMPTION_ROW_INVALID")
                receipt_id = str(row.get("receipt_id") or "")
                parent = parent_rows.get(receipt_id)
                if not parent:
                    raise sandbox_v2.SandboxError("SANDBOX_BROKER_FORGED_RECEIPT")
                for field in ("body_sha256", "url", "method"):
                    if str(row.get(field) or "") != str(parent.get(field) or ""):
                        raise sandbox_v2.SandboxError(f"SANDBOX_BROKER_PARENT_BINDING_MISMATCH:{field}")
            role = str(request.get("role") or "").upper()
            operation = str(request.get("operation") or "").upper()
            payload = request.get("payload") if isinstance(request.get("payload"), dict) else {}
            worker_result = ((envelope.get("worker") or {}).get("result") or {}) if isinstance(envelope.get("worker"), dict) else {}
            authoritative_read_required = bool(
                (role == "SCOUT" and operation == "DISCOVER" and (payload.get("sources") or []))
                or (role == "FALSIFIER" and operation == "VERIFY" and str(worker_result.get("verdict") or "") == "CONFIRM")
            )
            if authoritative_read_required and not consumed:
                raise sandbox_v2.SandboxError("SANDBOX_AUTHORITATIVE_READ_WITHOUT_PARENT_RECEIPT")
            proof["broker_parent_bound"] = True
            proof["broker_parent_receipt_count"] = len(parent_rows)
            proof["broker_parent_receipts_sha256"] = hashlib.sha256(
                __import__("json").dumps(sorted(parent_rows.values(), key=lambda x: x["receipt_id"]), sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            observation = dict(getattr(_sandbox_local, "aggregate_observation", {}) or {})
            proof["aggregate_output_bound_applied"] = True
            proof["aggregate_output_observed"] = observation
            envelope["sandbox"] = proof
            envelope["sandbox_receipt_digest"] = hashlib.sha256(__import__("json").dumps(proof, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
            return envelope
        finally:
            session = session or str(getattr(_sandbox_local, "session", ""))
            if session:
                _broker_sessions.pop(session, None)

    sandbox_v2._clean_env = _clean_env_with_session  # type: ignore[assignment]
    sandbox_v2.ReadOnlyHttpsFileProxy.__init__ = _proxy_init_with_session  # type: ignore[assignment]
    sandbox_v2.ReadOnlyHttpsFileProxy._handle = _parent_bound_handle  # type: ignore[assignment]
    sandbox_v2._writeback_staging = _bounded_writeback  # type: ignore[assignment]
    sandbox_v2.StructuralSandbox.run = _parent_bound_run  # type: ignore[assignment]

    # The Docker backend intentionally runs as the hosted-runner non-root UID.
    # Docker tmpfs mounts default to root ownership, so mode=700 made /home/atm
    # inaccessible to that child. Intercept only docker-create argv and bind the
    # sterile-home tmpfs owner to the exact --user UID/GID.
    _real_subprocess = sandbox_v2.subprocess

    class _SandboxSubprocessProxy:
        def run(self, args, *positional, **kwargs):
            adjusted = args
            if isinstance(args, list) and len(args) >= 2 and str(args[1]) == "create":
                adjusted = list(args)
                try:
                    user_index = adjusted.index("--user") + 1
                    uid, gid = str(adjusted[user_index]).split(":", 1)
                except (ValueError, IndexError):
                    uid = gid = ""
                if uid.isdigit() and gid.isdigit():
                    for index, value in enumerate(adjusted[:-1]):
                        if value != "--tmpfs": continue
                        spec = str(adjusted[index + 1])
                        if spec.startswith("/home/atm:") and "uid=" not in spec and "gid=" not in spec:
                            adjusted[index + 1] = f"{spec},uid={uid},gid={gid}"
            return _real_subprocess.run(adjusted, *positional, **kwargs)
        def __getattr__(self, name: str): return getattr(_real_subprocess, name)

    sandbox_v2.subprocess = _SandboxSubprocessProxy()  # type: ignore[assignment]

    # Hosted Linux RLIMIT_NPROC is charged against the kernel UID. Keep a finite
    # 256-process ceiling plus cgroup pids.max while preserving all other caps.
    from . import role_runtime as role_runtime
    from .sandbox_boundary_v2 import SandboxLimits as _RealSandboxLimits

    class _RuntimeSandboxLimits(_RealSandboxLimits):
        def normalized(self) -> "_RuntimeSandboxLimits":
            base = super().normalized()
            return _RuntimeSandboxLimits(cpu_seconds=base.cpu_seconds,memory_mb=base.memory_mb,max_file_bytes=base.max_file_bytes,max_open_files=base.max_open_files,max_processes=256,wall_seconds=base.wall_seconds)

    def _runtime_limits(cpu_seconds: int=30,memory_mb: int=512,max_file_bytes: int=8*1024*1024,max_open_files: int=64,max_processes: int=16,wall_seconds: int=120) -> _RuntimeSandboxLimits:
        del max_processes
        return _RuntimeSandboxLimits(cpu_seconds=cpu_seconds,memory_mb=memory_mb,max_file_bytes=max_file_bytes,max_open_files=max_open_files,max_processes=256,wall_seconds=wall_seconds)

    role_runtime.SandboxLimits = _runtime_limits  # type: ignore[assignment]

    # RT3: first TaskMarket selection is settlement-first and does not consume
    # guessed p_accept/p_pay. Allocation authority still lives in Canon verify.
    from . import money_velocity as mv
    _legacy_choose_money_velocity = mv.choose_money_velocity
    _tier_rank = {"S0":0,"S1":1,"S2":2,"S3":3,"S4":4}

    def _taskmarket_settlement_first_key(opportunity, now):
        explicit_tier=str(getattr(opportunity,"settlement_tier","") or "").upper()
        if explicit_tier in _tier_rank: tier=explicit_tier
        else:
            proof=opportunity.funding_proof or {}; funded=bool(proof.get("escrow") or proof.get("escrow_tx_hash") or proof.get("funding_tx_hash") or proof.get("settlement_tx_hash")); tier="S1" if funded else "S0"
        competition=int(opportunity.competition) if int(opportunity.competition)>=0 else 10**9
        effort=max(Decimal("0.01"),opportunity.total_effort_hours); net_per_effort=opportunity.reward_net/effort
        return (_tier_rank[tier],1 if bool(getattr(opportunity,"maker_supported",False)) else 0,mv.task_alive_probability(opportunity,now),-competition,net_per_effort,-mv.expected_minutes_to_withdrawable(opportunity),opportunity.reward_net)

    def _settlement_first_choose(opportunities, *, now=None, hard_default_hours=Decimal("3")):
        rows=list(opportunities); taskmarket=[row for row in rows if str(getattr(row,"source","")).lower()=="taskmarket" and mv.reject_reason(row,now) is None]
        if taskmarket:
            short=[row for row in taskmarket if row.expected_agent_hours<=hard_default_hours]; pool=short or taskmarket
            return max(pool,key=lambda row:_taskmarket_settlement_first_key(row,now))
        return _legacy_choose_money_velocity(rows,now=now,hard_default_hours=hard_default_hours)

    mv.choose_money_velocity=_settlement_first_choose  # type: ignore[assignment]
    mv.order018_taskmarket_selection_key=_taskmarket_settlement_first_key  # type: ignore[attr-defined]

    # RT2/RT4/RT5 Cash Canon hardening.
    from . import cash_canon as canon
    from . import order018_taskmarket as tm
    from . import taskmarket_cli as cli
    _decision=tm.order018_taskmarket_cash_decision

    def _hardened_decision(task:dict[str,Any],*,canonical_wallet:str,existing_submission:bool,signer_ready:bool,now=None,max_competition:int=12,admission_mode:str="ECONOMIC",shadow_contract:dict[str,Any]|None=None,capability_runtime_ready:bool|None=None,effective_competition:int|None=None,action_proof=None,settlement_evidence=None):
        if effective_competition is None: effective_competition=tm._explicit_count(task)
        result=_decision(task,canonical_wallet=canonical_wallet,existing_submission=existing_submission,signer_ready=signer_ready,now=now,max_competition=max_competition,admission_mode=admission_mode,shadow_contract=shadow_contract,capability_runtime_ready=capability_runtime_ready,effective_competition=effective_competition,action_proof=action_proof,settlement_evidence=settlement_evidence)
        description=str(task.get("description") or "")
        forbidden_video=result.work_class=="VIDEO_SHORT_FORM_ASSEMBLY" and bool(re.search(r"\b(?:paid (?:stock|voice|api)|celebrity likeness|copyrighted footage|post (?:to|on) (?:youtube|tiktok|instagram))\b",description,re.I))
        if forbidden_video and "VIDEO_RIGHTS_OR_PAID_DEPENDENCY_UNSUPPORTED" not in result.reasons:
            reasons=tuple(sorted(set((*result.reasons,"VIDEO_RIGHTS_OR_PAID_DEPENDENCY_UNSUPPORTED")))); disposition=result.disposition if not result.allocation_allowed else "REJECTED"
            result=canon.TaskmarketCanonDecision(disposition=disposition,reasons=reasons,task_id=result.task_id,authoritative_object_hash=result.authoritative_object_hash,net_reward_usdc=result.net_reward_usdc,competition=result.competition,work_class=result.work_class,executor_id=result.executor_id,checker_id=result.checker_id,attack_priority=result.attack_priority,admission_mode=result.admission_mode,allocation_allowed=False)
        return result

    tm.order018_taskmarket_cash_decision=_hardened_decision  # type: ignore[assignment]
    canon.taskmarket_cash_decision=_hardened_decision  # type: ignore[assignment]

    def _submit_side_effect_gate(self,task:dict[str,Any])->None:
        if task.get("stakeRequired") is not False: raise cli.TaskmarketCliError("TaskMarket task requires stake")
        rows=[row for row in (task.get("pendingActions") or []) if isinstance(row,dict) and row.get("role")=="worker" and str(row.get("action") or "").lower()=="submit"]
        if len(rows)!=1: raise cli.TaskmarketCliError("exact worker submit pendingAction is unavailable")
        action=rows[0]; eligible=str(action.get("eligibleAddress") or "").strip().lower()
        if eligible and eligible!=self.canonical_wallet.lower(): raise cli.TaskmarketCliError("worker submit pendingAction is not eligible for canonical wallet")
        if action.get("requiresPayment") is not False or action.get("paymentAmount") not in (None,"",0,"0"): raise cli.TaskmarketCliError("TaskMarket submit requires outgoing payment")

    cli.TaskmarketCliLane.assert_side_effect_gate=_submit_side_effect_gate
