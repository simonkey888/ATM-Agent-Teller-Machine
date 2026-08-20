from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable


REGISTRY_SCHEMA = "ATM_CAPABILITY_REGISTRY_V2"


@dataclass(frozen=True)
class CapabilityContract:
    capability_id: str
    version: str
    executor_id: str
    checker_id: str
    required_tools: tuple[str, ...]
    sandbox_profile: str
    supported_input_types: tuple[str, ...]
    supported_output_types: tuple[str, ...]
    cost_ceiling_usd: str
    commercial_use_contract: str
    benchmark_fixture_ids: tuple[str, ...]
    benchmark_result: str
    max_runtime_seconds: int
    max_memory_mb: int
    network_policy: str
    artifact_contract: str
    failure_policy: str
    enabled: bool


def _contract(
    capability_id: str,
    executor_id: str,
    checker_id: str,
    *,
    tools: Iterable[str] = (),
    inputs: Iterable[str] = ("text/plain",),
    outputs: Iterable[str] = ("text/markdown",),
    benchmark: str = "PASS",
    fixtures: Iterable[str] = (),
    enabled: bool = True,
    runtime: int = 900,
    memory: int = 1024,
    network: str = "DENY_EXCEPT_JOB_ALLOWLIST",
    artifact: str = "ONE_REGULAR_FILE",
    commercial: str = "ATM_ZERO_SPEND_COMMERCIAL_TASK_V1",
) -> CapabilityContract:
    return CapabilityContract(
        capability_id=capability_id,
        version="2.0.0",
        executor_id=executor_id,
        checker_id=checker_id,
        required_tools=tuple(tools),
        sandbox_profile="EPHEMERAL_SECRETLESS_WORKER_V2",
        supported_input_types=tuple(inputs),
        supported_output_types=tuple(outputs),
        cost_ceiling_usd="0",
        commercial_use_contract=commercial,
        benchmark_fixture_ids=tuple(fixtures),
        benchmark_result=benchmark,
        max_runtime_seconds=runtime,
        max_memory_mb=memory,
        network_policy=network,
        artifact_contract=artifact,
        failure_policy="FAIL_CLOSED_RETURN_TO_DISCOVERY",
        enabled=enabled,
    )


_PROVEN: tuple[CapabilityContract, ...] = (
    _contract("RESEARCH_SYNTHESIS", "ZERO_COST_SOURCE_MAKER_V1", "SOURCE_MARKDOWN_CHECK_V1", fixtures=("research-sources-v1",)),
    _contract("SOURCE_BACKED_FACT_TABLE", "ZERO_COST_SOURCE_TABLE_V1", "SOURCE_CSV_CHECK_V1", outputs=("text/csv",), fixtures=("source-table-v1",)),
    _contract("CONTENT_STRUCTURED", "ZERO_COST_STRUCTURED_WRITER_V1", "STRUCTURE_LIMIT_CHECK_V1", fixtures=("structured-content-v1",)),
    _contract("CSV_JSON_TRANSFORM", "DETERMINISTIC_PY_TRANSFORM_V1", "SCHEMA_INVARIANT_CHECK_V1", inputs=("text/csv", "application/json"), outputs=("text/csv", "application/json"), fixtures=("csv-json-roundtrip-v1",)),
    _contract("DATA_ANALYSIS_BOUNDED", "DETERMINISTIC_PY_ANALYSIS_V1", "REPRODUCIBILITY_CHECK_V1", inputs=("text/csv", "application/json"), fixtures=("bounded-analysis-v1",)),
    _contract("DOCS_TECHNICAL", "ZERO_COST_DOCS_MAKER_V1", "STRUCTURE_LIMIT_CHECK_V1", fixtures=("technical-docs-v1",)),
    _contract("GITHUB_BOUNDED_PATCH", "EXISTING_PATCH_WORKER", "TEST_AND_DIFF_CHECK", tools=("git", "github"), fixtures=("bounded-patch-regression",)),
    _contract("API_INTEGRATION", "EXISTING_PATCH_WORKER", "TEST_AND_CONTRACT_CHECK", tools=("python", "git"), fixtures=("api-contract-regression",)),
    _contract("TEST_FIX", "EXISTING_PATCH_WORKER", "TARGETED_REGRESSION_CHECK", tools=("python", "git"), fixtures=("test-fix-regression",)),
    _contract(
        "BROWSER_PUBLIC_DYNAMIC_EXTRACTION",
        "OBSCURA_V0_2_0_READONLY",
        "DOM_PROVENANCE_CHECK_V1",
        tools=("obscura@0.2.0",),
        inputs=("application/vnd.atm.browser-job+json",),
        outputs=("application/json", "text/markdown"),
        fixtures=("browser-dynamic-local-v1",),
        runtime=180,
        memory=384,
        network="READ_ONLY_HTTPS_ALLOWLIST_NO_STEALTH",
        artifact="DOM_OR_MARKDOWN_PLUS_SOURCE_PROVENANCE",
        commercial="APACHE_2_0_BINARY_PINNED_PUBLIC_READ_ONLY",
    ),
    _contract(
        "VIDEO_SHORT_FORM_ASSEMBLY",
        "FFMPEG_LOCAL_ASSEMBLER_V1",
        "FFPROBE_STRUCTURAL_CHECK_V1",
        tools=("ffmpeg", "ffprobe"),
        inputs=("application/vnd.atm.video-job+json", "video/mp4", "image/png", "audio/wav"),
        outputs=("video/mp4", "application/vnd.atm.provenance+json"),
        fixtures=("video-short-local-rights-v1",),
        runtime=1200,
        memory=2048,
        network="DENY",
        artifact="MP4_H264_AAC_PLUS_PROVENANCE",
        commercial="OWNER_OR_CC0_LOCAL_ASSETS_ONLY",
    ),
)


