# Meme trader conversion

## Current meme execution runner

### Per-coin entry tolerance and paired testing

The configured live policy is now `entry_profile_mode: adaptive`, explicitly
selected by the user. On each fresh coin observation, paper and live assess:

| Profile | Maximum 1m range | Minimum hourly buys | Pullback |
| --- | ---: | ---: | ---: |
| Current | 30% | $5,000 | 2–15% |
| Moderate | 45% | $2,500 | 2–20% |
| Aggressive | 60% | $1,000 | 2–25% |

The rules-based selector chooses the **narrowest qualifying profile for that
coin**, not the best profile in hindsight. It logs all three assessments and
the selected profile. No qualifying profile means watch/reject, never forced
entry. Full on-chain, execution, cost, reserve and duplicate-candle checks still
run after market-profile selection. This is not a trained profit predictor.
Dollar exposure, $1 net target, $0.20 loss trigger, 5% drawdown halt and 0.5%
slippage are identical across profiles; adaptive selection cannot widen them.
The $50,000 `min_liquidity_usd` setting applies to the older ATH profile, not
the trend profile: trend sizing is limited by the configured pool-allocation
fraction and available funds, followed by execution validation.

For a flat live state, explicitly adopt the new selector on your next restart:

```bash
.venv/bin/python run_meme_live.py --execute --loop --profile adaptive --adopt-rules
```

This is a user-operated real-money command. No funded process is restarted by
the comparison. Fixed `--profile current`, `moderate`, or `aggressive` choices
remain available in both runners. Existing paper runs preserve their saved
policy: use a fresh `--state-dir` or an explicit archived reset for a new policy.

The shared-data paper experiment runs the three fixed profiles **plus a fourth
portfolio using the per-coin selector**. All start with identical independent
virtual inventory and receive the exact same durable observation stream in the
same order. Each market snapshot is fetched once; union-held exit quotes are
prioritized. A journal recovers partial fan-out without duplicate paper fills.
There is no `.env` read, signer, submission, or automatic live promotion.

```bash
# New forward experiment: each portfolio starts with $6.50 modeled entirely as SOL.
.venv/bin/python run_paper_compare.py --bankroll-usd 6.50 --loop
# Resume the same experiment without resetting inventory or rules.
.venv/bin/python run_paper_compare.py --loop
# Read the saved report without launching any funded trader.
.venv/bin/python run_paper_compare.py --status
# Historical replay: choose the starting period's SOL valuation, not today's.
.venv/bin/python run_paper_compare.py --state-dir state/comparison_replay --replay-db state/paper_memes/paper.sqlite --bankroll-usd 6.50 --sol-price STARTING_SOL_USD
```

Use `--balance`, `--native-sol`, and `--sol-price` instead of `--bankroll-usd` to
model a particular SOL/USDC split. `--feed FILE.jsonl` supports shared historical
replay; `--run-id` selects an archived recorded run. `--no-selector` compares only
the three fixed portfolios in a fresh experiment. `report.json` and the frozen
`manifest.json` are under `state/paper_comparison/`; changing profiles/inventory
requires a separate experiment directory. The earlier single paper ledger is
preserved and is not merged into these results.

Reports include entries/closed trades, net realized P&L, open positions, estimated
liquidation equity after modeled exit costs, marked drawdown, rejected entry
observations, blocked exits and modeled slippage. Real execution failure rates
and observed slippage remain unknown in paper. Results are broken down by coin,
selected entry profile, and observed candle-range bands (<=30%, 30–45%, 45–60%,
>60%). Native SOL price changes also affect portfolio equity and are not
realized trading profit. The three entry dimensions change together, so these
comparisons do not isolate their separate causal effects. Target/stop methods
are held constant; trailing or volatility-adjusted exits need a separate paired
experiment with defined parameters. No outcome proves future positive returns.

`run_meme_live.py` is separate from the legacy Donchian `run_live.py` runner.
It connects Pump market data and the shared wallet/Hot Callouts feed to the
trend/pullback strategy. Transactions are built locally from pinned official
Pump IDLs in `vendor/`, not downloaded from a transaction-building service.

The live configuration is `meme_live_rules.json`: **$1.78 maximum input per
position**, **$1 net profit trigger**, **$0.20 net loss trigger**, **5% peak-equity
drawdown halt**, one position, a 15-minute holding limit, 0.5% maximum slippage,
a $1 spendable-capital reserve and 0.005 native SOL fee reserve. Other limits
can reduce position size. The profit trigger also checks minimum net proceeds;
fees count toward P&L. Drawdown/time exits can occur below the profit target.
SOL/USD movement, gaps, unavailable quotes and failed sales can exceed a loss
trigger. These thresholds are not guaranteed dollar outcomes.

Supported execution is native-SOL-quoted Pump bonding curves (V3) and
migrated PumpSwap pools (V2), including a restricted Token-2022 subset.
Mint/freeze authorities, account ownership, pool/curve identity, extensions,
fees, native debits and exact minimum output are checked. Cashback, mayhem,
holder rewards, transfer-fee/hook and unknown extensions are rejected. Every
entry first requires an unsigned atomic buy/sell probe, then a fresh snapshot
and simulation of the actual order. A probe does not reserve liquidity or
establish that a later sell will succeed. Migration exits resolve and validate
the new pool for the same mint.

An additional on-chain entry gate samples the 20 largest token accounts and
up to eight recent mint-referenced transactions. It excludes the verified pool
vault, groups sampled accounts by owner and rejects a non-pool owner above 10%
of supply or sampled top-ten owners above 50%. It flags repeat-buyer dominance
and multiple buyers clustered in one slot; these are suspicion signals, not
proof of automation/bundling. At least three classified trades and two distinct
buyers are required. Missing/insufficient observations block **new entries**;
exits remain independent. Reports are journaled with explicit coverage and
unknown full-history/bundle/human labels. Results, including failures, are
cached for at most 30 seconds to limit free-RPC traffic.

The public mainnet RPC returned HTTP 429 during indexed-holder validation;
the alternate free provider checked was unreachable in this environment. These
are actual data-access blockers, not passing risk checks. `--risk-rpc HTTPS_URL`
selects an optional read-only mainnet RPC for holder/signature/receipt queries;
it never receives signed transactions. The trade RPC remains the configured
RPC. Provider failure remains an entry rejection; do not remove gates to make
fills appear. Full launch/funding reconstruction and verified bot/human labels
remain incomplete. This is not an all-clear for unattended live trading.

