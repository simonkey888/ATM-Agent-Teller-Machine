from __future__ import annotations

import csv
import io
import re
import urllib.parse
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

_INSTALLED = False
MAX_SAFE_MAKER_WORDS = 7000


def _number(value: str) -> int:
    return int(value.replace(",", ""))


def word_bounds(description: str) -> tuple[int | None, int | None]:
    text = str(description or "")
    minimum = maximum = None
    ranged = re.search(r"\b([0-9][0-9,]*)\s*(?:-|–|to)\s*([0-9][0-9,]*)\s+words?\b", text, re.I)
    if ranged:
        minimum, maximum = _number(ranged.group(1)), _number(ranged.group(2))
    low = re.search(r"\b(?:at\s+least|minimum(?:\s+of)?|min(?:imum)?\s*[:=]?)\s*([0-9][0-9,]*)\s+words?\b", text, re.I)
    high = re.search(r"\b(?:at\s+most|maximum(?:\s+of)?|max(?:imum)?\s*[:=]?)\s*([0-9][0-9,]*)\s+words?\b", text, re.I)
    if low:
        minimum = _number(low.group(1))
    if high:
        maximum = _number(high.group(1))
    return minimum, maximum


def capability_reason(description: str) -> str:
    minimum, maximum = word_bounds(description)
    if minimum is not None and minimum > MAX_SAFE_MAKER_WORDS:
        return "CAPABILITY_WORD_BUDGET_NOT_READY"
    if minimum is not None and maximum is not None and minimum > maximum:
        return "CAPABILITY_WORD_BOUNDS_INVALID"
    return ""


def mandatory_core_ambiguous(description: str) -> bool:
    text = str(description or "")
    ambiguous = re.compile(r"\b(subjective|strongest|best\s+one|brand\s+identity|creatively\s+open)\b", re.I)
    if not ambiguous.search(text):
        return False
    cleaned = re.sub(
        r"(?:optional(?:ly)?|may|can|creatively\s+open)\b[^.\n]{0,180}(?:outside|beyond|after|apart\s+from)\b[^.\n]{0,100}(?:mandatory|required|acceptance|core)\b[^.\n]*",
        "", text, flags=re.I,
    )
    cleaned = re.sub(r"\bcreatively\s+open\b[^.\n]{0,160}\boutside\s+the\s+mandatory\s+core\b", "", cleaned, flags=re.I)
    hard = re.search(
        r"\b(?:must|required|acceptance|judge|judged|winner|select|choose)\b[^.\n]{0,120}\b(?:subjective|strongest|best\s+one|brand\s+identity|creatively\s+open)\b",
        cleaned, re.I,
    )
    return bool(hard or ambiguous.search(cleaned))


def _csv_columns(description: str) -> list[str] | None:
    for pattern in (
        r"(?:required|exact)\s+columns?\s*[:=]\s*([A-Za-z0-9_. -]+(?:\s*,\s*[A-Za-z0-9_. -]+)+)",
        r"columns?\s*[:=]\s*([A-Za-z0-9_. -]+(?:\s*,\s*[A-Za-z0-9_. -]+)+)",
    ):
        match = re.search(pattern, str(description or ""), re.I)
        if match:
            raw = re.split(r"[.;\n]", match.group(1), maxsplit=1)[0]
            cols = [part.strip(" `\"'") for part in raw.split(",")]
            if len(cols) >= 2 and all(cols):
                return cols
    return None


def _csv_rows(description: str) -> int | None:
    match = re.search(r"\bexactly\s+([0-9][0-9,]*)\s+(?:data\s+)?rows?\b", str(description or ""), re.I)
    return _number(match.group(1)) if match else None


def _required_literals(description: str) -> tuple[str, ...]:
    values = re.findall(r"\b(?:must\s+(?:include|contain)|required\s+literal)\s*[=:]?\s*[\"'`](.{1,160}?)[\"'`]", str(description or ""), re.I)
    return tuple(dict.fromkeys(values))


def _filename(description: str, original) -> str:
    match = re.search(r"\b([a-z0-9][a-z0-9._-]*\.csv)\b", str(description or ""), re.I)
    if match:
        return Path(match.group(1)).name
    low = str(description or "").lower()
    if re.search(r"\bcsv\b", low) and any(token in low for token in ("single file", "one file", "csv file", "table")):
        return "submission.csv"
    return original(description)


