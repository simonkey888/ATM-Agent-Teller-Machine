# ATM Worker Fabric R1

ATM remains the sole deterministic economic authority. The Worker Fabric is a replaceable execution layer, not a second supervisor.

## Authority boundary

ATM alone admits opportunities, computes money velocity, owns CLAIM/WORK/SUBMIT leases, freezes scope, accepts lifecycle transitions, configures payout identity, verifies payment and writes `REALIZED_WITHDRAWABLE_USD`.

Workers receive a frozen `WorkerJobSpec` plus an ATM-created `WorkLease`. They may produce code, tests, browser traces, replay evidence or other artifacts. They may not mint/renew ATM economic leases, alter Money Board eligibility, hold private keys, sign transactions, write payment truth, spend money or widen their own network/tool scope.

## Service seam

The R1 seam follows the useful DeepSeek-Harness/Cordis pattern without taking it as a dependency:

`typed service definition -> replaceable provider -> consumer`

Worker manifests are providers. `WorkerRegistry` and `CapabilityResolver` are deterministic consumers. Per-job capability matching prevents installing every tool in every worker.

## Initial workers

- `boqa`: enabled, zero-spend, one concurrent WORK slot; QA/repro/CI/evidence/small-code capabilities.
- `across-edge`: registered but disabled by default; protocol verification is read-only and no signing capability is present.
- `senex-prophet`: registered but disabled by default; data/replay/research only, no trading or payment capability.

`cuda`, `web3_signer` and `payment_writer` are reserved. Signing and payment-writing are hard disabled under OWNER_MASTER_ORDER_ATM_WORKER_FABRIC_R1.

## Model gateway

Models are capabilities, never authorities. `ModelGateway` admits only routes whose current metadata was verified inside the configured freshness window and whose input and output prices are exactly zero. There is no paid fallback.

The initial candidate is OpenCode Zen `deepseek-v4-flash-free`. Runtime integration must re-fetch current official model/free metadata at the integration boundary; a stale name ending in `-free` is not sufficient evidence by itself.

## Evidence

`WorkerJobSpec.scope_hash` and `WorkerResult.envelope_hash` use canonical JSON plus SHA-256. Work leases are SQLite/WAL durable, single-owner per canonical opportunity, survive process restart, and can only be heartbeated through the supervisor-owned lease store.