_UNPROVEN: tuple[CapabilityContract, ...] = (
    _contract("WEB_SINGLE_FILE_INTERACTIVE", "ZERO_COST_HTML_MAKER_V1", "PLAYWRIGHT_BROWSER_CHECK", benchmark="NOT_RUN", enabled=False),
    _contract("LIGHT_WEB_DESIGN", "ZERO_COST_HTML_MAKER_V1", "PLAYWRIGHT_BROWSER_CHECK", benchmark="NOT_RUN", enabled=False),
    _contract("WEB_QA", "NONE", "PLAYWRIGHT_BROWSER_CHECK", benchmark="NOT_RUN", enabled=False),
    _contract("TRANSLATION_LOCALIZATION", "ARGOS_CANDIDATE", "NUMBER_TERMINOLOGY_CHECK", benchmark="NOT_RUN", enabled=False),
    _contract("DOCUMENT_OCR_EXTRACTION", "TESSERACT_CANDIDATE", "ANCHOR_CONFIDENCE_CHECK", benchmark="NOT_RUN", enabled=False),
    _contract("DOCUMENT_DATA_EXTRACTION", "MARKITDOWN_CANDIDATE", "PROVENANCE_CHECK", benchmark="NOT_RUN", enabled=False),
    _contract("SPREADSHEET_TRANSFORM", "OPENPYXL_CANDIDATE", "REOPEN_INVARIANT_CHECK", benchmark="NOT_RUN", enabled=False),
)


CAPABILITY_REGISTRY: tuple[CapabilityContract, ...] = _PROVEN + _UNPROVEN


def get_capability(capability_id: str) -> CapabilityContract | None:
    wanted = str(capability_id).strip().upper()
    return next((row for row in CAPABILITY_REGISTRY if row.capability_id == wanted), None)


def capability_enabled(capability_id: str) -> bool:
    row = get_capability(capability_id)
    return bool(row and row.enabled and row.cost_ceiling_usd == "0" and row.benchmark_result == "PASS")


def enabled_capability_ids() -> tuple[str, ...]:
    return tuple(row.capability_id for row in CAPABILITY_REGISTRY if capability_enabled(row.capability_id))


def public_registry() -> dict[str, object]:
    return {"schema": REGISTRY_SCHEMA, "capabilities": [asdict(row) for row in CAPABILITY_REGISTRY]}
