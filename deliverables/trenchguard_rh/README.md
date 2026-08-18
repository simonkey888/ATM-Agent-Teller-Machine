# TrenchGuard RH

A zero-key, read-only evidence CLI for Robinhood Chain token candidates.

It is intentionally **not** a trading bot or a “next winner” score. It turns a contract address into a deterministic provenance/risk packet that can be archived at decision time and used in forward paper testing.

## What it checks

- Robinhood Chain mainnet (`chainId=4663`)
- token metadata
- contract creator, creation transaction, and deployment age
- Blockscout contract verification state
- transfer and holder counters
- top-1 / top-10 concentration over the returned holder sample
- exact-match or ticker/name collision against Robinhood's official Stock Token asset registry
- explicit material fetch failures
- cautious evidence flags with `not_a_return_prediction=true`

## Requirements

Python 3.11+. No third-party packages, wallet, API key, or RPC signing authority.

## Run

```bash
cd deliverables/trenchguard_rh
python trenchguard.py scan 0xYOUR_TOKEN_CONTRACT
```

Batch mode accepts a JSON list of addresses or `{ "address": "0x..." }` objects:

```bash
python trenchguard.py batch candidates.json
```

Output is stable JSON suitable for hashing and time-frozen evaluation.

## Example interpretation

A packet can contain:

```json
{
  "canonical_stock_registry": {
    "canonical_robinhood_stock_token": false,
    "ticker_or_name_collision_with_robinhood_registry": true
  },
  "contract": {
    "verified": false
  },
  "risk_summary": {
    "tier": "HIGH_CAUTION",
    "flags": [
      "UNVERIFIED_CONTRACT",
      "CANONICAL_TICKER_OR_NAME_COLLISION"
    ],
    "not_a_return_prediction": true
  }
}
```

This means “inspect further / identity evidence is weak,” not “sell” or “the token is fraudulent.”

## Early-token definition

The MVP labels contracts at most 72 hours old as the forward-test “early” cohort. This is an operational sampling rule, not a proven alpha signal. The research report requires sensitivity tests at multiple age windows.

## Data-quality semantics

Material HTTP failures are preserved under:

```json
"data_quality": {
  "complete": false,
  "fail_closed": true,
  "material_fetch_failures": { ... }
}
```

Missing evidence is never silently converted into a clean result.

## Tests

From the repository root:

```bash
python -m pytest -q tests/test_trenchguard_rh.py
python scripts/order009-r2-artifact-check.py
```

The independent checker falsifies malformed addresses, deterministic output, identity collisions, unverified contracts, concentration, low activity, missing evidence, research requirements, and read-only constraints.

## Safety boundary

The executable performs only HTTP GET operations. It contains no private-key interface, wallet, approval, transfer, swap, DEX router, transaction broadcast, sniping, leverage, copy-trading, or token creation.

## Research

See [`RESEARCH.md`](./RESEARCH.md) for the market map, verified evidence, assumptions, user workflow, opportunity selection, and bias-controlled validation protocol.

Key primary sources:

- Robinhood Chain: https://docs.robinhood.com/chain/
- Robinhood Stock Token API: https://docs.robinhood.com/chain/stock-token-apis/
- Robinhood Chain Blockscout: https://robinhoodchain.blockscout.com/
- Blockscout API reference: https://docs.blockscout.com/api-reference/
