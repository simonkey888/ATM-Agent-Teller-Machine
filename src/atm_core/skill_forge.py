from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


SKILL_FORGE_SCHEMA = "ATM_DEMAND_DRIVEN_SKILL_FORGE_V2"
PROMOTION_GATE_SCHEMA = "ATM_DETERMINISTIC_PROMOTION_V2"
FORGE_STAGES = (
    "GAP",
    "SPEC",
    "EXISTING_PATTERN_SEARCH",
    "EXECUTOR",
    "CHECKER",
    "SECRETLESS_SANDBOX",
    "HAPPY",
    "EDGE",
    "ADVERSARIAL",
    "MARKET_SHAPED_FIXTURE",
    "FALSIFY",
    "BENCHMARK",
    "DETERMINISTIC_PROMOTION",
)
FIXTURE_CLASSES = ("happy", "edge", "adversarial", "market_shaped")
ALLOWED_SANDBOX_NETWORK_POLICIES = {"DENY", "READ_ONLY_ALLOWLIST"}


@dataclass(frozen=True)
class SandboxFixtureEvidence:
    """Attestation emitted by an external secretless worker/sandbox, never candidate code."""

    fixture_class: str
    sandbox_id: str
    process_isolated: bool
    environment_secret_count: int
    network_policy: str
    tool_allowlist_enforced: bool
    executor_pass: bool
    checker_pass: bool
    artifact_contract_pass: bool

    @property
    def secretless(self) -> bool:
        return self.environment_secret_count == 0

    @property
    def passed(self) -> bool:
        return all(
            (
                self.fixture_class in FIXTURE_CLASSES,
                bool(self.sandbox_id),
                self.process_isolated,
                self.secretless,
                self.network_policy in ALLOWED_SANDBOX_NETWORK_POLICIES,
                self.tool_allowlist_enforced,
                self.executor_pass,
                self.checker_pass,
                self.artifact_contract_pass,
            )
        )


@dataclass(frozen=True)
class ForgeEvidence:
    owner_cost_usd: str
    commercial_use_ok: bool
    supply_chain_pinned: bool
    license_verified: bool
    checker_independent: bool
    tool_contract_explicit: bool
    network_contract_explicit: bool
    resource_contract_explicit: bool
    no_existing_capability_duplicate: bool
    sandbox_evidence: tuple[SandboxFixtureEvidence, ...]
    benchmark_pass: bool
    falsification_pass: bool


@dataclass(frozen=True)
class ForgeDecision:
    schema: str
    capability_id: str
    promoted: bool
    failures: tuple[str, ...]
    registry_mutated: bool = False


def deterministic_promotion_gate(capability_id: str, evidence: ForgeEvidence) -> ForgeDecision:
    by_class: dict[str, list[SandboxFixtureEvidence]] = {name: [] for name in FIXTURE_CLASSES}
    for row in evidence.sandbox_evidence:
        if row.fixture_class in by_class:
            by_class[row.fixture_class].append(row)
    checks = {
        "ZERO_SPEND": evidence.owner_cost_usd == "0",
        "COMMERCIAL_USE": evidence.commercial_use_ok,
        "SUPPLY_CHAIN_PINNED": evidence.supply_chain_pinned,
        "LICENSE_VERIFIED": evidence.license_verified,
        "CHECKER_INDEPENDENT": evidence.checker_independent,
        "TOOL_CONTRACT_EXPLICIT": evidence.tool_contract_explicit,
        "NETWORK_CONTRACT_EXPLICIT": evidence.network_contract_explicit,
        "RESOURCE_CONTRACT_EXPLICIT": evidence.resource_contract_explicit,
        "NO_EXISTING_CAPABILITY_DUPLICATE": evidence.no_existing_capability_duplicate,
        "HAPPY_PASS": bool(by_class["happy"]) and all(row.passed for row in by_class["happy"]),
        "EDGE_PASS": bool(by_class["edge"]) and all(row.passed for row in by_class["edge"]),
        "ADVERSARIAL_PASS": bool(by_class["adversarial"]) and all(row.passed for row in by_class["adversarial"]),
        "MARKET_SHAPED_PASS": bool(by_class["market_shaped"]) and all(row.passed for row in by_class["market_shaped"]),
        "FALSIFICATION_PASS": evidence.falsification_pass,
        "BENCHMARK_PASS": evidence.benchmark_pass,
    }
    failures = tuple(name for name, passed in checks.items() if not passed)
    return ForgeDecision(PROMOTION_GATE_SCHEMA, str(capability_id), not failures, failures, False)


class DemandDrivenSkillForge:
    """Consumes sandbox attestations only; candidate executors/checkers never run in the supervisor."""

    schema = SKILL_FORGE_SCHEMA
    stages = FORGE_STAGES
    fixture_classes = FIXTURE_CLASSES
    registry_mutation_authority = False
    external_effect_authority = False
    in_process_candidate_execution = False

    def evaluate(
        self,
        capability_id: str,
        sandbox_evidence: Iterable[SandboxFixtureEvidence],
        *,
        commercial_use_ok: bool,
        supply_chain_pinned: bool,
        license_verified: bool,
        existing_duplicate: bool = False,
        falsification_pass: bool,
        benchmark_pass: bool,
        owner_cost_usd: str = "0",
    ) -> ForgeDecision:
        evidence = ForgeEvidence(
            owner_cost_usd=str(owner_cost_usd),
            commercial_use_ok=bool(commercial_use_ok),
            supply_chain_pinned=bool(supply_chain_pinned),
            license_verified=bool(license_verified),
            checker_independent=True,
            tool_contract_explicit=True,
            network_contract_explicit=True,
            resource_contract_explicit=True,
            no_existing_capability_duplicate=not existing_duplicate,
            sandbox_evidence=tuple(sandbox_evidence),
            benchmark_pass=bool(benchmark_pass),
            falsification_pass=bool(falsification_pass),
        )
        return deterministic_promotion_gate(capability_id, evidence)


def public_skill_forge_contract() -> dict[str, object]:
    return {
        "schema": SKILL_FORGE_SCHEMA,
        "stages": list(FORGE_STAGES),
        "fixture_classes": list(FIXTURE_CLASSES),
        "candidate_code_execution": "EXTERNAL_SECRETLESS_SANDBOX_WORKER_ONLY",
        "in_process_candidate_execution": False,
        "required_sandbox_network_policies": sorted(ALLOWED_SANDBOX_NETWORK_POLICIES),
        "registry_mutation_authority": False,
        "external_effect_authority": False,
        "promotion_gate": PROMOTION_GATE_SCHEMA,
        "llm_can_self_enable": False,
        "owner_cost_ceiling_usd": "0",
        "fail_closed": True,
    }
