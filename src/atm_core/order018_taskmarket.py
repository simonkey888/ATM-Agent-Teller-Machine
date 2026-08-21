from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from . import cash_canon as canon
from .models import Opportunity


@dataclass(frozen=True)
class SettlementEvidence:
    tier: str
    requester: str
    prior_settled_tasks: int
    settlement_tx_count: int
    objective_acceptance: bool
    worker_locked: bool
    permissionless_finalize_path: bool
    evidence_digest: str


@dataclass(frozen=True)
class ActionCostProof:
    action: str
    available_now: bool
    eligible_wallet: bool
    stake_zero: bool
    requires_payment_false: bool
    payment_amount_zero: bool
    mandatory_worker_cost_to_delivery_usd: str
    evidence_digest: str

    @property
    def zero_cost_ready(self) -> bool:
        return all((self.available_now,self.eligible_wallet,self.stake_zero,self.requires_payment_false,self.payment_amount_zero,self.mandatory_worker_cost_to_delivery_usd=="0"))


def _explicit_count(task: dict[str, Any]) -> int | None:
    for key in ("submissionCount","submissionsCount"):
        if key in task and task.get(key) is not None:
            try: return max(0,int(task[key]))
            except (TypeError,ValueError): return None
    rows=task.get("submissions")
    return len(rows) if isinstance(rows,list) else None


def reconcile_competition(opportunity: Opportunity | None, detail: dict[str, Any], submissions: list[dict[str, Any]] | None=None) -> int | None:
    values=[]
    if opportunity is not None: values.append(max(0,int(opportunity.competition)))
    detail_count=_explicit_count(detail)
    if detail_count is not None: values.append(detail_count)
    if submissions is not None: values.append(len(submissions))
    return max(values) if values else None


