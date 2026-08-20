# ORDER-015-R2 implementation receipt

Authority is the current R2 body of Issue #42 plus AUD comments `5356639697`, `5356645804`, and `5357566170`. The fresh remote base was `a3e59af6ccae566c873740bd2ab5c9c2cd22e905` on `pivot/universal-money-radar-r1`.

## Decision

Control plane A wins: refactor the existing single GitHub Actions supervisor and retain Cloudflare as the exact-head public read plane. Control plane B—moving authority to Durable Objects, Workflows, and Queues—is rejected for R2 because it adds migration and dual-authority risk, introduces free-plan hard limits and billing dimensions, and solves no measured controller bottleneck.

The implementation removes the actual dual canon: TaskMarket discovery, verification, shadow allocation, Cash Mode, and the first-party submit lane all call `atm_core.cash_canon.taskmarket_cash_decision`. A fresh individual object, funding, current submit action, identity, duplicate truth, work-class fixture, cost, expiry, competition, and policy must pass before maker allocation. `SHADOW_BENCHMARK` is explicit, signerless, mutationless, resource-bounded, and never counted as economic execution.

`ATM_UNIVERSAL_EFFECT_BOUNDARY_V1` records `PREPARED → PRECONDITION_REFETCH → COMMITTING → AUTHORITATIVE_VERIFY → COMMITTED` in SQLite with full synchronization. A restart after `COMMITTING` cannot retry blindly; only authoritative absence can re-arm, while a matching provider receipt recovers the effect as committed. TaskMarket remains final truth.

## Benchmarks and adoption

- Obscura: pinned Linux release `v0.2.0`, archive SHA-256 `d601f4f542319c3b9fa8dca9f5ccfc134a2ca001648da528db5f03c9e6c2599b`. A local dynamic fixture proves plain HTTP does not render JavaScript and Obscura does. Adopted only for public, credentialless, HTTPS-allowlisted, read-only DOM work; no stealth.
- MoneyPrinterTurbo: the full repository is not installed. The useful pattern is reduced to a local FFmpeg/FFprobe adapter accepting only owner-provided or CC0 assets with source hashes. The benchmark verifies H.264, audio, subtitles, duration, resolution, artifact size, and zero spend. No provider APIs, WebUI, social publishing, or paid fallback.
- OmniRoute and `awesome-free-llm-apis`: discovery/pattern only. OmniRoute was not connected after ToolCheck reported concern 38/100. Lists never establish provider terms.
- `career-ops`: legitimacy, work-authorization, current-object, and concrete human-gate patterns were extracted. Paid onboarding, cold outreach, USA-only owner-ineligible work, credential harvesting, fake review/impersonation, referral-commission work, and unpaid-test bait fail closed.
- jcode and pi: not adopted because no coding-worker bottleneck was measured. AirLLM is rejected for the same reason on memory. Substrate is pattern-only without Kubernetes. ai-memory is development continuity only. reverse-skill cannot enable offensive/security work. ego-lite, skills, and superpowers are pattern-only.

## Free model fabric

The provider registry requires official terms, official pricing, no credit card, no auto-overage, commercial paid-task permission, data-use disclosure, regional truth, credential presence, and a live model/rate probe. Google Gemini free is conditionally admissible only for public, non-confidential task input on an unbilled project; unpaid content may be used to improve Google products. OpenCode Zen remains discovery-only because model-specific commercial permission and no-overage behavior are not proven. No paid fallback exists.

## Live contract

The Cloudflare read plane exposes the sanitized canonical registries at `/api/capabilities` and `/api/providers`. Exact-head deployment fails unless the browser and video benchmarks reproduce, the two R2 capability rows are enabled, OpenCode remains discovery-only, Cash Mode is `ACTIVE` or `SEARCH`, the heartbeat and radar SHA match the deployed commit, and the existing ORDER-012 submission remains present without a duplicate submit.

All invariants remain: one ATM, one controller, one wallet, no merge, no gas, no subscription, no paid model/API, no spend, continuous discovery, continuous `IN_FLIGHT`, and settlement watch.

## AUD liveness correction

AUD `5358674042` invalidated the GitHub-schedule part of decision A without invalidating the functional R2 patch. GitHub's `schedule` event executes only the latest commit on the default branch, so the unmerged PR workflow could never provide recurrent exact-head authority. The observed default-branch runs were also best-effort at roughly 24–60 minute intervals rather than the configured five minutes, and the most recent runs failed closed because the pre-R2 code rejected the extended R2 state archive. The last healthy exact-head cycle was therefore a push event, not recurrent proof.

The corrected decision is a scheduling-only `SURGICAL_PIVOT`: the existing canonical Cloudflare Worker owns one zero-cost `*/5` Cron Trigger and dispatches the exact deployed SHA to `atm-cloud-cycle.yml`. GitHub Actions remains an ephemeral worker. The existing `atm-economic-authority` concurrency group, remote exact-head fence, CAS lease, Canon, wallet, state and mutation boundary remain the sole economic authority. The default-branch GitHub cron is disabled rather than widened, and the 900-second health SLO is unchanged.
