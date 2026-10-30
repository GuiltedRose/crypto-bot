# Learning and source audit — 2026-10-30

Read-only snapshot: 170 valid paper closes (36 wins, 134 losses) across 37 runs;
243 continuation quote pairs across 64 positions. Three live closes are a separate
training source. History was not edited, reset, relabeled, or deleted by this audit.
Reproduce with `python audit_learning.py` (no HTTP, fitting, new inference or promotions).

## All recorded completed challenger decisions

| Challenger | OOS positions | Changed exits | Net USD | Improvement over rules | Failed requirements |
| --- | ---: | ---: | ---: | ---: | --- |
| Legacy 1 | 10 | 0 | -0.141984 | 0 | Positive neural net; >=3 changed exits; positive improvement lower bound |
| Legacy 2 | 10 | 0 | -2.246423 | 0 | Same three |
| df48df957fd3 | 10 | 0 | +0.236439 | 0 | >=3 changed exits; positive improvement lower bound |
| ff56b1ce267e | 10 | 0 | -0.762631 | 0 | Same three as Legacy 1 |

The two newer snapshots contain 94 and 142 training samples, with different weight
hashes (7e8da8015c42 and 9d6c996c5f60). Saved validation-row replay reproduced their
position counts, accuracy, Brier score and paired net improvement exactly. The
oldest two decisions contain aggregates only: exact prediction replay is unavailable.
The current challenger 8d2505663afa has three OOS positions, not another rejection.

Historical score inspection on post-cutoff recorded quotes showed ranges
0.5626–0.7326 and 0.5607–0.7300 for the two archived models. All exceed the unchanged
1/3 early-exit threshold. Thus Brier improvements do not translate into different
exit actions. These four failures are consistent with genuine lack of measured
exit advantage, not broken promotion arithmetic. No threshold, learning rate,
epoch count, class weight, architecture or risk limit was relaxed.

Selection limitations: entry filtering plus paper exploration conditions which
positions are observed. Neural-closed positions have no rule-only counterfactual;
the evaluator excludes them instead of inventing one. One-minute quote labels
are not executable fills. Concurrent positions share drawdown conditions, so the
normal-approximation lower bound is not strong independent-trade evidence.
The ten-position holdout requirement is deliberate; three current positions are
insufficient. More training examples alone do not establish improved decisions.

## Minimal corrections

- `exit_evaluation.py`: exact failed promotion requirements; no changed criteria.
  Explicit ten-consecutive-promotion live eligibility, with rejection/model rollback
  breaking the streak; funding resets and process recovery do not count.
- `meme_live.py`, `run_meme_live.py`: fail-closed live-entry promotion-history guard;
  pending-order reconciliation and existing position exits remain independent.
- `data_policy.py`, `paper_sources.py`, `pump_narratives.py`: Pump HTTP collectors
  blocked before transport. Existing provider-derived training data is retained but
  paused pending rights review. No approved-API key or scraper bypass was added.
- `wallet_signals.py`: Pump leaderboard/hot-board/callout background sources and
  their cached web context disabled; configured-wallet Solana RPC remains.
- `provider_limits.py`, `wallet_watch.py`, `live.py`: shared cross-process pacing,
  Retry-After seconds/date support without truncation, exponential failure backoff,
  persistent 401/403 denial, and no automatic transaction resend. Redirects refused.
- `meme_learning.py`, `neural_exits.py`: recorded provenance and model-use guards.
- `paper_console.py`, `meme_console.py`: expose the live promotion gate.
- `audit_learning.py`: read-only ledger/saved-score audit. Focused regression tests
  cover guards, pacing/cache, both RPC transports, eligibility, replay and recovery.

## Acquisition inventory and policy findings

