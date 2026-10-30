# Crypto Bot

A Python toolkit for researching and trading Solana spot markets. It combines
persistent paper trading, public market and wallet observation, strategy
research, and a from-scratch neural network with optional live execution.

The project is experimental. Backtests, modeled paper returns, and small live
samples do not establish profitability.

## What it does

| Area | Entry points | Purpose |
| --- | --- | --- |
| Paper trading | `run_paper.py`, `run_paper_compare.py` | Simulates trades with virtual balances, modeled costs, persistent state, and strategy comparisons. |
| Meme trading | `run_meme_live.py` | Evaluates Pump momentum/pullback/reclaim setups. Defaults to unsigned simulation; `--execute` enables real transactions. |
| Observation | `pump_market.py`, `wallet_watch.py`, `wallet_signals.py` | Collects market data and public activity from saved wallets. Observations provide research context rather than copying positions. |
| Research and learning | `meme_learning.py`, `neural_network.py`, `exit_evaluation.py`, `neural_autonomy.py` | Evaluates strategy candidates, trains neural weights, and qualifies frozen models before they influence entries. |
| Manual-wallet research | `run_manual_research.py` | Archives wallet receipts separately from bot performance. |

The project now focuses on meme-coin momentum/pullback setups and neural exit
research. The earlier hourly portfolio, breakout/EMA/RSI, VWAP/range/squeeze,
short-strategy and atomic-arbitrage experiments have been removed. Historical
outcome journals remain intact for research and reconciliation.

## Learning and autonomy

The neural network learns from eligible completed paper trades, confirmed bot
trades, and receipt-backed outcomes from configured saved wallets. Both wins
and losses contribute. Full-batch training gives the positive class twice the total weight of the
negative class, regardless of class counts; the selection threshold compensates for this bias.
Scores remain uncalibrated; online selection is experimental, not validated profitability. Completed paper outcomes remain available across balance
resets; refills, rent returns, voided fills, and synthetic trades are not profit
labels. Paper results remain modeled data, separate from confirmed live returns.

Paper training refreshes between scans, including while other paper positions
are open. Live loads saved weights for inference and retrains only while flat inside its
one-hour consecutive-loss cooldown. It can bootstrap from the paper `learning.json`;
without at least 30 training outcomes, active mode blocks new entries. Paper saves its model to
`learning.json` in its state directory; the meme runner rebuilds shared weights
from the outcome ledgers. Its default paper source is `state/paper_volume/paper.sqlite`.

Paper defaults to `--neural-mode learning`. Live defaults to `--neural-mode active`:
after 30 eligible outcomes,
the saved neural weights select among otherwise eligible entries using the 2/3
score threshold. Live retains its position limit, cooldown, sizing, safety and
execution checks; neural scores never bypass them. No live process is started
by training alone.

Paper defaults to four distinct positions sharing one virtual bankroll, no
loss cooldown, and exploration of one quarter of below-threshold candidates.
This keeps gathering genuine modeled outcomes even when the network rejects
most setups. Paper fills remain hypothetical and cannot establish a live edge.

