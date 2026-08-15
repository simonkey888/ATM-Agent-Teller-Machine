# Windows setup — exact path

## 0. Requirements

- Windows 10/11 x64
- Internet
- GitHub account
- one-time browser login for Qwen
- optional Z.ai account/API key

Hermes' Windows installer provisions its own Python/uv, Node and portable Git when needed.

Hermes Windows data/config lives under `%LOCALAPPDATA%\hermes` (for example `C:\Users\<you>\AppData\Local\hermes`).

## 1. Install ATM repo

If Git is already available:

```powershell
git clone https://github.com/simonkey888/ATM-Agent-Teller-Machine.git
cd ATM-Agent-Teller-Machine
```

During ATM v1 development use branch `agent/atm-v1`:

```powershell
git switch agent/atm-v1
```

## 2. Install Hermes + GitHub CLI

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-atm.ps1
```

Hermes direct installer:

https://hermes-agent.nousresearch.com/install.ps1

GitHub CLI releases:

https://github.com/cli/cli/releases/latest

## 3. Authenticate GitHub once

```powershell
gh auth login
gh auth status
```

Choose GitHub.com and HTTPS. Give `gh` enough repository permission to fork/push/open PRs for bounty work.

## 4. Authenticate the $0 worker once

```powershell
hermes model
```

Select:

- provider family: `Qwen`
- auth path: `Qwen CLI OAuth`
- model: `qwen3-coder-plus`

The browser opens. Log in and approve OAuth.

## 5. Add the free GLM API fallback

Create key:

https://z.ai/manage-apikey/apikey-list

Then open Hermes' real Windows env file:

```powershell
notepad "$env:LOCALAPPDATA\hermes\.env"
```

Add:

```text
GLM_API_KEY=YOUR_KEY
```

Do not put the key in this repo.

Z.ai currently lists GLM-4.7-Flash as free input/output. Check current pricing before running:

https://docs.z.ai/guides/overview/pricing

## 6. Marketplace payout onboarding

Do this only on the specific rails ATM will use. ATM never needs a private key.

Typical one-time work:
- sign in;
- connect GitHub;
- add public wallet address / Stripe / PayPal;
- KYC if required.

Never paste wallet seed phrases/private keys into ATM, Hermes, prompts or `.env`.

## 7. Doctor

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\doctor.ps1
```

## 8. Start

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-atm.ps1
```

ATM persists state, so closing/restarting the supervisor does not reset the mission.

## 9. Emergency stop

Ctrl+C.

There is intentionally no ATM service auto-install in v1. Run interactively until the first real bounty completes; only then should it be promoted to a Windows Scheduled Task/service.