def _extract_csv(text: str) -> str:
    raw = str(text or "").replace("\r\n", "\n").strip()
    begin, end = raw.find("ATM_FILE_BEGIN"), raw.rfind("ATM_FILE_END")
    if begin >= 0 and end > begin:
        raw = raw[begin + len("ATM_FILE_BEGIN"):end].strip()
    fenced = re.fullmatch(r"```(?:csv)?\s*\n?([\s\S]*?)\n?```", raw, re.I)
    if fenced:
        raw = fenced.group(1).strip()
    try:
        rows = list(csv.reader(io.StringIO(raw)))
    except csv.Error as exc:
        raise ValueError("maker response CSV cannot be parsed") from exc
    if len(rows) < 2 or len(rows[0]) < 2:
        raise ValueError("maker response CSV lacks header/data rows")
    return raw


def _check_csv(path: Path, description: str) -> None:
    content = path.read_text(encoding="utf-8")
    if re.search(r"(?:BEGIN [A-Z ]*PRIVATE KEY|TASKMARKET_KEYSTORE|GEMINI_API_KEY|SEED_PHRASE|MNEMONIC)", content, re.I):
        raise ValueError("generated artifact contains forbidden secret marker")
    try:
        rows = list(csv.reader(io.StringIO(content)))
    except csv.Error as exc:
        raise ValueError("generated CSV cannot be parsed") from exc
    if len(rows) < 2:
        raise ValueError("generated CSV requires header and data")
    header = [cell.strip() for cell in rows[0]]
    if not header or any(not cell for cell in header) or len(set(header)) != len(header):
        raise ValueError("generated CSV has invalid headers")
    if any(len(row) != len(header) for row in rows[1:]):
        raise ValueError("generated CSV row width mismatch")
    required = _csv_columns(description)
    if required is not None and header != required:
        raise ValueError(f"generated CSV columns mismatch: expected {required!r}")
    required_rows = _csv_rows(description)
    if required_rows is not None and len(rows) - 1 != required_rows:
        raise ValueError(f"generated CSV row-count mismatch: expected {required_rows}")
    for literal in _required_literals(description):
        if literal not in content:
            raise ValueError(f"generated CSV missing required literal: {literal}")


def _check_words(path: Path, description: str) -> None:
    minimum, maximum = word_bounds(description)
    if minimum is None and maximum is None:
        return
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".html":
        text = re.sub(r"<[^>]+>", " ", text)
    count = len(re.findall(r"\b[\w’'-]+\b", text, re.UNICODE))
    if minimum is not None and count < minimum:
        raise ValueError(f"generated artifact word-count below minimum: {count} < {minimum}")
    if maximum is not None and count > maximum:
        raise ValueError(f"generated artifact word-count above maximum: {count} > {maximum}")


def _taskmarket_from_detail(adapter: Any, task: dict[str, Any], floor: Decimal):
    from .models import Opportunity
    from .opportunities import _dt, external_state_hash
    from .order018_taskmarket import _explicit_count
    from .security import PromptInjectionRisk, assert_external_task_safe
    from .taskmarket_maker import TaskmarketZeroCostMaker

    task_id = str(task.get("id") or task.get("taskId") or "")
    if not task_id:
        return None, "AUTHORITATIVE_ID_MISSING"
    if task_id in getattr(adapter, "skipped_task_ids", set()):
        return None, "LOCAL_SKIP_SET_ACTIVE"
    mode = str(task.get("mode") or "").lower()
    if mode not in {"bounty", "claim"}:
        return None, "MODE_NOT_ZERO_SPEND"
    if task.get("stakeRequired") is True:
        return None, "STAKE_REQUIRED"
    description = str(task.get("description") or "")
    try:
        assert_external_task_safe(description)
    except PromptInjectionRisk:
        return None, "PROMPT_INJECTION_RISK"
    reward = Decimal(str(task.get("reward") or "0")) / Decimal(1_000_000)
    if reward < floor:
        return None, f"BELOW_CURRENT_REWARD_FLOOR:{reward}<{floor}"
    competition = _explicit_count(task)
    if competition is None:
        return None, "COMPETITION_UNKNOWN"
    maker = TaskmarketZeroCostMaker.supported_task(description)
    escrow = task.get("escrowTxHash") or task.get("fundingTxHash") or task.get("createTxHash")
    return Opportunity(
        canonical_opportunity_id=f"taskmarket:{task_id}", source="taskmarket",
        authoritative_url=f"https://taskmarket.dev/tasks/{task_id}", upstream_status=str(task.get("status", "unknown")),
        created_at=_dt(task.get("createdAt")), updated_at=_dt(task.get("updatedAt")),
        deadline=_dt(task.get("expiryTime") or task.get("deadline")), reward_gross=reward,
        expected_fees=reward * Decimal("0.075"),
        funding_proof={"escrow": escrow, "escrow_tx_hash": escrow, "reward_base_units": task.get("reward")},
        competition=competition, claims=1 if task.get("claimedBy") else 0, open_prs=0,
        ai_policy="AGENT_NATIVE", eligibility="PUBLIC_AGENT_TRUSTED_SIGNER_LANE",
        claim_method="fresh pendingAction proof required", submission_method="first-party TaskMarket CLI after CHECK=PASS",
        payment_method="USDC Base award settlement", payout_latency="on settlement", payout_latency_hours=Decimal("48"),
        payment_proof_method="TaskDetailResponse.awards + settlementTxHash + Base receipt",
        expected_agent_hours=Decimal("1.5") if maker else Decimal("6"), expected_owner_minutes=Decimal("0"),
        p_eligible=Decimal("0.95"), p_claim=Decimal("1"), p_complete=Decimal("0.85") if maker else Decimal("0"),
        p_accept=Decimal("0.50"), p_pay=Decimal("0.50"), p_withdrawable=Decimal("0.98"),
        external_state_hash=external_state_hash(task), task_mode=mode, task_description=description,
        task_title=str(task.get("title") or description[:160]), maker_supported=maker,
        settlement_probability_authority="NONE_PREVERIFY",
    ), ""


