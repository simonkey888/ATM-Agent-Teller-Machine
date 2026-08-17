from __future__ import annotations

import json

import atm_cloud
import atm_fabric
from atm_core.funding_gate import install_workprotocol_chain_funding_gate


# Extend the existing canonical durable cloud state; do not introduce a second supervisor.
atm_cloud.STATE_ALLOWLIST.add("worker-fabric.sqlite3")
install_workprotocol_chain_funding_gate()
atm_fabric.install()

_original_build_status = atm_cloud.build_status


def _fabric_build_status(core, state, ledger, targets, board, lease, control):
    status = _original_build_status(core, state, ledger, targets, board, lease, control)
    active = status.get("active_task")
    opp = getattr(state, "active_opportunity", None)
    if isinstance(active, dict) and opp is not None:
        active.update(
            {
                "selector": getattr(opp, "selection_provenance", None),
                "same_pass_rediscovered": bool(getattr(opp, "same_pass_rediscovered", False)),
                "worker_id": getattr(opp, "worker_id", None),
                "work_lease_id": getattr(opp, "worker_lease_id", None),
                "worker_commit_sha": getattr(opp, "worker_commit_sha", None),
            }
        )
    eligible = []
    for row in board.top(limit=5, eligible_only=True):
        eligible.append(
            {
                "canonical_id": row.get("canonical_id"),
                "source": row.get("source"),
                "status": row.get("status"),
                "falsifier_verdict": row.get("falsifier_verdict"),
                "verified_at": row.get("verified_at"),
                "scout_sources_json": row.get("scout_sources_json"),
            }
        )
    status.setdefault("money_board", {})["eligible_provenance"] = eligible
    return status


atm_cloud.build_status = _fabric_build_status


def main() -> int:
    status = atm_cloud.run_cloud_cycle()
    print(json.dumps(atm_cloud.sanitize_status(status), sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