**Evidence scope:** the supplied live profile uses `evidence_mode: market_only`,
matching the user's trend/popularity experiment. It checks candles, buy volume,
social freshness and on-chain execution constraints. The bounded entry gate can flag some concentration and coordination, but
it does not identify all bots, bundles or pump schemes. Use `strict` with real,
fresh independent evidence if those entry gates are required. Unknown evidence
is never marked verified. The strategy is not validated as profitable.

Use the project's virtual environment. The default command is **unsigned** and
needs only a public address, never `.env` or a private key:

```bash
.venv/bin/python run_meme_live.py --wallet YOUR_PUBLIC_WALLET --loop
```

Unsigned mode rejects `sendTransaction` at the RPC boundary and stores diagnostic
observations in `state/meme_unsigned/`; it creates no positions or fills.
`--mint ADDRESS --no-discover` restricts discovery to explicit mints plus watched
leads. `--watchlist` and `--wallet-source-dir` select the shared public collector.
The live profile now accepts **three completed contiguous one-minute candles**
(`trend_min_bars: 3`). A rise between two completed candles with higher support, followed by a
completed declining candle can form the short-launch setup; existing gain,
pullback, social, volume, volatility and execution gates still apply. This is a
short-window hypothesis, not proof of sustained momentum. Up to 12 available
completed bars are used, with up to six run-up bars when history permits.
Two candles are insufficient. Missing intervals or partial candles are never
filled with made-up data. The reader requests 13 candles to allow one unfinished
minute and reports raw/partial counts. Existing paper experiments retain their
saved eight-bar policy for comparison.

A changed policy must be adopted explicitly by a **flat** runner:
`--adopt-rules` journals the change while preserving P&L, peak equity and any
existing drawdown halt. It refuses changes with open positions, pending intents
or unresolved submitted orders. Stop the old process and add this flag to your
usual invocation once; subsequent restarts use the saved matching rules.

The runner also keeps an automatic **candidate watchlist**, separate from the
public trader-wallet watchlist. It retains trend candidates awaiting a pullback
and socially active/high-buy-volume candidates with temporary entry rejections.
Up to three of the ten scan slots revisit retained candidates after a minimum
60-second delay, even if their mint disappears from current incoming feeds.
Actual revisit time includes scan and source latency. Retention expires after
30 minutes without extending the original expiry on every observation; capacity
is 100, with older due observations prioritized. Fresh incoming leads can add a
coin again after expiry. Confirmed buys, unsupported markets and known on-chain
concentration/coordination flags remove candidates. Temporary provider failures
do not certify or permanently blacklist them. Every revisit fetches fresh
market/social observations and reruns normal entry, safety, cost and execution
checks. A retained coin is never an approved order. The SQLite table is
`candidate_watchlist` inside the runner's existing state database.

Per-token console analysis shows momentum, measured buy volume, watched buyers,
fresh callouts/authors, liquidity, actual on-chain check status, decision/reason
and retention. It never infers increasing volume from a single aggregate or
labels unperformed risk checks passed. Token labels have terminal control
characters removed. Detailed decisions/status remain available with `--json`.

An equally weighted **shadow scorecard** now records price structure, buying
demand, market depth and community interest before entry screening. Correlated
social inputs stay within one family; missing data remains unknown and weights
are not redistributed. The score is not a profit probability and currently
changes no order decision. Its purpose is to compare a combined-signal
hypothesis with the existing veto-based policy before replacing that policy.
Safety and dollar limits remain hard constraints.

`adaptive.py` evaluates the older hourly strategy; it does not train this meme
runner. Inspect confirmed meme outcomes with
`.venv/bin/python meme_results.py`. It reports wins, losses, breakeven trades,
average net outcomes, failed-transaction fees, realized P&L and unresolved/open
inventory flags. No-trade win rates/expectancy are unknown, not fictitious
success. Closed-trade averages exclude open inventory and are descriptive;
there is no automatic live optimization/promotion or established positive edge.

Each flat-wallet cycle selects up to **10 unique candidates** and then rests
**30 seconds** by default (`--interval` changes this rest). Fewer available
leads or an accepted/pending order can end a scan earlier. Open positions and
pending orders bypass new-entry scans, with at most 15 seconds of rest between
checks; source/RPC latency adds to the elapsed check interval. Existing rate
limit cooldowns still apply. Restart an existing process to load these defaults.

The console uses readable timestamped progress, candidate decisions and scan
summaries by default. The scan summary shows evaluated candidates, accepted
entries, rejected entries, confirmed buys and grouped rejection reasons. A coin
can contribute to multiple reason categories, so category totals can exceed
rejected entries. Unsupported markets and source errors are separate from
candidates actually evaluated. Console timestamps use the host local timezone
and include its timezone label. `--json` restores JSON console records. Unsupported markets
are reported as skips, separately from provider/data errors. `status.json`
and the SQLite event ledger remain structured. History diagnostics distinguish
fewer than the configured number of completed 1m candles, missing provider data, malformed prices,
and missing consecutive intervals. Rejection diagnostics do not relax limits.

The separate **user-operated** signed mode reads the owner-only `.env` through
`LiveConfig`. This command can spend real funds and is never started by tests:

```bash
.venv/bin/python run_meme_live.py --execute --loop
```

Signed state lives in `state/meme_live/`, with wallet/rules/mode binding and an
exclusive wallet lock shared with the legacy runner. Do not run both. `.env`
changes cannot silently bind old positions to a different wallet or policy.
Native SOL and existing WSOL can fund entries; PumpSwap creates the user's ATAs
and wraps only the shortfall. Sale proceeds remain WSOL for reuse. USDC is
valued as cash but is **not automatically converted** to SOL by this runner.
No paper balance, simulated position or fictitious fill is imported.

An intent is reserved before signing, and signed bytes are journaled before
submission. Uncertain sends block replacement orders across restart; do not
remove state files to bypass them. Confirmed transaction deltas determine
quantity, cost basis and P&L, including network and nonrecoverable setup costs.
Rent in the user's token accounts remains principal and is included in equity;
no automatic real-account closure or rent claim is performed. Restart the same
command with the same wallet/rules/state to reconcile an interrupted order.
Stopping the process preserves positions and **does not sell them**. Rules are
fixed for a funded state; trade logs do not automatically retrain the policy.