def refetch_board_candidate(canonical_id: str, *, adapters: dict[str, Any], floor: Decimal):
    source, sep, external_id = str(canonical_id).partition(":")
    if not sep or not source or not external_id:
        return None, "CANONICAL_ID_INVALID"
    adapter = adapters.get(source)
    if adapter is None:
        return None, f"SOURCE_ADAPTER_MISSING:{source}"
    if source == "taskmarket":
        try:
            task = adapter.http.get(f"{adapter.base_url}/api/tasks/{urllib.parse.quote(external_id)}")
        except Exception as exc:
            return None, f"AUTHORITATIVE_REFETCH_FAILED:{type(exc).__name__}"
        if not isinstance(task, dict):
            return None, "AUTHORITATIVE_REFETCH_INVALID"
        observed = str(task.get("id") or task.get("taskId") or "")
        if observed and observed != external_id:
            return None, "AUTHORITATIVE_ID_MISMATCH"
        return _taskmarket_from_detail(adapter, task, floor)
    try:
        rows = adapter.discover(Decimal("0"))
    except Exception as exc:
        return None, f"SOURCE_REFETCH_FAILED:{type(exc).__name__}"
    match = next((row for row in rows if row.canonical_opportunity_id == canonical_id), None)
    if match is None:
        return None, "AUTHORITATIVE_REDISCOVERY_MISS"
    if match.reward_net < floor:
        return None, f"BELOW_CURRENT_REWARD_FLOOR:{match.reward_net}<{floor}"
    return match, ""


