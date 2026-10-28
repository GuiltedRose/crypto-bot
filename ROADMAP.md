# Trading roadmap

## Current small-capital phase

- Spot only: buy approved tokens and sell actual owned balances. No borrowed
  shorts, perps, tokenized stocks or automatic copying of trader wallets.
- Broaden token discovery beyond SOL: ATLAS, AFKHERO, protocol tokens and
  screened established meme candidates are listed in `assets.json` (24 mints).
- Multi-token live portfolio routing is implemented: direct PumpSwap for SOL
  and keyless Jupiter for the other approved assets, with mint-specific
  accounting, account/rent costs and reconciliation. Fresh history, screening
  and executable cost checks still determine which assets can enter now.
- Retain startup sizing, stop/take-profit, promotion/demotion and review halt.
  Unknown/new tokens do not become eligible merely through a profile mention.

## Future phase at $5,000 account capital

User wants to broaden the meme-token universe, including newer launches, and
consider a dedicated sniping algorithm. This is future work, not an existing
strategy or an automatic deployment switch. Establish capital measurement,
strategy/routing support and applicable risk checks before implementation.
The separate maximum of two simultaneous meme-token positions still applies.
The $5,000 milestone does not authorize removing all exposure limits or blindly
signing launch transactions. No sniping or new-launch execution is enabled now.

## Candidate discovery from public profiles

The supplied profile `4ugDhHJ8XDXAeABmrNmGffFaLbJb9BkPyiFGVSV9ocwo` is a
read-only candidate source. An inspection found 663 standard-SPL held/received
mints, many unidentified; only JUP overlapped the current registry. This does
not verify market value, profitable trade intent or ranking; holdings may
include unsolicited tokens. No candidate was automatically approved and no
orders/position sizes were copied. Reports are in `data/discovery/`.

## Autonomous multi-strategy improvement

Implemented a bounded registry of Donchian breakout, EMA trend and RSI recovery
strategies, sharing the existing live portfolio risk controls. A separate
credential-free worker archives history, tests one challenger against later
validation/holdout data, records decisions and can activate qualified profiles
for new entries. Deteriorating replacements roll back without clearing halts.
No unrestricted code rewriting, risk optimization or unsupported profitability
claims. Insufficient evidence keeps the incumbent live strategy.


## Additional strategies and venue review

Added Bollinger reversion, session VWAP pullback, volatility squeeze, structural
range and volume momentum challengers. Verified volume/liquidity coverage is
required where appropriate. Direct Orca/PumpSwap SOL/USDC atomic arbitrage now
has a locally validated one-transaction build/simulate/sign/reconcile path.
New Jupiter and Raydium entries are excluded by the U.S. venue review.

Expanded established-meme allowlist remains independent of sniping capital.
Automatic confirmed new-pool discovery unlocks at $5,000; candidates require
separate approval and the sniping execution adapter remains future work.
