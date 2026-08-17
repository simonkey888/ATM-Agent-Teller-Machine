# Zungun Worker

`zungun` is ATM's narrow network-reliability and offline-delivery specialist. It extends the existing Worker Fabric; it is not a second supervisor, generic coding agent, model authority, financial executor, wallet authority, or replacement for BOQA.

## Authority boundary

ATM remains the sole deterministic authority for Money Board admission, S2 ingress, claim decisions, WorkLease creation/renewal, lifecycle transitions, checker acceptance, delivery, payment verification and the money ledger.

Zungun receives an ATM-frozen `WorkerJobSpec` and ATM-created `WorkLease`. The scope hash binds the target repository, exact target base SHA, allowed writable paths, acceptance criteria, structured requirements, deterministic checks, deadline and zero-spend ceiling. Zungun cannot mint or renew a WorkLease and cannot declare external acceptance, payment or withdrawability.

All Zungun work must satisfy `OUTGOING_SPEND_USD=0`. No paid model/provider, paid runner, card, subscription, deposit, stake, gas or owner-PC production dependency is permitted. Financial, claim, submission and model authority are false in the manifest and validated by Worker Fabric.

## Canonical capabilities

- `zungun.network_resilience`
- `zungun.offline_sync`
- `zungun.idempotency_retry`
- `zungun.ambiguous_timeout`
- `zungun.reconciliation`
- `zungun.android_background_work`
- `zungun.resumable_transfer`
- `zungun.blackout_testing`
- `zungun.reliability_audit`
- `zungun.evidence_generation`

The manifest intentionally does not advertise browser/Playwright, web3, CUDA, trading, wallet, financial execution, deployment, generic security audit, or generic code-generation capabilities.

## Selection

Selection is deterministic and based on normalized structured requirement fields, not keyword scanning of arbitrary task prose. Reliability requirement labels map to the specialized capability vocabulary. A target selected for Zungun must also provide an explicit HTTPS GitHub target repository, exact 40-character base SHA, and bounded allowed paths. Missing target binding fails closed.

Examples:

- Android offline mutation + process death + ambiguous timeout + reconciliation -> Zungun.
- WorkManager/background sync or durable outbox/retry correctness -> Zungun.
- Playwright checkout regression -> BOQA.
- Generic CSS/document work -> not Zungun.
- Financial execution/trading/wallet signing -> Zungun ineligible.

## Link Doctor preflight

The worker-side deterministic preflight is bound to the current Zungun Link Doctor rule family `ZL001` through `ZL016`. Findings retain rule ID, severity, path/location, evidence, explanation, limitation, assurance tier and status.

Semantic locks are mandatory:

- `UNKNOWN != PASS`
- `AMBIGUOUS != SUCCESS`
- transport accepted != durable receiver effect
- HTTP 2xx != business effect
- retryable != idempotent
- network available != endpoint reachable
- emulator/unit proof != OEM production proof

A worker result that attempts to collapse uncertainty to success is rejected.

## BOQA composition

Zungun diagnoses and implements network/offline correctness. BOQA remains the preferred browser/QA/regression specialist. Where an external work protocol permits composition, one ATM economic claim and one exclusive WorkLease lineage can feed Zungun implementation followed by BOQA verification and then the independent ATM checker. Composition must not create duplicate claims, duplicate deliveries or duplicate economic events.

## Isolation and trust

Worker work is expected to use an isolated target checkout/worktree with no shared mutable owner workspace. Writable paths are bounded by the frozen JobSpec. Absolute paths, `..`, `.git` paths and symlink escapes are rejected. Worker result ingestion is allowlisted to the selected worker repository/target and rejects secret-like fields or attempts to write `PAID`, `WITHDRAWABLE`, `EXTERNAL_ACCEPTED`, FSM state or money-ledger truth.

Repository text, OpenAPI documents and target scripts are untrusted inputs. They do not execute merely because they are documentation or evidence. Ambient financial credentials are not provided to the worker.

## Architecture

```text
ATM DETERMINISTIC AUTHORITY
        |
        +-- MONEY BOARD / LEDGER
        +-- deterministic FSM
        +-- S2 authority ingress
        +-- WorkLease authority
        |
        v
WORKER FABRIC
        |
        +-- BOQA
        +-- ZUNGUN
        +-- Across [disabled]
        +-- SeneX [disabled]
              |
              v
ZUNGUN
        +-- Link Doctor
        +-- protocol/link core
        +-- reconciliation
        +-- receiver
        +-- Android runtime
        +-- resilience lab
        +-- evidence
              |
              v
CHECKER
              |
              v
DELIVERY AUTHORITY
```

## Qualification

`tests/test_zungun_worker.py` covers deterministic selection, negative controls, one-lease semantics, immutable input binding, Link Doctor uncertainty, path/symlink boundaries, worker authority rejection, and the non-economic `fixture-zungun-offline-001` pipeline. `atm_core.zungun_qualification` covers ACK-loss ambiguity, process restart, reconnect convergence, concurrent retry dedupe, resumable transfer progress, blackout recovery and deterministic static-audit semantics.

Read-only real-world shadow mappings are recorded under `evidence/zungun-worker/shadow-qualification.json`; they never comment, claim, submit or mutate upstream work.
