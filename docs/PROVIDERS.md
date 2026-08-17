# Provider policy — 2026-08-15

ATM separates the local supervisor from inference. Provider output is disposable; economic truth is deterministic.

## Primary

Hermes provider `gemini`, model `gemini-3.5-flash`, credential `GOOGLE_API_KEY` in the Hermes local env. This exact route was manually proven with `ATM_MODEL_OK` before the hardening branch update.

ATM does not silently change provider because a different model benchmarks better. Fallbacks are enabled only after inference, strict JSON, terminal/tool, web, and quota behavior are validated.

## Known historical failures

- Qwen OAuth: not a current baseline.
- Z.AI/GLM: prior local credential/authentication path failed; disabled.
- OpenAI Codex OAuth: authentication succeeded, but the observed call returned HTTP 429 usage-limit exhaustion; disabled as an automatic fallback until quota is independently green.

## Runtime failure behavior

Provider failures are recorded with provider/model/session id when observable, class, HTTP status, Retry-After and timestamp. HTTP 429 opens a circuit breaker (default 15 minutes when no Retry-After is supplied) so ATM does not hammer an exhausted provider every cycle.
