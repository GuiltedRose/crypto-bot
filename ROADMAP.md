# Meme trading roadmap

## Current scope

- Pump meme momentum/pullback entries, spot inventory only.
- Persistent paper trading with shared-bankroll positions and audited virtual resets.
- Recorded public market/wallet observations, entry and continuation networks.
- Position-grouped chronological evaluation, rule-exit comparisons and exit
  champion/challenger selection with rollback.
- Optional locally signed meme execution, with durable pending-order recovery,
  simulation, fee reserves and unchanged position/drawdown/exit controls.
- Live execution stays inactive during the current paper-validation phase.

## Retired experiments

The earlier hourly portfolio runner, Donchian/EMA/RSI and alternative candle
strategies, short research, Jupiter/Orca routes, and atomic arbitrage have been
removed. Their execution launchers and unused strategy/history modules are gone.
Shared RPC, signing, account validation and journal code remain. Existing state,
valid training samples, and historical outcomes have not been deleted.

## Next evidence

- Accumulate later complete positions for frozen exit-model comparisons.
- Compare position-weighted accuracy and net quote-replay outcomes against
  existing rule exits; keep paper and confirmed-live evidence distinct.
- Continue checking slippage, quote coverage and model drift. More samples or
  passing tests do not establish profitability.
- Keep new-launch discovery separate from established meme research. Its exact
  $5,000 verified-equity gate and independent adapter requirements remain.

See MEME_TRADER.md for current policy and research/neural_network_review.md for
neural architecture and evaluation limitations.