def choose_swarm_candidate(found: list[Any], eligible_order: list[str], *, adapters: dict[str, Any], config: dict[str, Any], hard_cap: Decimal):
    from . import money_velocity as mv
    floor = Decimal(str(config.get("min_reward_usd", 1)))
    fresh = {row.canonical_opportunity_id: row for row in found}
    ledger: list[dict[str, Any]] = []
    for canonical_id in eligible_order:
        candidate = fresh.get(canonical_id)
        origin = "CURRENT_DISCOVER_PASS"
        reason = ""
        if candidate is None:
            candidate, reason = refetch_board_candidate(canonical_id, adapters=adapters, floor=floor)
            origin = "DIRECT_SOURCE_REFETCH"
        if candidate is None:
            ledger.append({"canonical_id": canonical_id, "origin": origin, "result": "REJECTED", "reason": reason or "REDISCOVERY_MISS_UNEXPLAINED"})
            continue
        trace = {"canonical_id": canonical_id, "origin": origin, "external_state_hash": candidate.external_state_hash, "reward_net": str(candidate.reward_net), "competition": int(candidate.competition)}
        rank_reject = mv.reject_reason(candidate)
        if rank_reject:
            ledger.append({**trace, "result": "REJECTED", "reason": rank_reject})
            continue
        selected = mv.choose_money_velocity([candidate], hard_default_hours=hard_cap)
        if selected is None:
            ledger.append({**trace, "result": "REJECTED", "reason": "RANKER_RETURNED_NONE"})
            continue
        ledger.append({**trace, "result": "SELECTED_FOR_CANON_VERIFY", "money_velocity": str(mv.money_velocity(selected))})
        return selected, ledger
    return None, ledger


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    from . import cash_canon as canon
    from . import money_velocity as mv
    from . import opportunities as oppmod
    from . import order018_taskmarket as order018
    from . import taskmarket_maker as maker
    from . import universal_radar as radar

    original_qualify = radar.qualify
    def truthful_qualify(opportunity, *, floor_usd=Decimal("5"), now=None):
        result = original_qualify(opportunity, floor_usd=floor_usd, now=now)
        reasons = set(result.rejection_reasons)
        if opportunity.external_object_exists is None:
            reasons.discard("REMOTE_404"); reasons.add("REMOTE_EXISTENCE_UNKNOWN")
        status = str(opportunity.external_status or "").strip().upper()
        if status not in radar.OPEN_EXTERNAL_STATES and status not in {"CLOSED", "CANCELLED", "CANCELED", "DELETED", "COMPLETED", "EXPIRED"}:
            reasons.discard("CLOSED"); reasons.add("EXTERNAL_STATUS_UNKNOWN")
        if opportunity.paid_action_required is None and opportunity.execution_cost_usd <= 0:
            reasons.discard("PAID_ENTRY"); reasons.add("ACTION_COST_UNKNOWN")
        if opportunity.stake_required is None:
            reasons.discard("STAKE_REQUIRED"); reasons.add("STAKE_STATUS_UNKNOWN")
        if opportunity.submission_window_open is None:
            reasons.add("SUBMISSION_WINDOW_UNKNOWN")
        if opportunity.current_worker_action_available is None:
            reasons.add("WORKER_ACTION_UNKNOWN")
        if opportunity.submission_window_open is not False and opportunity.current_worker_action_available is not False and (opportunity.submission_window_open is None or opportunity.current_worker_action_available is None):
            reasons.discard("NO_CURRENT_WORKER_ACTION")
        if opportunity.canonical_identity_eligible is None:
            reasons.add("IDENTITY_ELIGIBILITY_UNKNOWN")
        if opportunity.credential_boundary_ready is None:
            reasons.add("CREDENTIAL_BOUNDARY_UNKNOWN")
        if opportunity.canonical_identity_eligible is not False and opportunity.credential_boundary_ready is not False and (opportunity.canonical_identity_eligible is None or opportunity.credential_boundary_ready is None):
            reasons.discard("IDENTITY_NOT_READY")
        if opportunity.already_submitted_by_atm is None:
            reasons.discard("ALREADY_SUBMITTED"); reasons.add("SUBMISSION_HISTORY_UNKNOWN")
        result.rejection_reasons = sorted(reasons)
        return result
    radar.qualify = truthful_qualify

    original_funding = mv.has_funding_evidence
    original_reject = mv.reject_reason
    def has_funding_evidence(opportunity):
        escrow = (opportunity.funding_proof or {}).get("escrow")
        return bool(original_funding(opportunity) or (isinstance(escrow, str) and escrow.startswith("0x")))
    def reject_reason(opportunity, now=None):
        if str(getattr(opportunity, "source", "")).lower() != "taskmarket":
            return original_reject(opportunity, now)
        now = now or datetime.now(timezone.utc)
        if opportunity.reward_net <= 0: return "NO_POSITIVE_PAYOUT"
        if mv.task_alive_probability(opportunity, now) == 0: return "STALE_OR_CLOSED"
        age = mv._age_hours(opportunity.updated_at or opportunity.created_at, now)
        if age is not None and age > 24 * 30: return "STALE_DEMAND"
        if not has_funding_evidence(opportunity): return "NO_FUNDING_PROOF"
        if str(getattr(opportunity, "payment_timing", "")).lower() in {"post_campaign", "subjective_campaign"}: return "POST_CAMPAIGN_PAY"
        if bool(getattr(opportunity, "requires_upfront_payment", False)): return "PAID_BID_STAKE_GAS"
        if str(opportunity.eligibility).upper() in {"HUMAN_ONLY", "HUMAN_WORK_REQUIRED"}: return "HUMAN_WORK_REQUIRED"
        if not opportunity.payment_method or opportunity.payment_method.upper() == "UNKNOWN": return "NO_WITHDRAWAL_PATH"
        return None
    mv.has_funding_evidence = has_funding_evidence
    mv.reject_reason = reject_reason

    original_decision = order018.order018_taskmarket_cash_decision
    def cash_decision(*args, **kwargs):
        decision = original_decision(*args, **kwargs)
        task = args[0] if args else kwargs.get("task", {})
        description = str((task or {}).get("description") or "")
        reasons = set(decision.reasons)
        if "AMBIGUITY_TOO_HIGH" in reasons and not mandatory_core_ambiguous(description):
            reasons.discard("AMBIGUITY_TOO_HIGH")
        cap = capability_reason(description)
        if cap: reasons.add(cap)
        if reasons == set(decision.reasons): return decision
        mode = str(decision.admission_mode or "ECONOMIC").upper()
        if not reasons: disposition = "SHADOW_BENCHMARK" if mode == "SHADOW_BENCHMARK" else "EXECUTABLE"
        elif "ALREADY_SUBMITTED" in reasons: disposition = "WATCH_ONLY"
        elif {"CLOSED", "EXPIRED"} & reasons: disposition = "STALE"
        elif "IDENTITY_NOT_READY" in reasons: disposition = "HUMAN_GATE"
        else: disposition = "REJECTED"
        return replace(decision, reasons=tuple(sorted(reasons)), disposition=disposition, allocation_allowed=not reasons)
    order018.order018_taskmarket_cash_decision = cash_decision
    canon.taskmarket_cash_decision = cash_decision

    original_filename = maker._deterministic_filename
    original_extract = maker._extract_artifact_content
    original_supported = maker.TaskmarketZeroCostMaker.supported_task
    original_static = maker.TaskmarketZeroCostMaker._static_check
    def deterministic_filename(description): return _filename(description, original_filename)
    def extract_artifact(text, description):
        if deterministic_filename(description).lower().endswith(".csv"):
            try: return _extract_csv(text)
            except ValueError as exc: raise maker.TaskmarketMakerError(str(exc)) from exc
        return original_extract(text, description)
    def supported_task(description):
        if capability_reason(description): return False
        low = str(description or "").lower()
        csv_ok = bool((".csv" in low or re.search(r"\bcsv\b", low)) and any(token in low for token in ("single file", "one file", "csv file", "table")))
        return bool(original_supported(description) or csv_ok)
    def static_check(path, description):
        try:
            if path.suffix.lower() == ".csv": _check_csv(path, description)
            else: original_static(path, description)
            _check_words(path, description)
        except (ValueError, maker.TaskmarketMakerError) as exc:
            if isinstance(exc, maker.TaskmarketMakerError): raise
            raise maker.TaskmarketMakerError(str(exc)) from exc
    maker._deterministic_filename = deterministic_filename
    maker._extract_artifact_content = extract_artifact
    maker.TaskmarketZeroCostMaker.supported_task = staticmethod(supported_task)
    maker.TaskmarketZeroCostMaker._static_check = staticmethod(static_check)
    maker.TaskmarketZeroCostMaker.capability_reason = staticmethod(capability_reason)

    original_discover = oppmod.TaskmarketOpportunityAdapter.discover
    def taskmarket_discover(self, minimum):
        rows = original_discover(self, minimum)
        for opportunity in rows:
            opportunity.open_prs = 0
            proof = dict(opportunity.funding_proof or {})
            if proof.get("escrow") and not proof.get("escrow_tx_hash"): proof["escrow_tx_hash"] = proof["escrow"]
            opportunity.funding_proof = proof
        return rows
    def inspect_competition(self, opportunity, snapshot):
        try: submissions = self.lane.submissions(opportunity.canonical_opportunity_id.split(":", 1)[1])
        except Exception: submissions = None
        value = order018.reconcile_competition(opportunity, snapshot, submissions)
        if value is None: raise oppmod.OpportunityValidationError("Taskmarket competition is UNKNOWN")
        return {"claims": 1 if snapshot.get("claimedBy") else 0, "competition": int(value), "open_prs": 0}
    original_canonical = oppmod.TaskmarketOpportunityAdapter.canonical_admission
    def canonical_admission(self, opportunity, snapshot):
        if not self.lane.signer_present():
            raise oppmod.OpportunityValidationError("TaskMarket Cash Canon rejected allocation:TASKMARKET_SIGNER_SECRET_ABSENT")
        return original_canonical(self, opportunity, snapshot)
    oppmod.TaskmarketOpportunityAdapter.discover = taskmarket_discover
    oppmod.TaskmarketOpportunityAdapter.inspect_competition = inspect_competition
    oppmod.TaskmarketOpportunityAdapter.canonical_admission = canonical_admission
