from __future__ import annotations

from typing import Any

from .models import HumanGate, Phase
from .opportunities import (
    HumanGateRequired,
    OpportunityValidationError,
    WorkProtocolOpportunityAdapter,
    persist_local_atm_values,
)


class CurrentWorkProtocolOpportunityAdapter(WorkProtocolOpportunityAdapter):
    """WorkProtocol adapter pinned to the current documented agent-registration shape."""

    def _register(self) -> tuple[str, str]:
        if not self.payment_recipient_public_identifier:
            raise HumanGateRequired(
                HumanGate(
                    kind="PAYOUT_ONBOARDING",
                    reason="WorkProtocol registration can be automated, but a public payout wallet is required before economic claim",
                    exact_human_action="Set payment_recipient_public_identifier in config/atm.json to the OWNER public Base-compatible payout address; never provide a private key",
                    resume_phase=Phase.CLAIM,
                )
            )

        response: Any = self.http.post_json(
            f"{self.base_url}/api/agents/register",
            {
                "name": "atm-agent-teller-machine",
                "description": "Autonomous code-work agent with deterministic verification and payment-proof accounting",
                "walletAddress": self.payment_recipient_public_identifier,
                "capabilities": {
                    "categories": ["code"],
                    "languages": ["python", "typescript", "javascript"],
                },
            },
        )
        agent = response.get("agent") if isinstance(response, dict) and isinstance(response.get("agent"), dict) else response
        if not isinstance(agent, dict):
            raise OpportunityValidationError("WorkProtocol registration returned malformed response")
        api_key = str(
            response.get("apiKey")
            or response.get("api_key")
            or agent.get("apiKey")
            or agent.get("api_key")
            or ""
        ).strip()
        agent_id = str(agent.get("id") or agent.get("agentId") or response.get("agentId") or "").strip()
        if not api_key or not agent_id:
            raise OpportunityValidationError("WorkProtocol registration response lacks api key or agent id")
        persist_local_atm_values({"WORKPROTOCOL_API_KEY": api_key, "WORKPROTOCOL_AGENT_ID": agent_id})
        return api_key, agent_id


def upgrade_workprotocol_adapter(adapters: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    existing = adapters.get("workprotocol")
    if existing is None:
        return adapters
    cfg = config.get("opportunity_adapters", {}).get("workprotocol", {})
    adapters["workprotocol"] = CurrentWorkProtocolOpportunityAdapter(
        base_url=str(cfg.get("base_url") or "https://workprotocol.ai"),
        payment_recipient_public_identifier=str(config.get("payment_recipient_public_identifier") or ""),
    )
    return adapters
