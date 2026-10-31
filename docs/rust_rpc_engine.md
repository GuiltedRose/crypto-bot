# Experimental Rust RPC engine — UNTESTED

No builds, tests, dependency downloads, requests, benchmarks or deployments ran.
Python remains the default; no unsafe operations were introduced (`deny(unsafe_code)`).

## Reviewable migration boundaries

1. **Transport:** feature-gated `native/solana_binary/src/rpc.rs` adds Tokio,
   reqwest/rustls, bounded WebSocket transport and the PyO3 async-runtime bridge.
   Read-only method allowlist, no redirects/proxies, TLS or explicit literal
   loopback, 30-second HTTP deadline, 64MB response cap, 2MB WS message/frame cap,
   bounded write buffers, one in-flight operation per client, no response cache.
   Busy calls fail instead of building an unbounded queue. Runtime uses two worker
   threads/two blocking threads with explicit stack sizing. These are component
   limits, not an aggregate process RAM guarantee. Identity responses only; gzip
   from servers despite that request is rejected rather than decoded unboundedly.
2. **Compatibility:** `solana_rpc_compat.make_rpc` preserves IndexRpc's call,
   validate, source and telemetry contracts. `backend=python` is default;
   `auto` conservatively selects Python. `rust` is explicit and requires the
   feature-enabled extension; absence does not silently switch implementations.
   Existing Ingester performs provider failover ONLY for transport failures,
   applies program deployment/version checks and commits durable prefixes.
   Existing shared ProviderBudget governs backoff/Retry-After. Malformed response
   envelopes/access denials are terminal, not backend/provider fallback triggers.
   `call_async` is available to async callers; synchronous call is for existing
   ingestion workers and rejects use inside an active event loop.
3. **Subscriptions:** explicit `ws_backend` (defaulting to `backend`) selects a
   connector for the existing validated SubscriptionSession/StreamHints pipeline.
   Acknowledgments, bounded reconnect/backoff, dedup, durable gap notifications
   and finalized catch-up remain Python-owned. Rust handles bounded socket I/O,
   not a second competing subscription/recovery loop. Authorization/429 handshake
   denials persist provider state and stop for review. Silent WS idleness currently
   triggers a bounded 30-second receive timeout; production tuning remains pending.
4. **Processing/storage:** real binary decoder is reused by an offline RPC replay
   summarizer and `RustIndexRpc.decode_binary`. JSON blocks still pass through the
   established Pump/PumpSwap decoder and EventStore. Native program-event extraction,
   compact live market-event output and native event storage are NOT implemented.
   No equivalent validated native IDL/upgrade evidence exists yet; unknown layouts
   retain their existing quarantine. This is a transport migration, not completed
   end-to-end Rust indexing, independent verification or production readiness.

## Network authorization and persistent state

All live network operations require approved Linux isolation. Native transport
checks its current network namespace inode before opening/using sockets. Python
also checks fresh root-owned `/run/solana-budget-status.json`, whose matching inode,
expiry and optional-budget flag are published by the existing root controller.
It is read-only authorization telemetry, not the accounting database. The private
monthly ledger remains root-owned and outside the Nix store. The module supplies
coreutils for the controller's namespace-stat operation. Kernel quotas/shaping,
120GB normal cap, 150GB ceiling and 5GB margin remain authoritative and unchanged.
This code cannot attest nft rules against a malicious host administrator; service
inventory, namespace isolation and existing kernel/VM validation remain required.

Configuration additions on an explicitly reviewed source:

```json
{
  "backend": "rust",
  "ws_backend": "rust",
  "network_budget": {"status_file": "/run/solana-budget-status.json"}
}
```

Source URLs, source IDs, storage/stream rights and mint/program configuration stay
as in existing local/public/hybrid files. No source is enabled automatically.
The controller removes status on cleanup; stale status (>5 seconds), wrong inode,
normal-budget exhaustion or expiration denies native I/O. Optional collection never
uses reserve automatically. Each network session still needs explicit manual
startup and a charged grant. A status file is not permission to create a different
namespace or bypass the systemd boundary. Losing the controller is fail-closed,
subject to the documented kernel-lease/ISP limitations.

Live Rust transport on macOS is deliberately denied because this repository has
no equivalent approved kernel-boundary implementation there. Portable parsing,
offline replay and native packaging remain declared on all four platforms. No
platform build has been demonstrated. No credentials/production databases touched.

## Dependencies and exact operator commands

Cargo.toml adds exact direct versions of Tokio, reqwest, tungstenite, futures,
serde_json, base64 and pyo3-async-runtimes. The existing Cargo.lock is now STALE
and must be regenerated/reviewed; no transitive hashes or download results were
fabricated. Keep all new source/packaging/lock files tracked for git-backed flakes.
The existing pinned nixpkgs supplies maturin via maturinBuildHook. Optional native
package builds remain blocked until the lock resolves all new features.

```bash
nix develop
cargo generate-lockfile --manifest-path native/solana_binary/Cargo.toml
# Review and git-add Cargo.lock and new source/packaging files.
nix build .#native-rpc .#native-rpc-app
nix develop .#native-rpc
cargo test --locked --features rpc-engine --manifest-path native/solana_binary/Cargo.toml
python -m unittest discover -s tests -p 'test_solana_rpc_*.py'
python -m unittest discover -s tests -p 'test_solana_binary*.py'
python -m unittest discover -s tests -p 'test_onchain_indexer.py'
python -m unittest discover -s tests -p 'test_onchain_stream_candles.py'
python -m unittest discover -s tests -p 'test_solana_network_lifecycle.py'
nix build .#checks.x86_64-linux.solana-network-lifecycle
nix flake show
```

The existing kernel smoke tests remain separately opt-in/server-only. Offline
compatibility tests mock all native I/O; Rust tests cover request restrictions,
URL policy and backpressure without networking. The existing indexer/stream tests
cover checkpoint failover and bounded reconnect. End-to-end Rust HTTP/WS integration,
Rust cancellation/resource cleanup under actual sockets, and sustained memory
pressure remain UNVERIFIED and need isolated server fixtures before deployment.

```bash
python benchmark_solana_rpc_replay.py /isolated/authentic-base64-rpc-response.json
```

Identical hashed original RPC captures are replayed in separate Python/Rust worker
processes, serial document batches 1/8. Metrics cover RPC JSON parsing, base64 wire
parsing, status/order summary and Python binding—not network latency, decompression,
program-event decoding or storage. It reports throughput, document median/p95,
mean transaction time and process peak RSS. Allocation counts are null because no
reliable cross-language instrumentation is installed. Existing JSON-only captures
are unsupported rather than re-serialized into binary or used to invent a speedup.
No authentic binary workload, live transport performance or full Rust pipeline
speedup has been demonstrated.
