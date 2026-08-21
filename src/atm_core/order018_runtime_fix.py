from __future__ import annotations

import re
from typing import Any

_INSTALLED = False


def install() -> None:
    """Close exact-head ORDER-018 regressions without changing economic authority."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    # Linux Docker applies RLIMIT_NPROC against the host kernel UID. The former
    # value (16) can be exhausted by unrelated hosted-runner processes before
    # the container can exec Python. Keep a finite hard process boundary, but
    # raise the per-UID RLIMIT/cgroup cap to 64 so the container can start while
    # remaining tightly bounded and independently enforced by pids.max.
    from . import role_runtime as role_runtime
    from .sandbox_boundary_v2 import SandboxLimits as _RealSandboxLimits

    def _runtime_limits(
        cpu_seconds: int = 30,
        memory_mb: int = 512,
        max_file_bytes: int = 8 * 1024 * 1024,
        max_open_files: int = 64,
        max_processes: int = 16,
        wall_seconds: int = 120,
    ) -> _RealSandboxLimits:
        return _RealSandboxLimits(
            cpu_seconds=cpu_seconds,
            memory_mb=memory_mb,
            max_file_bytes=max_file_bytes,
            max_open_files=max_open_files,
            max_processes=max(64, int(max_processes)),
            wall_seconds=wall_seconds,
        )

    role_runtime.SandboxLimits = _runtime_limits  # type: ignore[assignment]

    # ORDER-018 reconciles competition from the freshest authoritative sources.
    # Direct Canon callers still need an explicit count in the supplied fresh
    # object to remain valid; absence is UNKNOWN, never zero.
    from . import cash_canon as canon
    from . import order018_taskmarket as tm
    from . import taskmarket_cli as cli

    _decision = tm.order018_taskmarket_cash_decision

    def _hardened_decision(
        task: dict[str, Any],
        *,
        canonical_wallet: str,
        existing_submission: bool,
        signer_ready: bool,
        now=None,
        max_competition: int = 12,
        admission_mode: str = "ECONOMIC",
        shadow_contract: dict[str, Any] | None = None,
        capability_runtime_ready: bool | None = None,
        effective_competition: int | None = None,
        action_proof=None,
        settlement_evidence=None,
    ):
        if effective_competition is None:
            effective_competition = tm._explicit_count(task)
        result = _decision(
            task,
            canonical_wallet=canonical_wallet,
            existing_submission=existing_submission,
            signer_ready=signer_ready,
            now=now,
            max_competition=max_competition,
            admission_mode=admission_mode,
            shadow_contract=shadow_contract,
            capability_runtime_ready=capability_runtime_ready,
            effective_competition=effective_competition,
            action_proof=action_proof,
            settlement_evidence=settlement_evidence,
        )

        # Preserve the pre-existing rights/paid-dependency kill. The settlement
        # layer must never weaken a previously proven safety rejection.
        description = str(task.get("description") or "")
        forbidden_video = result.work_class == "VIDEO_SHORT_FORM_ASSEMBLY" and bool(
            re.search(
                r"\b(?:paid (?:stock|voice|api)|celebrity likeness|copyrighted footage|post (?:to|on) (?:youtube|tiktok|instagram))\b",
                description,
                re.I,
            )
        )
        if forbidden_video and "VIDEO_RIGHTS_OR_PAID_DEPENDENCY_UNSUPPORTED" not in result.reasons:
            reasons = tuple(sorted(set((*result.reasons, "VIDEO_RIGHTS_OR_PAID_DEPENDENCY_UNSUPPORTED"))))
            disposition = result.disposition if not result.allocation_allowed else "REJECTED"
            result = canon.TaskmarketCanonDecision(
                disposition=disposition,
                reasons=reasons,
                task_id=result.task_id,
                authoritative_object_hash=result.authoritative_object_hash,
                net_reward_usdc=result.net_reward_usdc,
                competition=result.competition,
                work_class=result.work_class,
                executor_id=result.executor_id,
                checker_id=result.checker_id,
                attack_priority=result.attack_priority,
                admission_mode=result.admission_mode,
                allocation_allowed=False,
            )
        return result

    tm.order018_taskmarket_cash_decision = _hardened_decision  # type: ignore[assignment]
    canon.taskmarket_cash_decision = _hardened_decision  # type: ignore[assignment]

    # Submission uses the exact fresh *submit* pendingAction regardless of how
    # the worker entered the task (bounty vs a proven-free claim). Preserve
    # precise fail-closed reasons used by the mutation boundary and negatives.
    def _submit_side_effect_gate(self, task: dict[str, Any]) -> None:
        if task.get("stakeRequired") is not False:
            raise cli.TaskmarketCliError("TaskMarket task requires stake")
        rows = [
            row
            for row in (task.get("pendingActions") or [])
            if isinstance(row, dict)
            and row.get("role") == "worker"
            and str(row.get("action") or "").lower() == "submit"
        ]
        if len(rows) != 1:
            raise cli.TaskmarketCliError("exact worker submit pendingAction is unavailable")
        action = rows[0]
        eligible = str(action.get("eligibleAddress") or "").strip().lower()
        if eligible and eligible != self.canonical_wallet.lower():
            raise cli.TaskmarketCliError("worker submit pendingAction is not eligible for canonical wallet")
        if action.get("requiresPayment") is not False or action.get("paymentAmount") not in (None, "", 0, "0"):
            raise cli.TaskmarketCliError("TaskMarket submit requires outgoing payment")

    cli.TaskmarketCliLane.assert_side_effect_gate = _submit_side_effect_gate
