# Solana spot trading bot

The meme execution runner is now `run_meme_live.py`: Pump trend/pullback signals,
locally built Pump/PumpSwap swaps, unsigned simulation by default, and an explicit
user-operated signed mode. See [setup, limits and execution scope](MEME_TRADER.md#current-meme-execution-runner).
The approved dollar limits are $1.78 per position, a $1 net profit trigger and
$0.20 net loss trigger, with a 5% portfolio drawdown halt. The supplied profile uses market/candle evidence plus a bounded on-chain
concentration and trade-clustering gate; full bundle coverage and independent
human-buyer verification remain unavailable. No real-money run has been launched or profitability established.

The following sections also describe the separate legacy runner and earlier
paper work. They do not change the new runner's selected rules.


Meme-trader conversion is in progress: see [MEME_TRADER.md](MEME_TRADER.md).
Meme entries now also require fresh narrative, distribution/bundle and trading
evidence. No collector supplies that evidence yet, so missing evidence blocks
entries. Paper entries use a configurable 35–50% ATH pullback zone, with
optional rebound confirmation and bankroll-based sizing. The persistent
paper runner now reads Pump-owned price/candle/activity/ATH/holder feeds, with
1-minute candles as the default. This change does
not convert USDC or enable live meme/SOL execution. Reclaimed SOL rent counts
as trading principal; SOL-funded paper trades are supported. The reported
$6.50 bankroll is total equity including SOL, recorded in `paper_bankroll.json`.

Paper trading now has a persistent runner with restart recovery, separate
USDC/native-SOL accounting and archived resets. See the commands and input
requirements in [MEME_TRADER.md](MEME_TRADER.md#persistent-paper-runner-restart-and-reset).
Public observation mode records missing-proof blockers; it cannot establish
bundle safety or human buying from metadata alone. Originality proof is no
longer required; entries use upward-trend and candle-quality checks instead.
Your eight supplied wallets in `paper_watchlist.json` now feed both paper and
live through one shared, credential-free collector. Live uses fresh activity
to prioritize already approved registry assets and reports unreviewed candidates;
wallet activity never copies positions or bypasses approval/risk checks.
The shared source also reads main-page Hot Callouts anonymously, keeping
callout authors/posts separate from confirmed buys. Offline paper replay
stays isolated from live data.

Current execution: direct PumpSwap SOL/USDC spot and atomic Orca→PumpSwap
SOL/USDC cycles. New Jupiter entries are blocked for the U.S. wallet after a
terms review. The 24-token registry is an allowlist, not a claim that every
asset currently has an eligible execution route. Sniping discovery unlocks at
$5,000; new-launch trade execution still requires a validated adapter.

Self-custodial, locally signed Solana spot trading through PumpSwap and keyless Jupiter, initially with
one open position. Five consecutive net-profitable confirmed round trips unlock
two open position slots for the same strategy. No exchange login or KYC
credential is used by the code.
Direct execution does not establish legal eligibility for every service/token.

The active $6.50 exploratory paper trial now tests trend/popularity followed
by an early pullback, using `paper_trend_rules.json`. The earlier ATH-dip
experiment is archived. It uses live Pump data and modeled
fees, with safety evidence explicitly unqualified. Check actual fill counts in
`state/paper_memes/report.json`; collection or rejection events are not trades.
Details and limits are in [MEME_TRADER.md](MEME_TRADER.md#running-the-authorized-exploratory-paper-trial).

## Current implementation and observed status

`run_live.py` connects the hourly strategy, durable single-position coordinator,
locally constructed PumpSwap V2 swaps, simulation, local signing, RPC submission,
and confirmed on-chain token-balance reconciliation. This is real-money code.
The explicitly approved one-time inventory conversion has been submitted and
confirmed; no strategy entry/exit round trip has been confirmed yet.

A mainnet readiness cycle verified pool
`Gf7sXMoP8iRw4iiXmJ1nq4vxcRycbGXy5RL8a8LnTd3v`, a USDC-base/WSOL-quote
pool, and read a 30-basis-point fee estimate from its flat fee config.
Initial readiness found 0.045508668 native SOL, no WSOL and no USDC. After
explicit user approval, setup created the WSOL account and converted exactly
0.039 SOL to 3.956426 USDC, leaving 0.005015228 native SOL and zero WSOL.
Setup rent was 0.00148844 SOL and the network fee 0.000005 SOL. This was an
inventory conversion, not a strategy entry or profit. Price and USD estimates
change continuously. No strategy profitability is established.

Remaining prerequisites for the first live strategy trade:

- Token and user-volume accounts are now present. Future swaps refuse implicit
  setup rent. Native SOL is an operational fee reserve, not an untouchable
  floor: buys must leave room for a bounded exit fee; sells may spend it.
- USDC is funded and the $1 accounting reserve applies. Native SOL above
  0.00502 blocks new entries as unprepared inventory (0.005 reserve plus
  20,000-lamport fee headroom). No further conversion is automatic.
- Exact-pool history is installed: 981 contiguous completed hourly candles on
  initial import. The active 20/10 Donchian signal needs 21 warmup bars. The 250-bar /
  10.5-day delay applied to the retired pullback signal and no longer applies.
- Validate successful swap simulation, signing and on-chain fills with prepared
  inventory. Unit tests are not proof of successful mainnet execution.

## Local setup and commands

Use Python 3.10+ on Linux. Install into the local virtual environment:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
```

If `.env` already exists, edit it rather than copying over it. Set
`SOLANA_RPC_URL` and exactly one of `SOLANA_PRIVATE_KEY` (Phantom-exported
base58 secret key) or `SOLANA_KEYPAIR_PATH` (Solana CLI JSON keypair file).
Never enter a recovery phrase. Keypair files must also be owner-only.
Secrets stay local and `.env` is gitignored. The public RPC is rate-limited;
production reliability has not been established. `SOLANA_WS_URL` can override
the WSS endpoint if the provider uses a different WebSocket URL.

Read-only checks:

```bash
.venv/bin/python wallet.py
.venv/bin/python pumpswap_status.py
.venv/bin/python inventory_plan.py
.venv/bin/python reconcile_setup.py
```

Collect confirmed trade events directly from Solana RPC, without ZeroSlip:

```bash
.venv/bin/python collect_pool.py --output data/live_pool.jsonl
.venv/bin/python refresh_history.py --loop
```

A reconnect writes a coverage reset. Partial initial/reconnection hours and
prior coverage are discarded, rather than pretending no trades were missed.
Historical archive import must independently verify completeness and program
identity. The collector currently supports the selected USDC-base/WSOL-quote
pool only. It validates on-chain pair identity before decoding amounts.

`refresh_history.py` fetches public GeckoTerminal OHLCV for the exact pool,
verifies its metadata against the on-chain pair, rejects wrong orientations,
duplicate/partial/invalid bars, discards history before the last missing hour,
and checks sample swap prices against confirmed on-chain transactions. Raw
responses, CSV hashes and sample signatures are preserved with
`data/verified_pool_hourly.manifest.json`. This is identity/schema/sample
verification of provider data, not independent reconstruction of every trade.
No empty hours are forward-filled. No exchange account/API key is used.
The runner checks provenance/hash and merges subsequent complete live candles;
the provider refresh can also bridge a collector outage without inventing bars.
Provider source documentation:
https://docs.coingecko.com/reference/pool-ohlcv-contract-address.

The live runner below can submit real swaps when every gate passes:

```bash
.venv/bin/python run_live.py --events data/live_pool.jsonl --once
.venv/bin/python run_live.py --events data/live_pool.jsonl
```

Run the collector separately while the runner is active. A missing or stale
feed blocks entries unless fresh verified provider candles are available.
ATR stops still run with unavailable candle history if a current pool quote
is available. Channel exits require candle history. RPC outages can prevent
exits. Stopping the process does not sell an open position. Exit monitoring is
local; no exchange-hosted stop orders are created.

`state/` holds private SQLite journals, a wallet/pool binding and a process lock.
Do not delete or edit state with outstanding orders or positions. An ambiguous
send is never automatically replaced. A crash after reservation but before
journaling preserves the slot for manual reconciliation. Confirmed transactions
must be fetched and parsed before applying a fill. Partial exits keep the slot
reserved rather than falsely declaring the position closed.

## Five-win promotion

Only fully closed, chain-confirmed strategy positions count. Net P&L is actual
quantity times the exit/entry fill-price difference, minus both recorded native
execution fees valued in USD. Pool fees and slippage are already embedded in
actual fill prices. A positive result above floating-point noise counts as a
win; losses and break-even reset the streak. A failed on-chain attempt resets
it too. Local signal/cost rejections do not count as trades. Setup conversion,
unrealized gains, historical studies and duplicate fill notifications do not
count. Pending or partial exits retain their slot and cannot count as a win.

Promotion is persisted in SQLite and scoped to the strategy identity. Five
consecutive wins unlock a maximum of **two positions**, not unlimited orders.
After promotion, **two consecutive completed net losses demote the cap to one
position without halting** and reset the loss streak to zero. Losses use the same actual-fill and recorded-fee accounting as
wins. Break-even resets both consecutive streaks; a win resets the loss streak.
At one position, **two consecutive net losses latch a manual-review trading
halt**. Before any promotion, that means the first pair of consecutive losses.
After demotion, it requires two **additional** consecutive losses; the losses
that triggered demotion are not counted again. Thus, a promoted bot with four
straight losses demotes after the second and halts after the fourth. A win or
break-even interrupts the consecutive loss streak at either level.
New entries stop immediately only when that halt triggers. Open lots retain
their normal stops/targets and channel exits; they are not forcibly sold by
the halt. Once positions and pending orders are cleared, the runner exits.
The collector/history refresher may keep running. Restarting the runner does
not clear the halt: it reads the persisted flag and stops again.

Resumption requires a deliberate `Coordinator.clear_review_halt()` after
review/tweaks and after all positions/orders are reconciled. The live loop
never calls it automatically. It resets promotion and win/loss streaks, but
does not reset the separate drawdown halt. After an explicit resume, five
new consecutive net wins are required to unlock two again.
Failed on-chain attempts reset the win streak but are not counted as completed
net-loss trades. Existing drawdown/entry-halt rules remain active. A new strategy
identity starts with one slot. Win/loss streaks and the cap persist across
restarts and are displayed by the runner.

Each lot has its own entry, quantity, ATR stop and take-profit. The coordinator
reconciles quantities separately for each mint, prioritizes exits, serializes
pending transactions and permits at most one entry per token per hourly signal
candle. Slots are shared across the approved live token registry. Per-token
and portfolio exposure limits still apply to the sum of all lots.

## Strategy and sizing

The active signal is **Donchian 20/10 breakout trend following**: a completed
hourly close above the preceding 20 hourly highs enters long; a quote below
the preceding 10 hourly lows exits. Channels exclude the signal candle. ATR
uses Wilder's 20-bar recurrence, with an initial stop two ATR below the actual
fill. New positions also take profit at **2R**: entry plus twice the initial
stop distance (4 ATR with the default 2-ATR stop). The stop, profit target and
strategy identity are persisted from the confirmed fill. Neither stop nor
target moves when later ATR values change. The target triggers a full exit
before candle-history/freshness checks; the channel exit remains active.
There is no 72-hour time exit for Donchian positions.
Quotes deviating over 0.5% from the signal close and candles more than five
minutes stale still block entries as execution safeguards.

This uses the Donchian channel family documented in the original Turtle rules,
with explicit adaptations: hourly bars instead of daily, close confirmation
instead of intraday breakout orders, long-only crypto spot instead of long/
short futures, no pyramiding, and the user's Kelly/capital constraints instead
of Turtle sizing, plus the user's requested 2R take-profit. The System 1
previous-winner skip rule is not implemented. It is not a complete Turtle reproduction or a crypto-native
invention. Primary rules:
https://www.tradingblox.com/originalturtles/originalturtlerules.pdf
and https://www.tradingblox.com/docs/users-guide/17-built-in-systems-and-blox/turtle-system-rules/.
The current strategy identity is `donchian-20-10-atr20-hourly-long-tp2r-cost1p5-v3`;
outcomes from the previous no-target strategy do not seed its Kelly history.
`DonchianRules.take_profit_r` controls the multiple; the evaluator also exposes
`--take-profit-r`. A target is a gross price level, not a guaranteed net profit:
the local runner must be online, and execution fees, slippage and delays apply.
The existing cost screen remains in force before entry.
The old custom pullback is retained only as `decide_pullback` / `EntryRules`
for comparison. Existing legacy positions keep their original exit rules.

Initial allocations are capped at 1% of equity while fewer than 100 completed
strategy observations exist. This is exploratory sizing, not Kelly or proof of
an edge. Quarter Kelly takes over afterward, using conservative win probability
and estimated costs. Rejected Kelly allocations do not fall back to startup.
The legacy standalone `bot.size` still requires 100 samples. Kelly histories
are scoped to the strategy identity; old pullback outcomes are not reused for
Donchian sizing. Historical candle import does not seed live Kelly outcomes.

Limits: one open position initially, two after five consecutive net wins,
including reserved pending buys; 5% maximum aggregate allocation per
token; 10% portfolio ceiling; $1 accounting reserve; 0.5% intended stop-risk
budget; 10% peak-equity drawdown halt for new entries. The take-profit move must
cover **1.5 times estimated round-trip costs**, including the slippage allowance.
Both strategy budgeting and final execution use the same multiplier from
`Risk.min_target_cost_multiple`. This reduced the previous 3x hurdle without
increasing the 1% startup allocation or allowing targets below modeled costs.
Existing lots are not topped up;
after promotion, a subsequent signal can create a separate second lot. The live runner includes native SOL in equity
and separately blocks unprepared native inventory. Native fee reserves still
fluctuate in USD. Kelly, stops and reserves cannot guarantee avoiding $0.

The latest policy permits established meme candidates before $5,000, replacing
the earlier capital-only exclusion. New/unknown memes remain blocked at any
balance. Mature-meme approvals must be fresh and expire after one hour; they
are not inferred from a ticker, token verification badge or age alone. The
two-meme-position ceiling and the stricter initial one-position cap still apply.
The registry supports the approved token identities subject to
venue eligibility, validated execution adapters, screening and execution checks; scanning approval alone does not grant live access.

## Execution boundaries

`pumpswap.py` uses the official program
`pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA` and locally builds one swap
instruction with exact input and a hard minimum output. It supports only
standard SPL Token SOL/USDC pools without creator fees, cashback, mayhem or
holder-reward features. It validates pool owner/discriminator/PDA, exact mints,
standard token-account authority/state/delegates and reserves, including signed
virtual quote reserves. Snapshot age is limited to 10 seconds, slippage to
0.5%, input to 0.1% of pool reserves and network/extra native spending to
20,000 lamports per swap. No address lookup tables or third-party transfers
are used. Message mutation, unexpected signers/programs, failed simulation,
and unexpected simulated account changes block signing. Pool fee estimates
must still be checked against actual simulation/fills; fee rounding may differ.

The vendored IDL is from
https://raw.githubusercontent.com/pump-fun/pump-public-docs/main/idl/pump_amm.json.
Official V2 account and bound semantics:
https://github.com/pump-fun/pump-public-docs/blob/main/docs/instructions/PUMP_SWAP_TRADE_V2.md.
The specification is pinned locally; future program/layout changes require
review rather than executing downloaded transaction-builder code.

## Evaluation and verification

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python inspect_market.py events.jsonl --pool-id POOL_ADDRESS
.venv/bin/python evaluate.py data/sol_usdc_pool_hourly.csv --notional .20
.venv/bin/python evaluate.py data/verified_pool_hourly.csv --notional .0446 --fee-per-side .003 --slippage-per-side .005 --fixed-per-side .000509
```

Historical evaluator CSV: Unix-second `timestamp,open,high,low,close,volume`.
Evaluation uses next-open fills, adverse slippage, per-side proportional/fixed
fees, gap-aware stops and stop-first ordering if both barriers are touched.
It separates development and holdout positions/signals and reports net P&L,
closed-trade drawdown, unfinished position estimates and signal skip counts.
It is a fixed-notional signal study, not a fully sized wallet simulator.
Defaults are illustrative; set `--fee-per-side`, `--slippage-per-side` and
`--fixed-per-side` from measured costs. Liquidity, latency, rent, failed
transaction fees and market impact still need measurement. A repeatedly tuned
holdout is no longer unseen data.

`data/donchian_report.json` preserves the first Donchian evaluation on the
installed exact-pool data, at $0.0446 fixed notional, 0.3% fee and 0.5% adverse
slippage per side, and an approximately $0.000509 network fee per side.
Development: four completed trades, about $0.010 loss; holdout: three completed
trades, about $0.008 loss. All seven lost under those assumptions. These are
historical signal-study results, not realized wallet losses or a fully sized
live simulation. The small sample does not establish an edge. Do not tune
against this holdout and continue calling it unseen data.

`data/donchian_take_profit_report.json` records the same-cost evaluation with
the requested 2R take-profit. None of the seven completed trades reached that
target, so results were unchanged. This is an implementation comparison on an
already observed sample, not fresh out-of-sample validation. No live position
or pending order existed when the take-profit update was installed.

The saved `data/sol_usd_hourly.csv` and `data/strategy_report.json` are old
Kraken public reference artifacts. Kraken is no longer a feed or execution
dependency. Development diagnostics in `data/entry_diagnostics.json` showed
three completed trades for revised rules, all losing after assumed costs,
plus an unfinished losing position. These are not PumpSwap validation and
must not seed live Kelly estimates. The old `bot.py` replay is a separate
momentum baseline, not the live strategy.

## Eligibility research

Personal self-hosted use appears excluded from Pennsylvania's money-transmitter
licensing requirement under section 2(b)(2), except third-party transmission
services for consideration. This does not establish a provider's licensing or
permit every asset. Sources:
https://www.legis.state.pa.us/WU01/LI/LI/US/HTM/1965/0/0249..HTM
and https://ofac.treasury.gov/faqs/560.

Pump's published terms dated October 17, 2026 do not list the US as generally
prohibited for spot, but restrict automation to permitted means; perpetuals
have separate US restrictions. See https://pump.fun/docs/terms-and-conditions.
No derivative/RWA trading is implemented. ZeroSlip is not used for this live
execution path. Its optional historical records remain accepted by the event
parser; no-account availability is not a legal-eligibility determination.

The one-time setup tooling is deliberately separate from strategy caps:
`setup_inventory.py --convert-sol AMOUNT` only simulates. Adding `--execute`
creates missing accounts, wraps the specified amount, and performs the bounded
SOL-to-USDC conversion in one atomic transaction. Do not rerun setup: the
existing `state/setup.sqlite` journal prevents a second execution. Check the
original signature with `reconcile_setup.py`; never replace an uncertain send.
The completed setup signature is
`snrRBvnwoZz5t93Y54HYfnrCkfWf2jp4aSXoZcVBkag42exM2n4rHvDipSN8Sy3yPLuQ6rW8aG1L68mfjByVgTG`.
The collector and runner were started in terminal sessions after setup. They
are not installed as operating-system services and will stop if the host or
sessions terminate. On restart, verify state and start one of each process.

## Entry-frequency diagnostics and reliability update

`data/entry_tweak_comparison.json` preserves a development-only comparison on
verified exact-pool data. The previous 55/20 signal produced nine overlapping
signals in that window and none passed the 3x cost gate. The revised 20/10
signal produced twenty signals, of which four passed the 1.5x gate at a $4.46
assumed equity and 1% startup allocation. These counts are not four filled
positions or evidence of profitability; the holdout was not used to select
these settings. The separate all-window diagnostic found thirteen old signals,
all rejected by the cost gate. Position caps, reserve, stop, 2R take-profit,
promotion/demotion and the two-loss manual-review halt remain enforced.

Pool snapshots now use dynamic fee/virtual-reserve accounting from the same
batch as vault balances, rather than requiring mutable counters to equal an
earlier read. Identity/config mismatches invalidate the snapshot and refresh;
other account validation still applies. Recognized temporary RPC/network
failures retry without replacing an uncertain order. Authentication/region
HTTP rejections and other validation failures still stop the runner. The
original earlier validation stop cannot be diagnosed retrospectively from its
old type-only log; this addresses a reproducible strictness issue in that path.
Credential-free current status and events now persist in
`state/last_status.json` and `state/events.jsonl` for future diagnostics.

## Broader token scan and mature-meme policy

`assets.json` currently identifies 24 exact Solana mints: SOL, DRIFT, HNT, JTO,
JitoSOL, JUP, MNDE, ORCA, PYTH, RAY, RENDER, BONK, WIF, POPCAT, ATLAS and AFKHERO. This is an
explicit starter registry, **not every non-meme token on Solana**. Mint identities
were resolved from CoinGecko's public platform registry. Unknown tokens are not
classified as non-meme merely because they are verified or popular.

Run `.venv/bin/python scan_markets.py --loop` for read-only discovery across
liquid SOL/USDC-quoted DEX pools. Results, upper/lower channels, signals and
execution blockers appear in `state/market_scan.json`; per-token source files
are retained in `data/markets/`. Provider schema/pair checks for these additional
markets do not yet include the exact on-chain checks of the live SOL adapter.
API failures are reported and rate limits back off. Do not interpret missing
or failed markets as having no breakout.

BONK/WIF/POPCAT are mature-meme **candidates**, not automatically approved as
safe. `MatureMemePolicy` requires at least 180 days since the risk provider first
observed the token, a 30-day selected pool, $500,000 pool liquidity, $100,000
24-hour volume, disabled mint/freeze authorities, supported SPL token handling,
no high-severity risk warnings, normalized risk score <=15, largest reported
holder <=20%, top ten <=60%, and >=80% lock/burn evidence for that specific
pool's LP. Missing data fails screening. These conservative thresholds may
exclude legitimate concentrated-liquidity pools where LP-lock percentages are
not meaningful. Report data are third-party snapshots, not independent chain
proof or a guarantee against rug pulls. The source specification is
https://api.rugcheck.xyz/swagger/doc.json.

The default live runner now uses a single portfolio coordinator for this
registry. SOL retains its direct PumpSwap adapter; the other registered tokens
have a Jupiter V2 adapter that is now blocked for new U.S. entries. Its technical
instructions are locally validated, assembled,
simulated and signed, then submitted through the configured RPC. No Jupiter
API key is needed. Keyless requests are throttled to 0.5 requests/second.
The adapter validates exact-input amounts, encoded 0.5% slippage bounds,
wallet source/destination accounts, on-chain lookup tables, simulation token
deltas, account rent and native fee reserves. Remote transfers, tips,
approvals and external signers are rejected. Standard SPL tokens and the reviewed AFKHERO metadata-only Token-2022 layout
can trade. Transfer fees/hooks and other Token-2022 extensions are rejected. A fresh reverse route is checked before buying.

Run `.venv/bin/python run_live.py --events data/live_pool.jsonl` for live
portfolio execution; `--sol-only` selects the previous direct-only runner.
One open position is shared across all assets until promotion. Token mint IDs,
decimals and actual fills persist; Kelly observations are separated by token
and strategy. Stops/take-profit exits can operate without fresh entry history
or entry-screen approval. Missing quotes for an open position block entries. At portfolio activation,
existing untracked token holdings are frozen as a separate inventory baseline:
they are excluded from bot positions/equity and are not sold automatically.
Later wallet changes are not silently absorbed into that baseline.
Meme caps, promotion/demotion and manual-review loss halts remain global.

Live support does not mean immediate entry eligibility. Missing/stale history,
failed screening, unavailable or unsupported routes, excessive impact, account
rent and round-trip costs can block an asset. With a roughly $4 wallet, account
rent can exceed the expected gain on a 1% startup allocation. Account setup is
only submitted as part of an eligible trade; the bot does not pre-fund all token
accounts. `state/last_status.json` reports current market/history blockers;
`state/route_audit.json` records the last read-only route audit. To refresh it:
`.venv/bin/python probe_routes.py --wallet PUBLIC_WALLET_ADDRESS`.
Unsigned route checks are not executed trades or proof of profitability.

References: https://developers.jup.ag/docs/swap/build and
https://raw.githubusercontent.com/jup-ag/docs/main/llms.txt.

`short_strategy.py` contains tested mirrored short/cover research logic but is
not connected to the live signal loop or scanner following the user's spot-only
direction. `Execution` explicitly rejects short/cover intents before network or
signing calls. Selling owned inventory remains a normal spot exit. Pump perps
are separately restricted for US residents under section 30B.2 of its terms.
Tokenized stocks/RWA tokens are not in the approved registry and are not
supported by the current execution adapter. Provider availability is not proof
of issuer eligibility for US persons.

## Autonomous strategy launcher and improvement loop

The live portfolio runner now starts a credential-free research worker
(`improve.py --loop`). No dashboard, additional exchange account, API key or
AI service is required. The existing RPC, public routing/data services and
funded signing wallet still supply live execution.

The strategy registry in `strategies.py` contains three families:

- Donchian breakout: current 20/10, plus 30/10 and 55/20 candidates.
- EMA trend following: hourly 50/200 bullish crossover, bearish crossover exit.
- RSI pullback recovery: Wilder RSI(14) crosses back above 30 while price is
  above EMA(200); RSI reaching 60 provides an additional exit.

All retain 2-ATR stops, 2R take-profit, completed hourly data, the five-minute
entry window and execution-price bounds. These parameterizations are research
candidates, not established profitable systems. Only the incumbent can place
live entries until another candidate passes the evidence gates. Different
approved assets can eventually select different strategies, but all share the
same wallet, position limits, exposure caps and pending-order serialization.

`adaptive.py` archives completed candles and selects exactly one challenger
using chronological training data. The minimum is 1,881 contiguous hourly
bars: 201 warmup, at least 672 training, 336 validation and 672 holdout. Both
validation and holdout require at least 20 completed trades and improvement
in net PnL after modeled costs. Holdout also requires positive results at
double modeled fees/slippage/fixed costs, a positive 99% lower
seven-day-block-bootstrap bound on daily improvement, and a drawdown gate.
This bootstrap filter is not proof of future profitability or a calculation
of the probability of backtest overfitting. Open inventory is charged at
estimated liquidation value; zero-trade days stay in comparisons. The same
1.5x target/cost hurdle is used in research. Research remains a fixed-notional
per-token simulation; the live wallet separately enforces Kelly and portfolio
rules, screening, route checks and actual account rent.

A consumed holdout is never reused to try another challenger. New experiments
need 28 days of fresh non-overlapping observations. The worker also attempts
one older public-history page per review, at most once a day per asset (failed requests retry after an hour),
retaining raw responses and provenance. Missing hours remain gaps; they are
never synthesized to manufacture sufficient evidence. Candle history preserves
the first recorded completed observation; later provider revisions cannot
rewrite past experiments. Costs are explicit conservative assumptions and
observed native execution costs can raise them. Missing data or insufficient
evidence retains the incumbent. The worker does not manufacture trades or
lower live filters just to accumulate samples.

Accepted, recent profiles activate only while flat and reconciled, with no
review/drawdown halt. Existing positions keep their original strategy identity
and exit rules. Two consecutive net losses, or nonpositive net results after
at least five closes since activation, roll a replacement back to its previous
champion. The current live strategy is the champion; the other registered
strategies are challengers, and they cannot place entries before qualification.
Rollback never clears the existing manual-review halt or drawdown halt, never
replays a rejected/rolled-back experiment, and never changes risk limits,
token approval, wallet access, fees, leverage or source code. New identities
start with their own trade evidence; historical research does not count as
five real wins or seed Kelly observations.

`state/improvement_status.json` explains evidence and missing data;
`state/adaptation.sqlite` stores archived candles, experiments, active profiles
and activation/rollback events. Worker output is in `state/improvement.log`.
Run `.venv/bin/python improve.py` for a one-time local evaluation. Historical
validation runs alongside actual trading; this is not a paper-trading mode.

The separation of training and later evaluation follows chronological
validation principles ([TimeSeriesSplit documentation](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html)).
The limited candidate set and sealed holdout address concerns described in
[The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf);
these checks do not establish that overfitting has been eliminated.


## Current venue policy and additional strategies (2026-10-28)

The earlier keyless Jupiter API check established technical access, not U.S.
eligibility. Its published terms exclude U.S. wallets; new Jupiter entries are
now blocked both in the portfolio router and adapter. Raydium's published
disclaimer also excludes U.S. residents. Existing pending orders still reconcile;
exit management is kept separate from entry eligibility. A registry entry is
not a claim that a usable eligible route is available today.

Direct PumpSwap spot and the direct Orca SOL/USDC atomic adapter are the current
reviewed execution paths. Pump's spot prohibited-jurisdiction list and Orca's
published terms did not name Pennsylvania or the U.S. as a blanket spot
exclusion in this review. This is a terms review, not a blanket legal clearance
for every token/product. Wallet screening, applicable law, feature-specific
restrictions and later terms changes still apply. The direct SDK/RPC path
requires no exchange API key or centralized exchange credentials. Pump perps
remain excluded for U.S. users. Other venues without verified terms/adapters
remain blocked; aggregators cannot bypass venue restrictions.

Sources: https://developers.jup.ag/docs/legal/terms-of-use,
https://v2.raydium.io/docs/disclaimer/, https://www.orca.so/terms-of-use,
https://pump.fun/docs/terms-and-conditions.

Five additional candle challengers are implemented: Bollinger lower-band
recovery (high priority), UTC-session VWAP pullback, Bollinger/Keltner squeeze
release, repeated-support/resistance range trading and volume-confirmed
momentum. Bollinger/range strategies target their mean/resistance rather than
forcing a 2R target. All preserve ATR stops and external risk/cost controls.
VWAP uses actual quote turnover divided by base-token volume, not USD candle
volume relabeled as token volume. The confirmed PumpSwap event collector now
records these volumes and reserve-based liquidity estimates. Missing coverage
blocks volume signals; momentum also needs time-aligned liquidity. Incomplete
volume/liquidity histories exclude those challengers from selection. Volume
alone is not proof that buying pressure is genuine.

`atomic_execution.py` builds an Orca exact-output USDC→SOL purchase followed by
an exact-input SOL→USDC PumpSwap sale, in one versioned transaction, with one
signature and one journaled submission. Exact matching SOL quantities prevent
residual token exposure. The encoded sell minimum must cover the maximum buy
cost, a conservative native fee reserve and positive profit cushion. Full
message simulation, program/account/PDA checks, message hashing, confirmed
receipt checks and restart reconciliation are required. There is no sequential
fallback. Both legs revert together if either fails; failed transactions may
still charge network fees. Two consecutive reverted atomic attempts latch a
separate arbitrage halt. Normal portfolio halts and sizing remain enforced.
The first pair is SOL/USDC only; it does not grant arbitrary Orca token access.

The local Orca helper uses the pinned official `orca_whirlpools` 8.0.0 Rust SDK
and never receives a private key. Build it with `./build_orca_helper.sh` if
`bin/orca-quote` is absent. Only public account data and unsigned instructions
cross the helper boundary. An unsigned combined on-chain simulation passed;
that sampled cycle was unprofitable and was not signed or submitted.

Established meme candidates may trade below $5,000 subject to the same
screening, route and cost checks. Current candidates: BONK, WIF, POPCAT, BOME,
MEW, PENGU, FARTCOIN, GIGA, PNUT, MOODENG and TRUMP. This is an explicit
allowlist, not access to every meme coin or an endorsement of these tokens.

`discover_memes.py` automatically discovers confirmed PumpSwap pool-creation
candidates after fresh wallet equity reaches $5,000. Below the threshold it
opens no launch feed connection. It validates the emitting program, on-chain
pool identity/PDA and event age, deduplicates pools and caps the short-lived
queue. A new pool does not prove a new mint. Discovery never edits `assets.json`
or authorizes a trade. Candidates still require mint-age, token-risk,
liquidity and validated execution screening. Sniping trade execution remains
disabled until its launch execution adapter is independently validated;
capital alone never enables it. Global position limits and the two-meme cap
continue to apply. The learner cannot lower the threshold. The launcher starts
the credential-free discovery worker automatically; status is recorded in
`state/discovery_status.json`.
