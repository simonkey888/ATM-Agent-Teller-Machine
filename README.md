# 🏧 ATM — Agent Teller Machine

**FLAG: HURRY UP, TIME IS MONEY.**

ATM is an always-on supervisor that converts short-duration autonomous digital work into **externally verified, withdrawable money**.

Month 1 target: **USD 500 realized/withdrawable**. The target increases every month:

`TARGET_M(n+1) = max(TARGET_M(n) × 1.10, REALIZED_M(n) × 1.05)`

Reaching the target never stops the supervisor.

## Money loop

`DISCOVER → VERIFY → CLAIM → WORK → CHECK → SUBMIT → MONITOR → PAYMENT_VERIFY → DISCOVER`

Ranking optimizes money velocity, not headline bounty:

`net payout × task alive × payer active × claim now × finish fast × accept × pay ÷ minutes-to-withdrawable`

Short, fresh, funded, claimable work with an active payer is preferred. Stale, unfunded, saturated, human-only, paid-bid/stake/gas, or subjective post-campaign opportunities are rejected/degraded.

Only deterministic payment proofs from authoritative platform/chain data enter `.atm/validated-payment-proofs.jsonl`. Claims, escrow, PRs, screenshots, expected payouts and model narration are not realized revenue.

## GitHub command bus

Issue **#4 — ATM CONTROL BUS** is the canonical remote control plane. The controller accepts only strict owner-authored `ATM_CMD_V1` comments from `simonkey888` on this repository/issue.

Supported verbs: `STATUS`, `DOCTOR`, `SYNC`, `START`, `STOP`, `PAUSE`, `RESUME`, `RUN_ONCE`, `EXECUTE_ORDER` (verify-only), `TAIL_LOGS`, `RELOAD_CONFIG`.

There is no arbitrary shell command surface. Command/comment IDs and body hashes are persisted locally before mutation; replay cannot execute a command twice. Results use `ATM_RESULT_V1`, so controller output cannot recursively become a command.

GitHub polling uses authenticated conditional requests with ETag plus exponential failure/rate-limit backoff. ChatGPT is not required after bootstrap.

## Windows bootstrap

Canonical repository path: `C:\Users\Simon\ATM\ATM-Agent-Teller-Machine-agent-atm-v1`

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap-atm-control.ps1
```

The bootstrap fast-forwards only, refuses tracked local changes, verifies `gh` authentication, installs the `ATM-GitHub-Control` Scheduled Task, starts exactly one controller and verifies its singleton lock. Runtime/payment state is preserved; reset is not part of the canonical runner.

Human-only gates are persisted per opportunity in `.atm/human-gates.jsonl` and routed around; they do not terminate independent discovery.

## Cloudflare

Public status: `https://atm.simondalmasso44.workers.dev/` — health: `https://atm.simondalmasso44.workers.dev/health`.

`.github/workflows/deploy-cloudflare.yml` tests the exact branch SHA, deploys Worker `atm`, injects only the public build SHA, then fails unless `/health` returns that exact SHA. Deployment credentials live only in GitHub Actions secrets `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`.

WorkProtocol and Taskmarket are revalidated from authoritative APIs at runtime. One rail/provider/task failure is a routing event, not a reason to stop independent work. Private keys, seed phrases and transaction signing remain outside ATM/model context.
