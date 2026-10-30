# Incremental market engine: partial, fail-closed implementation

2026-10-30. No operational trading/learning state or risk/NN parameters changed.
Live execution remains disabled for this source. Existing ten-promotion eligibility
and source-specific training restrictions remain mandatory.

## Implemented

`onchain_candles.py` persistently queues changed minute buckets and stores 1m,
5m, 15m and 1h OHLCV, buy/sell USD volume and trade counts. Higher timeframes use
cached lower-timeframe buckets; they do not fetch transactions again. Open/close
follow canonical slot/transaction/event order. UTC periods are aligned. States
are distinct: provisional indexed minute, complete traded interval, confirmed
zero-trade interval, missing interval. Empty intervals have no invented OHLC.
The indexed watermark and existing freshness checks distinguish a lagging indexed
minute from a genuinely current market.

Source/quote changes dirty affected minutes. A durable cursor schedules newly
closed minutes, including zero-trade periods. Rebuild work is bounded; queued
revisions prevent concurrent updates from being erased. Restarts resume pending
work; duplicate blockchain events cannot add volume. Approved broad historical
corrections must call `CandleEngine.invalidate(start,end)` for the entire affected
range. Canonical event conflicts still require review; this does not introduce
silent event mutation. Readers fail closed on pending/incomplete cache buckets.

Exact trailing-hour volume uses cached full minutes and locally retained events
for partial boundary minutes. It does not round the hour to candle boundaries.
Paper and unsigned live share these local cache reads when installed; an incomplete
cache does not silently fetch Pump.fun or treat missing minutes as zero. Existing
$5,000 hourly buy volume, configured completed-candle minimum and freshness/risk
gates remain unchanged. Existing databases without a cache retain their prior
local-event analytics path. Pyth's reviewed event-time conversion path is reused;
no current price or stablecoin peg substitutes for missing historical rates.
Oracle collection remains the existing explicit operator-scheduled capture, not
an automatically authorized stream feature.

`onchain_stream.py` adds one explicitly authorized WSS connection, up to two
configured mentions subscriptions, bounded client buffering and a durable bounded
signature-hint queue. Duplicate hints collapse; overflow and disconnects explicitly
record gaps. Failed transactions do not become trades. Reconnect attempts are
bounded; 401/403 stop, and shared provider cooldown/Retry-After governs retries.
No per-notification HTTP transaction storm is generated. Hints wake the existing
finalized block recovery worker, which resumes its saved checkpoint and updates
candles. Raw undecodable receipts remain retained by the existing indexer.
HTTP counters report process-lifetime requests, compressed body bytes and decoded
bytes alongside existing lag/rate metrics. Stream gap evidence is not silently
cleared when a cursor advances: finalized manifests still establish coverage.

## Explicitly incomplete

This is a **notification path plus finalized ingestion**, not a complete independent
low-latency transaction stream. Notifications do not supply full transactions or
canonical ordering. There is no separate optimistic/provisional swap decoder or
per-hint transaction fast path; provisional candles currently contain verified
finalized events from an unfinished minute. No complete real live chart, creation
followed by swaps, sustained loss-free run, independent ledger comparison or full
real hour is demonstrated. Global finalized recovery still has the measured RPC
throughput limitation. Stream hints cannot fix that bottleneck or the unresolved
Pump event layouts. No accepted real swap prices were manufactured for this work.

[Solana logsSubscribe](https://solana.com/docs/rpc/websocket/logssubscribe) documents
one mention per subscription; two programs require two subscriptions. Optional
accountSubscribe can hint at reserve/oracle changes, but account snapshots alone
cannot reconstruct complete trades or historical candles, so it was not added.
[Yellowstone](https://docs.triton.one/project-yellowstone/dragons-mouth-grpc-subscriptions)
can carry complete transactions and replay information, but provider endpoint/token,
protocol version, quotas and recovery/retention rights are required. None was
available or purchased; no speculative gRPC dependency was introduced.

## Isolated deployment

Continue using an external isolated database and private runtime configuration.
In the existing endpoint record add an **explicit reviewed** `ws_url` and source
policy `stream_verified: true` only after confirming permission. Do not infer a
WSS URL from an authenticated HTTP URL or copy secrets into source/Nix storage.
`stream_mentions` may contain one or two verified pool/program addresses; prefer
a single known pool first. Default mentions are the two official Pump program IDs.
Top-level `markets` names up to 32 `{ "mint": "...", "pool": "..." }` pairs.
Only these markets are aggregated. There are no enabled/approved source defaults.

```bash
python run_onchain_indexer.py --config /path/to/reviewed.json \
  --db /path/to/isolated/index.sqlite --start-slot <REVIEWED_SLOT> \
  --loop --stream --retain-blocks
```

Without `--stream`, configured markets also aggregate during normal finalized
polling. Paper's existing `--onchain-db` consumes local results. Do not point this
experiment at existing paper/learning state. Retained full blocks remain necessary
for the separate `python -m onchain_hour` audit, not for every live evaluation.
`--stream` uses bounded configured batches; the existing non-stream runner retains
its adaptive catch-up mode. Neither bypasses provider limits or source rights.

## Evidence

[Measurements](../research/realtime_engine_validation.json): a bounded public
WSS probe received two subscription acknowledgments and 1,000 notifications,
982 unique signatures, in 2.14 seconds; body traffic was 3,904,430 bytes. It stopped
at the explicit message cap. This is connectivity/burst evidence, not a sustained
capacity or completeness test. Event-to-delivery latency is UNKNOWN because logs
notifications lack blockTime; strategy latency and real candle accuracy are not
demonstrated while swap decoding is blocked. A broad feed can overflow the bounded
queue when the recovery worker falls behind; the system reports that condition.

An isolated synthetic cache benchmark rebuilt 61 minute buckets in about 0.244s
(0.241s CPU), grew the DB by 28,672 bytes and processed zero buckets on the next
idle pass (~0.001s). Process peak RSS was about 45 MB. These small-fixture figures
are not production scaling evidence. No historical hour was downloaded repeatedly.

**48 focused tests passed** (7 stream/cache, 30 indexer, 11 hour/oracle).
Tests cover all four timeframes, exact partial-minute rolling volume,
missing/zero/provisional distinctions, deterministic rebuild, restart, dirty-update
races, notification deduplication, overflow and bounded reconnects without trade
insertion. Existing indexer and frozen-hour/oracle regressions are also run.
Operational source retention/ML rights remain unverified; no flags were enabled.
Remaining blockers are unchanged deployed schemas, causal historical USD coverage,
sufficient authorized finalized recovery throughput and independent verification.

Acceptance evidence: creation→swaps and real candles/hour: **not demonstrated**;
1m/5m/15m/1h, exact rolling volume and deterministic cache rebuild: **synthetic
fixtures only**; interruption/restart/recovery: **focused simulated tests**;
paper consumption without website calls: **isolated fixture test, entry rejected
under existing gates**. No sustained-load, independent-ledger or profitability
claim follows from these tests.
