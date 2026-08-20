# Authoritative payment proof

Minimum ledger fields:

- source
- platform
- payout_id_or_txid
- event_index_or_unique_id
- amount
- currency
- recipient_public_identifier
- status
- timestamp
- authoritative_url_or_api
- evidence_hash
- normalized_usd

Countable states: `RELEASED`, `WITHDRAWABLE`, `PAID`.

Non-countable: `FUNDED_ESCROW`, `ACCEPTED`, pending/expected/claimed/merged states.

Dedupe key: lowercased `(platform, payout_id_or_txid, event_index_or_unique_id)`.

USD/USDC normalize 1:1. Any other currency requires an explicit FX source, timestamp, and rate; the proof validator recomputes `amount * fx_rate`.

For Base USDC, adapters fetch `eth_getTransactionReceipt`, require `status=0x1`, parse USDC `Transfer` logs, and require a transfer to the configured recipient for at least the claimed net amount.
