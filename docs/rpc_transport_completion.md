# RPC transport completion pass — UNEXECUTED

This implements transport and bridge changes only. No tests/builds/downloads,
network requests, benchmarks or deployment commands were executed. Passing tests,
measured speedup and production readiness are NOT claimed. Existing user-reported
passes predate these changes. Decoder, event, storage, NN and trading rules unchanged.

## Interface audit

| Consumer operation | Contract / owner |
| --- | --- |
| getGenesisHash | inherited IndexRpc.validate; exact existing mainnet check |
| getSlot, getBlocks, getBlock | finalized Ingester polling, canonical range/checkpoint commits remain Python |
| getAccountInfo | program/ProgramData provenance and version checks remain Python |
| getBlockTime, getSignaturesForAddress, getTransaction | existing read-only HTTP allowlist, unchanged result/null handling |
| logsSubscribe / ack / logsNotification | Rust bounded socket, Python SubscriptionSession validation and durable hints |
| disconnect / reconnect | existing bounded Python listen loop owns reconnect and gaps; no new hidden retries |
| sendTransaction / signing | prohibited; no native wallet API |

## Lifecycle changes

Native HTTP now exposes `request_sync` using the single persistent PyO3 Tokio
runtime with GIL release. Python IndexRpc.call no longer creates/destroys asyncio
loops. Client construction enters that runtime explicitly, so pooled connections
and timers are not attached to ephemeral Python loops. Async `request` remains
available and shares the same client, one-request permit and request formatting.
Concurrent calls fail backpressure rather than accumulating an unbounded queue.

`close` signals active HTTP cancellation and drops the cached client/idle pool.
The Python bridge supports close/aclose/context managers and rejects calls after
close. Sync calls inside an active Python event loop fail clearly; use call_async.
The global bounded Tokio runtime intentionally lives until process exit; closing
one client must not tear down another client's runtime. Native futures cancellation
and shutdown propagation are covered by written actual-socket tests, not yet run.

WebSocket send/read remain separate bounded halves. A quiet stream now uses a
20-second idle ping and 10-second liveness wait rather than failing after 30
seconds without a text notification. Replies/control floods remain bounded;
close interrupts both operations. The compatibility connector binds the supplied
URL to explicitly authorized ws_url/stream policy. HTTP 5xx handshakes may reach
existing bounded reconnect; access denial, malformed input and 429 do not silently
switch backend/provider. Retry-After/provider state remain persisted. No additional
independent reconnect/poll loop is introduced.

## Preserved fail-closed limits

Python/default and conservative auto backend remain unchanged. Explicit Rust mode
requires the rebuilt extension; no silent old-extension or malformed-data fallback.
Request allowlist/64KiB bounds, TLS or literal opted-in loopback, no HTTP redirects
or environment proxies, one idle pooled connection, 30s HTTP/15s connect bounds,
64MB identity-response cap and bounded WS buffers remain enforced. Unexpected
compression is rejected; native HTTP intentionally requests identity encoding.
Malformed JSON, mismatched IDs and ambiguous envelopes are terminal evidence errors.

Approved source policy, fresh root session status, namespace inode, shared provider
pacing, kernel quotas/shaping and existing 120/150GB allocations/5GB margin remain
required. Body-byte telemetry is not total traffic accounting. Nothing here grants
reserve bandwidth, changes local/public/hybrid sources, starts services or touches
persistent operational state. Local test mocks are confined to fixture harnesses.

## Server validation commands (not run)

```bash
nix build .#native-rpc .#native-rpc-app
nix develop .#native-rpc
cargo test --locked --features rpc-engine --manifest-path native/solana_binary/Cargo.toml
python -m unittest discover -s tests -p 'test_solana_rpc_compat.py'
SOLANA_NATIVE_LOOPBACK_STRESS=1 python -m unittest discover -s tests -p 'test_rpc_transport_complete.py'
SOLANA_NATIVE_LOOPBACK_STRESS=1 python -m unittest discover -s tests -p 'test_native_transport_stress.py'
python benchmark_rpc_transport.py --allow-loopback
```

Actual Rust socket tests use only ephemeral 127.0.0.1 servers. Cases cover every
HTTP allowlisted method, repeated sync calls, sync/async alternation, TCP connection
reuse, chunked responses, status/Retry-After, redirect rejection, oversized/invalid
responses, explicit disconnect retry, concurrent backpressure, cancellation,
HTTP close interrupting active work, WS send-during-recv, close/reconnect and a
31-second quiet interval. No external URLs/credentials or privileged setup. The
opt-in tests skip without Linux/current native extension; skips are not passes.

The benchmark uses identical fixed JSON responses and separate worker processes
for Python and Rust, serial batches 1/16. It measures calls/s, median/p95, connections
and whole-process peak RSS; local fixture-server memory is included. Fixture-only
mock provider pacing is disabled equally to measure transport rather than imposed
sleep. Allocations remain unavailable. This is neither WAN performance nor full
indexer/event throughput. No benchmark values exist until the operator executes it.

## Remaining operational acceptance

Rebuild and run the above suites; inspect all failures/skips, cancellation cleanup,
connection counts and reports. Real provider identity/compression behavior, TLS/DNS
outages, sustained RSS, simultaneous multiple clients, long-running WS liveness,
ISP/kernel accounting and rollout/rollback still need authorized monolith/shadow
validation. macOS live transport remains blocked pending equivalent enforcement;
portable parsing/offline builds are unchanged. Under the no-execution constraint,
this deliverable cannot honestly include passing tests or measured results.