For the stricter live policy, use `--neural-mode qualified`: a frozen candidate
must pass prospective confirmed-live evaluation first. Use `--neural-mode off`
for live rule-only entries or `--neural-mode shadow` for paper rule-only entries.
See the [qualification policy](MEME_TRADER.md#autonomous-neural-qualification)
and [network review](research/neural_network_review.md).

Chart inputs now include completed-candle returns, drawdown from the recent
peak, volume change, and bearish-candle frequency. A separate exit network
learns one-minute changes in recorded net liquidation quotes while positions
are held. After sufficient training data and a successful position-grouped out-of-sample
comparison, its champion may suggest an earlier exit. Existing stops and time
limits take priority; live early-profit exits require positive minimum net
proceeds. Missing or stale chart data disables this optional signal.
These quote-based labels show associations, not causes or guaranteed fills.

The network uses structured market, saved-wallet, chart, position-return,
execution-cost and available on-chain concentration inputs. Missing values have
explicit indicators. Live entry inference runs after on-chain checks and swap
simulation, so those results participate in its decision. Paper uses its modeled
costs and leaves unavailable chain observations unknown. It does not treat raw
provider claims or unavailable data as verified evidence.

Exit evaluation freezes a challenger and tests it on chronologically later,
whole positions; overlapping train/test positions are purged. Only positions
with a known rule-based close support the exit-outcome comparison. The console
shows position-weighted accuracy, Brier score, estimated net improvement versus
rules, champion status and rollback counts. These are out-of-sample quote-replay
metrics, not actual counterfactual fills or proof of live profitability.

Exit champions require positive improvement with no worse replay drawdown or
prediction metrics. Later deterioration rolls back to the previous champion,
or to rule-based exits. All valid historical samples stay in the ledgers;
consumed evaluation blocks can train later challengers, which need fresh tests.
Evaluation state is saved in `exit_evaluation.json` with a transactional
`exit_evaluation.checkpoint.sqlite` recovery checkpoint. Challenger IDs, frozen
weights and partial evaluation counts survive process restarts in both runners.
Console output distinguishes current progress from completed challengers and
cumulative evaluated positions. A completed rejection starts a new challenger;
it does not discard the previous result. Interrupted exports recover from the
durable checkpoint. Missing observations or incompatible feature versions pause
evaluation with an explicit reason; a restart alone never rejects a challenger.
Saved position scores are immutable. Back up the whole state directory.
Unchanged data is cached.

Both paper and live summaries show `LIFETIME` totals alongside `LAST 20`: valid completed trades,
net P&L, win rate, and wins/losses/break-even counts. A partial window shows its
actual count. Paper spans archived runs without counting virtual refills;
invalid/synthetic outcomes and open positions are excluded. Live also subtracts
recorded failed-order fees since the earliest included entry. This recent window
can span different models and is separate from lifetime training and exit OOS
metrics. Learning parameters and risk limits are unchanged.

## Getting started

Use Python 3.10 or later. From the repository directory:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### Paper trading

For a **new** paper session using the current meme rules:

```bash
.venv/bin/python run_paper.py \
  --state-dir state/paper_volume \
  --rules meme_live_rules.json \
  --balance 0 --native-sol 0.1 --sol-price 100 \
  --watchlist paper_watchlist.json --discover --loop --interval 30
```

These are virtual funds. The example starts with 0.1 SOL valued at an illustrative
$100 per SOL; adjust the inventory and initial valuation for your experiment.
Review `paper_watchlist.json` to choose the public wallets to observe. Run the
new-session command only for an unused state directory; the resume command
keeps existing balances and rules. Stop a running loop with `Ctrl+C`.

Resume an existing session with its saved inventory and rules:

```bash
.venv/bin/python run_paper.py --state-dir state/paper_volume \
  --watchlist paper_watchlist.json --discover --loop --interval 30
```

Inspect the same session without starting its collection loop:

```bash
.venv/bin/python run_paper.py --state-dir state/paper_volume --status
```

Export outcome summaries and neural weights:

```bash
.venv/bin/python meme_learning.py --paper-db state/paper_volume/paper.sqlite
```

Paper defaults to `--max-positions 4 --loss-cooldown-seconds 0`. Up to eight
slots are supported, subject to the shared spendable bankroll and costs. Use
`--max-positions 1 --loss-cooldown-seconds 3600` for a single-position paper trial
with the live-length cooldown. These options never change live limits.
The console shows trades, equity, positions, training counts and current blockers.
Use `--json` for full console reports. Full recorded observations and network
inputs remain in the journals and reports; display formatting does not filter
training data.

Reports and journals stay in the selected state directory. By default, exhausted or drawdown-halted paper runs archive
and refill to `max($6, confirmed milestone × 0.85)` when flat. Milestones
($10, $25, $50, $100, $250, $500, $1,000, …) persist across runs and use
settled starting capital plus realized trading P&L, excluding temporary marks. `--no-auto-reset` keeps
them stopped instead. Offline `--feed` replay does not auto-reset. See the
[paper guide](MEME_TRADER.md#persistent-paper-runner-restart-and-reset) for
explicit resets, other cost models, and rule changes.

### Unsigned simulation

Set your public address, then observe and simulate the meme runner:

```bash
BOT_PUBLIC_WALLET="REPLACE_WITH_YOUR_PUBLIC_SOLANA_ADDRESS"
.venv/bin/python run_meme_live.py --wallet "$BOT_PUBLIC_WALLET" --loop
```

Replace the placeholder with your public address. This mode reads network data
and simulates transactions without submitting them. It needs no private key or
`.env` file and does not maintain a virtual paper account.

### Live execution

Review [MEME_TRADER.md](MEME_TRADER.md) and [.env.example](.env.example) before
using real funds. Create an owner-only `.env` from the example if one does not
already exist. Configure an HTTPS `SOLANA_RPC_URL` and exactly one signing source:
`SOLANA_KEYPAIR_PATH` or `SOLANA_PRIVATE_KEY`.

Create `.env` without overwriting an existing configuration, then edit it locally:

```bash
if [ ! -e .env ]; then cp .env.example .env; fi
chmod 600 .env
```

To explicitly enable real meme trades:

```bash
.venv/bin/python run_meme_live.py --execute --loop
```

Use one live runner per wallet. Exit monitoring requires the process and network
to remain available; stopping the runner does not liquidate an open position.
Preserve `state/` when orders or positions need reconciliation. To check an
existing pending meme order without scanning for or submitting a new order:

```bash
.venv/bin/python run_meme_live.py --execute --reconcile-only
```

Keep private keys, recovery phrases, `.env`, and live state out of source control,
chat, logs, and issue reports. Live execution can lose funds.

## Documentation

- [Meme trader guide](MEME_TRADER.md): configuration, execution scope, paper workflows, and learning policy.
- [Meme strategy review](research/meme_strategy_review.md): strategy research and limitations.
- [Neural-network review](research/neural_network_review.md): architecture, adaptation, and verification.
- [Project roadmap](ROADMAP.md): implementation history and remaining work.

## Development and support

Run the test suite with:

```bash
.venv/bin/python -m unittest discover -s tests
```

Use focused tests for behavior changes. Review changes to execution limits and
preserve outcome journals when investigating failures.

Maintained by [GuiltedRose](https://github.com/GuiltedRose). Report bugs and
request features through [GitHub Issues](https://github.com/GuiltedRose/crypto-bot/issues),
without credentials or private state.

Licensed under the [GNU Affero General Public License v3](LICENSE).
