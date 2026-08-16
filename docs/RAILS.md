# Cash rails implemented

## WorkProtocol

Official API semantics verified against current docs: public job discovery; authenticated claim/delivery; job details include claims/payments; job creation locks escrow; payment release follows verification; 429 carries Retry-After. ATM requires explicit per-job escrow evidence before auto-work and uses platform settlement plus Base receipt for payment truth.

## Taskmarket

Official API exposes public task list/detail and canonical settlement `awards[]`. ATM excludes stake-required work and uses `workerPayment` (net), `settlementTxHash`, `settledAt`, recipient address, and Base receipt verification. Write paths remain signature-gated because ATM never stores private keys.

## Algora

Official SDK/API is useful for bounty discovery, but board state alone is not payout authority. Adapter remains WATCH_ONLY until an authoritative payout endpoint is configured.

## Opire

GitHub bot lifecycle is useful for discovery/claim reconciliation. Payment is handled through platform payout onboarding; until a deterministic payout source is integrated, ATM keeps this rail WATCH_ONLY rather than infer payment from `/claim` or merge state.
