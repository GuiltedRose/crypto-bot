# Observer migration stress review — 2026-10-31

## Execution/result status

User reports the preceding offline suites passed. This change was NOT executed:
new tests/builds/benchmarks/network requests: **0**. New pass/failure/skip counts
and Python/Rust throughput, p95 and RSS: **NOT MEASURED**, not zero performance.
No larger transaction-context port, live deployment or production data changes.

## New bounded verification software

- `test_observer_migration_stress.py`: default socket-free tests with scripted
  bursts, deterministic scheduling delays, three disconnect/reconnect sessions,
  8-entry hint capacity, 256 notifications, duplicate pressure, SQLite reopen,
  finalized-prefix recovery, unchanged checkpoints/events, cancellation cleanup,
  and provider-error/malformed-response bursts. Maximum async scenarios 2–5s.
  These validate Python integration, not the actual Rust network implementation.
- `test_native_transport_stress.py`: explicitly opt-in Linux native HTTP/WS tests
  using only ephemeral 127.0.0.1 servers. Tests request shape, HTTP 429/503 and
  Retry-After preservation, single-request backpressure, cancellation/permit reuse,
  simultaneous WS send/receive, 32-message bursts, three explicit reconnects and
  close interrupting pending receive. Each wait is bounded. No external endpoints,
  DNS lookup, namespace creation, firewall changes or production files are used.
  Native availability and the explicit environment flag are required, otherwise
  tests skip. Loopback success is NOT kernel quota or Internet validation.
- `observer_migration_report.py`: verifies original fixture SHA-256 manifests,
  replays authentic recorded slot/index/time across Python/Rust/compare decoders,
  reports exact parity, errors/quarantine and native availability. Optional
  subprocess benchmarks have 120s per-fixture limits and separate workers.
  Outputs refuse overwrite. Source ML rights remain UNKNOWN; no training import.
- Existing event benchmarks now include quarantine rates. Measurements cover
  receipt decoding/context checks/normalization/projection, not full HTTP+DB
  performance. Native schema compilation and Python binding costs remain included;
  allocation metrics remain unavailable. No memory ceiling is inferred from RSS.

## Recorded layout blockers, not newly measured results

The captured fixture manifest and prior validation artifacts already record:

| Program/event | Recorded payload | Pinned fields | Unknown tail |
| --- | ---: | ---: | ---: |
| Pump TradeEvent | 397 bytes | 389 bytes | 8 bytes |
| PumpSwap SellEvent | 441 bytes | 433 bytes | 8 bytes |
| PumpSwap BuyEvent (prior hour audit) | 489 bytes | 481 bytes | 8 bytes |

Sources: `tests/fixtures/onchain/manifest.json`,
`research/onchain_indexer_validation.json`, `research/onchain_hour_validation.json`.
These are historical local records, not a fresh replay or official schema update.
Equal eight-byte tails do not establish identical meaning or permission to trim.
Pinned IDLs, event authority/CPI binding and exact-length requirements remain in
force. A shared quarantine result proves rejection parity, not useful event coverage.
Need deployed-version-bound authoritative layouts plus authentic accepted receipts
for both venues before promoting a native transaction-context implementation.
No external documentation/source retrieval was attempted in this task.

## Next migration gates and priority

1. **Gate:** run new socket tests and differential report; inspect all skips and
   exact errors. Obtain valid accepted captures alongside quarantined ones. Run
   repeated bounded workers for sustained RSS evidence; investigate divergence
   before any larger port. Nothing is yet newly certified safe to migrate.
2. **First candidate after the gate:** pure canonical account-key/index resolution,
   retaining original tx order, static/loaded key distinctions and explicit missing
   lookup errors. Compare output on the same authentic receipts before activation.
3. Metadata status/fee/decimal validation and standard SPL transfer parsing, retaining
   strict transaction-scoped attribution and unresolved-extension quarantine.
4. Authenticated CPI stack/parent binding and event-authority PDA verification,
   with version/source checks still authoritative. No missing-height inference.
5. Swap normalization and duplicate/supplement matching; only after exact event
   schemas and amounts agree. Keep storage/checkpoints Python-owned until the
   accepted-event differential corpus demonstrates equivalence.

Python remains default throughout. Do not turn cached observations, binary parsing,
field parity or mocks into execution/consensus evidence or trade authorization.

## Exact operator commands (not run)

```bash
nix develop .#native-rpc
python -m unittest discover -s tests -p 'test_observer_migration_stress.py'
python -m unittest discover -s tests -p 'test_solana_rpc_compat.py'
python -m unittest discover -s tests -p 'test_native_events.py'
# Explicit loopback-only native tests, on the monolith with no production workload:
SOLANA_NATIVE_LOOPBACK_STRESS=1 python -m unittest discover -s tests -p 'test_native_transport_stress.py'
python observer_migration_report.py --output /tmp/migration-replay-001.json
python observer_migration_report.py --benchmarks --output /tmp/migration-benchmark-001.json
```

Reports contain actual operator replay/benchmark results only after those commands
run. Unit-test counts/failures/skips come from unittest output; the report program
itself executes no unit suites. Missing native support must not be counted as a
successful Rust validation. Production readiness and real-network latency remain
unproven even if all these offline/loopback checks pass.
