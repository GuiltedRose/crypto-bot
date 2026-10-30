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
| Portfolio trading | `run_live.py` | Runs the separate hourly spot strategy and reconciles orders against Solana. This runner can submit real transactions. |
| Observation | `collect_pool.py`, `wallet_watch.py`, `wallet_signals.py` | Collects market data and public activity from saved wallets. Observations provide research context rather than copying positions. |
| Research and learning | `improve.py`, `meme_learning.py`, `neural_network.py`, `neural_autonomy.py` | Evaluates strategy candidates, trains neural weights, and qualifies frozen models before they influence entries. |
| Manual-wallet research | `run_manual_research.py` | Archives wallet receipts separately from bot performance. |

Strategy research includes Donchian breakouts, EMA trends, RSI recovery,
Bollinger and range setups, VWAP, volatility squeezes, and volume signals.
Available code is not necessarily enabled or qualified for live trading.

## Learning and autonomy

The neural network learns from eligible completed paper trades, confirmed bot
trades, and receipt-backed outcomes from configured saved wallets. Both wins
and losses contribute. Positive outcomes receive a 2x multiplier on top of
class-frequency weighting; the selection threshold compensates for this bias.
Scores remain uncalibrated; online selection is experimental, not validated profitability. Completed paper outcomes remain available across balance
resets; refills, rent returns, voided fills, and synthetic trades are not profit
labels. Paper results remain modeled data, separate from confirmed live returns.

Paper training refreshes between scans, including while other paper positions
are open. Live training refreshes while flat so it cannot delay exits. Paper saves its model to
`learning.json` in its state directory; the meme runner rebuilds shared weights
from the outcome ledgers. Its default paper source is `state/paper_volume/paper.sqlite`.

Both runners default to `--neural-mode learning`: after 30 eligible outcomes,
the current neural weights may veto otherwise eligible entries below the 2/3
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
Detailed wallet observations stay in the report rather than flooding the console.

Reports and journals stay in the selected state directory. By default, exhausted or drawdown-halted paper runs archive
and refill their original virtual inventory when flat. `--no-auto-reset` keeps
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

The separate `run_live.py` portfolio runner can also submit real transactions.
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
