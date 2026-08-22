# TrenchGuard RH — research report

**Task:** Create the Most Useful Tool for Crypto Trenching  
**MVP network:** Robinhood Chain (EVM chain ID 4663)  
**Research freeze:** 2026-08-18 UTC  
**Product boundary:** read-only decision intelligence. No trading, custody, transaction signing, sniping, leverage, copy-trading, or token creation.

## Executive conclusion

The research does **not** support a defensible claim that a simple “new-token score” predicts positive risk-adjusted returns on Robinhood Chain. It does support a narrower and more defensible opportunity: reduce avoidable losses and false positives by proving token identity, contract provenance, concentration, deployment age, and activity *before* a retail trader treats a newly discovered contract as investable.

That distinction matters because Robinhood Chain combines a permissionless EVM environment with an official tokenized-stock ecosystem. A ticker or familiar name is not enough to establish authenticity. Robinhood publishes canonical Stock Token deployments through its own asset API, while the chain’s Blockscout explorer exposes contract and holder evidence. Independent chain research has also documented a large volume of ticker impersonation and extremely short-lived token launches. The highest-confidence MVP is therefore a **proof packet**, not another alert feed.

`TrenchGuard RH` accepts a candidate contract address found anywhere—Telegram, a launchpad, an explorer, a social feed, or another scanner—and returns deterministic JSON covering:

1. deployment transaction and age;
2. contract verification state;
3. transfer/holder activity;
4. top-holder concentration over the explorer sample;
5. collision or match against Robinhood’s canonical Stock Token registry;
6. explicit data-quality failures;
7. a cautious evidence tier that is **not** a return prediction.

The product’s job is to compress fragmented due diligence into one reproducible pre-trade evidence object. The expected value is primarily in avoiding catastrophic identity/provenance errors and obviously fragile concentration/activity profiles. Any claim that it improves returns must be earned through forward paper testing.

## 1. Verified evidence

### 1.1 Robinhood Chain characteristics

Robinhood’s first-party Chain documentation identifies Robinhood Chain as an EVM-compatible Arbitrum L2 and exposes a public RPC and Blockscout explorer. The mainnet chain ID is 4663. This makes standard Ethereum contract addresses, ERC-20 interfaces, transaction hashes, and explorer evidence usable without proprietary wallet integration.

Primary sources:

- https://docs.robinhood.com/chain/
- https://docs.robinhood.com/chain/connecting/
- https://docs.robinhood.com/chain/contracts/

Robinhood’s Stock Token documentation describes Stock Tokens as ERC-20 assets representing tokenized debt securities. Its REST asset API exposes chain deployments for supported assets. This is unusually useful for trenching risk: a contract can be compared to a first-party canonical deployment instead of trusting a ticker, logo, or social claim.

Primary sources:

- https://docs.robinhood.com/chain/stock-tokens/
- https://docs.robinhood.com/chain/stock-token-apis/
- https://api.robinhood.com/rhj/assets

The contracts documentation also warns, in effect, that canonical addresses matter: tokens with the same display identity at another address are not made canonical merely by reusing a name or ticker.

### 1.2 Explorer evidence is available without signing

Robinhood Chain’s Blockscout deployment exposes read APIs that can be used to inspect token metadata, holders, address creation details, transactions, transfer counters, and verified-contract metadata. The MVP uses only GET requests.

Relevant Blockscout API references:

- https://docs.blockscout.com/api-reference/get-token-info
- https://docs.blockscout.com/api-reference/token/get-token-holders
- https://docs.blockscout.com/api-reference/get-address-info
- https://docs.blockscout.com/api-reference/get-tokens-list
- https://robinhoodchain.blockscout.com/api-docs

This is important operationally: the evidence packet can be produced without a wallet, API key, approval transaction, swap router, or signing authority.

### 1.3 Concentration is a legitimate risk context, not an alpha claim

Robinhood’s wallet support material explicitly treats top-holder concentration as useful risk context because a small set of wallets can have outsized influence over price and liquidity. It also cautions against treating raw holder count as sufficient evidence.

Primary source:

- https://robinhood.com/us/en/support/articles/robinhood-wallet-faqs/

TrenchGuard therefore reports sampled top-1 and top-10 concentration. It labels the denominator honestly: the returned holder sample may not represent the entire supply. Concentration is used as a caution flag, not as proof of fraud and not as a forecast of future return.

### 1.4 Identity deception and short token lifetimes are material

Robinhood’s own scam guidance describes pump-and-dump, rug-pull, counterfeit, and impersonation threats. This supports a product priority on identity/provenance and deployer evidence.

Primary source:

- https://robinhood.com/us/en/support/articles/how-to-identify-and-report-scams/

A detailed independent Bitquery investigation of Robinhood Chain trading in July 2026 reported a particularly strong version of this problem: hundreds of contracts used the GME ticker, most were not the canonical token, and the study observed heavy trading in impostor assets. The same research reported that a large majority of newly launched memecoins did not trade on a second day and that many open positions were underwater. Those numbers are observational and depend on Bitquery’s methodology and retention window; they are **not** treated here as a causal alpha model. They are strong evidence that “new” is an extremely noisy discovery feature and that token identity deserves an explicit gate.

Independent source and methodology:

- https://bitquery.io/investigations/robinhood-chain-tokenized-stocks

Older cross-chain academic work also documents large populations of one-day tokens, spam tokens, rug pulls, and automated launch behavior on Ethereum/BSC. That evidence is useful as background but is not transferred mechanically to Robinhood Chain.

Research reference:

- https://arxiv.org/abs/2206.08202

## 2. Market map

The current trenching workflow is fragmented across several product categories.

### Discovery feeds and DEX terminals

These tools optimize for speed and breadth: recent pairs, trending assets, volume, liquidity, price changes, and activity. Their strength is discovery. Their weakness for this task is that a fast feed does not itself prove that a contract is authentic, durable, or sufficiently distributed.

Robinhood Chain also has a growing ecosystem of launchpads and automated trading interfaces. Public Maestro Bot updates have advertised Robinhood mainnet support and integrations across multiple Robinhood-chain launch venues. That is evidence that discovery/execution is already competitive; building another generic “new pair” list would be weak differentiation.

Public product evidence:

- https://t.me/s/MaestroSniperUpdates

### Explorers

Blockscout is authoritative for raw chain observations such as contract creation, holder balances, transaction history, and source verification. It is powerful but not arranged around the retail user’s decision question: “Is this exact contract the thing I think it is, and what obvious fragility is visible right now?”

### Social intelligence

Telegram, X, Discord, and creator feeds are often where retail users first encounter a token. They are fast but adversarial: contract addresses can be swapped, labels can be copied, and popularity can be manufactured. Social momentum without contract identity is not sufficient evidence.

### Risk scanners

Generic token-risk products can identify permissions, honeypot patterns, taxes, and other chain-specific hazards when supported. The gap on Robinhood Chain is not merely a missing risk score. The more defensible gap is a chain-native provenance packet combining Robinhood’s own canonical registry with explorer evidence, with explicit uncertainty instead of an opaque 0–100 score.

## 3. User workflow and pain points

A realistic retail workflow today is:

1. discover a token from a launchpad, bot, social post, or trending list;
2. copy the contract address;
3. open an explorer;
4. inspect creator/contract/holders;
5. search the ticker/name elsewhere;
6. compare liquidity/activity;
7. decide whether it is worth deeper research.

The workflow is slow enough that users are tempted to skip steps. It also creates three recurring failure classes.

### Failure A — name/ticker substitution

A familiar ticker is cognitively persuasive but technically weak. In a permissionless EVM environment, anyone can deploy another ERC-20 with the same symbol. The right question is not “Does this token say AAPL/GME/etc.?” but “Does this exact contract match Robinhood’s canonical deployment for that asset?”

### Failure B — fragmented evidence and tab fatigue

The user has to reconcile explorer pages, official asset lists, social posts, and trading terminals. A material fact can be missed simply because it lives in another tab. TrenchGuard turns those observations into one machine-readable packet.

### Failure C — false precision

Many scanners collapse heterogeneous evidence into a single score. A score can obscure whether the underlying source was absent, stale, or incomplete. TrenchGuard exposes `material_fetch_failures`, labels sample-based concentration, and fails closed on missing material evidence.

## 4. Opportunity selected

The selected opportunity is **pre-trade provenance and fragility triage**.

This is intentionally narrower than “find the next winner.” The product accepts candidates from any upstream discovery source and attempts to answer:

- What is the exact contract?
- When was it deployed?
- Is source verification observable?
- How concentrated is the returned holder sample?
- Is activity nearly nonexistent?
- Does its ticker/name collide with a Robinhood canonical asset?
- If it claims to be a Robinhood Stock Token, is this exact contract in the official registry?
- Which evidence could not be fetched?

This product form is more useful than another generic dashboard because it complements, rather than duplicates, existing discovery and execution products. It also has a falsifiable gain path: if screened cohorts suffer fewer identity-related catastrophic losses or lower drawdown than unscreened cohorts after realistic costs, the tool has demonstrated value.

## 5. MVP behavior

### Input

One Robinhood Chain ERC-20 contract address:

