# Replacement market-data provider evaluation

Reviewed 2026-10-30. Research and bounded read-only probes only; no migration,
account creation, paid service, training import, risk change or live execution.
Baseline: [source audit](learning_source_audit_2026-10-30.md). UNKNOWN means
unverified, **not permission**. Published coverage is distinguished from tested
coverage. Prices/quotas below are advertised figures, not purchased entitlements.

## Decision

**CONDITIONAL overall. No source is presently approved as a complete, free,
production market-data and persistent NN-training replacement.**

Prioritize ZeroSlip for a free technical pilot **after written retention/ML
clarification**, paired with direct RPC verification and independent gap recovery.
Its documented downloadable replay makes it unusually promising. Use an
independently reconstructed on-chain dataset as the long-term alternative, subject
to the RPC operator's terms and infrastructure budget. GeckoTerminal is a useful
conditional graduated-pool cross-check, not a bonding-curve/full-hour substitute.
Bitquery Data Store has the clearest reviewed derived-model/retention grant, but
is a separately licensed purchase, not a free API entitlement. No purchases proposed.

The existing 10 consecutive successful challenger promotions remain required for
live eligibility. Changing providers cannot itself create promotions or establish
profitability. Missing source rights or market evidence must continue to block use.

## Repository requirements: minute observations versus evaluation

`run_paper.py`, `run_meme_live.py` and `run_paper_compare.py` currently instantiate
`PumpMarketSources`/`ModeledPumpSources`. Their disabled sources supply discovery,
SOL/USD, token/pool prices, activity, candles and optional holder/ATH context.
`wallet_snapshot.py` also depends on the old SOL-price route.

- `volume_candles.py`: fresh market observation (30 seconds), verified 3+ completed
  60-second candles, fresh candle retrieval/completion, and >=95% coverage of the
  configured 3,600-second buy-volume window, minimum $5,000. Other strategy/risk
  requirements remain mandatory. Total volume or buyer counts cannot replace
  USD **buy** volume. Three candles alone do not establish hourly coverage.
- `paper_sources.PublicSources.observation`: reusable GeckoTerminal pool identity,
  price/liquidity, trade and OHLCV parsing. Currently accepts identity-matched
  graduated PumpSwap pools only; truncated recent trades fail hourly coverage.
- `neural_exits.py`: at most one held-position quote/minute; continuation labels
  require later same-position observations. Minute quotes are estimates, not fills.
- `exit_evaluation.py`/`evaluation_state.py`: frozen, chronological, position-grouped
  OOS evaluation using later rule-closed positions. Minute labels are inputs;
  independent positions determine challenger evidence. Durable state must survive
  source outages, restarts and paper funding resets without resetting metrics.
- `meme_learning.py`: preserve valid wins/losses across runs; funding is not P&L.
  Provider-derived features require reviewed training/evaluation rights.
- `wallet_watch.py`/`wallet_signals.py`: retain configured saved-wallet receipt
  monitoring through RPC. Discovery metadata or KOL counts cannot establish human
  buyers, independent wallets or profitable copy trades.

## Provider comparison

Scores are **technical suitability**, 0–5: 0 unsuitable, 3 useful with material
work/coverage constraints, 5 strong documented fit. They do not grant permission.
L/H/M = live market data / historical research / ML dataset utility; ML score
requires the separately stated rights gate. UNKNOWN capabilities remain unproven.

