from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

_INSTALLED = False
MAX_SAFE_MAKER_WORDS = 7000
_TASKMARKET_ACTION_BY_MODE = {
    "bounty": "submit",
    "claim": "claim",
    "pitch": "pitch",
    "bid": "bid",
    "proof": "proof",
}
_TASKMARKET_X402_MODES = {"pitch", "bid", "proof"}


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


def _explicit_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _payment_amount_state(action: dict[str, Any]) -> str:
    if "paymentAmount" not in action or action.get("paymentAmount") in (None, ""):
        return "MISSING"
    try:
        amount = Decimal(str(action.get("paymentAmount")))
    except (InvalidOperation, TypeError, ValueError):
        return "AMBIGUOUS"
    if amount < 0:
        return "AMBIGUOUS"
    return "ZERO" if amount == 0 else "NONZERO"


def _safe_pending_actions(task: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    pending = task.get("pendingActions")
    if not isinstance(pending, list):
        return rows
    for raw in pending[:16]:
        if not isinstance(raw, dict):
            continue
        requires = raw.get("requiresPayment") if isinstance(raw.get("requiresPayment"), bool) else None
        amount = raw.get("paymentAmount")
        rows.append({
            "role": str(raw.get("role") or ""),
            "action": str(raw.get("action") or ""),
            "eligibleAddress": str(raw.get("eligibleAddress") or "") or None,
            "requiresPayment": requires,
            "paymentAmount": None if amount in (None, "") else str(amount),
            "availableUntil": str(raw.get("availableUntil") or "") or None,
        })
    return rows


def taskmarket_action_evidence(task: dict[str, Any], canonical_wallet: str, *, now: datetime | None = None) -> dict[str, Any]:
    """Transport tri-state action facts from fresh TaskDetail without becoming Cash Canon."""
    observed = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    mode = str(task.get("mode") or "").strip().lower()
    expected_action = _TASKMARKET_ACTION_BY_MODE.get(mode, "")
    pending = task.get("pendingActions")
    matches: list[dict[str, Any]] = []
    if expected_action and isinstance(pending, list):
        matches = [
            row for row in pending
            if isinstance(row, dict)
            and str(row.get("role") or "").strip().lower() == "worker"
            and str(row.get("action") or "").strip().lower() == expected_action
        ]
    action = matches[0] if len(matches) == 1 else {}

    if not expected_action or not isinstance(pending, list):
        available: bool | None = None
    elif len(matches) == 0:
        available = False
    elif len(matches) != 1:
        available = None
    else:
        available = True
        until = action.get("availableUntil")
        if until not in (None, ""):
            try:
                deadline = datetime.fromisoformat(str(until).replace("Z", "+00:00"))
                if deadline.tzinfo is None:
                    available = None
                elif deadline.astimezone(timezone.utc) <= observed:
                    available = False
            except ValueError:
                available = None

    eligible: bool | None = None
    eligible_address = str(action.get("eligibleAddress") or "").strip() if action else ""
    if action:
        eligible = True if not eligible_address else eligible_address.lower() == canonical_wallet.lower()

    requires_raw = action.get("requiresPayment") if action else None
    if "requiresPayment" in action and not isinstance(requires_raw, bool) and requires_raw is not None:
        requires_state: bool | None | str = "AMBIGUOUS"
    else:
        requires_state = requires_raw if isinstance(requires_raw, bool) else None
    amount_state = _payment_amount_state(action) if action else "MISSING"

    paid_required: bool | None = None
    cost_authority = "UNKNOWN"
    if mode in _TASKMARKET_X402_MODES:
        paid_required = True
        cost_authority = "TASKMARKET_ENDPOINT_CONTRACT_X402"
    elif action and available is True:
        if requires_state is True or amount_state == "NONZERO":
            paid_required = True
            cost_authority = "TASK_DETAIL_PENDING_ACTION_EXPLICIT"
        elif requires_state is False and amount_state == "ZERO":
            paid_required = False
            cost_authority = "TASK_DETAIL_PENDING_ACTION_EXPLICIT"
        elif (
            mode == "bounty"
            and expected_action == "submit"
            and requires_state != "AMBIGUOUS"
            and amount_state != "AMBIGUOUS"
        ):
            # TaskMarket's authoritative endpoint contract classifies bounty worker
            # submit as non-X402. This fallback is allowed only after the exact fresh
            # worker/submit action chain is proven and cost fields are absent/partial.
            paid_required = False
            cost_authority = "TASKMARKET_ENDPOINT_CONTRACT_BOUNTY_SUBMIT_NON_X402"

    stake_required = _explicit_bool(task.get("stakeRequired"))
    submission_window_open = _explicit_bool(task.get("submissionWindowOpen"))
    safe_actions = _safe_pending_actions(task)
    raw = {
        "mode": mode or "UNKNOWN",
        "expected_action": expected_action or "UNKNOWN",
        "worker_action_count": len(matches),
        "pending_actions": safe_actions,
        "stakeRequired": stake_required,
        "submissionWindowOpen": submission_window_open,
        "paid_action_required": paid_required,
        "cost_authority": cost_authority,
        "canonical_wallet": canonical_wallet.lower(),
    }
    digest = hashlib.sha256(json.dumps(raw, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    return {
        "mode": mode or "UNKNOWN",
        "expected_action": expected_action or "UNKNOWN",
        "current_worker_action_available": available,
        "canonical_identity_eligible": eligible,
        "paid_action_required": paid_required,
        "stake_required": stake_required,
        "submission_window_open": submission_window_open,
        "eligible_address": eligible_address or None,
        "requires_payment": requires_state if isinstance(requires_state, bool) else None,
        "payment_amount_state": amount_state,
        "cost_authority": cost_authority,
        "worker_action_count": len(matches),
        "pending_actions": safe_actions,
        "action_evidence_digest": digest,
    }


def taskmarket_action_cost_proof(task: dict[str, Any], canonical_wallet: str):
    from .order018_taskmarket import ActionCostProof

    evidence = taskmarket_action_evidence(task, canonical_wallet)
    available = evidence["current_worker_action_available"] is True
    eligible = evidence["canonical_identity_eligible"] is True
    stake_zero = evidence["stake_required"] is False
    zero_cost = evidence["paid_action_required"] is False
    mandatory = "0" if available and eligible and stake_zero and zero_cost else "UNKNOWN_OR_NONZERO"
    return ActionCostProof(
        evidence["expected_action"] if evidence["expected_action"] != "UNKNOWN" else "UNPROVEN_ACTION_CHAIN",
        available,
        eligible,
        stake_zero,
        zero_cost,
        zero_cost,
        mandatory,
        evidence["action_evidence_digest"],
    )


def _taskmarket_detail_object(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    candidates = [payload, payload.get("task"), payload.get("data")]
    for row in candidates:
        if isinstance(row, dict) and (row.get("id") or row.get("taskId")):
            return row
    return None


def _taskmarket_submission_rows(payload: Any) -> list[dict[str, Any]] | None:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return None
    for key in ("submissions", "items"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    data = payload.get("data")
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        for key in ("submissions", "items"):
            value = data.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
    return None


def _taskmarket_existing_submission_state(http: Any, task_id: str, canonical_wallet: str) -> bool | None:
    try:
        payload = http.get_json(
            f"https://api.taskmarket.dev/api/tasks/{urllib.parse.quote(task_id)}/submissions",
            timeout=4,
        )
    except Exception:
        return None
    rows = _taskmarket_submission_rows(payload)
    if rows is None:
        return None
    return any(str(row.get("workerAddress") or "").strip().lower() == canonical_wallet.lower() for row in rows)


def _taskmarket_signer_boundary_ready() -> bool | None:
    from .taskmarket_cli import CANONICAL_TASKMARKET_WALLET, TaskmarketCliLane

    lane = TaskmarketCliLane()
    try:
        path = lane.keystore_path
        if not path.is_file():
            return False
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    required = ("encryptedKey", "walletAddress", "deviceId", "apiToken")
    if not isinstance(parsed, dict) or not all(isinstance(parsed.get(key), str) and parsed[key] for key in required):
        return False
    wallet = str(parsed.get("walletAddress") or "")
    if not re.fullmatch(r"0x[0-9a-fA-F]{40}", wallet):
        return False
    return wallet.lower() == CANONICAL_TASKMARKET_WALLET.lower()


def _taskmarket_external_status(detail: dict[str, Any]) -> str:
    status = str(detail.get("status") or "").strip().lower()
    phase = str(detail.get("phase") or "").strip().lower()
    if status in {"closed", "cancelled", "canceled", "deleted", "completed", "expired"}:
        return status.upper()
    if status == "open" and phase in {"active", "open"}:
        return "OPEN"
    return "UNKNOWN"


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
    adapter_source = {
        "taskmarket-daydreams": "taskmarket",
    }.get(source, source)
    adapter = adapters.get(adapter_source)
    if adapter is None:
        return None, f"SOURCE_ADAPTER_MISSING:{adapter_source}"
    if adapter_source == "taskmarket":
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
    from . import order009_sources as order009
    from . import order018_taskmarket as order018
    from . import taskmarket_cli as cli
    from . import taskmarket_maker as maker
    from . import universal_radar as radar

    # Cash Canon keeps authority. ORDER-019 only replaces the evidence helper it
    # already calls, adding the documented endpoint-contract fallback without
    # allowing a mode label or missing action chain to imply zero cost.
    order018.action_cost_proof = taskmarket_action_cost_proof

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

    original_readonly_discover = order009.TaskMarketReadOnlySource.discover
    def taskmarket_readonly_discover(self, floor_usd):
        rows = original_readonly_discover(self, floor_usd)
        if not rows:
            return rows
        signer_ready = _taskmarket_signer_boundary_ready()

        def bridge(row):
            task_id = str(row.external_id)
            try:
                payload = self.http.get_json(
                    f"{cli.TASKMARKET_PRODUCTION_API}/api/tasks/{urllib.parse.quote(task_id)}",
                    timeout=4,
                )
                detail = _taskmarket_detail_object(payload)
            except Exception:
                detail = None
            observed_id = str((detail or {}).get("id") or (detail or {}).get("taskId") or "")
            if detail is None or observed_id != task_id:
                return row.model_copy(update={
                    "paid_action_required": None,
                    "current_worker_action_available": None,
                    "canonical_identity_eligible": None,
                    "credential_boundary_ready": signer_ready,
                    "already_submitted_by_atm": None,
                    "external_status": "UNKNOWN",
                    "submission_window_open": None,
                    "stake_required": None,
                    "taskmarket_detail_authority": "UNAVAILABLE_OR_INVALID",
                })

            action = taskmarket_action_evidence(detail, cli.CANONICAL_TASKMARKET_WALLET)
            existing_submission = _taskmarket_existing_submission_state(
                self.http, task_id, cli.CANONICAL_TASKMARKET_WALLET
            )
            status = str(detail.get("status") or "")
            phase = str(detail.get("phase") or "")
            transport = {
                **action,
                "status": status or "UNKNOWN",
                "phase": phase or "UNKNOWN",
                "submissionWindowOpen": action["submission_window_open"],
                "stakeRequired": action["stake_required"],
                "existing_submission": existing_submission,
                "signer_ready": signer_ready,
            }
            tags = list(dict.fromkeys([*list(row.provenance_tags or []), "TASKMARKET_AUTHORITATIVE_DETAIL"]))
            return row.model_copy(update={
                "paid_action_required": action["paid_action_required"],
                "current_worker_action_available": action["current_worker_action_available"],
                "canonical_identity_eligible": action["canonical_identity_eligible"],
                "credential_boundary_ready": signer_ready,
                "already_submitted_by_atm": existing_submission,
                "external_status": _taskmarket_external_status(detail),
                "submission_window_open": action["submission_window_open"],
                "stake_required": action["stake_required"],
                "source_state_hash": radar.source_hash(detail),
                "taskmarket_detail_authority": "TASK_DETAIL_RESPONSE",
                "taskmarket_mode": action["mode"],
                "taskmarket_phase": phase or "UNKNOWN",
                "taskmarket_action_evidence": transport,
                "provenance_tags": tags,
            })

        with ThreadPoolExecutor(max_workers=min(8, len(rows))) as pool:
            return list(pool.map(bridge, rows))

    order009.TaskMarketReadOnlySource.discover = taskmarket_readonly_discover