```bash
python trenchguard.py scan 0x...
```

Or a JSON list of candidate addresses:

```bash
python trenchguard.py batch candidates.json
```

The tool deliberately does not scrape a “hot tokens” list. Discovery is modular. The user can feed candidates from any venue and keep the evidence layer stable.

### Output

The deterministic JSON packet includes:

- `chain_id=4663`;
- normalized token address;
- token name/symbol/decimals/supply when available;
- creator and creation transaction;
- deployment timestamp and age;
- `early_token_definition_hours=72`;
- contract verification state;
- holder sample concentration;
- transfer and holder counters;
- official Robinhood registry match/collision;
- fetch failures;
- evidence flags and a cautious tier;
- source URLs;
- an explicit `not_a_return_prediction=true`.

### Operational definition of “early token”

For the MVP, an **early token** is a contract whose creation transaction is at most 72 hours old at observation time.

This is a *cohort boundary for forward testing*, not an assertion that 72 hours predicts return. The duration is short enough to represent the launch/discovery phase while providing a reproducible timestamp rule. It should be varied in sensitivity tests (for example 6h, 24h, 72h, and 7d) before becoming a production default.

## 6. Why these features trace to demonstrated need

### Canonical registry check

**Need:** ticker/name impersonation is possible and has been observed at scale.  
**Evidence:** first-party canonical deployment API plus independent Robinhood-chain impersonation research.  
**Risk-adjusted-return path:** avoids buying an unintended contract whose label imitates a known asset.

### Deployment age

**Need:** “new” feeds contain many short-lived contracts.  
**Evidence:** explorer creation transaction plus independent mortality research.  
**Risk-adjusted-return path:** enables honest forward cohorts and prevents survivor-only backtests.

### Contract verification state

**Need:** users need inspectable code/provenance.  
**Evidence:** Blockscout verified-contract endpoint.  
**Risk-adjusted-return path:** an unverified contract is not automatically malicious, but it reduces inspectability and should increase caution.

### Holder concentration

**Need:** concentrated ownership can increase price/liquidity fragility.  
**Evidence:** Robinhood wallet risk guidance and live holder data.  
**Risk-adjusted-return path:** concentration is a risk filter, especially when combined with low activity and very early age.

### Activity counters

**Need:** a newly created contract with negligible transfers can look “new” while having no meaningful market participation.  
**Evidence:** Blockscout counters.  
**Risk-adjusted-return path:** filters obvious dead/near-empty candidates; this is not a substitute for liquidity/slippage modeling.

### Explicit data-quality gate

**Need:** silently missing evidence can be mistaken for a clean result.  
**Evidence:** common multi-API failure mode; direct design requirement from the research workflow.  
**Risk-adjusted-return path:** prevents absence-of-data from becoming false reassurance.

## 7. Assumptions and inference

The following are **assumptions or engineering inferences**, not verified alpha:

- A 72-hour age threshold is useful for constructing an “early” test cohort.
- Top-10 sample concentration at or above 80% is a reasonable caution threshold.
- Top-1 sample concentration at or above 50% is a reasonable caution threshold.
- Fewer than 10 observed transfers is a useful “very low activity” warning.
- Verified contract source should reduce uncertainty, although verification alone does not imply safety.
- A canonical ticker/name collision should increase caution even if the noncanonical asset is intentionally unrelated.

These thresholds must be sensitivity-tested. The tool exposes underlying values so users can replace the thresholds without treating them as universal constants.

## 8. What the research does not prove

The current evidence **does not prove** that:

- verified contracts outperform unverified contracts;
- low concentration causes higher returns;
- a particular age window predicts positive returns;
- social momentum is predictive;
- any single risk flag should trigger a trade;
- avoiding ticker collisions alone produces excess return;
- past Robinhood Chain behavior will persist.

Accordingly, the MVP has no “buy” output, no target price, no profit promise, and no automated execution.

## 9. Validation protocol

A credible validation must freeze the information set at decision time and compare cohorts prospectively.

### 9.1 Candidate universe

At fixed intervals, capture all candidates from one or more upstream discovery feeds. Store the candidate contract and timestamp before querying future outcomes. Do not backfill only tokens that later became liquid.

Create age cohorts using creation time:

- <=6 hours
- <=24 hours
- <=72 hours
- <=7 days

The 72-hour cohort is the MVP headline cohort, but all four should be reported.

### 9.2 Treatment and baselines

Compare:

1. **TrenchGuard-screened** candidates: no critical provenance collision, material data complete, and no predeclared severe concentration/activity exclusion.
2. **Unscreened feed**: all candidates from the same discovery source.
3. **Random selection**: repeated random samples matched by observation time.
4. **Market-cap-weighted exposure** when reliable market-cap data exists.
5. **Existing discovery-tool ranking** if the upstream product exposes a reproducible ranking.

No baseline may use future information unavailable at the candidate timestamp.

### 9.3 Simulated execution

Because this is intelligence-only, evaluation uses paper fills. The simulator must model:

- quoted liquidity at observation time;
- realistic slippage as a function of order size and available liquidity;
- swap/network fees;
- failed transactions;
- latency between signal and executable quote;
- partial/no fill if liquidity is insufficient.

A candidate that cannot support the test order size is a failed/abstained decision, not a zero-cost perfect fill.

### 9.4 Outcome metrics

For every cohort and baseline report:

- net return after fees and slippage;
- volatility;
- maximum drawdown;
- hit rate;
- false-positive rate;
- sample size;
- abstention rate;
- catastrophic-loss rate under a predeclared threshold.

If Sharpe or Sortino ratios are reported, show the exact sampling and annualization assumptions.

### 9.5 Bias controls

**Survivorship bias:** preserve every candidate observed at discovery time, including contracts that disappear from interfaces or lose all liquidity.

**Look-ahead bias:** version and timestamp every input. No later verification, holder state, social history, or revised metadata can be inserted into the earlier packet.

**Selection bias:** use a predeclared upstream feed and capture schedule rather than manually choosing interesting tokens.

**Manipulated data:** treat volume, holder count, and social activity as potentially gameable. Prefer contract/transaction facts for identity. Cross-check suspicious metrics.

**Multiple testing:** if many thresholds are tried, separate exploratory tuning from a held-out period.

### 9.6 Success criterion

The MVP succeeds only if a held-out or forward paper cohort demonstrates meaningfully better risk-adjusted outcomes than credible baselines after costs, with enough sample size to reject a one-off result.

A useful risk tool may also succeed by materially reducing catastrophic false positives or maximum drawdown even if mean return is unchanged. That outcome would fit the gain function because risk-adjusted performance, not raw upside, is the objective.

If the forward evidence does not show an advantage, the recommendation is to retain the provenance packet as a due-diligence utility and investigate the next-best direction: liquidity trajectory and deployer-history features, again with time-frozen data and no execution.

## 10. Security and safety properties

TrenchGuard RH:

- performs HTTP GETs only;
- contains no wallet code;
- has no private-key input;
- has no RPC write method;
- never constructs transaction calldata;
- never approves or transfers a token;
- never connects to a DEX router;
- never creates a token;
- never emits “buy”, “sell”, or profit-promise instructions.

This keeps the MVP aligned with the task’s intelligence-only boundary and makes the artifact independently auditable.

## 11. Source register

### First-party Robinhood

1. Robinhood Chain docs — https://docs.robinhood.com/chain/
2. Connecting to Robinhood Chain — https://docs.robinhood.com/chain/connecting/
3. Contracts — https://docs.robinhood.com/chain/contracts/
4. Stock Tokens — https://docs.robinhood.com/chain/stock-tokens/
5. Stock Token APIs — https://docs.robinhood.com/chain/stock-token-apis/
6. Live asset registry — https://api.robinhood.com/rhj/assets
7. Wallet risk FAQ — https://robinhood.com/us/en/support/articles/robinhood-wallet-faqs/
8. Scam guidance — https://robinhood.com/us/en/support/articles/how-to-identify-and-report-scams/

### Explorer / infrastructure

9. Robinhood Chain Blockscout — https://robinhoodchain.blockscout.com
10. Blockscout token API — https://docs.blockscout.com/api-reference/get-token-info
11. Blockscout holder API — https://docs.blockscout.com/api-reference/token/get-token-holders
12. Blockscout address API — https://docs.blockscout.com/api-reference/get-address-info

### Independent / user-market evidence

13. Bitquery Robinhood Chain investigation — https://bitquery.io/investigations/robinhood-chain-tokenized-stocks
14. Maestro product updates — https://t.me/s/MaestroSniperUpdates
15. Token spam/rug-pull research — https://arxiv.org/abs/2206.08202

## Final recommendation

Build and evaluate the provenance gate before adding prediction. Robinhood Chain’s permissionless token surface makes a deterministic “prove this exact contract” step immediately useful, while current evidence is not strong enough to justify a profitable early-token forecasting claim.

The artifact submitted with this report implements that recommendation. It is small by design: zero keys, zero writes, transparent evidence, deterministic JSON, and explicit failure states. That is a stronger foundation for risk-adjusted decision improvement than an opaque score built from unvalidated signals.
