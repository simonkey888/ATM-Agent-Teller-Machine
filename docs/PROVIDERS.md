# Provider decision — 2026-08-15

ATM needs two separate things:

1. a **local harness that keeps restarting and persisting state**;
2. an **inference provider**.

No hosted model is literally infinite. Rate limits, service errors and context limits exist. ATM solves that at the harness layer: every model invocation is disposable and the supervisor resumes from `.atm/state.json`.

## Recommended $0 baseline

### 1. Hermes Agent — runner

Install on Windows:

```powershell
iex (irm https://hermes-agent.nousresearch.com/install.ps1)
```

Official source:

https://github.com/NousResearch/hermes-agent

Why ATM uses it:

- native Windows 10/11 support;
- one-shot `hermes chat -q`;
- provider fallback;
- local terminal and local Chromium integration;
- persistent auth/checkpoint facilities;
- configurable max turns (current docs expose up to 500 per run);
- Qwen OAuth and Z.ai providers.

Hermes itself is open source. Nous Portal is a paid optional service and is **not required** by ATM.

### 2. Qwen OAuth — primary coding worker

Setup:

```powershell
hermes model
```

Choose `Qwen OAuth (Portal)` and `qwen3-coder-plus`.

Qwen OAuth uses a browser login and stores a refresh token under `~/.hermes/auth.json`. No API key is required.

Direct login:

https://chat.qwen.ai/

ATM treats this as a $0/no-key consumer-provider path, but does **not** claim that Alibaba guarantees unlimited throughput. If it rate-limits, ATM falls back/retries rather than dying.

### 3. Z.ai GLM-4.7-Flash — strict $0 API fallback

Official pricing:

https://docs.z.ai/guides/overview/pricing

As of 2026-08-15, Z.ai lists:

- `GLM-4.7-Flash`: Free input / Free output
- `GLM-4.5-Flash`: Free input / Free output

Create/manage API key:

https://z.ai/manage-apikey/apikey-list

Rate limits:

https://z.ai/manage-apikey/rate-limits

Hermes provider: `zai`.

ATM uses `GLM-4.7-Flash` for SCOUT/CHECK/MONITOR first when the key exists, preserving Qwen for coding.

### GLM-5.2

GLM-5.2 is the stronger model, but it is not the permanent $0 API rail. Z.ai's current Coding Plan starts paid, and the general API is metered.

ZCode gives new users a five-day trial (3M GLM-5.2 tokens/day during the trial). ATM deliberately does not depend on a temporary promotion.

ZCode:

https://zcode.z.ai/en

### Optional: OpenAI Codex OAuth

Hermes also supports `openai-codex` via ChatGPT OAuth, no API key. This is useful only if the user already has an eligible ChatGPT plan and wants to consume its included usage. It is not part of the strict `$0-from-scratch` baseline.

## Why not a local frontier model?

The local process should be the **agent**, not necessarily the LLM weights. A normal 8 GB Windows machine can run Hermes, git, tests and browser automation, but it cannot run GLM-5.2-class weights locally at useful speed.

ATM therefore keeps orchestration local and inference replaceable.
