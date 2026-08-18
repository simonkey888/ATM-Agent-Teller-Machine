from __future__ import annotations

import os
import urllib.parse
from dataclasses import dataclass
from typing import Any

from .radar_sources import _load_secret_state, _persist_secret_state
from .universal_radar import JsonHttpClient


class SuperteamAgentError(RuntimeError):
    pass


@dataclass(frozen=True)
class SuperteamCredentialState:
    agent_id: str
    username: str | None
    status: str


class SuperteamAgentApi:
    """Exactly-one-agent boundary for the official Superteam Earn Agent API.

    apiKey and claimCode are write-only secrets: public methods never return them.
    """

    def __init__(self, http: JsonHttpClient | None = None, base_url: str = "https://superteam.fun"):
        self.http = http or JsonHttpClient()
        self.base_url = base_url.rstrip("/")

    def _secret_state(self) -> dict[str, Any]:
        return _load_secret_state()

    def _credential_tuple(self) -> tuple[str, str, str, str | None]:
        state = self._secret_state()
        api_key = os.getenv("SUPERTEAM_AGENT_API_KEY", "").strip() or str(state.get("superteam_api_key") or "").strip()
        agent_id = str(state.get("superteam_agent_id") or "").strip()
        claim_code = str(state.get("superteam_claim_code") or "").strip()
        username = str(state.get("superteam_username") or "").strip() or None
        present = (bool(api_key), bool(agent_id), bool(claim_code))
        if any(present) and not all(present):
            raise SuperteamAgentError("PARTIAL_CREDENTIAL_STATE_OWNER_GATE")
        if not all(present):
            raise SuperteamAgentError("SUPERTEAM_AGENT_NOT_REGISTERED")
        return api_key, agent_id, claim_code, username

    def register_exactly_once(self, name: str = "atm-universal-money-radar") -> SuperteamCredentialState:
        state = self._secret_state()
        existing = [state.get("superteam_api_key"), state.get("superteam_agent_id"), state.get("superteam_claim_code")]
        env_key = os.getenv("SUPERTEAM_AGENT_API_KEY", "").strip()
        if all(existing):
            return SuperteamCredentialState(str(state["superteam_agent_id"]), str(state.get("superteam_username") or "") or None, "EXISTING")
        if any(existing) or env_key:
            # An env-only key cannot prove the matching claim code/agent identity. Never register again.
            raise SuperteamAgentError("PARTIAL_OR_EXTERNAL_CREDENTIAL_STATE_OWNER_GATE")
        data, _, _ = self.http.request_json("POST", f"{self.base_url}/api/agents", body={"name": name}, timeout=12)
        if not isinstance(data, dict):
            raise SuperteamAgentError("REGISTER_RESPONSE_MALFORMED")
        api_key = str(data.get("apiKey") or "").strip()
        claim_code = str(data.get("claimCode") or "").strip()
        agent_id = str(data.get("agentId") or "").strip()
        username = str(data.get("username") or "").strip() or None
        if not api_key or not claim_code or not agent_id:
            raise SuperteamAgentError("REGISTER_RESPONSE_INCOMPLETE")
        state.update({
            "superteam_api_key": api_key,
            "superteam_claim_code": claim_code,
            "superteam_agent_id": agent_id,
            "superteam_username": username,
        })
        _persist_secret_state(state)
        return SuperteamCredentialState(agent_id, username, "CREATED")

    def _headers(self) -> dict[str, str]:
        api_key, _, _, _ = self._credential_tuple()
        return {"Authorization": f"Bearer {api_key}"}

    def live_agent_eligible(self, *, take: int = 50, deadline: str | None = None, listing_type: str | None = None) -> dict[str, Any]:
        params: dict[str, str] = {"take": str(max(1, min(100, int(take))))}
        if deadline:
            params["deadline"] = deadline
        if listing_type:
            params["type"] = listing_type
        data = self.http.get_json(
            f"{self.base_url}/api/agents/listings/live?{urllib.parse.urlencode(params)}",
            headers=self._headers(), timeout=10,
        )
        return data if isinstance(data, dict) else {"listings": data if isinstance(data, list) else []}

    def listing_details(self, slug: str) -> dict[str, Any]:
        data = self.http.get_json(
            f"{self.base_url}/api/agents/listings/details/{urllib.parse.quote(slug)}",
            headers=self._headers(), timeout=10,
        )
        if not isinstance(data, dict):
            raise SuperteamAgentError("LISTING_DETAILS_MALFORMED")
        access = str(data.get("agentAccess") or (data.get("listing") or {}).get("agentAccess") or "").upper()
        if access and access not in {"AGENT_ALLOWED", "AGENT_ONLY"}:
            raise SuperteamAgentError("LISTING_NOT_AGENT_ELIGIBLE")
        return data

    def create_submission(
        self,
        *,
        listing_id: str,
        link: str,
        telegram: str,
        tweet: str = "",
        other_info: str = "",
        eligibility_answers: list[Any] | None = None,
        ask: str = "",
    ) -> dict[str, Any]:
        if not telegram.startswith("https://t.me/"):
            raise SuperteamAgentError("PUBLIC_TELEGRAM_URL_REQUIRED")
        data, _, _ = self.http.request_json(
            "POST", f"{self.base_url}/api/agents/submissions/create", headers=self._headers(),
            body={"listingId": listing_id, "link": link, "tweet": tweet, "otherInfo": other_info, "eligibilityAnswers": eligibility_answers or [], "ask": ask, "telegram": telegram},
            timeout=12,
        )
        return data if isinstance(data, dict) else {}

    def update_submission(self, payload: dict[str, Any]) -> dict[str, Any]:
        data, _, _ = self.http.request_json("POST", f"{self.base_url}/api/agents/submissions/update", headers=self._headers(), body=payload, timeout=12)
        return data if isinstance(data, dict) else {}

    def create_comment(self, payload: dict[str, Any]) -> dict[str, Any]:
        data, _, _ = self.http.request_json("POST", f"{self.base_url}/api/agents/comments/create", headers=self._headers(), body=payload, timeout=12)
        return data if isinstance(data, dict) else {}

    def submission_status(self, submission_id: str) -> dict[str, Any]:
        # Official agent API status endpoint may evolve; keep path explicit and fail closed on incompatibility.
        data = self.http.get_json(
            f"{self.base_url}/api/agents/submissions/{urllib.parse.quote(submission_id)}",
            headers=self._headers(), timeout=10,
        )
        if not isinstance(data, dict):
            raise SuperteamAgentError("SUBMISSION_STATUS_MALFORMED")
        return data

    def winner_human_gate(self) -> dict[str, str]:
        _, agent_id, _, _ = self._credential_tuple()
        return {
            "kind": "SUPERTEAM_WINNER_HUMAN_CLAIM",
            "agent_id": agent_id,
            "action": "Owner opens the private claim URL using locally stored claimCode and completes claim with ready Talent profile.",
            "claim_code_exposed": "false",
        }
