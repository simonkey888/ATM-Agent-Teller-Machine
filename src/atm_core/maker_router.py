from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .provider_fabric import DEFAULT_PROVIDER_STATE, DurableFreeProviderFabric, probe_gemini_route
from .zero_cost_model import ZeroCostModelGate


class MakerRouteError(RuntimeError):
    pass


class MakerRouteUnavailable(MakerRouteError):
    pass


@dataclass(frozen=True)
class MakerRoute:
    provider: str
    model: str
    ready: bool
    zero_cost_proven: bool
    billed_usd: str | None
    reason: str
    provider_id: str = "gemini-api-free"


@dataclass(frozen=True)
class MakerGeneration:
    text: str
    route: MakerRoute


class ZeroCostMakerRouter:
    """Durable zero-cost provider routing for the existing WORK/CHECK maker authority."""

    MAX_PROVIDER_ATTEMPTS = 3
    GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"

    def __init__(
        self,
        *,
        gate: ZeroCostModelGate | None = None,
        opener: Callable[..., Any] | None = None,
        provider_state_path: Path = DEFAULT_PROVIDER_STATE,
    ):
        self.gate = gate or ZeroCostModelGate()
        self.opener = opener or urllib.request.urlopen
        self.provider_state_path = Path(provider_state_path)

    def routes(self, preferred_gemini_model: str, gemini_failovers: tuple[str, ...]) -> list[MakerRoute]:
        routes: list[MakerRoute] = []
        seen: set[str] = set()
        fabric = DurableFreeProviderFabric(self.provider_state_path)
        try:
            for model in (preferred_gemini_model, *gemini_failovers):
                if model in seen:
                    continue
                seen.add(model)
                status = probe_gemini_route(fabric, self.gate, model)
                evidence = status.get("probe_evidence") or {}
                routes.append(MakerRoute(
                    provider="gemini-api",
                    provider_id="gemini-api-free",
                    model=model,
                    ready=bool(status.get("available")),
                    zero_cost_proven=bool(evidence.get("zero_cost_tier_proven")) and bool(status.get("available")),
                    billed_usd="0" if status.get("available") else None,
                    reason=str(status.get("last_error") or evidence.get("reason") or status.get("state")),
                ))
        finally:
            fabric.close()
        return routes

    @staticmethod
    def _gemini_text(payload: Any) -> str:
        candidates = payload.get("candidates") or [] if isinstance(payload, dict) else []
        if not candidates or not isinstance(candidates[0], dict):
            raise MakerRouteError("Gemini returned no candidate")
        parts = ((candidates[0].get("content") or {}).get("parts") or [])
        text = "".join(str(part.get("text") or "") for part in parts if isinstance(part, dict) and part.get("thought") is not True).strip()
        if not text:
            raise MakerRouteError("Gemini returned empty final candidate text")
        return text

    def _gemini_generate(self, route: MakerRoute, prompt: str, max_output_tokens: int, structured_json: bool) -> str:
        key = os.getenv("GEMINI_API_KEY", "").strip()
        if not key:
            raise MakerRouteUnavailable("GEMINI_API_KEY absent")
        generation_config: dict[str, Any] = {"temperature": 0 if structured_json else 0.2, "maxOutputTokens": max_output_tokens}
        if structured_json:
            generation_config["responseMimeType"] = "application/json"
        body = json.dumps({"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": generation_config}).encode("utf-8")
        req = urllib.request.Request(
            self.GEMINI_ENDPOINT.format(model=route.model, key=key), data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with self.opener(req, timeout=300) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if int(exc.code) == 429:
                raise MakerRouteUnavailable("PROVIDER_429") from exc
            raise MakerRouteUnavailable(f"Gemini HTTP {exc.code}") from exc
        except TimeoutError as exc:
            raise MakerRouteUnavailable("PROVIDER_TIMEOUT") from exc
        except urllib.error.URLError as exc:
            reason = str(getattr(exc, "reason", ""))
            if "timed out" in reason.lower():
                raise MakerRouteUnavailable("PROVIDER_TIMEOUT") from exc
            raise MakerRouteUnavailable("PROVIDER_NETWORK") from exc
        except json.JSONDecodeError as exc:
            raise MakerRouteUnavailable("PROVIDER_INVALID_RESPONSE") from exc
        return self._gemini_text(payload)

    def _request_route(self, route: MakerRoute, prompt: str, max_output_tokens: int, structured_json: bool) -> str:
        if route.provider != "gemini-api" or route.provider_id != "gemini-api-free":
            raise MakerRouteUnavailable("UNVERIFIED_OR_PAID_PROVIDER_BLOCKED")
        return self._gemini_generate(route, prompt, max_output_tokens, structured_json)

    def _circuit_failure(self, route: MakerRoute, error: MakerRouteUnavailable) -> None:
        fabric = DurableFreeProviderFabric(self.provider_state_path)
        try:
            fabric.record_failure(route.provider_id, route.model, str(error))
        finally:
            fabric.close()

    def generate(
        self,
        prompt: str,
        *,
        preferred_gemini_model: str,
        gemini_failovers: tuple[str, ...],
        max_output_tokens: int,
        structured_json: bool = False,
        semantic_validator: Callable[[str], None] | None = None,
        repair_prompt: str | None = None,
    ) -> MakerGeneration:
        failures: list[str] = []
        attempts = 0
        for route in self.routes(preferred_gemini_model, gemini_failovers):
            if not route.ready or not route.zero_cost_proven:
                failures.append(f"{route.provider}/{route.model}:{route.reason}")
                continue
            if attempts >= self.MAX_PROVIDER_ATTEMPTS:
                break
            route_prompts = [prompt, repair_prompt] if repair_prompt else [prompt]
            for route_prompt in route_prompts:
                if route_prompt is None or attempts >= self.MAX_PROVIDER_ATTEMPTS:
                    break
                attempts += 1
                try:
                    text = self._request_route(route, route_prompt, max_output_tokens, structured_json)
                    if semantic_validator is not None:
                        semantic_validator(text)
                    return MakerGeneration(text=text, route=route)
                except MakerRouteUnavailable as exc:
                    self._circuit_failure(route, exc)
                    failures.append(f"{route.provider}/{route.model}:{str(exc)[:120]}")
                    break
                except MakerRouteError as exc:
                    failures.append(f"{route.provider}/{route.model}:{type(exc).__name__}:{str(exc)[:120]}")
            # Transport/quota/rate failure is now persisted; next VERIFIED_FREE route is tried.
        raise MakerRouteUnavailable("DEGRADED_DETERMINISTIC_SEARCH:no READY zero-cost maker route: " + " | ".join(failures))
