# ATM readiness gates

A green model response is not economic readiness.

## Pre-economic gates G01-G20

- G01 remote repository reconstructed at exact HEAD
- G02 config validated
- G03 primary provider live
- G04 deterministic external discovery live
- G05 GitHub auth/read live
- G06 state persistence verified
- G07 singleton/second-instance fail-closed
- G08 real opportunity discovery
- G09 eligibility/funding/freshness validation
- G10 claim path contract verified
- G11 isolated WORK path
- G12 independent CHECK path
- G13 deterministic SUBMIT preflight/path
- G14 deterministic MONITOR path
- G15 authoritative PAYMENT_PROOF contract
- G16 model cannot write realized revenue
- G17 payment adapters validate recipient/finality/dedupe
- G18 secrets/redaction/untrusted-data boundary
- G19 crash/corruption/stale-lock recovery
- G20 one real external route reaches a verified external state change without bypassing safety gates

## Economic gates G21-G25

These cannot be faked by tests:

- G21 real submission
- G22 real acceptance
- G23 real payout release
- G24 real withdrawable/settled proof
- G25 ledger derives realized value correctly

Economic completion is only:

`SUM(unique VALIDATED_PAYMENT_PROOFS.normalized_usd) >= target_paid_usd`
