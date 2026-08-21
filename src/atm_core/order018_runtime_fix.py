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

    # The Docker backend intentionally runs as the hosted-runner non-root UID.
    # Docker tmpfs mounts default to root ownership, so mode=700 made /home/atm
    # inaccessible to that child. Intercept only the module-local docker-create
    # argv and bind the sterile-home tmpfs owner to the exact --user UID/GID.
    # The child remains non-root; no supervisor HOME is mounted or inherited.
    from . import sandbox_boundary_v2 as sandbox_v2
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
                        if value != "--tmpfs":
                            continue
                        spec = str(adjusted[index + 1])
                        if not spec.startswith("/home/atm:"):
                            continue
                        if "uid=" not in spec and "gid=" not in spec:
                            adjusted[index + 1] = f"{spec},uid={uid},gid={gid}"
            return _real_subprocess.run(adjusted, *positional, **kwargs)

        def __getattr__(self, name: str):
            return getattr(_real_subprocess, name)

    sandbox_v2.subprocess = _SandboxSubprocessProxy()  # type: ignore[assignment]

    # Linux RLIMIT_NPROC is charged against the kernel UID, not the container's
    # cgroup alone. Hosted runners may already have >16 or >64 same-UID host
    # processes before ATM starts. Keep an explicit finite hard process ceiling
    # while avoiding false exhaustion: the economic role sandbox uses 256 for
    # both RLIMIT_NPROC and cgroup pids.max. CPU/memory/fd/file caps are unchanged.
    from . import role_runtime as role_runtime
    from .sandbox_boundary_v2 import SandboxLimits as _RealSandboxLimits

    class _RuntimeSandboxLimits(_RealSandboxLimits):
        def normalized(self) -> "_RuntimeSandboxLimits":
            base = super().normalized()
            return _RuntimeSandboxLimits(
                cpu_seconds=base.cpu_seconds,
                memory_mb=base.memory_mb,
                max_file_bytes=base.max_file_bytes,
                max_open_files=base.max_open_files,
                max_processes=256,
                wall_seconds=base.wall_seconds,
            )

    def _runtime_limits(
        cpu_seconds: int = 30,
        memory_mb: int = 512,
        max_file_bytes: int = 8 * 1024 * 1024,
        max_open_files: int = 64,
        max_processes: int = 16,
        wall_seconds: int = 120,
    ) -> _RuntimeSandboxLimits:
        del max_processes
        return _RuntimeSandboxLimits(
            cpu_seconds=cpu_seconds,
            memory_mb=memory_mb,
            max_file_bytes=max_file_bytes,
            max_open_files=max_open_files,
            max_processes=256,
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
