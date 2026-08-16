# ATM — Agent Teller Machine

ATM is a deterministic supervisor for pursuing externally funded software work. Its mission is not to report activity; it is to reach a configurable amount of **externally verified, withdrawable value**.

`DISCOVER -> VERIFY -> CLAIM -> WORK -> CHECK -> SUBMIT -> MONITOR -> PAYMENT_VERIFY`

Default target: `REALIZED_WITHDRAWABLE_USD >= 200`.

## Economic truth

The LLM cannot write realized revenue. `paid_usd`, `accepted_usd`, `claimed_usd` and equivalent model fields are rejected.

Validated money lives only in:

`/.atm/validated-payment-proofs.jsonl`

Each proof is append-only and includes platform, payout/tx id, unique event id, amount/currency, recipient public identifier, terminal status, timestamp, authoritative source, evidence hash and normalized USD. Base/USDC adapters additionally verify the settlement transaction receipt and USDC transfer to the expected recipient.

Escrow funding, pending balances, merged PRs, screenshots, HTML and model narration never count toward the target.

## Current rails

### WorkProtocol — primary deterministic rail

Public discovery uses `GET /api/jobs`. VERIFY requires the job to remain open and exposes explicit per-job escrow/funding evidence before ATM will claim. Claim/delivery use the official authenticated API after one-time agent registration.

Payment verification distinguishes `escrowTxHash` from `settlementTxHash`, then verifies the settlement on Base before crediting the ledger.

Required one-time local secrets/ids when enabling writes:

- `WORKPROTOCOL_API_KEY`
- `WORKPROTOCOL_AGENT_ID`
- public payout recipient in `config/atm.json`

### Taskmarket — deterministic discovery/payment, signature-gated writes

Discovery excludes `stakeRequired=true`. Payment uses canonical `awards[]`, net `workerPayment`, `settlementTxHash`, and Base receipt verification.

Taskmarket claim/submission requires EIP-191 wallet signatures. ATM never stores private keys, so write actions fail closed into a human/external-signer gate.

### Algora / Opire / MoltJobs

Present as WATCH_ONLY until an authoritative lifecycle/payment contract is configured and verified. A board listing is not payout truth.

## Provider

Current committed primary is the already-tested Hermes Gemini route:

- provider: `gemini`
- model: `gemini-3.5-flash`
- credential: `GOOGLE_API_KEY` in Hermes' local env

429/quota errors are classified and open a circuit breaker rather than being hammered every cycle.

## Windows

From the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\doctor.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\run-atm.ps1
```

Status does not start a second runtime:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-atm.ps1 -Status
```

`run-atm.ps1` is protected by a PID + process-start-time singleton lock. A second live instance fails closed. Stale locks are quarantined.

`-Reset` resets runtime state only. It deliberately does **not** erase the payment ledger.

## Doctor

The doctor is read-only and checks:

- Hermes/version;
- GitHub auth and repo read;
- primary credential presence without printing it;
- writable state filesystem;
- singleton status;
- WorkProtocol/Taskmarket read-only health;
- one-shot provider inference;
- strict JSON output;
- Hermes terminal tool;
- Hermes web tool.

It never claims, submits, signs, pays, or changes payout settings.

## Tests

```powershell
$env:PYTHONPATH="src"
python -m unittest discover -s tests -v
```

Coverage includes model-money rejection, duplicate settlements, escrow/pending rejection, recipient mismatch, restart replay, FX provenance, WorkProtocol settlement-vs-escrow separation, Taskmarket net award settlement, singleton recovery, provider 429 classification, corrupt-state quarantine, v1 monetary-state discard, and prompt-injection/context-exfiltration regressions.

## Security

All bounty-controlled repositories and instructions are untrusted. ATM rejects work that asks for hidden prompts, conversation context, secrets, credential files, environment dumps, private keys or other protected context. The payment validator runs outside bounty workspaces.

See `AGENTS.md` and `docs/GATES.md`.
