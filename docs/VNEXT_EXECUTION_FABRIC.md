# ATM VNext Execution Fabric R1

ATM remains the only deterministic economic authority. VNext extends the landed Worker Fabric; it does not replace the FSM, Money Board, payment ledger, S2 ingress, WorkLease authority, BOQA or Zungun.

## Execution

`ExecutionJob` is durable process identity recorded before worker side effects. The zero-secret checkout actuator pins the public worker source SHA, starts BOQA/Zungun on the ATM cloud runner, records an ACK bound to `execution_job_id + work_lease_id + scope_hash`, persists progress receipts, generates the execution artifact automatically, executes an independent checker command, and collapses a supervisor restart/retry onto the same process identity instead of launching twice.

Worker subprocesses receive a minimal environment containing process identity and `ATM_MAX_SPEND_USD=0`; ambient GitHub/OCI/payment credentials are not inherited. Workers remain unable to claim, submit, sign, spend, mark external acceptance, or write payment truth.

## Economic episodes and optionality

`EconomicEpisode` wraps the existing FSM without replacing it. It separates `CASHFLOW_BOOK` from `OPTION_BOOK`, persists at most five top options, allows at most three simultaneous read-only/probe episodes, and preserves one normal mutable claim lane per opportunity. Probing never creates a claim. Reputation, information, skill and follow-on option values never enter realized money.

Progress is falsifiable. No artifact delta, no new evidence and no uncertainty/acceptance reduction is `NO_PROGRESS`. One no-progress receipt causes replan; two require a different recovery strategy from the last measurable checkpoint; three kill the episode unless the deterministic supervisor has a separate justification. The same error three times without new evidence also kills. Workers cannot extend their own time budget.

## Adversarial sibling and immune memory

Configured reward/risk thresholds require a distinct critic execution before submit. The critic receives the frozen acceptance contract and artifact/evidence/limitations hashes, but no submit or economic authority. `NEGATIVE` and `UNKNOWN` block submit when the sibling policy is required.

The immune store is compact and append-only. Evidence-backed failure signatures prevent known bad external assumptions from silently re-entering as truth. Capability quarantine is granular; re-enable requires deterministic doctor evidence plus shadow/canary evidence. Behavioral fingerprints record only bounded execution metadata: worker/source identity, command/test/evidence hashes, duration bucket, terminal state and optional error signature.

## Skill Forge

A `SkillCapsule` requires code refs, tests and fixtures; failure-memory refs may be attached. Promotion is supervisor-owned and follows exactly:

`SOLVE -> DISTILL -> REPLAY -> ADVERSARIAL_TEST -> SHADOW_LIVE -> LOW_RISK_CANARY -> PROMOTE`

No worker can self-promote. Promotion requires measured improvement against an incumbent/baseline, not merely one solved task.

## Calibration and high-ticket radar

The calibration ledger stores observed outcomes and bounded probability bands. It reports `UNKNOWN` at low sample size and never invents precise probabilities.

The high-ticket radar is read-only/shadow. It classifies only explicit externally paid demand with independently verified payment path and deterministic acceptance. Work over the normal short horizon can enter `OPTION_BOOK`; it cannot be auto-claimed merely for being high value. Demand origination, cold outreach, sales/marketing spam and speculative consulting are not authorized.

## Constitutional locks

- `OUTGOING_SPEND_USD=0`
- `OWNER_PC_IN_PRODUCTION_GRAPH=0`
- `WINDOWS_AUTHORITY=0`
- `PAID_MODEL_FALLBACK=DISABLED`
- `WORKER_FINANCIAL_AUTHORITY=0`
- `WORKER_CLAIM_AUTHORITY=0`
- `WORKER_SUBMISSION_AUTHORITY=0`
- Across remains disabled.
- SeneX remains disabled.
- Only independently proven `PAID_WITHDRAWABLE` enters realized money.
