from __future__ import annotations

import csv
import hashlib
import ipaddress
import json
import re
import socket
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable

from .capability_registry import CAPABILITY_REGISTRY, capability_enabled, enabled_capability_ids


class WorkClass(str, Enum):
    WEB_SINGLE_FILE_INTERACTIVE = "WEB_SINGLE_FILE_INTERACTIVE"
    GITHUB_BOUNDED_PATCH = "GITHUB_BOUNDED_PATCH"
    API_INTEGRATION = "API_INTEGRATION"
    TEST_FIX = "TEST_FIX"
    DOCS_TECHNICAL = "DOCS_TECHNICAL"
    RESEARCH_SYNTHESIS = "RESEARCH_SYNTHESIS"
    SOURCE_BACKED_FACT_TABLE = "SOURCE_BACKED_FACT_TABLE"
    CONTENT_STRUCTURED = "CONTENT_STRUCTURED"
    TRANSLATION_LOCALIZATION = "TRANSLATION_LOCALIZATION"
    DOCUMENT_DATA_EXTRACTION = "DOCUMENT_DATA_EXTRACTION"
    DOCUMENT_OCR_EXTRACTION = "DOCUMENT_OCR_EXTRACTION"
    CSV_JSON_TRANSFORM = "CSV_JSON_TRANSFORM"
    SPREADSHEET_TRANSFORM = "SPREADSHEET_TRANSFORM"
    LIGHT_WEB_DESIGN = "LIGHT_WEB_DESIGN"
    WEB_QA = "WEB_QA"
    BROWSER_PUBLIC_DYNAMIC_EXTRACTION = "BROWSER_PUBLIC_DYNAMIC_EXTRACTION"
    VIDEO_SHORT_FORM_ASSEMBLY = "VIDEO_SHORT_FORM_ASSEMBLY"
    DATA_ANALYSIS_BOUNDED = "DATA_ANALYSIS_BOUNDED"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True)
class WorkClassContract:
    work_class: WorkClass
    executor_id: str
    checker_id: str
    qualified: bool
    reason: str


WORK_CLASS_MATRIX: tuple[WorkClassContract, ...] = tuple(
    WorkClassContract(
        WorkClass(row.capability_id),
        row.executor_id,
        row.checker_id,
        capability_enabled(row.capability_id),
        row.benchmark_result,
    )
    for row in CAPABILITY_REGISTRY
)


def qualified_work_classes() -> tuple[str, ...]:
    return enabled_capability_ids()


