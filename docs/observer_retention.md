# Observer historical retention — UNTESTED

No tests, benchmarks, node software or collection were run. This lifecycle manages
only an observer database and its own archives. It never deletes native accounts,
ledger, snapshots or other replay state. It does not resolve mainnet RAM demand.

## Tiers and limits

`observer_retention.example.json` configures a 24-hour hot target, 30-day warm
classification, 100GB archive cap, 25GB database cap, and two 50GB reserves.
These are decimal planning limits, not disk allocations or measured capacities.
Cold candles/conversions, coverage, checkpoints, source policies, relevant receipts
and event indexes remain queryable in SQLite. Older full blocks use bounded Zstd
archives, with column-oriented compressed market-event sidecars and source/conversion
provenance. Native node storage is not included in observer accounting.

This initial implementation prunes **only duplicate full-block blobs**. It does
not evict warm receipts, events, candle aggregates or archives after 30 days.
Those deletions remain blocked pending complete dependency integration. Consequently
configured caps pause writes rather than guaranteeing indefinite collection.
Archive deletion eligibility is always false: archived evidence may be the only
remaining reproducible block. No missing data is rewritten as a zero-trade interval.

## Safe operation

Default planning is read-only:

```bash
python observer_retention.py --db /isolated/observer.sqlite \
  --config observer_retention.example.json --archive-directory /isolated/archives
```

Before mutation, stop all ingestion/consumers and use an isolated observer copy.
Explicitly review recovery, trade, evaluation, fork and training dependencies;
record protected ranges with `observer_retention.pin(store,id,first,last,reason)`.
Pins have no automatic expiry/unpin. Keep `dependencies_reviewed=false` until that
review is complete. Set `safe_before_slot` to the lowest slot still needed by any
recovery consumer; the default zero protects everything. Automatic synchronization
with trading/model references is NOT implemented; lack of a pin is not evidence
of lack of a dependency. The explicit review gate prevents default pruning.

`--apply` archives/verifies only. Add `--delete-duplicates` to authorize deletion
of eligible duplicate `raw_blocks` rows. Both flags are necessary for deletion.
Work is bounded to `max_batch` old blocks per invocation. Blocked oldest rows can
stall a batch: resolve/review their dependencies instead of silently skipping them.

Eligibility requires clean finalized records, a checkpoint beyond the slot,
complete bracketed minute/range evidence, no recovery hint or pin, and the explicit
reviewed recovery boundary. Archive publication is fsynced and atomic. Full block
and sidecar hashes, identities and counts are read back before a fresh SQLite
transaction rechecks eligibility and exact original block bytes, records the
archive catalog and deletes the duplicate together. Failures preserve originals;
crashes before the catalog may leave a reusable, verified orphan archive.

`EventStore.retained_block(slot)` verifies hot or archived evidence. Frozen-hour
replay uses it; ordinary candles/volume still query retained indexes directly.
Restore with `observer_retention.restore(store,slot)` during stopped maintenance.
Keep archives and catalog backups together. Missing/corrupt archives fail closed.
Move archives only with an explicit catalog/path migration; absolute paths are stored.
Filesystem corruption or operator deletion after verification remains a recovery
risk: keep a separate verified backup before deleting any duplicate.

## Capacity and maintenance

Accounting includes observer DB/WAL/SHM and the archive tree. Reserves/headroom are
checked before archival batches. To pause stream ingestion at budget boundaries,
set an endpoint's `retention` object to the same budget configuration plus an
absolute `archive_directory`; `onchain_stream` checks it before each recovery batch.
Other runners are not automatically guarded. Stop them during retention maintenance.
Caps are pre-write thresholds, not filesystem quotas: one in-flight batch, WAL,
codec temporary storage and concurrent unrelated writers can exceed estimates.
Use dedicated filesystem quotas and operator disk monitoring for hard limits.

Freed SQLite pages are reusable; deletion does not shrink the file immediately.
Compaction is planned, never automatically executed: stop consumers, checkpoint
WAL, back up DB/catalog/archives, verify restoration and budget sufficient temporary
space before an operator performs VACUUM. Never VACUUM under disk exhaustion.
The lifecycle retains indexed coverage/order headers, transactions and cold metrics.

## Server-only verification commands (not run)

```bash
python -m unittest discover -s tests -p 'test_observer_retention.py'
python -m unittest discover -s tests -p 'test_selfhost_observer.py'
python -m unittest discover -s tests -p 'test_onchain_indexer.py'
python benchmark_observer_archive.py --server-only /isolated/copied-evidence.json
```

New lifecycle tests mock the codec; existing archive tests exercise actual Zstd
when installed. Validate real archive restoration and frozen-hour replay on the
server before enabling deletion. No independence or production-readiness claim is made.

## Streaming compression

`observer_zstd.compress_stream` uses the CPython `zstandard` C extension: input
chunks up to 64KiB are compressed directly to the caller's sink with backpressure,
rolling SHA-256 hashes, single-threaded native compression and an 8MiB window.
No custom C allocation/pointer management is introduced. Input/frame totals remain
bounded; the sink and caller must discard partial output on failure. Timeouts are
cooperative between chunks. Arbitrarily blocking sinks must provide their own I/O
deadlines. The legacy `pack(bytes)` wrapper still materializes a bounded frame for
compatibility; block JSON decoding still materializes one bounded block. This is
not a claim that the entire existing ingestion pipeline has constant memory use.

`requirements.txt` and the Nix Python environment include `zstandard`; decompression
verification retains the existing bounded CLI implementation. API reference:
[python-zstandard streaming compression](https://python-zstandard.readthedocs.io/en/latest/compressor.html).
Run `test_observer_zstd.py` yourself alongside the retention tests. Neither was run here.
