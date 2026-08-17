# Windows setup

Requirements: Windows 10/11, Git, GitHub account, Hermes Agent, Google AI Studio API key.

## One-time setup

```powershell
gh auth login
hermes setup
```

Configure Hermes with Google AI Studio / Gemini and store `GOOGLE_API_KEY` in Hermes' local env. Never commit it.

Then:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\doctor.ps1
```

The doctor performs only read-only/provider/tool health checks.

## Payout onboarding

For WorkProtocol, register an agent once and set `WORKPROTOCOL_API_KEY` and `WORKPROTOCOL_AGENT_ID` locally. Configure only the **public** payout recipient in `config/atm.json` under `payment_recipient_public_identifier`.

Never put a wallet private key/seed phrase in ATM, Hermes, config, `.env`, prompts or GitHub.

Taskmarket requires EIP-191 signatures for write actions; ATM intentionally gates those to an external/human signer and does not accept private-key storage.

## Run

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-atm.ps1
```

A PID + process-start-time lock prevents a second live instance. If the previous process died, a stale lock is quarantined automatically.

Status only:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-atm.ps1 -Status
```

Emergency stop: `Ctrl+C`. State persists. Payment ledger is append-only and is not erased by `-Reset`.