| Source | Pump curve / graduated; data and transport | Access, cost and limits | Main limits; L/H/M | Assessment |
| --- | --- | --- | --- | --- |
| ZeroSlip | Both documented; WS creations, swaps, migrations, liquidity events, transfers; hourly compressed JSONL replay. Build candles, directional volume and wallet flows locally. | Advertised free stream/replay, no key in documented WS example; one connection/client. Other numeric quotas UNKNOWN; trading service has separate fees and is unnecessary. | No verified completeness/SLA; live probe received a transfer, not a Pump swap. USD history, retention and ML permission unresolved. 4/4/3 | **CONDITIONAL**; first free pilot candidate after rights review. |
| Bitquery | Both documented; GraphQL candles/trades/launches/buyers, liquidity and migrations; WS, gRPC/Kafka; separate downloadable datasets. | Account/token required. Seven-day trial: 1,000 points, two streams; real-time trial, archive add-on for older API history. Exact ongoing paid API cost/rate is order-specific/UNKNOWN here. | Strongest documented broad schema. API and Data Store licenses differ; history windows differ by cube. 5/5/5 for licensed Store data | **CONDITIONAL**; paid historical option, no free sustained deployment established. |
| dex.fun | Chain-indexed curve launches/graduations, pools, candles, trades, holders, market windows, wallet P&L; REST/WS, batches of 100 mints. | Manually approved closed-beta bearer key; default 120 requests/min, 25,000/day, five WS. Price/free entitlement UNKNOWN. | No beta uptime promise; schemas may change; historical depth and ML retention UNKNOWN. 4/3/2 | **CONDITIONAL**; needs approval, license and coverage tests. |
| Solana Tracker | Both documented; launches/curves/graduations, trades, charts, liquidity, price, buyers/holders, wallet analytics; REST/WS. | Key; free 2,500 requests/month, 3/s. Advanced €50/month, 200K requests; Premium €397/month, 10M and ongoing Datastream. Verify actual entitlement. | Free quota inadequate for continuous per-minute collection: one request/minute is 43,200/30 days. Historical depth and ML rights UNKNOWN. 5/4/2 | **CONDITIONAL**; convenient managed option if license/budget resolved. |
| Birdeye | Price/chart/trade/wallet/holder APIs advertised; REST and paid WS. Exact curve/PumpSwap completeness and history per mint UNKNOWN in this review. | Key/account. Standard $0: 30K CUs, limited APIs, 1/s; Lite $39, Starter $99; Premium $199/month includes WS, 50/s. CU costs vary. | Explicit restrictive collection/storage terms require a controlling supplemental license; free plan is not full feature entitlement. 3/3/1 | **NO-GO under reviewed default terms** for retained NN pipeline; reconsider only with written permission. |
| CoinGecko / GeckoTerminal | Gecko graduated PumpSwap verified; REST pools, recent trades, minute OHLCV. Ordinary CoinGecko listings do not guarantee every launch. Curve coverage UNKNOWN, not demonstrated. | Gecko public no key; conflicting published 10/30 per minute guidance: retain existing lower shared 10/min budget. CoinGecko Demo/Pro are separate keyed plans; paid endpoints/WS not implied by Gecko access. | Recent trade cap cannot prove busy-hour buys; cache refresh/deletion terms obstruct permanent training archive without clarification. 3/3/1 | **CONDITIONAL** for graduated cross-checks; **NO-GO for durable ML archive under unresolved terms**. |
| Dune | Solana raw/decoded/curated trades and SQL aggregates; API JSON/CSV exports; not a demonstrated low-latency trading feed. Specific Pump program/version coverage UNKNOWN without query validation. | Account/key; current docs say free view-only has no API; trial/paid required. Plus 70 low-limit / 200 high-limit requests/min. Credit/export charges separate; exact price UNKNOWN. | Indexing/query delay, USD nulls, multi-hop rows, truncation; ML retention not expressly verified. 1/4/2 | **CONDITIONAL** historical research only; **NO-GO as live primary**. |
| Direct Solana RPC / own indexer | Both reconstructible from successful transactions, program events/accounts and migrations; HTTP JSON-RPC/WS; own persisted events/candles. | Public RPC free, variable limits; private/archive/gRPC usually provider-dependent. Own node/indexer has hardware/storage/bandwidth costs. | No built-in OHLCV, USD oracle, social feed or complete historical index. Operator terms/retention rights need review; technical feasibility is not blanket legal permission. 3/4/4 | **CONDITIONAL** market/indexing replacement; **GO for existing bounded receipt/account observations within applicable provider terms**. |

## Terms and authoritative evidence

Checked 2026-10-30. Website scraping is not authorized by the existence of an API.
No reviewed marketing promise is treated as an uptime or completeness guarantee.

