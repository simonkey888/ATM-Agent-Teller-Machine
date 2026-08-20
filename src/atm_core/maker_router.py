from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

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


@dataclass(frozen=True)
class MakerGeneration:
    text: str
    route: MakerRoute


class ZeroCostMakerRouter:
    """Bounded provider failover inside the existing maker authority.

    This router never owns marketplace mutation authority. It accepts only model
    credentials and never reads TaskMarket/marketplace/payout/signing secrets.
    GitHub Models is deliberately represented as a fail-closed tombstone because
    GitHub retired its playground/catalog/inference API on 2026-07-30.
    """

    MAX_PROVIDER_ATTEMPTS = 3
    GITHUB_MODELS_RETIRED_ON = "2026-07-30"
    GITHUB_MODELS_REASON = "GITHUB_MODELS_RETIRED_2026_07_30"
    GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    OPENCODE_ENDPOINT = "https://opencode.ai/zen/v1/chat/completions"

    def __init__(
        self,
        *,
        gate: ZeroCostModelGate | None = None,
        opener: Callable[..., Any] | None = None,
    ):
        self.gate = gate or ZeroCostModelGate()
        self.opener = opener or urllib.request.urlopen

    @staticmethod
    def _github_models_retired_route() -> MakerRoute:
        return MakerRoute(
            provider="github-models",
            model="RETIRED",
            ready=False,
            zero_cost_proven=False,
            billed_usd=None,
            reason=ZeroCostMakerRouter.GITHUB_MODELS_REASON,
        )

    def routes(self, preferred_gemini_model: str, gemini_failovers: tuple[str, ...]) -> list[MakerRoute]:
        routes: list[MakerRoute] = []
        seen: set[str] = set()
        for model in (preferred_gemini_model, *gemini_failovers):
            if model in seen:
                continue
            seen.add(model)
            health = self.gate.google_health(model)
            routes.append(
                MakerRoute(
                    provider="gemini-api",
                    model=model,
                    ready=bool(health.usable and health.zero_cost_tier_proven),
                    zero_cost_proven=bool(health.zero_cost_tier_proven),
                    billed_usd="0" if health.zero_cost_tier_proven else None,
                    reason=health.reason,
                )
            )

        opencode = self.gate.opencode_health()
        routes.append(
            MakerRoute(
                provider="opencode-free",
                model=opencode.model,
                ready=bool(opencode.usable and opencode.zero_cost_tier_proven),
                zero_cost_proven=bool(opencode.zero_cost_tier_proven),
                billed_usd="0" if opencode.usable and opencode.zero_cost_tier_proven else None,
                reason=opencode.reason,
            )
        )
        routes.append(self._github_models_retired_route())
        return routes

    @staticmethod
    def _gemini_text(payload: Any) -> str:
        candidates = payload.get("candidates") or [] if isinstance(payload, dict) else []
        if not candidates or not isinstance(candidates[0], dict):
            raise MakerRouteError("Gemini returned no candidate")
        parts = ((candidates[0].get("content") or {}).get("parts") or [])
        text = "".join(str(part.get("text") or "") for part in parts if isinstance(part, dict)).strip()
        if not text:
            raise MakerRouteError("Gemini returned empty candidate text")
        return text

    @staticmethod
    def _openai_compatible_text(payload: Any) -> str:
        choices = payload.get("choices") or [] if isinstance(payload, dict) else []
        if not choices or not isinstance(choices[0], dict):
            raise MakerRouteError("OpenAI-compatible provider returned no choice")
        message = choices[0].get("message") or {}
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            text = "".join(str(x.get("text") or "") for x in content if isinstance(x, dict)).strip()
        else:
            text = str(content or "").strip()
        if not text:
            raise MakerRouteError("OpenAI-compatible provider returned empty content")
        return text

    def _gemini_generate(self, route: MakerRoute, prompt: str, max_output_tokens: int, structured_json: bool) -> str:
        key = os.getenv("GEMINI_API_KEY", "").strip()
        if not key:
            raise MakerRouteUnavailable("GEMINI_API_KEY absent")
        generation_config: dict[str, Any] = {"temperature": 0.2, "maxOutputTokens": max_output_tokens}
        if structured_json:
            generation_config["responseMimeType"] = "application/json"
        body = json.dumps(
            {
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": generation_config,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            self.GEMINI_ENDPOINT.format(model=route.model, key=key),
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self.opener(req, timeout=300) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise MakerRouteUnavailable(f"Gemini HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise MakerRouteUnavailable(f"Gemini unavailable:{type(exc).__name__}") from exc
        return self._gemini_text(payload)

    def _opencode_generate(self, route: MakerRoute, prompt: str, max_output_tokens: int, structured_json: bool) -> str:
        key = os.getenv("OPENCODE_API_KEY", "").strip()
        if not key:
            raise MakerRouteUnavailable("OPENCODE_API_KEY absent")
        payload: dict[str, Any] = {
            "model": route.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": max_output_tokens,
        }
        if structured_json:
            payload["response_format"] = {"type": "json_object"}
        req = urllib.request.Request(
            self.OPENCODE_ENDPOINT,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self.opener(req, timeout=300) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise MakerRouteUnavailable(f"OpenCode HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise MakerRouteUnavailable(f"OpenCode unavailable:{type(exc).__name__}") from exc
        return self._openai_compatible_text(response_payload)

    def generate(
        self,
        prompt: str,
        *,
        preferred_gemini_model: str,
        gemini_failovers: tuple[str, ...],
        max_output_tokens: int,
        structured_json: bool = False,
    ) -> MakerGeneration:
        failures: list[str] = []
        attempts = 0
        for route in self.routes(preferred_gemini_model, gemini_failovers):
            if not route.ready:
                failures.append(f"{route.provider}/{route.model}:{route.reason}")
                continue
            if attempts >= self.MAX_PROVIDER_ATTEMPTS:
                break
            attempts += 1
            try:
                if route.provider == "gemini-api":
                    text = self._gemini_generate(route, prompt, max_output_tokens, structured_json)
                elif route.provider == "opencode-free":
                    text = self._opencode_generate(route, prompt, max_output_tokens, structured_json)
                else:
                    raise MakerRouteUnavailable(f"unsupported provider:{route.provider}")
                return MakerGeneration(text=text, route=route)
            except MakerRouteError as exc:
                failures.append(f"{route.provider}/{route.model}:{type(exc).__name__}")
        raise MakerRouteUnavailable("no READY zero-cost maker route: " + " | ".join(failures))
