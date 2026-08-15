# ATM — Agent Teller Machine

A local, persistent bounty-work supervisor. The goal is not to "make money by prompting"; it is to keep a coding agent on a deterministic loop:

`DISCOVER -> VERIFY/CLAIM -> WORK -> CHECK -> SUBMIT -> MONITOR -> DISCOVER`

The target is configurable (`$200` by default). There is no guarantee of earnings. The machine only counts `PAID_USD` when a payout is independently confirmed.

## Why this stack

**Runner: Hermes Agent.** Hermes runs natively on Windows 10/11, has local terminal/browser tools, checkpoints, provider fallbacks, cron, non-interactive execution, and bounded tool-calling sessions. ATM does not depend on one infinite chat: if a Hermes run exits or a provider rate-limits, `src/atm.py` persists state and starts another run.

**$0 worker path: Qwen OAuth.** Hermes can authenticate to Qwen by browser OAuth with no API key. ATM uses `qwen3-coder-plus` for WORK/SUBMIT by default.

**$0 GLM fallback/scout: GLM-4.7-Flash.** Z.ai's official pricing currently lists `GLM-4.7-Flash` and `GLM-4.5-Flash` as free input/output API models. It still requires a Z.ai account + API key. ATM uses GLM-4.7-Flash for discovery/checking when a key is present.

**GLM-5.2 is not the permanent $0 API path.** ATM does not depend on temporary trials or paid coding-plan quotas.

## Exact links

- Hermes Agent repo: https://github.com/NousResearch/hermes-agent
- Hermes latest releases: https://github.com/NousResearch/hermes-agent/releases/latest
- Hermes Windows installer script: https://hermes-agent.nousresearch.com/install.ps1
- Hermes docs: https://hermes-agent.nousresearch.com/docs/
- Qwen: https://chat.qwen.ai/
- Z.ai API keys: https://z.ai/manage-apikey/apikey-list
- Z.ai pricing: https://docs.z.ai/guides/overview/pricing
- GitHub CLI: https://github.com/cli/cli/releases/latest
- This repo: https://github.com/simonkey888/ATM-Agent-Teller-Machine

## Windows quick start

Development branch:

```powershell
git clone -b agent/atm-v1 https://github.com/simonkey888/ATM-Agent-Teller-Machine.git
cd ATM-Agent-Teller-Machine
powershell -ExecutionPolicy Bypass -File .\scripts\install-atm.ps1
```

Then complete the one-time human onboarding:

```powershell
gh auth login
hermes model
```

In `hermes model`, choose **Qwen -> Qwen CLI OAuth -> qwen3-coder-plus**.

Optional second $0 rail: create a Z.ai API key at:

https://z.ai/manage-apikey/apikey-list

Hermes on Windows stores its secrets at `%LOCALAPPDATA%\hermes\.env`. Open it with:

```powershell
notepad "$env:LOCALAPPDATA\hermes\.env"
```

Add:

```text
GLM_API_KEY=PASTE_KEY_HERE
```

Run the doctor:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\doctor.ps1
```

Start ATM:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-atm.ps1
```

Status:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-atm.ps1 -Status
```

## One-time things the human may need to do

ATM intentionally refuses to invent an identity or financial authority. You may need to do these once for whichever bounty rails you enable:

1. GitHub login / token.
2. Create marketplace accounts.
3. Add a public payout address or Stripe/PayPal details.
4. Complete KYC/MFA/CAPTCHA if the platform genuinely requires it.
5. Accept marketplace terms.

After that, ATM is designed to handle reversible $0 actions itself: discovery, claim comments, cloning, coding, testing, PRs, review fixes and monitoring.

ATM never stores private keys and must not sign financial transactions.

## Configure

On first run, `config/atm.example.json` is copied to `config/atm.json`.

Important defaults:

- target: `$200 paid`
- minimum bounty: `$100`
- ideal bounty: `$200+`
- max estimated work: `12h`
- auto-claim: enabled for reversible $0 claims
- auto-PR: enabled
- provider fallback: Qwen OAuth <-> free Z.ai GLM-4.7-Flash
- persistent restart loop: enabled

`config/atm.json` is ignored by git so local changes remain local.

## State

Runtime state lives under `.atm/`:

- `.atm/state.json`
- `.atm/logs/`

Each Hermes call is a bounded worker invocation. The Python supervisor owns the long-running loop, so a model context reset does not erase the mission.

## Safety boundary

Autonomous:
- public research;
- GitHub/API reads;
- reversible claim comments;
- local git operations;
- code/test/build;
- push/PR/update PR;
- responding to review feedback.

Human gate:
- KYC;
- CAPTCHA/MFA;
- account identity creation when unavoidable;
- wallet creation/funding;
- financial signatures;
- purchases/subscriptions;
- private-key handling;
- irreversible financial/legal commitments.

See `AGENTS.md` for the operating contract.
