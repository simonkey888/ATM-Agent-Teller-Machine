from __future__ import annotations

import csv
import hashlib
import ipaddress
import json
import re
import socket
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable


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
    DATA_ANALYSIS_BOUNDED = "DATA_ANALYSIS_BOUNDED"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True)
class WorkClassContract:
    work_class: WorkClass
    executor_id: str
    checker_id: str
    qualified: bool
    reason: str


WORK_CLASS_MATRIX: tuple[WorkClassContract, ...] = (
    WorkClassContract(WorkClass.RESEARCH_SYNTHESIS, "ZERO_COST_SOURCE_MAKER_V1", "SOURCE_MARKDOWN_CHECK_V1", True, "fixture-qualified"),
    WorkClassContract(WorkClass.SOURCE_BACKED_FACT_TABLE, "ZERO_COST_SOURCE_TABLE_V1", "SOURCE_CSV_CHECK_V1", True, "fixture-qualified"),
    WorkClassContract(WorkClass.CONTENT_STRUCTURED, "ZERO_COST_STRUCTURED_WRITER_V1", "STRUCTURE_LIMIT_CHECK_V1", True, "fixture-qualified"),
    WorkClassContract(WorkClass.CSV_JSON_TRANSFORM, "DETERMINISTIC_PY_TRANSFORM_V1", "SCHEMA_INVARIANT_CHECK_V1", True, "fixture-qualified"),
    WorkClassContract(WorkClass.DATA_ANALYSIS_BOUNDED, "DETERMINISTIC_PY_ANALYSIS_V1", "REPRODUCIBILITY_CHECK_V1", True, "fixture-qualified"),
    WorkClassContract(WorkClass.DOCS_TECHNICAL, "ZERO_COST_DOCS_MAKER_V1", "STRUCTURE_LIMIT_CHECK_V1", True, "fixture-qualified"),
    WorkClassContract(WorkClass.WEB_SINGLE_FILE_INTERACTIVE, "ZERO_COST_HTML_MAKER_V1", "PLAYWRIGHT_BROWSER_CHECK", False, "browser fixture not pinned"),
    WorkClassContract(WorkClass.LIGHT_WEB_DESIGN, "ZERO_COST_HTML_MAKER_V1", "PLAYWRIGHT_BROWSER_CHECK", False, "browser fixture not pinned"),
    WorkClassContract(WorkClass.WEB_QA, "NONE", "PLAYWRIGHT_BROWSER_CHECK", False, "browser fixture not pinned"),
    WorkClassContract(WorkClass.TRANSLATION_LOCALIZATION, "ARGOS_CANDIDATE", "NUMBER_TERMINOLOGY_CHECK", False, "language-pair benchmark absent"),
    WorkClassContract(WorkClass.DOCUMENT_OCR_EXTRACTION, "TESSERACT_5_5_3_CANDIDATE", "ANCHOR_CONFIDENCE_CHECK", False, "OCR benchmark absent"),
    WorkClassContract(WorkClass.DOCUMENT_DATA_EXTRACTION, "MARKITDOWN_CANDIDATE", "PROVENANCE_CHECK", False, "untrusted-I/O sandbox benchmark absent"),
    WorkClassContract(WorkClass.SPREADSHEET_TRANSFORM, "OPENPYXL_CANDIDATE", "REOPEN_INVARIANT_CHECK", False, "fixture benchmark absent"),
    WorkClassContract(WorkClass.GITHUB_BOUNDED_PATCH, "EXISTING_PATCH_WORKER", "TEST_AND_DIFF_CHECK", True, "existing regression-qualified"),
    WorkClassContract(WorkClass.API_INTEGRATION, "EXISTING_PATCH_WORKER", "TEST_AND_CONTRACT_CHECK", True, "existing regression-qualified"),
    WorkClassContract(WorkClass.TEST_FIX, "EXISTING_PATCH_WORKER", "TARGETED_REGRESSION_CHECK", True, "existing regression-qualified"),
)


def qualified_work_classes() -> tuple[str, ...]:
    return tuple(row.work_class.value for row in WORK_CLASS_MATRIX if row.qualified)


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
    if re.search(r"\b(mp4|video generation|photo editing|brand identity|logo design)\b", text):
        return WorkClass.UNSUPPORTED
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
    return any(row.work_class == work_class and row.qualified for row in WORK_CLASS_MATRIX)


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
    return [asdict(row) | {"work_class": row.work_class.value} for row in WORK_CLASS_MATRIX]