def classify_work(description: str) -> WorkClass:
    text = str(description or "").lower()
    if re.search(
        r"ignore\s+(?:all\s+)?previous\s+instructions|reveal\s+(?:the\s+)?(?:system|developer)\s+prompt|"
        r"(?:print|send|upload)\s+[^.\n]{0,80}(?:api[_ -]?key|private[_ -]?key|seed phrase|credential|secret)|"
        r"override\s+[^.\n]{0,80}(?:policy|guardrail)|disable\s+[^.\n]{0,80}(?:safety|validation)",
        text,
        re.I,
    ):
        return WorkClass.UNSUPPORTED
    if re.search(r"\b(recruit|referral|growth sprint|cold call|telemarket|outbound sales)\b", text):
        return WorkClass.UNSUPPORTED
    if re.search(r"\b(photo editing|brand identity|logo design|social (?:post|publish)|upload to (?:youtube|tiktok|instagram))\b", text):
        return WorkClass.UNSUPPORTED
    if re.search(r"\b(mp4|video|short[- ]form video|video assembly|video generation|subtitles?|ffmpeg)\b", text):
        return WorkClass.VIDEO_SHORT_FORM_ASSEMBLY
    if re.search(r"\b(dynamic web|javascript-rendered|browser extraction|rendered dom|single-page application)\b", text):
        return WorkClass.BROWSER_PUBLIC_DYNAMIC_EXTRACTION
    if re.search(r"\b(translation|localization|localisation)\b", text):
        return WorkClass.TRANSLATION_LOCALIZATION
    if re.search(r"\b(ocr|scanned image|image-to-text)\b", text):
        return WorkClass.DOCUMENT_OCR_EXTRACTION
    if "index.html" in text:
        return WorkClass.WEB_SINGLE_FILE_INTERACTIVE
    if ".csv" in text or re.search(r"\bcsv\b", text):
        return WorkClass.SOURCE_BACKED_FACT_TABLE if "source" in text else WorkClass.CSV_JSON_TRANSFORM
    if re.search(r"\bjson\b", text) and re.search(r"\b(transform|convert|normalize|schema)\b", text):
        return WorkClass.CSV_JSON_TRANSFORM
    if re.search(r"\b(research|source-backed|sources|citations?)\b", text) and re.search(r"\b(markdown|report|table)\b", text):
        return WorkClass.RESEARCH_SYNTHESIS
    if re.search(r"\b(markdown|structured content|article|brief)\b", text):
        return WorkClass.CONTENT_STRUCTURED
    if re.search(r"\b(pull request|github|repository patch)\b", text):
        return WorkClass.GITHUB_BOUNDED_PATCH
    if re.search(r"\b(api integration|webhook|sdk|graphql)\b", text):
        return WorkClass.API_INTEGRATION
    if re.search(r"\b(failing test|test fix|regression)\b", text):
        return WorkClass.TEST_FIX
    if re.search(r"\b(documentation|readme|technical docs)\b", text):
        return WorkClass.DOCS_TECHNICAL
    if re.search(r"\b(data analysis|statistical analysis)\b", text):
        return WorkClass.DATA_ANALYSIS_BOUNDED
    return WorkClass.UNSUPPORTED


def work_class_qualified(work_class: WorkClass) -> bool:
    return capability_enabled(work_class.value)


@dataclass(frozen=True)
class AttackInputs:
    net_reward_usd: Decimal
    competition: int
    award_capacity: int
    remaining_minutes: int
    capability_match: bool
    deterministic_acceptance: bool
    artifact_class_proven: bool
    prior_settlement_observed: bool
    ambiguity_low: bool


@dataclass(frozen=True)
class TaskmarketCanonDecision:
    disposition: str
    reasons: tuple[str, ...]
    task_id: str
    authoritative_object_hash: str
    net_reward_usdc: str
    competition: int
    work_class: str
    executor_id: str
    checker_id: str
    attack_priority: str
    admission_mode: str
    allocation_allowed: bool


SHADOW_BENCHMARK_REQUIRED = {
    "mutation_authority": False,
    "signer_visible": False,
    "economic_state_unchanged": True,
    "not_counted_as_execution": True,
}


def attack_priority(inputs: AttackInputs) -> Decimal:
    """Deterministic ordinal score; it is deliberately not labelled dollar EV."""
    score = min(inputs.net_reward_usd, Decimal("100")) * Decimal("100")
    score -= Decimal(max(0, inputs.competition)) * Decimal("250")
    score += Decimal(min(max(0, inputs.award_capacity), 10)) * Decimal("300")
    score += Decimal(min(max(0, inputs.remaining_minutes), 4320)) / Decimal("6")
    score += Decimal("1000") if inputs.capability_match else Decimal("-5000")
    score += Decimal("800") if inputs.deterministic_acceptance else Decimal("-1800")
    score += Decimal("500") if inputs.artifact_class_proven else Decimal("-800")
    score += Decimal("400") if inputs.prior_settlement_observed else Decimal("0")
    score += Decimal("500") if inputs.ambiguity_low else Decimal("-1000")
    return score.quantize(Decimal("0.001"))


def competition_allows_attack(inputs: AttackInputs, *, max_competition: int = 12) -> tuple[bool, str]:
    if inputs.competition <= max_competition:
        return True, "WITHIN_THRESHOLD"
    if (
        inputs.award_capacity >= 3
        and inputs.net_reward_usd >= Decimal("20")
        and inputs.capability_match
        and inputs.deterministic_acceptance
        and inputs.ambiguity_low
    ):
        return True, "OVERRIDE_MULTI_AWARD_HIGH_FIT"
    return False, "COMPETITION_ABOVE_THRESHOLD"