def action_cost_proof(task: dict[str, Any], canonical_wallet: str) -> ActionCostProof:
    mode=str(task.get("mode") or "bounty").lower(); required_action="submit" if mode=="bounty" else "claim" if mode=="claim" else ""
    rows=[row for row in (task.get("pendingActions") or []) if isinstance(row,dict) and row.get("role")=="worker" and str(row.get("action") or "").lower()==required_action]
    action=rows[0] if len(rows)==1 else {}; eligible=str(action.get("eligibleAddress") or "").strip().lower(); eligible_ok=bool(action) and (not eligible or eligible==canonical_wallet.lower())
    amount=action.get("paymentAmount"); payment_zero=amount in (None,"",0,"0"); requires_false=action.get("requiresPayment") is False; known_free_mode=mode in {"bounty","claim"}
    raw={"mode":mode,"action":required_action,"action_row":action,"stakeRequired":task.get("stakeRequired"),"canonical_wallet":canonical_wallet.lower(),"known_free_mode":known_free_mode}
    return ActionCostProof(required_action or "UNSUPPORTED_PAID_MODE",len(rows)==1 and known_free_mode,eligible_ok,task.get("stakeRequired") is False,requires_false and known_free_mode,payment_zero and known_free_mode,"0" if known_free_mode and requires_false and payment_zero and task.get("stakeRequired") is False else "UNKNOWN_OR_NONZERO",hashlib.sha256(json.dumps(raw,sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest())


def _objective_acceptance(description: str) -> bool:
    return bool(re.search(r"pass/fail|acceptance|observable requirements|exact(?:ly)? one|required (?:duration|resolution|columns?)|must (?:contain|match|return|include)",str(description),re.I))


def derive_settlement_evidence(adapter: Any, task: dict[str, Any], *, history_limit: int=8) -> SettlementEvidence:
    requester=str(task.get("requester") or "").strip(); settled=[]
    if requester:
        try:
            query=urllib.parse.urlencode({"status":"completed","requester":requester,"sort":"newest","limit":min(20,max(1,history_limit))}); page=adapter.http.get(f"{adapter.base_url}/api/tasks?{query}"); prior=page.get("tasks") if isinstance(page,dict) else []
            for row in (prior or [])[:history_limit]:
                tid=str((row or {}).get("id") or (row or {}).get("taskId") or "") if isinstance(row,dict) else ""
                if not tid: continue
                try: detail=adapter.http.get(f"{adapter.base_url}/api/tasks/{urllib.parse.quote(tid)}")
                except Exception: continue
                awards=detail.get("awards") or [] if isinstance(detail,dict) else []; txs=[str(a.get("settlementTxHash")) for a in awards if isinstance(a,dict) and str(a.get("settlementTxHash") or "").startswith("0x")]
                if txs: settled.append({"task_id":tid,"txs":sorted(set(txs))})
        except Exception: settled=[]
    worker_locked=bool(str(task.get("claimedBy") or "").strip()); objective=_objective_acceptance(str(task.get("description") or "")); permissionless=any(isinstance(row,dict) and row.get("role")=="anyone" and str(row.get("action") or "").lower() in {"finalize","settle"} for row in (task.get("pendingActions") or []))
    if not (task.get("escrowTxHash") or task.get("fundingTxHash") or task.get("createTxHash")): tier="S0"
    elif worker_locked and objective and permissionless: tier="S4"
    elif settled and objective: tier="S3"
    elif settled: tier="S2"
    else: tier="S1"
    raw={"requester":requester,"settled":settled,"objective":objective,"worker_locked":worker_locked,"permissionless_finalize":permissionless,"tier":tier}
    return SettlementEvidence(tier,requester,len(settled),sum(len(x["txs"]) for x in settled),objective,worker_locked,permissionless,hashlib.sha256(json.dumps(raw,sort_keys=True,separators=(",", ":")).encode()).hexdigest())


def order018_taskmarket_cash_decision(task: dict[str,Any], *, canonical_wallet: str, existing_submission: bool, signer_ready: bool, now: datetime|None=None, max_competition: int=12, admission_mode: str="ECONOMIC", shadow_contract: dict[str,Any]|None=None, capability_runtime_ready: bool|None=None, effective_competition: int|None=None, action_proof: ActionCostProof|None=None, settlement_evidence: SettlementEvidence|None=None):
    observed=(now or datetime.now(timezone.utc)).astimezone(timezone.utc); reasons=[]; task_id=str(task.get("id") or task.get("taskId") or "")
    if not task_id: reasons.append("AUTHORITATIVE_ID_MISSING")
    if existing_submission: reasons.append("ALREADY_SUBMITTED")
    if str(task.get("status") or "").lower()!="open" or str(task.get("phase") or "").lower() not in {"active","open"}: reasons.append("CLOSED")
    if task.get("submissionWindowOpen") is not True: reasons.append("NO_CURRENT_WORKER_ACTION")
    proof=action_proof or action_cost_proof(task,canonical_wallet)
    if not proof.available_now: reasons.append("NO_CURRENT_WORKER_ACTION")
    if not proof.eligible_wallet: reasons.append("IDENTITY_NOT_READY")
    if not proof.stake_zero: reasons.append("STAKE_REQUIRED")
    if not proof.requires_payment_false or not proof.payment_amount_zero or proof.mandatory_worker_cost_to_delivery_usd!="0": reasons.append("PAID_ENTRY")
    if str(admission_mode).upper()=="ECONOMIC" and not signer_ready: reasons.append("IDENTITY_NOT_READY")
    reward=Decimal(str(task.get("netReward") or task.get("reward") or "0")); net=reward/Decimal(1_000_000) if reward>=Decimal("10000") else reward
    if net<=0 or not str(task.get("escrowTxHash") or task.get("fundingTxHash") or task.get("createTxHash") or "").startswith("0x"): reasons.append("UNVERIFIED_FUNDING")
    expiry=task.get("expiryTime")
    if expiry:
        try:
            if datetime.fromisoformat(str(expiry).replace("Z","+00:00")).astimezone(timezone.utc)<=observed: reasons.append("EXPIRED")
        except ValueError: reasons.append("STALE_FETCH")
    else: reasons.append("STALE_FETCH")
    description=str(task.get("description") or ""); work_class=canon.classify_work(description); contract=next((row for row in canon.WORK_CLASS_MATRIX if row.work_class==work_class),None); capability_match=canon.work_class_qualified(work_class)
    if work_class==canon.WorkClass.UNSUPPORTED: reasons.append("POLICY_OR_WORK_CLASS_UNSUPPORTED")
    elif not capability_match: reasons.append("WORK_CLASS_NOT_FIXTURE_QUALIFIED")
    if capability_runtime_ready is None: capability_runtime_ready=capability_match
    if capability_match and not capability_runtime_ready: reasons.append("CAPABILITY_RUNTIME_INPUT_OR_TOOLING_NOT_READY")
    deterministic=_objective_acceptance(description); ambiguity_low=not bool(re.search(r"subjective|creatively open|strongest|best one|brand identity",description,re.I))
    if not deterministic: reasons.append("ACCEPTANCE_CRITERIA_NOT_DETERMINISTIC")
    if not ambiguity_low: reasons.append("AMBIGUITY_TOO_HIGH")
    comp=effective_competition
    if comp is None: reasons.append("COMPETITION_UNKNOWN")
    elif comp>max_competition: reasons.append("COMPETITION_ABOVE_THRESHOLD")
    settlement=settlement_evidence or SettlementEvidence("S1",str(task.get("requester") or ""),0,0,deterministic,bool(task.get("claimedBy")),False,"UNOBSERVED")
    if settlement.tier=="S0": reasons.append("PAYOUT_PATH_UNPROVEN")
    mode=str(admission_mode or "ECONOMIC").upper()
    if mode=="SHADOW_BENCHMARK":
        supplied=shadow_contract or {}
        for key,expected in canon.SHADOW_BENCHMARK_REQUIRED.items():
            if supplied.get(key) is not expected: reasons.append("SHADOW_CONTRACT_INVALID")
    elif mode!="ECONOMIC": reasons.append("ADMISSION_MODE_INVALID")
    reasons=sorted(set(reasons)); disposition="SHADOW_BENCHMARK" if not reasons and mode=="SHADOW_BENCHMARK" else "EXECUTABLE" if not reasons else "WATCH_ONLY" if "ALREADY_SUBMITTED" in reasons else "STALE" if ("CLOSED" in reasons or "EXPIRED" in reasons) else "HUMAN_GATE" if "IDENTITY_NOT_READY" in reasons else "REJECTED"
    comp_text="UNKNOWN" if comp is None else str(comp); priority=f"{settlement.tier}|COMP={comp_text}|NET={net}|DET={int(deterministic)}"
    return canon.TaskmarketCanonDecision(disposition,tuple(reasons),task_id,canon.canonical_object_hash(task),str(net),(-1 if comp is None else int(comp)),work_class.value,contract.executor_id if contract else "NONE",contract.checker_id if contract else "NONE",priority,mode,not reasons)


def install() -> None:
    from . import opportunities as oppmod
    from . import taskmarket_cli as cli
    from . import universal_radar as radar
    from .taskmarket_maker import TaskmarketZeroCostMaker

    def discover(self,min_reward_usd:Decimal):
        min_base=int(min_reward_usd*Decimal(1_000_000)); query=urllib.parse.urlencode({"status":"open","minReward":str(min_base),"sort":"reward_desc","limit":50}); data=self.http.get(f"{self.base_url}/api/tasks?{query}"); result=[]
        from .security import PromptInjectionRisk,assert_external_task_safe
        for task in data.get("tasks",[]):
            task_id=str(task.get("id") or task.get("taskId") or ""); mode=str(task.get("mode") or "bounty").lower()
            if not task_id or task_id in self.skipped_task_ids or bool(task.get("stakeRequired")) or mode not in {"bounty","claim"}: continue
            count=_explicit_count(task)
            if count is None: continue
            description=str(task.get("description") or "")
            try: assert_external_task_safe(description)
            except PromptInjectionRisk: continue
            reward=Decimal(str(task.get("reward") or "0"))/Decimal(1_000_000)
            if reward<min_reward_usd: continue
            maker=TaskmarketZeroCostMaker.supported_task(description); p_accept=max(Decimal("0.05"),Decimal("1")/Decimal(count+2))
            result.append(Opportunity(canonical_opportunity_id=f"taskmarket:{task_id}",source=self.name,authoritative_url=f"https://taskmarket.dev/tasks/{task_id}",upstream_status=str(task.get("status","unknown")),created_at=oppmod._dt(task.get("createdAt")),updated_at=oppmod._dt(task.get("updatedAt")),deadline=oppmod._dt(task.get("expiryTime") or task.get("deadline")),reward_gross=reward,expected_fees=reward*Decimal("0.075"),funding_proof={"escrow":task.get("escrowTxHash") or task.get("fundingTxHash") or task.get("createTxHash"),"reward_base_units":task.get("reward")},competition=count,claims=1 if task.get("claimedBy") else 0,open_prs=count,ai_policy="AGENT_NATIVE",eligibility="PUBLIC_AGENT_TRUSTED_SIGNER_LANE",claim_method="free first-party claim when exact pendingAction proves zero cost" if mode=="claim" else "not required for bounty",submission_method="first-party TaskMarket CLI after CHECK=PASS",payment_method="USDC Base award settlement",payout_latency="on settlement",payout_latency_hours=Decimal("48"),payment_proof_method="TaskDetailResponse.awards + settlementTxHash + Base receipt",expected_agent_hours=Decimal("1.5") if maker else Decimal("6"),expected_owner_minutes=Decimal("0"),p_eligible=Decimal("0.95"),p_claim=Decimal("1"),p_complete=Decimal("0.85") if maker else Decimal("0"),p_accept=p_accept,p_pay=Decimal("0.98"),p_withdrawable=Decimal("0.98"),external_state_hash=oppmod.external_state_hash(task),task_mode=mode,task_description=description,task_title=str(task.get("title") or description[:160]),maker_supported=maker))
        return result

    def verify_eligibility(self,opportunity,snapshot):
        from .security import assert_external_task_safe
        assert_external_task_safe("\n".join([str(snapshot.get("title","")),str(snapshot.get("description",""))]))
        if bool(snapshot.get("stakeRequired")): raise oppmod.OpportunityValidationError("Taskmarket stakeRequired=true is prohibited")
        mode=str(snapshot.get("mode") or getattr(opportunity,"task_mode","bounty")).lower()
        if mode not in {"bounty","claim"}: raise oppmod.OpportunityValidationError(f"Taskmarket worker entry for {mode} is not proven zero-cost")

    def inspect_competition(self,opportunity,snapshot):
        try: submissions=self.lane.submissions(opportunity.canonical_opportunity_id.split(":",1)[1])
        except Exception: submissions=None
        value=reconcile_competition(opportunity,snapshot,submissions)
        if value is None: raise oppmod.OpportunityValidationError("Taskmarket competition is UNKNOWN")
        return {"claims":1 if snapshot.get("claimedBy") else 0,"open_prs":value}

    def canonical_admission(self,opportunity,snapshot):
        task_id=opportunity.canonical_opportunity_id.split(":",1)[1]
        try: preflight=self.lane.preflight_signer(); signer_ready=bool(preflight.get("signer_present")); fresh=self.lane.task_get(task_id)
        except Exception: signer_ready=False; fresh=dict(snapshot)
        try: submissions=self.lane.submissions(task_id)
        except Exception: submissions=None
        comp=reconcile_competition(opportunity,fresh,submissions); proof=action_cost_proof(fresh,self.lane.canonical_wallet); settlement=derive_settlement_evidence(self,fresh)
        decision=order018_taskmarket_cash_decision(fresh,canonical_wallet=self.lane.canonical_wallet,existing_submission=self.lane.existing_submission(task_id) is not None,signer_ready=signer_ready,capability_runtime_ready=TaskmarketZeroCostMaker.supported_task(str(fresh.get("description") or getattr(opportunity,"task_description",""))),effective_competition=comp,action_proof=proof,settlement_evidence=settlement)
        opportunity.competition=int(comp if comp is not None else opportunity.competition); setattr(opportunity,"settlement_tier",settlement.tier); setattr(opportunity,"settlement_evidence_digest",settlement.evidence_digest); setattr(opportunity,"action_cost_evidence_digest",proof.evidence_digest)
        if not decision.allocation_allowed: raise oppmod.OpportunityValidationError("TaskMarket Cash Canon rejected allocation: "+",".join(decision.reasons))
        return decision

    def claim(self,opportunity):
        mode=str(getattr(opportunity,"task_mode","bounty")).lower()
        if mode=="bounty": return {"claim_required":False,"mode":"bounty"}
        if mode!="claim": raise oppmod.OpportunityValidationError("Taskmarket worker entry is not proven zero-cost")
        task_id=opportunity.canonical_opportunity_id.split(":",1)[1]; first=self.lane.task_get(task_id); proof=action_cost_proof(first,self.lane.canonical_wallet)
        if proof.action!="claim" or not proof.zero_cost_ready: raise oppmod.OpportunityValidationError("Taskmarket claim pendingAction is not exact zero-cost")
        effects=self.lane._effects(); current=canon.canonical_object_hash(first); effect=effects.prepare(canonical_identity=self.lane.canonical_wallet,canonical_opportunity_id=f"taskmarket:{task_id}",external_action="CLAIM",canonical_args={"action_cost_evidence_digest":proof.evidence_digest},current_external_object_hash=current)
        if effect.state=="COMMITTED":
            fresh=self.lane.task_get(task_id)
            if str(fresh.get("claimedBy") or "").lower()==self.lane.canonical_wallet.lower(): return {"claim_required":True,"mode":"claim","reused":True,"claim_id":str(fresh.get("claimId") or "AUTHORITATIVE")}
            raise oppmod.OpportunityValidationError("claim effect committed but authoritative assignment absent")
        if effect.state in {"COMMITTING","AUTHORITATIVE_VERIFY"}: effect=effects.redrive_proven_absent(effect.effect_key)
        final=self.lane.task_get(task_id)
        if canon.canonical_object_hash(final)!=current: raise oppmod.OpportunityValidationError("TaskMarket claim object changed before mutation")
        effects.precondition_refetched(effect.effect_key); effects.committing(effect.effect_key); raw=cli._data(self.lane._run(["task","claim",task_id])); fresh=self.lane.task_get(task_id)
        if str(fresh.get("claimedBy") or "").lower()!=self.lane.canonical_wallet.lower(): effects.authoritative_verify(effect.effect_key); raise oppmod.OpportunityValidationError("TaskMarket claim not authoritative for canonical wallet")
        claim_id=str(raw.get("claimId") or fresh.get("claimId") or "AUTHORITATIVE"); effects.authoritative_verify(effect.effect_key); effects.committed(effect.effect_key,claim_id); return {"claim_required":True,"mode":"claim","claim_id":claim_id,"outgoingSpendUsd":"0"}

    def assert_side_effect_gate(self,task):
        proof=action_cost_proof(task,self.canonical_wallet)
        if not proof.zero_cost_ready: raise cli.TaskmarketCliError("TaskMarket exact worker action is not proven zero-cost")
        if proof.action!="submit": raise cli.TaskmarketCliError("TaskMarket submit gate requires exact submit pendingAction")

    def admission(task,*,canonical_wallet,existing_submission,signer_ready,now=None,capability_runtime_ready=None):
        proof=action_cost_proof(task,canonical_wallet); comp=_explicit_count(task); settlement=SettlementEvidence("S1",str(task.get("requester") or ""),0,0,_objective_acceptance(str(task.get("description") or "")),bool(task.get("claimedBy")),False,"UNOBSERVED")
        decision=order018_taskmarket_cash_decision(task,canonical_wallet=canonical_wallet,existing_submission=existing_submission,signer_ready=signer_ready,now=now,capability_runtime_ready=capability_runtime_ready,effective_competition=comp,action_proof=proof,settlement_evidence=settlement)
        mapping={"EXECUTABLE":radar.OperationalState.EXECUTABLE,"WATCH_ONLY":radar.OperationalState.WATCH_ONLY,"HUMAN_GATE":radar.OperationalState.HUMAN_GATE,"STALE":radar.OperationalState.STALE,"REJECTED":radar.OperationalState.WATCH_ONLY}; return mapping[decision.disposition],decision.reasons

    canon.taskmarket_cash_decision=order018_taskmarket_cash_decision
    oppmod.TaskmarketOpportunityAdapter.discover=discover; oppmod.TaskmarketOpportunityAdapter.verify_eligibility=verify_eligibility; oppmod.TaskmarketOpportunityAdapter.inspect_competition=inspect_competition; oppmod.TaskmarketOpportunityAdapter.canonical_admission=canonical_admission; oppmod.TaskmarketOpportunityAdapter.claim=claim
    cli.TaskmarketCliLane.assert_side_effect_gate=assert_side_effect_gate; radar.taskmarket_admission=admission; cli.taskmarket_admission=admission
