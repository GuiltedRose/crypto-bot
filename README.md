# Solana paper trading bot

Offline, dependency-free Python prototype for spot SOL or a single Solana token.
No wallet, private keys, network requests, or live execution are implemented.

Universe policy: no meme-token entries below **$5,000 current account equity**.
Eligibility is checked before each entry, including after a drop below $5,000.
Reaching the threshold does not automatically add any tokens. Approved meme
mints must be explicitly reviewed and configured in `Universe.meme_mints`;
unknown mints are always blocked. Only wrapped SOL is initially approved as an
established base token. `--mint` identifies the CSV's base token; it does not
approve it. The CSV price feed's identity is supplied by the caller and is not
authenticated by this offline tool. Position limits and Kelly still apply
above $5,000; the threshold never authorizes betting the wallet.

At most **two distinct meme coins** may be active at once. The universe entry
gate accepts active token mints, including pending buys, and rejects a third
meme coin. Additional buys of an already active coin do not consume another
slot and remain subject to position limits. A fully closed position with no
pending buy frees its slot. Established tokens do not consume meme slots.
The current single-market replay never holds concurrent positions. Future live
portfolio execution must supply complete position/pending-order state and
atomically reserve slots to enforce this limit across simultaneous signals.

Run `python3 bot.py prices.csv --balance 4` with chronological, consistently
formatted ISO-8601 UTC `timestamp,close` CSV data. Prices and all costs must be
in USD. Run checks with `python3 -m unittest discover -s tests -v`.

The example strategy enters after positive 20-bar momentum and exits at the
next close. This is a research baseline, not a demonstrated profitable signal.
Use executable prices: candle-close fills can be unrealistic. Results include
each allocation, skip reason, P&L and account balance.

## Risk model

Requires 100 completed shadow/strategy observations; estimates mean wins and
losses and uses a Wilson lower confidence bound on win probability. Applies
quarter Kelly, a 5% position cap, a $1 cash reserve and a 10% peak-equity
drawdown entry halt. Kelly allocation is `p/loss - (1-p)/gain`, with costs
incorporated. The cap assumes a token can lose its entire position value.
No positive estimated edge after costs means no trade. No upward rounding.

Illustrative costs: ZeroSlip 0.25% per side, 0.5% slippage per side and $0.01
fixed round-trip costs. These are assumptions, not quotes. Replace them in
`Risk` for a measured venue/token cost model, including network/priority fees,
DEX fees and token account creation. At $4, the position cap is $0.20; the
fixed-cost assumption alone consumes 5% of that position per round trip.

Kelly does not prevent ruin. Probability estimates can fail, observations are
correlated, and rugs, outages, gas costs and slippage can exceed estimates.
The reserve is a paper USD accounting floor, not protection for SOL held in a
wallet. The halt acts after a realized loss and can overshoot. The confidence
bound is heuristic for dependent market returns. Validate with independent
out-of-sample data before considering real funds. Meme-token safety screening,
liquidity checks and live fee quotes are not implemented.

## ZeroSlip US review — October 28, 2026

Assumed service: https://docs.zeroslip.ai/ (not similarly named projects).
Its [terms](https://docs.zeroslip.ai/legal), dated December 27, 2025, do not
explicitly exclude the US. They require compliance with applicable laws and
sanctions. This establishes no explicit published US ban, **not verified legal
eligibility or regulatory approval**. State, token and underlying venue rules
still need review; US eligibility remains unresolved.
[OFAC guidance](https://ofac.treasury.gov/faqs/560) applies sanctions obligations
to virtual currency transactions too.

The [FAQ](https://docs.zeroslip.ai/FAQ) lists a 0.25% trading fee. Lightning
mode sends a wallet private key or equivalent signing credential to the service;
Local mode returns unsigned transactions. A future execution integration should
use local signing and independently validate transaction instructions, recipients,
amounts, programs and fees. No ZeroSlip execution integration is enabled here.

### Pennsylvania and intended scope

User resides in Pennsylvania and intends to trade only tokens on Solana,
potentially beyond SOL/USDC. The scope is personal spot trading with the user's
own funds. The prototype still replays one market at a time; it does not yet
select tokens or manage a portfolio across markets.

Pennsylvania's [current statute, section 2(b)(2)](https://www.legis.state.pa.us/WU01/LI/LI/US/HTM/1965/0/0249..HTM)
excludes self-hosted wallet use unless used to transmit for another person as
a third-party service for consideration. This supports personal self-hosted
trading being outside that licensing requirement; it does not establish
ZeroSlip's own licensing status or authorize every token or venue.
Pennsylvania [expanded virtual-currency transmitter regulation in 2025](https://www.pa.gov/agencies/dobs/newsroom/dobs-regulating-virtual-currency-transmitters-thanks-to-new-bipa).
ZeroSlip's provider eligibility and underlying venue restrictions remain
unverified. Obtain confirmation that it serves Pennsylvania residents and
identify its operating legal entity and applicable license or exemption
before choosing it for live execution.

Kelly sizes an existing estimated edge; it cannot fix an unprofitable entry
strategy. Expanding into meme coins is not evidence of an edge. Evaluate each
candidate strategy after all costs on held-out data before funding it.