def taskmarket_cash_decision(
    task: dict[str, Any],
    *,
    canonical_wallet: str,
    existing_submission: bool,
    signer_ready: bool,
    now: datetime | None = None,
    max_competition: int = 12,
    admission_mode: str = "ECONOMIC",
    shadow_contract: dict[str, Any] | None = None,
    capability_runtime_ready: bool | None = None,
) -> TaskmarketCanonDecision:
    """One allocation/mutation admission truth for a fresh first-party task object."""

    observed = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    mode = str(admission_mode or "ECONOMIC").upper()
    reasons: list[str] = []
    task_id = str(task.get("id") or task.get("taskId") or "")
    if not task_id:
        reasons.append("AUTHORITATIVE_ID_MISSING")
    if existing_submission:
        reasons.append("ALREADY_SUBMITTED")
    if str(task.get("status") or "").lower() != "open" or str(task.get("phase") or "").lower() not in {"active", "open"}:
        reasons.append("CLOSED")
    if str(task.get("mode") or "bounty").lower() != "bounty":
        reasons.append("POLICY_REJECT")
    if task.get("submissionWindowOpen") is not True:
        reasons.append("NO_CURRENT_WORKER_ACTION")
    if task.get("stakeRequired") is not False:
        reasons.append("STAKE_REQUIRED")

    net_reward = Decimal(str(task.get("netReward") or "0"))
    net_reward_usdc = net_reward / Decimal(1_000_000) if net_reward >= Decimal("10000") else net_reward
    if not str(task.get("escrowTxHash") or "").startswith("0x") or net_reward_usdc <= 0:
        reasons.append("UNVERIFIED_FUNDING")

    expiry = task.get("expiryTime")
    remaining_minutes = 0
    if expiry:
        try:
            deadline = datetime.fromisoformat(str(expiry).replace("Z", "+00:00")).astimezone(timezone.utc)
            remaining_minutes = max(0, int((deadline - observed).total_seconds() // 60))
            if deadline <= observed:
                reasons.append("EXPIRED")
        except ValueError:
            reasons.append("STALE_FETCH")
    else:
        reasons.append("STALE_FETCH")

    actions = [
        row for row in (task.get("pendingActions") or [])
        if isinstance(row, dict) and row.get("role") == "worker" and row.get("action") == "submit"
    ]
    if len(actions) != 1:
        reasons.append("NO_CURRENT_WORKER_ACTION")
    else:
        action = actions[0]
        eligible = str(action.get("eligibleAddress") or "").lower()
        if eligible and eligible != canonical_wallet.lower():
            reasons.append("IDENTITY_NOT_READY")
        if action.get("requiresPayment") is not False or action.get("paymentAmount") not in (None, "", 0, "0"):
            reasons.append("PAID_ENTRY")
    if mode == "ECONOMIC" and not signer_ready:
        reasons.append("IDENTITY_NOT_READY")

    description = str(task.get("description") or "")
    work_class = classify_work(description)
    contract = next((row for row in WORK_CLASS_MATRIX if row.work_class == work_class), None)
    capability_match = work_class_qualified(work_class)
    if work_class == WorkClass.UNSUPPORTED:
        reasons.append("POLICY_OR_WORK_CLASS_UNSUPPORTED")
    elif not capability_match:
        reasons.append("WORK_CLASS_NOT_FIXTURE_QUALIFIED")
    if capability_runtime_ready is None:
        capability_runtime_ready = work_class not in {
            WorkClass.BROWSER_PUBLIC_DYNAMIC_EXTRACTION,
            WorkClass.VIDEO_SHORT_FORM_ASSEMBLY,
        }
    if capability_match and not capability_runtime_ready:
        reasons.append("CAPABILITY_RUNTIME_INPUT_OR_TOOLING_NOT_READY")
    if work_class == WorkClass.VIDEO_SHORT_FORM_ASSEMBLY and re.search(
        r"\b(?:paid (?:stock|voice|api)|celebrity likeness|copyrighted footage|post (?:to|on) (?:youtube|tiktok|instagram))\b",
        description,
        re.I,
    ):
        reasons.append("VIDEO_RIGHTS_OR_PAID_DEPENDENCY_UNSUPPORTED")

    competition = int(task.get("submissionCount") or 0) + int(task.get("pitchCount") or 0)
    deterministic = bool(re.search(r"pass/fail|acceptance|observable requirements|exact(?:ly)? one|required (?:duration|resolution|columns?)", description, re.I))
    ambiguity_low = not bool(re.search(r"subjective|creatively open|strongest|best one|brand identity", description, re.I))
    if not deterministic:
        reasons.append("ACCEPTANCE_CRITERIA_NOT_DETERMINISTIC")
    if not ambiguity_low:
        reasons.append("AMBIGUITY_TOO_HIGH")
    inputs = AttackInputs(
        net_reward_usd=net_reward_usdc,
        competition=competition,
        award_capacity=1,
        remaining_minutes=remaining_minutes,
        capability_match=capability_match,
        deterministic_acceptance=deterministic,
        artifact_class_proven=capability_match,
        prior_settlement_observed=False,
        ambiguity_low=ambiguity_low,
    )
    competition_ok, competition_reason = competition_allows_attack(inputs, max_competition=max_competition)

    if mode == "SHADOW_BENCHMARK":
        supplied = shadow_contract or {}
        for key, expected in SHADOW_BENCHMARK_REQUIRED.items():
            if supplied.get(key) is not expected:
                reasons.append("SHADOW_CONTRACT_INVALID")
        budget = supplied.get("resource_budget_seconds")
        if not isinstance(budget, int) or budget < 1 or budget > 900:
            reasons.append("SHADOW_RESOURCE_BUDGET_INVALID")
    elif mode != "ECONOMIC":
        reasons.append("ADMISSION_MODE_INVALID")
    elif not competition_ok:
        reasons.append(competition_reason)

    reasons = sorted(set(reasons))
    if not reasons:
        disposition = "SHADOW_BENCHMARK" if mode == "SHADOW_BENCHMARK" else "EXECUTABLE"
    elif "ALREADY_SUBMITTED" in reasons:
        disposition = "WATCH_ONLY"
    elif any(item in reasons for item in ("CLOSED", "EXPIRED")):
        disposition = "STALE"
    elif "IDENTITY_NOT_READY" in reasons:
        disposition = "HUMAN_GATE"
    else:
        disposition = "REJECTED"
    return TaskmarketCanonDecision(
        disposition=disposition,
        reasons=tuple(reasons),
        task_id=task_id,
        authoritative_object_hash=canonical_object_hash(task),
        net_reward_usdc=str(net_reward_usdc),
        competition=competition,
        work_class=work_class.value,
        executor_id=contract.executor_id if contract else "NONE",
        checker_id=contract.checker_id if contract else "NONE",
        attack_priority=str(attack_priority(inputs)),
        admission_mode=mode,
        allocation_allowed=not reasons,
    )


def canonical_object_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


@dataclass(frozen=True)
class JobEnvelope:
    canonical_opportunity_id: str
    source: str
    external_id: str
    fresh_object_hash: str
    fresh_checked_at: str
    net_reward: str
    competition_snapshot: int
    acceptance_criteria_hash: str
    deadline: str
    work_class: str
    executor_id: str
    checker_id: str
    artifact_contract: str
    zero_spend_gate: bool
    canonical_identity: str
    submission_dedupe_key: str


def build_job_envelope(
    *,
    source: str,
    external_id: str,
    current_object: dict[str, Any],
    checked_at: datetime,
    net_reward: Decimal,
    competition: int,
    acceptance_criteria: str,
    deadline: str,
    work_class: WorkClass,
    executor_id: str,
    checker_id: str,
    artifact_contract: str,
    canonical_identity: str,
) -> JobEnvelope:
    return JobEnvelope(
        canonical_opportunity_id=f"{source.lower()}:{external_id}",
        source=source,
        external_id=external_id,
        fresh_object_hash=canonical_object_hash(current_object),
        fresh_checked_at=checked_at.astimezone(timezone.utc).isoformat(),
        net_reward=str(net_reward),
        competition_snapshot=competition,
        acceptance_criteria_hash=hashlib.sha256(acceptance_criteria.encode()).hexdigest(),
        deadline=deadline,
        work_class=work_class.value,
        executor_id=executor_id,
        checker_id=checker_id,
        artifact_contract=artifact_contract,
        zero_spend_gate=True,
        canonical_identity=canonical_identity,
        submission_dedupe_key=hashlib.sha256(f"{source.lower()}:{external_id}:{canonical_identity.lower()}".encode()).hexdigest(),
    )


def final_refetch_matches(envelope: JobEnvelope, current_object: dict[str, Any]) -> tuple[bool, str]:
    if canonical_object_hash(current_object) != envelope.fresh_object_hash:
        return False, "CURRENT_OBJECT_CHANGED"
    return True, "MATCH"


def _safe_https_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return False
    try:
        addresses = {row[4][0] for row in socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)}
    except OSError:
        return False
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            return False
    return True


def live_url_probe(url: str) -> bool:
    if not _safe_https_url(url):
        return False
    request = urllib.request.Request(url, headers={"User-Agent": "ATM-Source-Checker/1.0"}, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return 200 <= response.status < 400
    except Exception:
        return False


def validate_research_markdown(
    path: Path,
    *,
    min_words: int,
    max_words: int,
    required_headings: Iterable[str],
    minimum_source_urls: int,
    url_probe: Callable[[str], bool] = live_url_probe,
) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    words = re.findall(r"\b[\w’-]+\b", re.sub(r"https://\S+", "", text))
    headings = {match.strip().casefold() for match in re.findall(r"^#{1,6}\s+(.+)$", text, re.M)}
    missing = [name for name in required_headings if name.strip().casefold() not in headings]
    urls = list(dict.fromkeys(re.findall(r"https://[^\s)>\]]+", text)))
    dead = [url for url in urls[:20] if not url_probe(url)]
    ok = min_words <= len(words) <= max_words and not missing and len(urls) >= minimum_source_urls and not dead
    return {"ok": ok, "word_count": len(words), "missing_headings": missing, "source_count": len(urls), "dead_urls": dead}


def validate_csv_contract(
    path: Path,
    *,
    expected_header: list[str],
    expected_rows: int,
    unique_columns: Iterable[str] = (),
) -> dict[str, Any]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        header = reader.fieldnames or []
    duplicates: dict[str, int] = {}
    for column in unique_columns:
        values = [str(row.get(column) or "").strip().casefold() for row in rows]
        duplicates[column] = len(values) - len(set(values))
    ok = header == expected_header and len(rows) == expected_rows and all(value == 0 for value in duplicates.values())
    return {"ok": ok, "header": header, "row_count": len(rows), "duplicates": duplicates}


def validate_structured_content(path: Path, *, required_headings: Iterable[str], min_words: int, max_words: int) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    words = re.findall(r"\b[\w’-]+\b", text)
    headings = {match.strip().casefold() for match in re.findall(r"^#{1,6}\s+(.+)$", text, re.M)}
    missing = [name for name in required_headings if name.strip().casefold() not in headings]
    return {"ok": min_words <= len(words) <= max_words and not missing, "word_count": len(words), "missing_headings": missing}


def public_matrix() -> list[dict[str, Any]]:
    return [row.__dict__.copy() for row in CAPABILITY_REGISTRY]
