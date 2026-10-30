# Pump.fun strategy review — 2026-10-29

## Current policy and evidence status

- The configured live rules enable `momentum_reclaim_enabled`. In addition to
  reported hourly buy volume and completed candles, `volume_candles` requires
  buy volume at least 1.1x sell volume, an intact upward impulse, pullback and
  volume-backed bullish reclaim. Missing sell flow remains WATCH. With that
  flag disabled, it falls back to the less selective volume/candle minimums.
- Two consecutive confirmed net losses pause new entries for one hour. This
  fixed cooldown is risk containment, not a learned edge.
- Live entries with the reclaim policy enabled invoke the bounded on-chain risk
  assessor. It does not prove human buyers, safe bundles or absence of
  manipulation.
- `meme_scorecard.py` remains shadow-only. Current/Moderate/Aggressive use
  shared entry requirements and stake limits; volatility changes response
  cadence, not admission. This is not learned strategy selection.
- Existing live, paper and paired-paper samples are too small and differ in
  execution/evidence assumptions to establish a profitable strategy or justify
  tuning funded thresholds.
- Decision contexts now retain flow, candle range, turnover/liquidity and
  bounded on-chain observations for source-separated outcome analysis. Cohorts
  need ten outcomes before they are flagged for review; there is no automatic
  strategy promotion or stake adaptation.

## Research findings

Pump.fun-specific research identifies wash trading, hidden creator relationships, coordinated selling and manipulated social signals. Thus volume, wallet counts and callouts can be correlated manifestations of one operator rather than independent evidence.
Source: https://arxiv.org/abs/2609.10246

A cross-chain study finds manipulation indicators prevalent among its high-return sample. This supports investigating market quality; it does not prove a profitable entry rule or supply a Pump.fun-wide manipulation rate.
Source: https://arxiv.org/abs/2507.01963

Jito bundles atomically group transactions and also serve legitimate arbitrage. Same-slot buys alone do not establish malicious bundling; no observed bundle does not establish independent ownership.
Source: https://docs.jito.wtf/lowlatencytxnsend/

Mint/freeze permissions must be checked directly. Revoked permissions do not establish a safe distribution or honest traders.
Source: https://solana.com/docs/tokens/basics/set-authority

Read actual market-specific fees rather than assuming one fee fits every curve and graduated pool.
Source: https://github.com/pump-fun/pump-public-docs/blob/main/docs/FEE_PROGRAM_README.md

## Primary candidate: momentum → pullback → reclaim

This is an implementation hypothesis, not a research-proven profitable strategy.

1. Discover through hot tokens, fresh callouts, followed-wallet buys and the existing candidate watchlist. Preserve mint identities and publication times. Deduplicate authors, transactions and wallets; exclude our own orders from endorsements.
2. Establish a local upward impulse from completed one-minute candles and actual trades. Three candles can seed a candidate; they cannot establish robust long-term trend or ownership independence.
3. Put the candidate into an armed watch state when it pulls back from that observed impulse high. Store that high and the pullback low. Do not recompute the reference each scan in a way that perpetually moves the entry.
4. Enter on a reclaim of the previous completed candle's high, provided the pullback low remains intact and recent buy flow is recovering. Compare completed-candle and trade-level confirmation in paper; trade confirmation requires trustworthy timestamped trades.
5. Invalidate on a new structural breakdown, concentrated distribution, stale evidence, a missed/chased entry or untradeable execution. Otherwise prioritize armed candidates over newly discovered ones.

Pullback depth should be evaluated relative to recent observed range and token maturity. The user's roughly 40% decline is one challenger hypothesis, not a requirement for every token. A decline without renewed demand remains a falling price, not a buy signal.

## Market-quality checks

- Separate executable sellability, token controls and ownership/coordination checks from popularity ranking. A high social score must not cancel a failed execution check.
- Restore bounded holder concentration checks, excluding verified pool vaults and aggregating token accounts by owner. Shared funding and creator relationships require additional history; unknown remains unknown.
- Measure buyer concentration, repeat buy/sell cycling, distinct funding clusters when available, fresh net buying and whether followed wallets subsequently sell. Unique addresses are not verified people.
- Record coverage and freshness. Free RPC sampling is bounded and may be rate-limited; it cannot certify bundle-free or human-only demand.
- Use current executable price impact and conservative round-trip costs. Smooth candles do not prove liquidity or absence of manipulation.

## Additional paper challengers

- Breakout/retest: sustained demand breaks a local range, then holds the former range high on a retest. Test separately from pullbacks; avoid buying merely because the hot-board rank increases.
- Deep-dip reclaim: test the approximate 40% idea only after a prior impulse and a confirmed reclaim; no automatic averaging down.
- Keep the current volume/candles policy as a comparison baseline, not as evidence that manipulation checks are unnecessary.

## Evaluation and promotion

Record identical timestamped candidate streams for all challengers, including rejected, failed and disappearing tokens. Use only data available before each decision; entry is at the next executable quote, not the candle low. Paper cannot assume fills during RPC outages or successful exits from unavailable routes.

Keep the approved position, TP/SL, reserves, slippage and drawdown policy unchanged while comparing entry strategies. Measure actual net P&L, drawdown, execution failures, cost share, maximum favorable/adverse excursions and opportunity expiry. Split chronologically and group overlapping token/narrative episodes to reduce leakage; use untouched forward paper results before claiming an edge. A single win is evidence of one profitable execution, not calibrated strategy profitability.

Data capture needed before implementing sophisticated flow rules: short-window buy/sell trades, source timestamps, sampled owner/funding relationships and repeatable executable quotes. Do not fabricate these from hourly aggregates or callout counts.

## Recovery hotfix in this working tree

Preflight/signature-verification rejections now become terminal rejected orders rather than uncertain submissions. Network timeouts stay uncertain. New buys record pre-send inventory; expired uncertain buys may clear only after missing history/receipt checks and exactly unchanged finalized inventory. Existing orders without that baseline remain blocked. No signed transaction is automatically replayed. The hotfix changes require restarting the user-operated process.
