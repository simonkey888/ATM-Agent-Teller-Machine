from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .universal_radar import JsonHttpClient


OWNER_PROJECT_CANDIDATES = (
    "radar-oportunidades",
    "radar-oportunidades-501015",
    "cryptic-pipe-275911",
    "project-45e938f4-e9d3-4f85-8ed",
)
FREE_E2_MICRO_REGIONS = {"us-central1", "us-east1", "us-west1"}


class GcpInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    projects: list[str] = Field(default_factory=list)
    instances: list[dict[str, Any]] = Field(default_factory=list)
    cloud_build_enabled_projects: list[str] = Field(default_factory=list)
    credential_present: bool = False
    read_only: bool = True


class GcpZeroCostDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: str | None
    region: str | None
    machine_type: str | None
    eligible: bool
    estimated_billable_usd: Decimal
    reason: str


class GcpReadOnlyBackend:
    """Inventory/validation only. It deliberately exposes no create/delete API."""

    def __init__(self, http: JsonHttpClient | None = None):
        self.http = http or JsonHttpClient()

    @property
    def credential_present(self) -> bool:
        return bool(os.getenv("GCP_ACCESS_TOKEN", "").strip())

    def inventory(self, projects: tuple[str, ...] = OWNER_PROJECT_CANDIDATES) -> GcpInventory:
        token = os.getenv("GCP_ACCESS_TOKEN", "").strip()
        if not token:
            return GcpInventory(projects=list(projects), credential_present=False)
        headers = {"Authorization": f"Bearer {token}"}
        instances: list[dict[str, Any]] = []
        build_enabled: list[str] = []
        for project in projects:
            try:
                data = self.http.get_json(
                    f"https://compute.googleapis.com/compute/v1/projects/{project}/aggregated/instances",
                    headers=headers,
                    timeout=8,
                )
                for scoped in (data.get("items") or {}).values() if isinstance(data, dict) else []:
                    if isinstance(scoped, dict):
                        for row in scoped.get("instances") or []:
                            if isinstance(row, dict):
                                instances.append(
                                    {
                                        "project": project,
                                        "name": row.get("name"),
                                        "status": row.get("status"),
                                        "machineType": str(row.get("machineType") or "").rsplit("/", 1)[-1],
                                        "zone": str(row.get("zone") or "").rsplit("/", 1)[-1],
                                    }
                                )
            except Exception:
                continue
            try:
                svc = self.http.get_json(
                    f"https://serviceusage.googleapis.com/v1/projects/{project}/services/cloudbuild.googleapis.com",
                    headers=headers,
                    timeout=6,
                )
                if isinstance(svc, dict) and str(svc.get("state") or "").upper() == "ENABLED":
                    build_enabled.append(project)
            except Exception:
                pass
        return GcpInventory(projects=list(projects), instances=instances, cloud_build_enabled_projects=build_enabled, credential_present=True)

    @staticmethod
    def validate_e2_micro(*, project: str, region: str, machine_type: str, estimated_billable_usd: Decimal | int | str = Decimal("0")) -> GcpZeroCostDecision:
        cost = Decimal(str(estimated_billable_usd))
        if cost > 0:
            return GcpZeroCostDecision(project=project, region=region, machine_type=machine_type, eligible=False, estimated_billable_usd=cost, reason="BILLABLE_ESTIMATE_GT_ZERO")
        if project not in OWNER_PROJECT_CANDIDATES:
            return GcpZeroCostDecision(project=project, region=region, machine_type=machine_type, eligible=False, estimated_billable_usd=cost, reason="PROJECT_NOT_OWNER_ALLOWLISTED")
        if region not in FREE_E2_MICRO_REGIONS:
            return GcpZeroCostDecision(project=project, region=region, machine_type=machine_type, eligible=False, estimated_billable_usd=cost, reason="REGION_NOT_FREE_E2_MICRO_ELIGIBLE")
        if machine_type != "e2-micro":
            return GcpZeroCostDecision(project=project, region=region, machine_type=machine_type, eligible=False, estimated_billable_usd=cost, reason="MACHINE_TYPE_NOT_E2_MICRO")
        return GcpZeroCostDecision(project=project, region=region, machine_type=machine_type, eligible=True, estimated_billable_usd=Decimal("0"), reason="ZERO_COST_SHAPE_POLICY_PASS")

    @staticmethod
    def validate_cloud_build(*, estimated_billable_usd: Decimal | int | str, free_quota_confirmed: bool) -> bool:
        return Decimal(str(estimated_billable_usd)) == 0 and bool(free_quota_confirmed)

    @staticmethod
    def choose_canonical_project(inventory: GcpInventory) -> str | None:
        preferred = os.getenv("ATM_GCP_PROJECT", "").strip()
        if preferred:
            return preferred if preferred in OWNER_PROJECT_CANDIDATES else None
        # Prefer an already-used radar project; never create a project implicitly.
        present = set(inventory.projects)
        for candidate in OWNER_PROJECT_CANDIDATES:
            if candidate in present:
                return candidate
        return None
