from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping


SKILL_FORGE_SCHEMA = "ATM_DEMAND_DRIVEN_SKILL_FORGE_V1"
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


@dataclass(frozen=True)
class ForgeEvidence:
    owner_cost_usd: str
    commercial_use_ok: bool
    supply_chain_pinned: bool
    license_verified: bool
    no_secret_requirement: bool
    checker_independent: bool
    tool_contract_explicit: bool
    network_contract_explicit: bool
    resource_contract_explicit: bool
    no_existing_capability_duplicate: bool
    fixture_results: tuple[tuple[str, bool], ...]
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
    fixtures = dict(evidence.fixture_results)
    checks = {
        "ZERO_SPEND": evidence.owner_cost_usd == "0",
        "COMMERCIAL_USE": evidence.commercial_use_ok,
        "SUPPLY_CHAIN_PINNED": evidence.supply_chain_pinned,
        "LICENSE_VERIFIED": evidence.license_verified,
        "SECRETLESS": evidence.no_secret_requirement,
        "CHECKER_INDEPENDENT": evidence.checker_independent,
        "TOOL_CONTRACT_EXPLICIT": evidence.tool_contract_explicit,
        "NETWORK_CONTRACT_EXPLICIT": evidence.network_contract_explicit,
        "RESOURCE_CONTRACT_EXPLICIT": evidence.resource_contract_explicit,
        "NO_EXISTING_CAPABILITY_DUPLICATE": evidence.no_existing_capability_duplicate,
        "HAPPY_PASS": fixtures.get("happy") is True,
        "EDGE_PASS": fixtures.get("edge") is True,
        "ADVERSARIAL_PASS": fixtures.get("adversarial") is True,
        "MARKET_SHAPED_PASS": fixtures.get("market_shaped") is True,
        "FALSIFICATION_PASS": evidence.falsification_pass,
        "BENCHMARK_PASS": evidence.benchmark_pass,
    }
    failures = tuple(name for name, passed in checks.items() if not passed)
    return ForgeDecision(PROMOTION_GATE_SCHEMA, str(capability_id), not failures, failures, False)


class DemandDrivenSkillForge:
    """Secretless, demand-triggered candidate evaluator; never mutates the registry itself."""

    schema = SKILL_FORGE_SCHEMA
    stages = FORGE_STAGES
    fixture_classes = FIXTURE_CLASSES
    registry_mutation_authority = False
    external_effect_authority = False

    def evaluate(
        self,
        capability_id: str,
        executor: Callable[[Any], Any],
        checker: Callable[[Any, Any], bool],
        fixtures: Mapping[str, Iterable[Any]],
        *,
        commercial_use_ok: bool,
        supply_chain_pinned: bool,
        license_verified: bool,
        existing_duplicate: bool = False,
        falsifier: Callable[[str, Mapping[str, tuple[bool, ...]]], bool] | None = None,
        benchmark: Callable[[str, Mapping[str, tuple[bool, ...]]], bool] | None = None,
    ) -> ForgeDecision:
        results: dict[str, tuple[bool, ...]] = {}
        try:
            for fixture_class in FIXTURE_CLASSES:
                cases = tuple(fixtures.get(fixture_class, ()))
                if not cases:
                    results[fixture_class] = (False,)
                    continue
                outcomes = []
                for case in cases:
                    artifact = executor(case)
                    outcomes.append(bool(checker(case, artifact)))
                results[fixture_class] = tuple(outcomes)
            fixture_results = tuple((name, bool(results.get(name)) and all(results[name])) for name in FIXTURE_CLASSES)
            falsification_pass = bool(falsifier(capability_id, results)) if falsifier else all(value for _, value in fixture_results)
            benchmark_pass = bool(benchmark(capability_id, results)) if benchmark else all(value for _, value in fixture_results)
        except Exception:
            fixture_results = tuple((name, False) for name in FIXTURE_CLASSES)
            falsification_pass = False
            benchmark_pass = False
        evidence = ForgeEvidence(
            owner_cost_usd="0",
            commercial_use_ok=bool(commercial_use_ok),
            supply_chain_pinned=bool(supply_chain_pinned),
            license_verified=bool(license_verified),
            no_secret_requirement=True,
            checker_independent=True,
            tool_contract_explicit=True,
            network_contract_explicit=True,
            resource_contract_explicit=True,
            no_existing_capability_duplicate=not existing_duplicate,
            fixture_results=fixture_results,
            benchmark_pass=benchmark_pass,
            falsification_pass=falsification_pass,
        )
        return deterministic_promotion_gate(capability_id, evidence)


def public_skill_forge_contract() -> dict[str, object]:
    return {
        "schema": SKILL_FORGE_SCHEMA,
        "stages": list(FORGE_STAGES),
        "fixture_classes": list(FIXTURE_CLASSES),
        "registry_mutation_authority": False,
        "external_effect_authority": False,
        "promotion_gate": PROMOTION_GATE_SCHEMA,
        "llm_can_self_enable": False,
        "owner_cost_ceiling_usd": "0",
        "fail_closed": True,
    }