| Acquisition path | Original endpoints / schedule | Current disposition |
| --- | --- | --- |
| Pump market collectors, paper/live/compare | `frontend-api-v3.pump.fun/coins`, `/coins-v3/{mint}`, `/candles/{chain}/{mint}`, `/sol-price`, `/token-holders/{mint}/count`; `swap-api.pump.fun/v1/coins/{mint}/market-activity`, `/ath`; >=3s per client, 30s flat / 10–15s held scan rests, no cross-process budget | HTTP blocked, zero requests. Prices, candles, activity, holder/ATH and SOL valuations unavailable until a permitted replacement exists. |
| Standalone narrative scraper | Frontend `/coins/{mint}` and recent `/coins?...`; no common request budget | Blocked before HTTP; offline inputs still readable, not a model-use authorization. |
| Background social/discovery | Frontend `/home-feed`, `/pnl-leaderboard?period=daily&limit=10`; `advanced-indexer.pump.fun/boards/trending?limit=20`; previously polled every 60s | Disabled, including cached web signals. No KOL/human-buyer claims substituted. |
| Wallet valuation utility | Pump `/sol-price`; indirect market HTTP through PublicSources | Same host guard; does not silently value unknown assets at zero. |
| Saved-wallet monitoring, risk observations, receipt research | Configured Solana HTTPS RPC: signatures, transactions, accounts/balances, indexed holder queries; wallet client previously >=2s/request | Retained, shared >=0.5s aggregate request gap (wallet client still >=2s), no secret-bearing URLs stored in limiter DB. Provider-specific slower intervals supported. |
| Capital-gated discovery | Solana WSS `logsSubscribe`, confirmed RPC pool checks | Existing $5,000 verified-equity/feed gates retained; independent on-chain interface, not a website scraper. WSS transport remains a single bounded connection with 20s pings. |
| GeckoTerminal public adapter | `/networks/solana/tokens/{mint}/pools`, `/pools/{pool}/trades`, `/ohlcv/minute`; previously >=3s/client | Documented API only, shared >=6s gap (10/min), 5s ordinary / 30s candle memory cache. No automatic migration of current Pump strategy; partial trade pages cannot replace verified full-hour activity. Model use/storage remains blocked pending rights review. |
| Vendor IDLs/local transaction builders | Local JSON, no HTTP | Unchanged. |

The old per-client 429 delay capped Retry-After at 300s. This could retry earlier
than a provider's instruction; it is replaced by the persistent shared budget.
No bot detection, CAPTCHA, session, authentication or rate-limit bypass is used.
No local collector process was found in the accessible process namespace during
inspection. Processes running old code elsewhere must be stopped/restarted to
load these guards. No live runner, transaction or paid service was started.

## Official sources checked

- Pump [Terms of Use](https://pump.fun/docs/terms-and-conditions), updated Oct 17,
  2026, §§6.1/21(h): automation requires express permission; publicly reachable
  web endpoints alone are not evidence of permission.
- Pump [Developer API Terms](https://docs.pump.fun/terms/), updated Oct 22, 2026,
  §8.3(e): API data cannot be used for model training, evaluation, benchmarking or
  related datasets. Merely obtaining an approved API key is not an ML-use fix.
- Pump [authentication](https://docs.pump.fun/authentication/),
  [getting started](https://docs.pump.fun/getting-started/) and
  [limits](https://docs.pump.fun/limits-and-headers/): approved organisations,
  server-side key at `api.pump.fun/v1`; default 2 requests/s and 50,000/day;
  429/503 Retry-After and remaining/reset headers must be honored. Not enabled.
- [Solana public RPC limits](https://solana.com/docs/references/clusters): published
  100 requests/10s/IP, 40/method/10s, limits may change; shared public RPC is not
  production infrastructure. 403 means access denied, 429 requires backoff.
- [GeckoTerminal API docs](https://api.geckoterminal.com/docs/index.html) describe
  an approximate 10/min public limit, while
  [CoinGecko support](https://support.coingecko.com/articles/6867086195-does-geckoterminal-have-an-api)
  says 30/min. The lower 10/min is used, with 429 respected.
- [GeckoTerminal terms](https://www.geckoterminal.com/terms-conditions) incorporate
  [CoinGecko API terms](https://www.coingecko.com/en/api_terms): attribution,
  caching/storage/security and usage-plan obligations require review before
  adopting it as a persistent ML dataset. No blanket ML-storage permission claimed.

## Remaining blockers

Promotions are mathematically possible (positive OOS fixtures promote), but no real
challenger has demonstrated changed exits or positive paired improvement. Live
eligibility remains 0/10. New acquisition/training based on unapproved provider
sources is paused, with existing histories retained. A permitted, adequately
covered market dataset and confirmed ML/storage rights are needed to resume.
An RPC/indexer replacement must reconstruct real trades/candles and obtain a
permitted USD valuation; no missing bars, volume coverage or safety evidence may
be synthesized. Custom RPC provider terms and external concurrent traffic remain
operator-specific uncertainties.
