from __future__ import annotations

from typing import Any, Mapping

from .learning import SkillCapsule, SkillStage


PLAYBOOK_SCHEMA = "ATM_PLAYBOOK_CANDIDATE_V1"


def playbook_candidate(skill: SkillCapsule, evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Successful trajectories are only reusable after replay and test evidence exists."""
    terminal_outcome = str(evidence.get("terminal_outcome") or "").upper()
    checker_pass = bool(evidence.get("checker_pass"))
    payment_evidence = bool(evidence.get("payment_evidence_ref"))
    replay_pass = bool(evidence.get("replay_pass"))
    adversarial_pass = bool(evidence.get("adversarial_pass"))
    replay_ref = str(evidence.get("replay_ref") or "").strip()
    test_ref = str(evidence.get("test_ref") or "").strip()
    stage_ready = skill.stage in {
        SkillStage.ADVERSARIAL_TEST,
        SkillStage.SHADOW_LIVE,
        SkillStage.LOW_RISK_CANARY,
        SkillStage.PROMOTE,
    }
    eligible = all(
        (
            terminal_outcome in {"ACCEPTED", "SETTLED", "PAID"},
            checker_pass,
            payment_evidence,
            replay_pass,
            adversarial_pass,
            bool(replay_ref),
            bool(test_ref),
            stage_ready,
        )
    )
    failures = []
    if terminal_outcome not in {"ACCEPTED", "SETTLED", "PAID"}:
        failures.append("NO_SUCCESSFUL_TERMINAL_OUTCOME")
    if not checker_pass:
        failures.append("CHECKER_EVIDENCE_MISSING")
    if not payment_evidence:
        failures.append("PAYMENT_EVIDENCE_MISSING")
    if not replay_pass or not replay_ref:
        failures.append("REPLAY_NOT_PROVEN")
    if not adversarial_pass or not test_ref:
        failures.append("TEST_NOT_PROVEN")
    if not stage_ready:
        failures.append("SKILL_STAGE_BEFORE_REPLAY_TEST")
    return {
        "schema": PLAYBOOK_SCHEMA,
        "skill_id": skill.skill_id,
        "state": "PLAYBOOK_CANDIDATE" if eligible else "NOT_CANDIDATE",
        "eligible": eligible,
        "failures": failures,
        "opaque_self_praise": False,
    }
