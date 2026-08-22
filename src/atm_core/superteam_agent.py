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

    Runtime LIST/SUBMIT operations authenticate with apiKey only and bind responses
    to a non-secret agentId. claimCode is deliberately owner-local and is never a
    cloud/runtime dependency. Public methods never return secret fields.
    """

    def __init__(self, http: JsonHttpClient | None = None, base_url: str = "https://superteam.fun"):
        self.http = http or JsonHttpClient()
        self.base_url = base_url.rstrip("/")

    def _secret_state(self) -> dict[str, Any]:
        return _load_secret_state()

    def _agent_identity(self) -> tuple[str, str | None]:
        state = self._secret_state()
        agent_id = os.getenv("SUPERTEAM_AGENT_ID", "").strip() or str(state.get("superteam_agent_id") or "").strip()
        username = os.getenv("SUPERTEAM_USERNAME", "").strip() or str(state.get("superteam_username") or "").strip() or None
        if not agent_id:
            raise SuperteamAgentError("SUPERTEAM_AGENT_ID_REQUIRED")
        return agent_id, username

    def _runtime_credentials(self) -> tuple[str, str, str | None]:
        state = self._secret_state()
        api_key = os.getenv("SUPERTEAM_AGENT_API_KEY", "").strip() or str(state.get("superteam_api_key") or "").strip()
        agent_id, username = self._agent_identity()
        if not api_key:
            raise SuperteamAgentError("SUPERTEAM_AGENT_API_KEY_REQUIRED")
        return api_key, agent_id, username

    def register_exactly_once(self, name: str = "atm-universal-money-radar") -> SuperteamCredentialState:
        state = self._secret_state()
        stored_key = str(state.get("superteam_api_key") or "").strip()
        stored_id = str(state.get("superteam_agent_id") or "").strip()
        stored_claim = str(state.get("superteam_claim_code") or "").strip()
        stored_username = str(state.get("superteam_username") or "").strip() or None
        env_key = os.getenv("SUPERTEAM_AGENT_API_KEY", "").strip()
        env_id = os.getenv("SUPERTEAM_AGENT_ID", "").strip()

        # A fully local credential set proves the already-created identity.
        if stored_key and stored_id and stored_claim:
            return SuperteamCredentialState(stored_id, stored_username, "EXISTING")

        # Any preserved identity/auth evidence forbids creating a second agent.
        # In particular, an env-only apiKey + public agentId is valid runtime auth,
        # but is intentionally insufficient authority to register another identity.
        if stored_key or stored_id or stored_claim or env_key or env_id:
            raise SuperteamAgentError("PRESERVED_IDENTITY_PRESENT_REGISTRATION_FORBIDDEN")

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
        api_key, _, _ = self._runtime_credentials()
        return {"Authorization": f"Bearer {api_key}"}

    def _assert_agent_binding(self, payload: dict[str, Any]) -> None:
        """Reject a response that explicitly identifies a different agent.

        Superteam does not include an agent id in every response, so absence is not
        promoted to a mismatch. If the field is present, it must match the preserved
        public identity.
        """
        _, expected_id, _ = self._runtime_credentials()
        candidates: list[Any] = [payload.get("agentId"), payload.get("agent_id")]
        for key in ("agent", "submission"):
            nested = payload.get(key)
            if isinstance(nested, dict):
                candidates.extend([nested.get("agentId"), nested.get("agent_id")])
        explicit = [str(v).strip() for v in candidates if v not in (None, "")]
        if explicit and any(v != expected_id for v in explicit):
            raise SuperteamAgentError("SUPERTEAM_AGENT_ID_MISMATCH")

    def live_agent_eligible(self, *, take: int = 50, deadline: str | None = None, listing_type: str | None = None) -> dict[str, Any]:
        params: dict[str, str] = {"take": str(max(1, min(100, int(take))))}
        # The current Superteam endpoint rejects the documented deadline query with
        # a server-side validation error. Keep deadline filtering local instead of
        # coupling discovery to that unstable parameter.
        if listing_type:
            params["type"] = listing_type
        data = self.http.get_json(
            f"{self.base_url}/api/agents/listings/live?{urllib.parse.urlencode(params)}",
            headers=self._headers(), timeout=10,
        )
        out = data if isinstance(data, dict) else {"listings": data if isinstance(data, list) else []}
        self._assert_agent_binding(out)
        if deadline:
            rows = out.get("listings") or out.get("data")
            if isinstance(rows, list):
                out = dict(out)
                out["listings"] = [row for row in rows if isinstance(row, dict) and str(row.get("deadline") or "") <= deadline]
        return out

    def listing_details(self, slug: str) -> dict[str, Any]:
        if not slug or "/" in slug or ".." in slug:
            raise SuperteamAgentError("LISTING_SLUG_INVALID")
        data = self.http.get_json(
            f"{self.base_url}/api/agents/listings/details/{urllib.parse.quote(slug)}",
            headers=self._headers(), timeout=10,
        )
        if not isinstance(data, dict):
            raise SuperteamAgentError("LISTING_DETAILS_MALFORMED")
        self._assert_agent_binding(data)
        listing = data.get("listing") if isinstance(data.get("listing"), dict) else data
        access = str(listing.get("agentAccess") or "").upper()
        if access and access not in {"AGENT_ALLOWED", "AGENT_ONLY"}:
            raise SuperteamAgentError("LISTING_NOT_AGENT_ELIGIBLE")
        return data

    def create_submission(
        self,
        *,
        listing_id: str,
        link: str,
        telegram: str = "https://t.me/simonkey888",
        tweet: str = "",
        other_info: str = "",
        eligibility_answers: list[Any] | None = None,
        ask: str | int | float | None = None,
    ) -> dict[str, Any]:
        if not listing_id:
            raise SuperteamAgentError("LISTING_ID_REQUIRED")
        if link and not link.startswith("https://"):
            raise SuperteamAgentError("SUBMISSION_LINK_MUST_USE_HTTPS")
        if not telegram.startswith(("https://t.me/", "http://t.me/")):
            raise SuperteamAgentError("PUBLIC_TELEGRAM_URL_REQUIRED")
        if not link and not other_info.strip():
            raise SuperteamAgentError("SUBMISSION_CONTENT_REQUIRED")
        data, _, _ = self.http.request_json(
            "POST", f"{self.base_url}/api/agents/submissions/create", headers=self._headers(),
            body={"listingId": listing_id, "link": link, "tweet": tweet, "otherInfo": other_info, "eligibilityAnswers": eligibility_answers or [], "ask": ask, "telegram": telegram},
            timeout=12,
        )
        out = data if isinstance(data, dict) else {}
        self._assert_agent_binding(out)
        return out

    def update_submission(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = dict(payload)
        telegram = str(body.get("telegram") or "https://t.me/simonkey888")
        if not telegram.startswith(("https://t.me/", "http://t.me/")):
            raise SuperteamAgentError("PUBLIC_TELEGRAM_URL_REQUIRED")
        body["telegram"] = telegram
        data, _, _ = self.http.request_json("POST", f"{self.base_url}/api/agents/submissions/update", headers=self._headers(), body=body, timeout=12)
        out = data if isinstance(data, dict) else {}
        self._assert_agent_binding(out)
        return out

    def comments(self, listing_id: str, *, skip: int = 0, take: int = 20) -> list[dict[str, Any]]:
        data = self.http.get_json(
            f"{self.base_url}/api/agents/comments/{urllib.parse.quote(listing_id)}?skip={max(0, int(skip))}&take={min(100, max(1, int(take)))}",
            headers=self._headers(), timeout=10,
        )
        rows = (data.get("comments") or data.get("data")) if isinstance(data, dict) else data
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    def create_comment(self, payload: dict[str, Any]) -> dict[str, Any]:
        message = str(payload.get("message") or "")
        if not message.strip() or len(message) > 4000:
            raise SuperteamAgentError("COMMENT_OUTSIDE_BOUND")
        data, _, _ = self.http.request_json("POST", f"{self.base_url}/api/agents/comments/create", headers=self._headers(), body=payload, timeout=12)
        out = data if isinstance(data, dict) else {}
        self._assert_agent_binding(out)
        return out

    def monitor_listing(self, slug: str) -> dict[str, Any]:
        """Read-only result monitor using the documented listing-details endpoint."""
        raw = self.listing_details(slug)
        listing = raw.get("listing") if isinstance(raw.get("listing"), dict) else raw
        submission = raw.get("submission") if isinstance(raw.get("submission"), dict) else {}
        status = str(submission.get("status") or "").upper()
        return {
            "listing_id": listing.get("id"),
            "slug": listing.get("slug") or slug,
            "listing_status": listing.get("status"),
            "submission_status": status or None,
            "is_winner": bool(submission.get("isWinner") or submission.get("is_winner") or status in {"WINNER", "ACCEPTED"}),
            "human_claim_required": True,
        }

    def winner_human_gate(self) -> dict[str, str]:
        agent_id, _ = self._agent_identity()
        return {
            "kind": "SUPERTEAM_WINNER_HUMAN_CLAIM",
            "agent_id": agent_id,
            "action": "Owner opens the private claim URL using the owner-local claimCode and completes the claim with a ready Talent profile.",
            "claim_code_exposed": "false",
        }