| Source | Evidence and date | API / storage / ML / derived signals disposition |
| --- | --- | --- |
| ZeroSlip | [Stream](https://docs.zeroslip.ai/stream), [replay](https://docs.zeroslip.ai/historical-replay), [terms](https://docs.zeroslip.ai/legal), terms updated 2025-12-27. | Documented automated WS and downloadable replay/backtesting support; replay advertised since 2026-04-18. Terms disclaim accuracy/availability. Explicit durable ML training/evaluation, model distribution and retention rights **UNKNOWN**. Backtesting permission is not an express ML license. |
| Bitquery | [Pump reference](https://docs.bitquery.io/docs/blockchain/Solana/Pumpfun/Pump-Fun-API/), [API terms](https://www.bitquery.io/terms-of-service), [Data Store license](https://bitquery.io/datastore/legal/data-license), Store license updated September 2026. | Store grant expressly covers perpetual downloaded-data storage/processing and derived models/signals/research; raw redistribution/competing feeds restricted. This supports models as an interpretation of the express model grant; confirm NN training/evaluation and ordered coverage before checkout. **Do not transfer Store rights to API trial results.** API ML/retention scope remains UNKNOWN absent applicable order. |
| dex.fun | [Partner overview/beta terms](https://docs.dex.fun/api), [limits](https://docs.dex.fun/rate-limits), [linked terms](https://www.dex.fun/terms). Effective legal date UNKNOWN. | Authorized partner REST/WS documented; raw-feed redistribution requires agreement. Linked terms yielded no readable text; storage, ML/evaluation and derived-model distribution **UNKNOWN**. Do not use internal `api.dex.fun`. |
| Solana Tracker | [Pump coverage](https://www.solanatracker.io/pumpfun-api), [plans](https://www.solanatracker.io/data-api), [docs](https://docs.solanatracker.io/). [Previously indexed terms PDF](https://www.solanatracker.io/terms-of-service.pdf) returned 404; preview copy 502. | Documented API intended for automation. Current enforceable retention/ML/derived-use clauses and effective date **UNKNOWN**; docs for LLM access concern documentation, not a market-data training license. |
| Birdeye | [Plans](https://docs.birdeye.so/docs/pricing), [Data API terms](https://birdeye.so/data-api/terms-of-service), updated 2025-04-11; [website terms](https://birdeye.so/terms). | Data terms clause 10.2 includes broad API collection/storage/copy restrictions; supplemental terms can control. Do not assume buying a key overrides them. Explicit training/evaluation/derived-model permission **UNKNOWN**; current retained-learning use blocked pending written controlling license. |
| Gecko | [API docs](https://api.geckoterminal.com/docs/index.html), [support limits](https://support.coingecko.com/articles/6867086195-does-geckoterminal-have-an-api), [Gecko terms](https://www.geckoterminal.com/terms-conditions), [CoinGecko API terms](https://www.coingecko.com/en/api_terms). Prior audit recorded terms dates 2025-08-11 / 2025-09-05; API clauses rechecked today. | Attribution, security, 24-hour cache refresh and termination deletion obligations; §6 restricts copying/storage/derivation beyond express grants. Immutable training history, retained weights/evaluation and derived signals require clarification; **UNKNOWN**, not approved. Paid Pro [OHLCV docs](https://docs.coingecko.com/reference/pool-ohlcv-contract-address) do not extend free rights. |
| Dune | [Terms](https://dune.com/terms), updated 2026-06-03; [limits](https://docs.dune.com/api-reference/overview/rate-limits); [Solana trades](https://docs.dune.com/data-catalog/curated/dex-trades/solana/solana-dex-trades). | Use official interfaces; no dashboard scraping. Public query-code reuse requires credit; that is not universal dataset ownership. Third-party datasets/order terms and ML/storage/derived rights **UNKNOWN**. Dune's own AI features do not license our training. |
| Solana | [RPC](https://solana.com/docs/rpc), [public clusters/limits](https://solana.com/docs/references/clusters). | Documented chain interaction; published public caps 100 requests/10s/IP, 40/method/10s, subject to change; not production infrastructure. A self-operated indexer avoids copying vendor enrichments, but hosted RPC terms still apply. No universal provider-independent ML license asserted. |

Pump website/private market scraping stays disabled. The prior audit documents
[Pump developer terms](https://docs.pump.fun/terms/) §8.3(e)'s ML/evaluation
restriction; an approved Pump social API key is not a replacement market-data or
training grant. Independent chain indexing must actually be independent, not a
proxy for obtaining prohibited API data.

## Read-only feasibility observations

Probes on 2026-10-30 used no credentials. Local sandbox DNS initially failed;
permitted network probes then succeeded. Browser fetch failures are not evidence
that a provider API is down. No rate-limit stress tests, bulk archive downloads,
registration or authenticated endpoints were attempted. Probe payloads were held
in memory and not imported into bot state, training or evaluation.

| Probe | Actual observation | What it proves / does not prove |
| --- | --- | --- |
| CoinGecko `/api/v3/ping` | Browser returned documented JSON `gecko_says`. | Public connectivity only; no token/history coverage test. |
| Gecko Solana `/tokens/pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn/pools` | HTTP 200; PumpSwap pool `2uF4Xh61rDwxnG9woyxsVQP7zuA6kLFpb3NvnRQeoiSd`; also Orca returned. Price, reserve, transactions and volume fields. | Actual graduated Pump token coverage; filter venue/pool/base/quote identity. No curve proof. |
| Same pool `/ohlcv/minute?aggregate=1&limit=4&currency=usd&token=base&include_empty_intervals=false` | HTTP 200, four six-value timestamp/OHLCV rows; example timestamp 1793395440. | Schema fits existing parser. Current bar may be unfinished: discard until closed. Four rows do not establish historical depth. |
| Same pool `/trades?trade_volume_in_usd_greater_than=0` | HTTP 200, 300 rows; fields include transaction hash, block, time, direction, token amounts, USD volume, anomaly flag. Oldest 21:23:08Z; retrieval approximately 21:25:50Z. | Only about 162 seconds, **not an hour**. Cannot verify $5,000 hourly buy threshold from this page. Reads paced >=6 seconds apart; no observed 429, no inferred higher limit. |
| ZeroSlip `wss://stream.zeroslip.ai/` | One bounded no-key connection received a JSON transfer event: signature, action, balances, transfers, block and timestamp. Connection closed. | WS/schema connectivity; live Pump swap completeness/latency not measured. No replay download or historical coverage verification. |
| dex.fun spec | Browser spec fetch unavailable; no partner key requested. | No authenticated API feasibility claim. |
| Bitquery, Tracker, Birdeye, Dune | Docs/terms only; required account/key/trial not created. | Coverage advertised, not measured. No false claim of successful free token/API access. |

No numerical latency/accuracy/SLA or outage-recovery guarantee was independently
established for any provider. Replay archive naming examples in ZeroSlip docs
have an apparent hour-boundary ambiguity; verify event timestamps and manifest
coverage rather than trusting filenames. Advertised earliest replay date does not
prove every hour exists. No historical data is cleared for NN ingestion by these probes.

## Proposed multi-source architecture

1. **Rights registry first.** Record provider, endpoint/product, terms version/date,
   reviewed uses (runtime, raw retention, training, evaluation, derived models,
   publication), expiry/deletion obligations and approval evidence. UNKNOWN fails
   closed per use. Do not relax `data_policy.py` using a host-wide allowlist.
2. **One shared acquisition process.** Candidate primary: approved ZeroSlip stream
   plus permitted replay repair. Direct RPC confirms accounts/receipts and checks
   sampled swaps. Conditional fallback: Gecko graduated prices/candles; licensed
   Bitquery/Tracker can replace missing history/stream only after explicit review.
   A second source must not consume the full primary budget merely to duplicate it.
3. **Canonical events before features.** Store chain, mint, pool, venue/program,
   base/quote mint/decimals, signature, outer/inner instruction/event index, slot,
   block timestamp, commitment, observed/retrieved timestamps, provider/schema
   version, raw hash, coverage and USD conversion source/time. One signature can
   contain multiple swaps: signature alone is not a safe deduplication key.
4. **Completeness ledger.** Checkpoint finalized slots/event cursors; reconnect with
   bounded exponential backoff/jitter and recover overlaps idempotently. Missing
   slots, null timestamps, unsupported instructions and archive hours remain gaps.
   WS alone cannot certify full-hour volume; require repaired continuous coverage
   and a closed-window watermark. Preserve pool changes across graduations.
5. **Local aggregation.** Compute genuine completed UTC minute OHLCV, buy/sell USD
   volume and distinct-wallet counts from one canonical swap set. Never add vendor
   totals to reconstructed totals. Keep pool versus token-wide scope explicit.
   Resolve USD at event time from a rights-approved source or independently
   decoded liquid SOL/stable pools; account for depegs and reject stale conversion.
   Curve virtual reserves are not withdrawable liquidity. Real reserves and venue
   liquidity must be represented separately. No-trade and missing intervals differ;
   do not manufacture eligible candles.
6. **Disagreement/outage policy.** Compare identity/time-matched data, retain both
   provenances, quarantine conflicts; do not average mismatched pools/times. Keep
   existing freshness/coverage controls. Use the current provider budget/backoff
   for REST plus endpoint/day/month/CU budgets, WS connection caps and bounded
   buffers. Honor 429/Retry-After and stop on access denial. Never rotate keys/IPs
   to evade limits. Freshness failures block new entries; preserve position/exit
   reconciliation and explicit unavailable status.
7. **Learning separation.** Review quarantined datasets before import; attach
   immutable feature/provenance versions and rights to every sample. Freeze feature
   transformation and evaluation cutoff; backfilled corrections cannot silently
   rewrite consumed evaluation positions. Keep paper modeled execution separate
   from confirmed live fills. Snapshot/quote histories are not executable prices.

Field ownership: primary canonical swaps supply prices, directional hourly flow,
buyers and candles; confirmed RPC supplies account/risk/position receipts and
local execution estimates; permitted USD conversion supplies valuations. Historical
replay supplies backfill only after coverage/license checks. Configured saved
wallets remain the wallet-learning universe. Token metadata/social links may aid
discovery only; Pump posts, leaderboards, narrative engagement, verified identities
and coordinated-buyer evidence are **not replaced** by these feeds. Mark absent
social/ATH/holder/risk fields unavailable; retain gates requiring them. Dune is an
optional offline cross-check, not an entry dependency.

Direct reconstruction requires versioned Pump/PumpSwap decoders, inner/CPI events,
failed-transaction filtering, token decimals/Token-2022 handling, account histories,
reserve changes, finality/reorg handling and migrations. `logsSubscribe` is a hint,
not durable history. `getSignaturesForAddress` pagination and `getTransaction`
are useful for selected accounts, not proof of a global complete dataset. Public
RPC pruning/rate caps and launch traffic make chain-wide backfill infeasible to
promise for free; archive/indexed access or paid/self-hosted infrastructure may
be required. Do not change existing capital-gated discovery to bypass this cost.

## Minimal implementation plan (not implemented)

| Phase | Relevant files | Acceptance criteria / focused tests |
| --- | --- | --- |
| Rights and quota configuration | `data_policy.py`, `provider_limits.py`; new provider configuration | Endpoint/use-specific authorization; unknown ML remains blocked. Cross-process minute/day/month/CU budgets; 429/503 Retry-After, 401/403 stop, WS cap/reconnect tests. No `.env` edit without approval. |
| Isolated event adapter/pilot | New provider adapter and canonical event store; reusable parsers in `paper_sources.py`, `candle_quality.py`; local IDLs/decoders in `pumpswap.py` | Offline fixtures: curve and graduated mint/pool identity, migration, decimals, multi-swap dedup, ms/seconds normalization, failed tx, out-of-order/reorg, stale USD, gap detection. No training or order submission during pilot. |
| Market interface replacement | `pump_market.py`, `run_paper.py`, `run_paper_compare.py`, `run_meme_live.py`, `wallet_snapshot.py` | Same snapshot contract for paper/live; SOL/holding valuations replaced too. Three completed minute candles pass only with full gate evidence; incomplete hourly buys, wrong pool and stale fields fail. Preserve modeled costs/local execution. |
| Saved-wallet/discovery context | `wallet_watch.py`, `wallet_signals.py`, `discover_memes.py`, `pump_narratives.py` | Saved-wallet continuity/receipt rules unchanged; scraper paths remain blocked. Explicit missing social context. Existing fresh-equity/discovery and all risk gates unchanged. |
| Reviewed replay and learning | `meme_learning.py`, `neural_exits.py`, `exit_evaluation.py`, `evaluation_state.py`, `audit_learning.py` | Approved isolated replay yields deterministic samples; preserve wins/losses/funding distinction. Position-grouped chronology, frozen challenger/cutoff/consumed IDs, restart/interrupted-write recovery and 10-promotion gate unchanged. No correction-induced metric reset. |
| Shadow acceptance | `paper_console.py`, `meme_console.py`; documentation | Show source age/gaps/rights/quota and unavailable context compactly. Compare sampled swaps to finalized chain receipts, account for every coverage gap, measure observed latency against existing freshness limits. Run paper shadow first; no automatic live enablement. |

Use targeted existing tests (`test_paper_sources`, `test_candle_quality`,
`test_volume_candles`, `test_exit_evaluation`, `test_evaluation_recovery`) only where
that phase changes behavior; add adapter/limiter fixtures as needed. This report
changes documentation only, so no trading/model test suite was run. Read-only
connectivity/schema probes above are the feasibility validation, not a replay or
proof of profitable execution.

## Blocking questions before migration

- Obtain explicit provider/product permission for durable raw/features retention,
  NN training, OOS evaluation, weights and derived signals after termination, and
  publication of research. Do not publish provider datasets or assume open-source
  bot licensing covers data. Bitquery Store's grant is promising but order-specific.
- Demonstrate actual curve and graduated swaps, exact per-mint historical depth,
  recovery coverage, stable schemas and event-time USD quality. Need at least a
  complete hour before validating the current entry-volume gate.
- Establish free stream/replay quotas, archive-size/bandwidth/disk budget and
  outage repair; market-wide unfiltered streaming may exceed a small host's budget.
- No free managed source here has demonstrated the complete required real-time,
  historical, licensed ML workflow. Free Tracker/Birdeye quotas and Gecko trade
  pagination are materially limiting; Dune free API is unavailable under current docs.
- Terms that could not be read remain UNKNOWN. Provider agreement changes must be
  rechecked before implementation. Existing historical data and evaluation state
  remain intact; none of these uncertainties justify deleting data, silently
  retraining, weakening gates or enabling live execution.