Validation artifacts under `state/meme_unsigned*validation*.json` and
`state/meme_unsigned_round_trip.json` record read-only mainnet checks. Real
Token-2022 PumpSwap and bonding-curve atomic buy/sell simulations passed; no
transaction was signed or submitted. Fixture tests cover order recovery,
failed/uncertain receipts, dollar exits, rent and supported instruction layouts.
These are execution checks, not a funded performance record or a guarantee.

Official interface references: [Pump curve V3](https://github.com/pump-fun/pump-public-docs/blob/8cda1fa30ea658b20909d8aedf002047119388d2/docs/instructions/TRADE_V3.md)
and [PumpSwap V2](https://github.com/pump-fun/pump-public-docs/blob/8cda1fa30ea658b20909d8aedf002047119388d2/docs/instructions/PUMP_SWAP_TRADE_V2.md).
The manifests record exact IDL hashes and source commits.

The older notes below describe the legacy runner and development history;
limits in this section and the selected rules file govern the new runner.


The conversion is incomplete. The existing live bot remains a USDC-accounted
spot trader. No funds were converted by this change. The direct PumpSwap
adapter supports SOL/USDC only; arbitrary meme/SOL pools need a separately
validated execution adapter. Existing venue restrictions remain in force.

New meme entries now require both the existing mature-token risk screen and
`MemeEvidencePolicy`. The scanner shows the evidence result and reasons;
the portfolio rechecks it when loading entry market data. Existing positions
retain their exits even if entry screening fails.

The policy reads `data/markets/<mint>/meme_evidence.json`. Missing evidence
blocks entry. No collector currently produces this file. This is a consumer
contract, not functioning social monitoring or independent bundle detection.
Do not populate unknown fields with zero or use the synthetic test fixtures
as real evidence. Refreshing candle data does not refresh this evidence.

The evidence object requires exact `mint`, exact `pool`, and `observed_at`
(Unix seconds), plus these three objects. Each requires its own `observed_at`
and HTTPS `source_url` for audit:

| Object | Required measurements | Default gate |
| --- | --- | --- |
| narrative | topic, independent_sources (source IDs), unique_authors, organic_engagement_pct | nonempty topic; 3 distinct sources, 20 authors, 70% organic |
| distribution | launch_history_complete (boolean), supply_coverage_pct, bundled_supply_pct, linked_supply_pct | complete launch history, 95% coverage, zero detected bundled supply, linked supply <=10% |
| trading | wash_volume_pct, price_change_1h_pct, liquidity_drop_1h_pct | wash <=10%, price rise <=50%, liquidity decline <=10% |

Every timestamp must be no more than five minutes old and cannot be in the
future. Percentages are 0–100 except signed price change (minimum -100).
These thresholds are provisional rejection rules, not a calibrated risk model.
Source IDs and organic/wash estimates must be verified by the future collector;
this validator cannot establish that independently. Wallet links and bundles
can remain undetected. Passing never proves a token is free of manipulation.

Historical conversion worklist (see current implementation and limitations above):

- Use pump.fun as the primary narrative/discovery source and implement
  timestamped, mint-linked observations, historical topic momentum and ranking. Repeated posts must not count as
  independent sources; assess coordinated activity and ambiguous ticker names.
- Implement bundle and wallet-cluster analysis with explicit launch coverage,
  funding links and ownership changes. Missing history must remain unknown.
- Validate a permitted direct meme/SOL adapter, reversible quotes, token
  extensions, fees, account rent, simulation and chain-confirmed accounting.
- Add SOL-denominated inventory and USDC equity accounting together. Keep
  native SOL for fees separate from WSOL trade capital. Retain USDC cash;
  convert only bounded amounts required by eligible trades, using a durable
  conversion journal and shared wallet lock. Do not reuse the completed
  SOL-to-USDC setup journal for the reverse conversion.
- Agree on risk limits, including a full-position-loss cap, combined exposure
  per narrative, daily realized loss, drawdown, liquidity deterioration,
  slippage and exit-failure handling. Existing live limits still apply:
  1% startup allocation, 5% per-token ceiling, 10% total exposure, 0.5%
  intended stop risk and 10% peak drawdown halt. Stops cannot bound rug losses.
- Replay historical observations without future leakage, then paper trade
  with live quotes and costs before enabling real entries.

## Agreed paper strategy

Include new launches in research without enabling live launch trades. Primary
narrative leads come from pump.fun creator metadata and its X post/profile or
community links. No paid API keys. Paper narrative leads do not require X or
Reddit readership metrics: the stricter social corroboration requirement stays
on the existing live meme gate. Distribution, bundle and buyer-quality evidence
is still required for paper entries. Metadata cannot supply that evidence.

User rules are at least **$5,000 recent buy volume** and an entry zone around
**a 40% pullback from ATH**, rather than an exact bottom prediction.
`meme_dip.py` implements a configurable **35–50% pullback zone** as the initial
paper setting. Both bounds include their endpoints. These bounds are test
assumptions, not thresholds attributed to the video.

Recent volume defaults to one rolling hour. Rebound confirmation defaults to
5% above the lowest observed price while the setup remains inside the zone;
set `rebound_fraction` to zero to enter without rebound confirmation. A price
outside either zone boundary clears the setup. A later return must qualify
again, preventing a recovery from a much deeper collapse from reusing an old
low. Changed ATH or failed screening also clears the setup. Launch-to-present
ATH provenance remains mandatory: direct Pump observations use its explicitly
reported USD market-cap ATH; historical replay can instead supply complete
launch-to-present price history.

Example JSON overrides for `paper_memes.py --rules /path/to/rules.json`:

```json
{
  "min_dip_fraction": 0.35,
  "max_dip_fraction": 0.50,
  "rebound_fraction": 0.05
}
```

These bounds replace the previous single `dip_fraction` setting. Entries must
remain inside the configured zone after any rebound; the old 63%-of-ATH cap
has been removed. The paper report records the actual pullback at entry.
Bankroll determines the dollar allocation and intended stop-risk budget.
Fixed execution costs can still make a small allocation ineligible.

Other provisional paper settings: $50,000 minimum liquidity; one open position;
1% allocation ceiling; 0.25% intended stop-risk budget; 15% stop; 2R target;
5% peak-equity drawdown halt; 0.3% fee and 0.5% adverse slippage per side.
All are configurable using a JSON file of `DipRules` fields. These are test
assumptions, not user-approved live limits or estimates of future returns.
The old replay command defaults to 100 virtual dollars; persistent runs require
explicit inventory. The current user-reported total bankroll is **$6.50**,
including reclaimed SOL rent (see `paper_bankroll.json`). This is independent
of the existing $5,000 live sniping capital threshold.

Buyer-quality gates require >=95% analysis coverage, >=20 independent buyers
**after clustering**, and <=30% suspected bot buy volume. These are estimates:
wallets are not people, common funding alone does not establish ownership,
and bots may evade detection. Do not equate a raw wallet count with independent
buyers. Exit decisions continue when new-entry evidence becomes unavailable.
Gaps fill at sampled quotes with modeled costs, not at an unavailable stop.

## Free pump.fun narrative collector

```bash
python3 pump_narratives.py --recent
python3 pump_narratives.py --mint EXACT_SOLANA_MINT
python3 pump_narratives.py --input /path/to/saved-pump-metadata.json
```

These commands load no `.env`, wallet or signing dependencies. State is separate
in `state/narratives/`. They validate metadata mint shape and requested mint
binding, normalize X links, group shared anchors and copied descriptions, and
keep first observation times. Links to arbitrary websites are not followed.
One hour of cached leads remains visible with its original observation times;
provider errors mark coverage incomplete. A shared link may indicate a copycat,
not independent narrative strength. Chain mint identity is still unverified.

The public pump.fun frontend endpoint is undocumented, rate-limited to one
request every two seconds in this collector, has a 15-second timeout and a
2MB response bound. There is no login, proxy rotation, key purchase or paid
fallback. The initial sandbox probe returned `URLError`; subsequent read-only checks
outside that sandbox returned HTTP 200 from both public services (see source
diagnostics below). Offline parsing and failure handling were also tested.
X links are collected; X timelines and Reddit are not scraped by this change.
Reddit has announced RSS retirement on November 13, 2026, so it is not the
foundation of the data pipeline.

## Paper replay

```bash
python3 paper_memes.py /path/to/observed-snapshots.jsonl --balance 100
```

This replays snapshots chronologically and writes `state/paper_memes/report.json`.
The replay command remains available for recorded snapshots. The persistent
`run_paper.py` loop described below also supports public market observations
and an externally produced JSONL feed. Supplemental safety/history collectors
must provide the following JSON object per line:

- `mint`, `pool`, `observed_at` (Unix seconds), `price_usd`.
- `ath_price_usd`, `ath_checked_at`, `ath_source_url` and
  `ath_history_complete_since_launch` (true only with verified full coverage).
- `buy_volume_usd` (buys only), `buy_volume_window_seconds` (3600 by default),
  `buy_volume_coverage_pct`, `liquidity_usd`, `fixed_cost_per_side_usd`.
- `token_safety_passed` and `safety_checked_at`: a separate verified screen of
  mint/freeze authorities, token extensions, holder/creator concentration and
  sellability. This replay accepts the supplied result; it does not perform
  those checks independently.
- `evidence`: the identity-bound `MemeEvidencePolicy` object above. Paper needs
  only topic/timestamp/source URL in its narrative section. Its trading section
  additionally requires `buyer_analysis_coverage_pct`, `independent_buyers`,
  and `suspected_bot_buy_volume_pct`.

Never create zero values to replace unknown bundle/bot/wash measurements.
The metadata collector intentionally does not generate this safety object.
Without fresh launch-history and buyer-analysis evidence, actual
paper entry eligibility remains blocked. No USDC conversion is needed to test the strategy.

Sources:
[pump.fun metadata fields](https://github.com/pump-fun/pump-fun-skills/blob/main/create-coin/references/METADATA.md),
[Reddit RSS retirement](https://www.reddit.com/r/modnews/comments/1wubgvt/continuing_our_infrastructure_updates_whats/),
[X API pricing](https://docs.x.com/x-api/getting-started/pricing).


## Strategy reference and tracking scope

Use the entire [video supplied by the user](https://www.youtube.com/watch?v=vYyOchs9P_g)
as a conceptual reference for a small-portfolio workflow. No timestamp is
required. The video transcript could not be retrieved in this environment;
the list below is the implementation tracking plan based on the conversation,
not a claim that every field or threshold was taught in the video. Do not
substitute a different upload with a similar title as evidence of its contents.

Pump.fun is the primary market-filter and narrative-discovery surface. Record
its observed filter values and units before translating them into bot rules.
Distinguish the website's displayed filter settings from the underlying data
endpoint: an endpoint error does not identify an entire trading venue as
unavailable. The specific market the user rejected after a 404 is excluded;
its identity is not recorded here, so no additional venue is inferred or removed.

| Area | Information to retain | Current implementation |
| --- | --- | --- |
| Token identity and lifecycle | Exact mint, creator, creation time, bonding-curve state, migration state and exact pool | Metadata mint validation exists; chain identity/lifecycle collection pending |
| Narrative and community | Description, linked X post/profile/community, topic, source age, copied descriptions and reused social links | Metadata text, canonical X anchors and copy/shared-link grouping exist; content/ownership verification pending |
| Pump.fun filters | Filter name, selected value, units, window, source and observation time | Tracking specification; filter capture pending |
| Buying demand | Buy-only USD volume, sell volume, buy/sell counts, net flow and rolling window coverage | Paper entry checks supplied buy volume and coverage; market collector pending |
| Buyer quality | New and returning buyers, wallet funding links, clustered buyers, repeat buy/sell patterns and estimated automated share | Paper gate accepts supplied clustered-buyer/bot estimates; independent analysis pending |
| Holder and creator risk | Largest holders, top-holder share, creator holdings/sales, linked-wallet share, bundle evidence and coverage | Existing mature-token screen plus supplied evidence gates; new-launch analysis pending |
| Market depth | Executable liquidity, liquidity changes, position impact, reverse sell quote and route availability | Paper liquidity check exists; executable meme-pool quotes pending |
| Price structure | Current price, complete-history ATH, ATH time/source, drawdown, dip low and rebound from that low | Paper dip/rebound state machine exists; verified ATH collector pending |
| Valuation | USD market cap, circulating supply, fully diluted value and their provenance | Tracking specification; collection pending; no valuation-based entry threshold agreed |
| Small-portfolio economics | Spendable cash, native fee reserve, account rent, network fees, swap fees, slippage and net target after costs | Paper sizing/cost screen exists; live meme execution costs pending |
| Outcomes | Entry/exit reason, observed versus simulated fill, fees, realized net result, exposure, drawdown and rejection reasons | Chronological paper replay report exists; persistent polling/feed loop implemented; strict evidence still required |

For every market measurement retain its exact mint/pool, observation time,
source, units, window and coverage. Preserve missing values as unknown. Buy
volume is distinct from total trading volume, market cap and liquidity. An
unknown automated share cannot be recorded as zero, and a wallet count cannot
be labeled a count of people. Narrative associations are leads, not endorsements.

Confirmed user strategy rules remain $5,000 minimum recent buy volume and an
entry zone around a 40% ATH pullback to avoid chasing spikes. The initial
35–50% zone, one-hour volume window, optional 5% rebound confirmation and
other numerical paper settings remain configurable test assumptions. The
video link alone does not authorize changing them or enabling real-money trades.


## Branding and duplicate research

Originality verification is no longer an entry requirement. An `originality`
section may be omitted; missing, stale, unverified or copycat labels do not
veto an otherwise eligible entry. Mint identity still must match the selected
market and every source. Duplicate names, tickers, descriptions and social
links remain research annotations, not automatic rejections.

The focus is now price structure and active demand. Paper entries require six
completed **one-minute** candles: a positive net close move, at least 60% rising
close-to-close steps, and a final low above the first low. Every candle must
have activity. Initial configurable rejection limits are a 30% single-candle
range, 10% opening gap or quote deviation, and at most 34% wick-dominated bars
(where wicks exceed 80% of candle range). These are provisional paper filters,
not thresholds from the video or a fraud classifier. Individual red candles
are allowed within a rising trend. The existing pullback zone, volume,
liquidity, position sizing and other safety checks remain active.

`candle_history` binds exact `mint` and `pool`, HTTPS `source_url`, `retrieved_at`,
`interval_seconds: 60`, and a chronological `candles` array. Each row contains
`timestamp` (candle-open Unix seconds), `open`, `high`, `low`, `close` and
`volume_usd`. Only completed, aligned, contiguous candles qualify; missing
history blocks new entries but never disables an existing position's exits.
The legacy live hourly strategy applies the same structural checks at its
hourly interval; it has not been switched to the paper dip strategy.


## Persistent paper runner, restart and reset

`run_paper.py` is the persistent entry point. It uses Python's standard library,
never reads `.env` or loads wallet keys, and has no transaction submission path.
Its SQLite ledger, process lock and reports are separate from live trading.

Start from observed wallet inventory using explicit **virtual** USDC and native
SOL amounts. These example amounts are illustrative; substitute the actual
spendable balances and current SOL/USD valuation to match a wallet:

```bash
python3 run_paper.py --balance 10 --native-sol 0.006 --sol-price 100 --feed /path/to/snapshots.jsonl --loop
python3 run_paper.py --feed /path/to/snapshots.jsonl --loop
python3 run_paper.py --status
```

The second command resumes the existing run. Starting inventory and rules
cannot change on resume. The feed must contain one complete JSON object per
newline. Partial trailing records wait for completion. Identical records are
idempotent across restarts; changed records with duplicate/out-of-order times
are rejected and journaled without changing positions or cash.

Native-SOL mode requires an additional `execution` object per observation:
exact `mint` and `pool`, `observed_at` (at most 30 seconds old relative to the
snapshot), `sol_price_usd`, `network_fee_sol`, `entry_rent_sol`, `exit_rent_sol`,
`buy_route_available`, and `sell_route_available`. These must be supplied route
and cost estimates, not default success flags. USD cash and native fees/rent
are debited separately. Entries leave the configured native fee reserve plus
estimated exit costs. Exits can spend that reserve; unavailable sell routes
or insufficient native funds preserve the open position and report failure.
Refundable account rent is debited from spendable SOL and tracked as locked
principal. Reused simulated accounts avoid a second rent deposit. After exit,
`--reclaim-paper-rent` returns eligible simulated rent with zero trading profit.
Legacy observations without the refundable flag retain cost treatment. Entry sizes include modeled fees/slippage and fixed costs in the
intended risk budget, preserve a $1 USDC reserve by default, and scale with
portfolio equity. Change `DipRules` through `--rules` on a new run/reset to
match the intended live policy. No automatic USDC/SOL conversion is simulated.

For explicit simplified scenarios, `--assume-usd-costs` permits starting without
a native ledger; its reports say `assumed_USD_costs`. It is not wallet parity.
The previous `paper_memes.py` replay command is also a simplified USD-cost
scenario. Neither mode claims executable swaps, measured latency or real
profitability. The one-position paper cap and configurable risk limits model
the new meme strategy; the separate live SOL strategy's promotion/Kelly logic
is not silently imported or claimed equivalent.

Reset archives the previous run and its complete event/snapshot history in
`state/paper_memes/paper.sqlite`. It starts a new run ID, cash/native inventory,
positions, armed setups and drawdown state. It never edits live state:

```bash
python3 run_paper.py --reset --balance 10 --native-sol 0.006 --sol-price 100
```

Reset requires an explicit bankroll. Stop the loop first: its single-writer
lock prevents a second process from resetting an active session. To redo the
same experiment, reset and then replay the same feed. To keep two experiments
active independently, use distinct `--state-dir` directories. Old events are
retained in the ledger, not deleted or relabeled as a new experiment.

Reports in `state/paper_memes/report.json` include the run ID, inventory,
marked equity/as-of time, open position, net realized P&L, event counts,
rejection reasons, source errors, and archive count. Marks may be stale during
an outage; an open position is never assumed liquidated because a feed failed.
Cash/fill state and its event commit in one SQLite transaction. Missing price
data never counts as an exit, and open holdings remain first in the poll queue.

## Public observation mode

```bash
python3 run_paper.py --balance 10 --native-sol 0.006 --sol-price 100 --discover --evidence-dir /path/to/proofs --loop
python3 run_paper.py --mint EXACT_SOLANA_MINT --evidence-dir /path/to/proofs --loop
```

Pump.fun is now the primary paper market source (`PumpMarketSources`), in
addition to discovery, watched wallets and Hot Callouts. The adapter reads the
same anonymous endpoints observed in Pump's public web client:

- Frontend `coins-v3/<mint>`: token identity, price, USD market cap, current
  market/curve identity and canonical-pool liquidity.
- Frontend `candles/<Solana CAIP chain>/<mint>?res=1m&measure=price_usd`: completed
  one-minute **USD token-price** candles and USD volume. Returned resolution
  and measure must match exactly; 5m or market-cap bars cannot masquerade as 1m
  token prices. Forming bars are discarded; gaps are not filled.
- Swap API `v1/coins/<mint>/market-activity?program=pump`: distinct rolling-hour
  buy and sell USD volumes and other provider activity windows. A provider
  aggregate is labeled as such, not an independently reconstructed trade tape.
- Swap API `v1/coins/<mint>/ath?currency=USD&program=pump`: explicitly reported
  USD market-cap ATH. Pullbacks and rebounds compare current market cap to that
  ATH in the same units; sizing and fills continue to use token price in USD.
- Frontend `token-holders/<mint>/count`: mint-bound holder count. Holder count
  is retained as information, not relabeled as independent buyers.

All these endpoints returned HTTP 200 in read-only checks, and the complete
adapter parsed a live observation with no source errors. The sampled chart
was rejected for falling closes/support, demonstrating that available data
alone does not authorize an entry. Frontend routes are not a supported API
contract; errors and schema/unit mismatches remain visible. No paid fallback,
wallet sign-in or undocumented credential is needed by these readers.
GeckoTerminal remains in the repository for legacy SOL/history/scanning code;
it is no longer the persistent meme paper runner's primary market provider.

The observer labels bonding curves and migrated pools separately and refuses
silently changed market identities. Observing a curve or custom quote asset
does not validate a trading adapter for it. `program=pump` is the currently
verified Pump data path; other protocols displayed in Pump's UI are not
implicitly authorized for execution.

Supplemental `<mint>.json` files in `--evidence-dir` bind exact `mint` and `pool`
and supply safety, `evidence` and `execution` fields. Their timestamps are
preserved. They cannot overwrite Pump market price, volume, history or ATH.
Originality proof is no longer required. Bundle/linked-wallet and buyer-quality
checks remain; a default provider security label or a chart pattern is not
silently converted into passing proof for those fields. Auxiliary history or
activity failures do not discard a valid current quote needed by an open
position, but do block entries requiring those fields.


## Reproducible synthetic smoke run

This creates explicitly synthetic data, never market evidence. Choose a new
directory; the generator refuses to overwrite an existing one:

```bash
python3 paper_demo.py --directory /tmp/meme-paper-demo
python3 run_paper.py --state-dir /tmp/meme-paper-demo/session --balance 10 --native-sol 0.006 --sol-price 100 --feed /tmp/meme-paper-demo/synthetic.jsonl
python3 run_paper.py --state-dir /tmp/meme-paper-demo/session --feed /tmp/meme-paper-demo/synthetic.jsonl
```

The example exercises dip, rebound, buy and exit with native fee accounting.
The second pass must not create additional trades. Reports label this data
`synthetic`; its P&L is a software test result, not strategy evidence.


## Watched wallets and source diagnostics

Wallet following is an additional paper-discovery source. It does not copy
position sizes, authorize entries, rank traders by profit, or establish that
the watched wallet is a person. Supply public Solana addresses from pump.fun:

```bash
python3 run_paper.py --watch-wallet PUBLIC_WALLET_ADDRESS --loop
```

This resumes an existing paper session. On first use also provide the same
explicit starting inventory/rules arguments as the normal runner. Repeat
`--watch-wallet` for up to 32 addresses, optionally with `--discover` or
`--mint`. Public wallet sources are separate from `--feed` replay mode. The saved watchlist is enabled by default for public paper discovery/market
polling and for the live runner; offline feed replay does not consume it. `--watch-rpc` optionally selects a public
HTTPS mainnet RPC; the default is `https://api.mainnet-beta.solana.com`.

The watcher checks mainnet genesis, rotates through at most three wallets per
poll, samples up to 20 recent wallet-referenced signatures per selected wallet,
and analyzes at most four unseen transactions per selected wallet per poll.
The rotation cursor survives restart. Reports identify each selected wallet
and whether every watchlist member has been checked recently.
Each transaction must be confirmed, successful, signature-matched, no older
than five minutes, and signed by the watched wallet. Pinned official Pump and
PumpSwap buy/sell instruction layouts must bind that same user and exact mint.
Net wallet token changes must agree with the instruction and quote-spending
or quote-receipt direction. Top-level and inner instructions are inspected.
Transfers/airdrops, unknown instructions, ambiguous multi-trade transactions,
and unsupported quote mints are not promoted into buy leads. Native quote
changes can include rent/other movements, so they are not exact trade costs
or realized profit measurements.

Observations persist in the shared `state/wallet_watch/wallet_watch.sqlite`
cache consumed by both paper and live runners. Signature/wallet deduplication survives restart. A later observed
sell removes that wallet's current buy lead; opposing actions at the same
second are treated as ambiguous. Counts describe watched wallets, never
independent humans. Coverage is explicitly a bounded recent sample: polling
can miss transactions, unavailable receipts retry, and deferred work is
reported. This is not complete trade history or a P&L leaderboard. Resetting
a paper run archives its simulated positions/events; the separate observational
wallet cache remains deduplicated and age-limited, and cannot replay old buys
as new trades.

Watched-wallet mints enter the regular market-observation queue, with the same
volume, liquidity, ATH, candle-quality, bundle and buyer-quality gates. They cannot
populate or bypass the independent-buyer safety field. Reports include wallet
signals and per-wallet source failures. Open positions are quoted before both
wallet/discovery work, so a slow research source cannot postpone their first
exit check. Trading amounts and exits remain the bot's own paper decisions.

Network diagnostics now distinguish DNS resolution, TLS/connection failures,
HTTP 404/not-indexed markets and HTTP 429 rate limiting. A 429 sets a per-host
cooldown using Retry-After (bounded to 30–300 seconds); subsequent requests
report cooldown without hammering that host. Credential-bearing URLs and raw
network exceptions are not included in error reports.

Live read-only verification on October 28, 2026 established HTTP 200 responses
from the pump.fun coin list and GeckoTerminal token/pool endpoint outside the
execution sandbox. Inside the sandbox, all tested public hosts failed DNS
resolution; this is an environment limitation, not evidence of bad endpoint
URLs. Newly launched tokens can separately return provider 404 before indexing.
Sampled custom pump-coin quote pairs were correctly outside the current
SOL/USDC-quote support boundary; no new quote asset or venue was enabled to
force an eligible trade. A bounded public-wallet probe completed without RPC
errors but found no recent transactions to classify; instruction parsing is
covered by synthetic tests, not claimed verified from that empty sample.

The instruction layouts are pinned in `pump_watch_specs.json`, drawn from
[official Pump IDLs](https://github.com/pump-fun/pump-public-docs/tree/main/idl).
The RPC sequence follows Solana's official
[getSignaturesForAddress](https://solana.com/docs/rpc/http/getsignaturesforaddress)
and [getTransaction](https://solana.com/docs/rpc/http/gettransaction) documentation.


### Configured user watchlist

All eight supplied public addresses are saved in `paper_watchlist.json`, without
profitability scores or assumed independence. Resume the paper runner with:

```bash
python3 run_paper.py --watchlist paper_watchlist.json --loop
```

For a new session, add explicit `--balance`, `--native-sol` and `--sol-price`
values as described above. The addresses can be edited in that JSON file.
At three wallets per poll, all eight are visited within three polls in normal
operation; actual cadence includes RPC request time and throttling. The
five-minute signal window never extends because a wallet was not polled.


## Shared wallet data for paper and live

The eight user-provided wallets now feed **both** modes through one
credential-free collector (`wallet_signals.py`). The historical filename
`paper_watchlist.json` is shared configuration, not a paper-only limitation.
The collector owns a single process lock, rotated RPC schedule and deduplicated
SQLite cache under `state/wallet_watch/`. Its atomic `status.json` is the only
wallet input consumed by the runners. Existing default paper-cache observations
are copied into the shared ledger once, without deleting the original cache.

Both runners start the same worker when needed. The worker's environment is
restricted to PATH, LANG and unbuffered output; live wallet/RPC credentials
are not inherited. A second worker exits on its single-writer lock. Trading
loops read cached data without making wallet RPC requests or waiting for the
research worker. Source failures never stop reconciliation, stops or exits.

```bash
# Paper public polling automatically uses the saved watchlist.
python3 run_paper.py --watchlist paper_watchlist.json --loop

# The normal live runner also uses it by default; this command CAN trade.
python3 run_live.py --events data/live_pool.jsonl

# Run just the read-only shared collector, with no trading loop.
python3 wallet_signals.py --watchlist paper_watchlist.json --loop
```

These are separate roles: starting the collector does not start live trading.
The live command is shown for configuration context and was not run as part
of wiring the data source. The shared worker writes `collector.log`; it can
continue after a runner exits. Existing paper starting-inventory requirements
still apply. Offline `--feed` replay remains isolated from current wallet data
so it cannot use future information from a live cache.

Live portfolio behavior: fresh wallet-buy observations prioritize assets
**already approved for live trading**, while leaving every approval, strategy,
position size, risk limit, screening gate and exit rule intact. Unknown mints
are reported as `unreviewed_candidates` in `state/last_status.json`; the watcher
does not add them to `assets.json` or give them execution access. Research
priority alone cannot trigger a buy. Existing meme venue/adapter restrictions
remain in force. The live strategy has not been switched to the paper dip
strategy by this data integration.

Cached reports older than 120 seconds, receipts older than five minutes,
future-dated data, foreign watchlists, unavailable wallets and mismatched
mint/action observations cannot contribute wallet priority. Counts are
recomputed from distinct watched-wallet observations rather than trusting a
cached aggregate. They remain watched-wallet counts, not independent-human
counts or proof of profitable performance. Paper and live readers use exactly
the same validation. The live status includes registered and unreviewed
wallet candidates even when trading is blocked for other reasons.

For a custom shared watchlist, pass the same `--watchlist` to both runners.
`--wallet-source-dir` selects the shared cache in either runner; use a distinct
directory for experiments with a different watchlist/RPC. Watchlist digest
mismatches fail closed instead of silently consuming another experiment's data.


## Main-page Hot Callouts

The shared worker now also reads pump.fun's anonymous main-page Hot Callouts
feed (`https://frontend-api-v3.pump.fun/home-feed`). This is the main-page feed
the user identified. A live unauthenticated HTTP 200 response was inspected
before implementing the parser. No browser login, wallet signing, cookies,
paid service or developer key is used. Pump.fun separately documents a free
approved-organisation API; that authenticated API is not required by this
frontend reader. The frontend route is undocumented and may change or stop
allowing anonymous reads; failures are reported without an authentication bypass.

`state/wallet_watch/callouts.sqlite` records callout IDs, exact mints, author
wallets, original posting times, observed times, theses, engagement and
provider-reported current positions/P&L. Text is untrusted source material,
not executable instructions. Performance numbers and a Hot ranking are not
independent proof of a profitable trader, original coin or human buying.
The parser excludes quote/repost entries, non-Solana assets, perpetuals,
future timestamps and malformed identities. A callout ID cannot switch its
mint, author or original posting time. Duplicate callout IDs do not inflate
interest, and raw engagement does not become a wallet-buy count.

Fresh callouts (within 15 minutes of original posting, refreshed within two
minutes) contribute candidates to **both** paper and live. Watched authors are
identified from the same eight-wallet configuration. Registry priority uses
confirmed wallet-buy observations first, then watched callout authors, then
other distinct callout authors. These are source-priority rules, not a trade
signal or an independent-human count. They never edit the asset registry,
supply bundle evidence, change risk or authorize a buy.
Paper snapshots retain the applicable research context in their event journal;
recorded/offline feed replay does not fetch current callouts.

The atomic shared report contains `callouts` and `callout_source`; live status
includes them in `wallet_watch`. A callout-source failure clears its candidate
contribution without removing a held position or blocking its exit. Original
posting timestamps remain unchanged when the feed refreshes. The historical
source cache is not injected into backtests as future information.

Sources: [pump.fun API access](https://docs.pump.fun/getting-started/) and
[callout/feed scopes](https://docs.pump.fun/scopes/). Anonymous frontend access
was observed directly and is distinct from the documented API-key service.


## Reclaimed rent and the $6.50 bankroll

The user-reported **$6.50 is total wallet equity including SOL**, not $6.50 USDC
plus SOL. Its source includes reclaimed SOL account rent. That SOL is eligible
trading principal, not excluded because it was a rent recovery. It is also not
strategy profit, a completed win or a Kelly observation. The recorded report
is in `paper_bankroll.json`; actual token units and valuation still need to be
matched when initializing the ledger rather than guessed from a USD total.

For total-equity initialization use `--total-equity 6.50` together with actual
`--native-sol` and current `--sol-price`. The runner computes USDC as total
value minus native SOL value, so it cannot double count the same funds. This
can be zero USDC if all capital is native SOL. `--balance` instead means USDC
alone and cannot be combined with `--total-equity`.

For SOL-quoted paper markets, spendable native SOL funds the trade directly;
network/rent costs and the trade input are debited from SOL, and sale proceeds
return to SOL. For USDC-quoted markets, USDC funds the input and native SOL
pays operational costs. Source/execution quote-asset mismatches are rejected.
The native fee reserve, bounded exit costs, accounting reserve and bankroll
risk caps still apply to either path. Only those operational amounts remain
reserved; rent-recovered principal is not categorically frozen. Paper tests
cover an all-SOL $6.50 starting portfolio buying and closing without USDC.
This is simulation support, not a live adapter approval or a real conversion.


## Running the authorized exploratory paper trial

The user authorized starting a live-data paper experiment with the reported
$6.50 total bankroll. `--exploratory` selects `evidence_mode: market_only` for
this paper run. This mode uses real Pump quotes, one-minute candle quality,
reported ATH/volume/liquidity and the normal bankroll, reserve, pullback and
cost gates. It does not require unavailable independent safety attestations
in order to study those market rules. Every event records the strict-entry
blockers separately and is explicitly **not qualified for live trading**.
Unknown bundle, wallet-cluster and buyer evidence remains unknown; no passing
proof is fabricated. Strict mode remains the default and live policy is
unchanged.

The initial virtual inventory is the $6.50 equivalent entirely in SOL at a
fresh Pump SOL/USD quote, with zero virtual USDC. This is a disclosed modeling
assumption based on the reclaimed-SOL funding report, not a verified reading
of the actual wallet's balances. Details, PID and valuation provenance are in
`state/paper_memes/runner.json`; the durable report is
`state/paper_memes/report.json` and the process log is `runner.log` alongside it.
No real-wallet credentials are inherited by the process.

`ModeledPumpSources` supplies explicitly labeled execution assumptions:
configured adverse slippage and fees, 20,000 lamports of network cost per side,
publicly measured account rent when fresh (otherwise 0.00203928 SOL),
and refundable rent tracked as locked principal. Existing canonical accounts
can avoid a new deposit when confirmed by a fresh public wallet snapshot. Only native SOL/WSOL and USDC quotes are modeled; other
quote assets are rejected. These are conservative simulation inputs, not
verified executable routes or exact measured costs. Current SOL valuations
must be fresh; missing sources do not create fills. Account setup can make
a small allocation uneconomic, and neither the sizing cap nor the cost gate
is relaxed merely to force paper trades.

After initialization the run can be resumed with:

```bash
python3 run_paper.py --watchlist paper_watchlist.json --discover --loop --interval 30
```

Do not run a second copy while its lock is held. Stop the existing PID before
resuming or resetting. The interval is a pause after collection, so actual
cadence includes throttled HTTP requests. View status without requesting the
runner's writer lock by reading `report.json`, `runner.json`, or the SQLite
ledger in read-only mode. Completed simulated buys/sells, rejected entries
and source errors are distinct; a running collector is never reported as a
completed paper trade. The process is session/host-dependent, not installed
as an operating-system service.


## Active paper variant: trend, popularity, early pullback

The user requested an approach closer to following market momentum and popular
views, then buying when a rising coin starts a controlled decline. This is now
a separate `entry_style: trend_pullback` paper experiment, configured in
`paper_trend_rules.json`. The prior ATH-dip run and its events/reports are
archived, not rewritten. The new trial starts with the same $6.50 virtual total
equity, modeled as SOL at its new start valuation. No live strategy was changed.

The provisional entry rules are:

- Use up to 12 completed one-minute price candles, with at least eight present.
  Validate identity, freshness, continuity, OHLC, activity, wicks, gaps and ranges.
- Identify the recent local high. Six candles ending at that high must have
  gained at least 5% in their closes, at least 60% rising close steps, and higher
  ending support. The current pullback candles need not themselves trend upward.
- Require fresh popularity evidence: a watched-wallet buy, a watched author's
  callout, or other fresh callouts with at least 100 reported views. IDs and
  authors are deduplicated. Views are an interest signal, not proof of organic
  participation, independent buyers or profitable traders.
- Buy on one to three completed declining bars, 2–15% below the recent price
  peak. No 35–50% ATH drop or rebound is required by this variant. A deep or
  prolonged decline is rejected; a still-rising peak stays on watch.
- Keep the $5,000 rolling-hour buy-volume requirement. Cap notional at 0.05%
  of reported pool liquidity instead of using the old fixed $50,000 depth
  threshold. The fixed liquidity field remains in the config for ATH-dip runs;
  it is not applied to this variant.

These are explicit hypotheses for testing the user's described approach, not
optimal thresholds or evidence that the approach is profitable. Candles cannot
establish the absence of manipulation. Exploratory safety remains unqualified.

The user-approved paper profile caps each position at $1.78 and targets $1
net profit, with a $0.20 net loss trigger, including modeled execution costs.
These absolute exits override the legacy percentage stop and 2R target. The
5% portfolio drawdown halt and 15-minute holding limit can close positions
before the profit target. Gaps and unavailable routes can exceed the loss
trigger. Liquidity limits, spendable funds and fee reserves can reduce size.
Direct Pump fees are modeled at 1.25% per side, plus adverse slippage and
network costs; these are assumptions rather than executable quotes.

Candidate ordering now considers watched buyers, distinct callout authors and
reported views rather than mint-address alphabetical order. Each pass retains
the top two ranked candidates and rotates remaining scan slots through the
other candidates, preserving its cursor across restart. Explicitly requested
mints and open positions retain priority. This avoids repeatedly spending all
requests on the same six tokens while preserving popular leads.

At $1.78 notional, a $1 net target requires a substantial gain. This paper
experiment does not establish profitability or live readiness. Unknown quote
currencies are skipped explicitly. Independent safety evidence and validated
live meme execution remain required before enabling real trades.

Resume the active profile with its saved rules, or explicitly supply them:

```bash
python3 run_paper.py --rules paper_trend_rules.json --watchlist paper_watchlist.json --discover --loop --interval 30
```

Use the normal single-writer procedure; do not launch a second copy while the
paper process is running. Current run ID/PID are in `state/paper_memes/runner.json`,
and current outcomes are in `report.json`. Archived reports are under
`state/paper_memes/archives/` with their original run IDs.
