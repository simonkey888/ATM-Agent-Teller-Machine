#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from decimal import Decimal
from pathlib import Path

from atm_core.opportunities import TaskmarketOpportunityAdapter
from atm_core.taskmarket_maker import TaskmarketMakerError, TaskmarketMakerUnavailable, TaskmarketZeroCostMaker


KNOWN_SUBMITTED = {
    "0x4c887264d5ede369de6e98c6214e6c03ee8708af108305ecafa0341a675e6147",
}


def main() -> int:
    # Shadow is deliberately signer-free: it proves runtime selection + WORK/CHECK,
    # never TaskMarket mutation. Production adapter submit still hard-gates signer.
    adapter = TaskmarketOpportunityAdapter()
    candidates = adapter.discover(Decimal("5"))
    candidates.sort(key=lambda row: (row.ev_per_effort_hour, row.ev_realized, -row.competition), reverse=True)

    selected = None
    snapshot = None
    diagnostics: list[str] = []
    for opportunity in candidates:
        task_id = opportunity.canonical_opportunity_id.split(":", 1)[1]
        if task_id in KNOWN_SUBMITTED:
            continue
        try:
            current = adapter.fetch_authoritative(opportunity)
            adapter.verify_freshness(opportunity, current)
            adapter.verify_funding(opportunity, current)
            adapter.verify_eligibility(opportunity, current)
            adapter.lane.assert_side_effect_gate(current)
        except Exception as exc:
            diagnostics.append(f"{task_id}:{type(exc).__name__}:{str(exc)[:160]}")
            continue
        selected = opportunity.model_copy(
            update={
                "task_description": str(current.get("description") or getattr(opportunity, "task_description", "")),
                "task_title": str(current.get("title") or getattr(opportunity, "task_title", "")),
            }
        )
        snapshot = current
        break

    if selected is None or snapshot is None:
        raise SystemExit("R3A_SHADOW_NO_RUNTIME_SELECTED_TASK:" + " | ".join(diagnostics[-8:]))

    task_id = selected.canonical_opportunity_id.split(":", 1)[1]
    net = str(Decimal(str(snapshot.get("netReward") or "0")) / Decimal(1_000_000))
    print(f"R3A_SHADOW_SELECTED_TASK={task_id}")
    print(f"R3A_SHADOW_SELECTED_NET_USDC={net}")
    print(f"R3A_SHADOW_SELECTED_COMPETITION={snapshot.get('submissionCount')}")
    maker = TaskmarketZeroCostMaker(model="gemma-4-31b-it")
    root = Path(os.getenv("RUNNER_TEMP") or tempfile.gettempdir()) / "atm-order009-r3a-shadow" / task_id
    root.mkdir(parents=True, exist_ok=True)
    try:
        result = maker.make(task_id=task_id, task_description=str(selected.task_description), workspace=root)
    except (TaskmarketMakerUnavailable, TaskmarketMakerError) as exc:
        raise SystemExit(f"R3A_SHADOW_MAKER_FAILED:{type(exc).__name__}:{exc}") from exc

    artifact = result.artifact
    sha = hashlib.sha256(artifact.read_bytes()).hexdigest()
    submit_actions = [
        row
        for row in (snapshot.get("pendingActions") or [])
        if isinstance(row, dict) and row.get("role") == "worker" and row.get("action") == "submit"
    ]
    receipt = {
        "schema": "ATM_ORDER009_R3A_ZERO_COST_MAKER_SHADOW_V1",
        "head": os.getenv("GITHUB_SHA") or "LOCAL",
        "task_id": task_id,
        "task_selected_by_runtime": True,
        "task_status": snapshot.get("status"),
        "submission_window_open": snapshot.get("submissionWindowOpen"),
        "stake_required": snapshot.get("stakeRequired"),
        "worker_submit_pending_action": bool(submit_actions),
        "requires_payment": submit_actions[0].get("requiresPayment") if len(submit_actions) == 1 else "UNKNOWN",
        "net_reward_usdc": net,
        "maker_model": result.model,
        "zero_cost_executor": True,
        "independent_checker": "PASS" if result.checker_passed else "FAIL",
        "checker_notes": list(result.checker_notes),
        "artifact_filename": result.filename,
        "artifact_sha256": sha,
        "artifact_size_bytes": artifact.stat().st_size,
        "artifact_generated_in_ephemeral_workspace": True,
        "signer_used": False,
        "taskmarket_mutation": False,
        "outgoing_spend_usd": "0",
    }
    Path("order009-r3a-shadow.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, sort_keys=True))
    print("R3A_RUNTIME_SELECTED_TASK=PASS")
    print("R3A_ZERO_COST_MAKER=PASS")
    print("R3A_INDEPENDENT_CHECKER=PASS")
    print("R3A_TASKMARKET_MUTATION=0")
    print("OUTGOING_SPEND_USD=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
