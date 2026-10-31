# Read-only Solana RPC backend: completion audit

Implementation status: ready for operator build/integration validation, **not
validated or production-ready**. No tests, builds, downloads, benchmarks, network
requests or deployments were executed for this assignment. No passing results,
performance improvement or independent-mainnet coverage is claimed.

## Implemented requirements and ownership

| Requirement | Implementation / owner | Remaining acceptance |
| --- | --- | --- |
| Finalized head/blocks/account/transaction reads | Rust HTTP; IndexRpc-compatible bridge | Actual provider fixtures |
| getGenesisHash/getSlot/getBlocks/getBlock/getAccountInfo/getBlockTime/getSignaturesForAddress/getTransaction | Native read-only allowlist; shared result contract | Monolith socket suite |
| getFirstAvailableBlock/minimumLedgerSlot | New native/Python read-only methods | Provider retention semantics |
| getHealth/getVersion | New optional diagnostics; never polled automatically | Provider support |
| Strict slot/results/errors | Shared rpc_contract; typed history/evidence/HTTP/source errors | Offline regressions |
| Historical gaps | Probe full-block floor before/after getBlocks; reject pruned/racing windows; null/pruned blocks preserve prefix | Real retention boundaries |
| Provider failover | Existing configured HTTP sources only; explicit source/category logging; no malformed/auth fallback | Fault/recovery fixtures |
| Throttles/retries | Shared ProviderBudget and existing bounded retry loop; Retry-After; no retry inside native HTTP | Actual 429/date headers |
| HTTP reuse/runtime | Persistent Tokio runtime, sync GIL-releasing entry, async bridge; one active request/client | Repeated-call/connection tests |
| HTTP bounds | No proxies/redirects, request/response caps, deadlines, idle pool bound; identity encoding | Sustained RSS |
| HTTP failure status | Status/Retry-After returned before parsing arbitrary error bodies/encodings | Encoded-error regression |
| Subscription requests/ack/identity | Rust bounded socket; existing Python SubscriptionSession remains protocol coordinator | Actual socket + protocol tests |
| WS heartbeat/cancellation | Independent bounded halves, control-rate bounds, ping/pong, close signal and cleanup | Quiet/stress tests |
| WS reconnect/failover | Existing bounded reconnect; new explicitly approved source set, gaps/switch counters/logging | Fault-injection tests |
| Finalized recovery/dedup/storage | Python-owned durable checkpoints and hints, unchanged semantics | Restart regressions |
| Initial finalized discovery | Configured Rust bridge, persisted initial boundary; checkpoint always wins | Existing startup tests |
| Cleanup | Observer/local/indexer runners close all owned clients, even on error/interrupt | Cleanup regressions |
| Namespace/accounting/quotas | Existing root session, source rights, provider budget and kernel boundary | Existing NixOS/kernel suites |
| Benchmark infrastructure | Existing identical loopback response benchmark + captured-data replay | Operator measurements |

No wallet, signing, transaction submission, P2P validator, transaction-context
migration or event-decoder rewrite is part of this RPC scope. Python coordination
is an intentional architectural owner, not a second competing Rust observer.

## New failure safeguards

Previously getBlocks=[] on a pruned provider could resemble genuine skipped slots.
Actual IndexRpc clients now advertise the required history probe. The Ingester
checks getFirstAvailableBlock before and after obtaining the produced-slot list;
an advancing/unavailable history floor cannot advance the checkpoint. Synthetic
legacy duck-typed test adapters without this capability are not production source
implementations. Real adapters must implement it; unsupported probes fail closed.
minimumLedgerSlot is diagnostic only: retained shreds do not prove full-block
availability. Remote RPC manifests remain provider claims, not independent consensus.

RPC history codes (-32001/-32004/-32009/-32011/-32014) are distinct from malformed
payloads and authorization errors. Explicit configured backups may recover unavailable
history from the existing durable prefix; empty results/nulls are not invented
blocks. Unsupported versions/invalid requests stop for review. HTTP errors preserve
status, source ID and category; bodies cannot obscure 401/403/429 classification.

Canceled streams now persist an explicit recovery gap. A listener error during the
last requested batch is checked before reporting success. Unexpected normal listener
exit is an error. Genesis validation precedes concurrent stream/recovery, avoiding
races for a native client's single permit. No checkpoint jumps or unbounded catch-up.

## Explicit WebSocket failover configuration

Default topology is unchanged. To approve WS failover, set top-level
`"stream_failover": true` with at most four enabled, uniquely identified endpoints.
Each needs reviewed storage AND stream policy, an explicit approved ws_url, and the
same network_budget as the primary. All sources pass configured-client mainnet
validation before streaming. Pure transport/reconnect exhaustion may switch to the
next approved source, with explicit logs/state/counters. Malformed notifications,
authorization failures, throttle/governor startup rejection and mismatched budgets
stop rather than switching. Native/Python connector selection follows each source's
explicit backend. No source is enabled and no permission is granted by this change.

## Modified files

- `native/solana_binary/src/rpc.rs`: methods; early HTTP error classification.
- `rpc_contract.py`: shared bounded result/error contract.
- `solana_rpc_compat.py`: validation/classification, complete client cleanup,
  explicit fixed WS-option enforcement.
- `onchain_ingest.py`: history probes, strict head check, logged approved failover.
- `onchain_stream.py`: source preflight/failover, cancellation/malformed gaps,
  final-batch listener error detection and native connector selection.
- `solana_observer.py`: approved bounded stream source configuration and cleanup.
- `run_onchain_indexer.py`, `run_local_observer.py`: owned RPC cleanup.
- `tests/rpc_loopback_fixture.py`, `tests/test_rpc_transport_complete.py`: added
  methods and encoded/non-UTF8 HTTP error fixtures.
- `tests/test_observer_migration_stress.py`: cancellation gap assertion.
- `tests/test_rpc_backend_contract.py`: result, history race, approved backup and
  all-client cleanup regressions.
- `tests/test_rpc_stream_failover.py`: explicit switching, budget rejection,
  terminal malformed evidence and last-batch failures.
- `docs/rpc_backend_completion_matrix.md`: this requirements/delivery record.

## Exact monolith commands (operator executes)

No new Cargo dependencies were added; keep the reviewed lockfile and locked/offline
Nix build. Ensure all new files are tracked before evaluating the git-backed flake.

```bash
nix build .#native-rpc .#native-rpc-app
nix develop .#native-rpc
cargo test --locked --features rpc-engine --manifest-path native/solana_binary/Cargo.toml
python -m unittest discover -s tests -p 'test_rpc_backend_contract.py'
python -m unittest discover -s tests -p 'test_rpc_stream_failover.py'
python -m unittest discover -s tests -p 'test_observer_start_slot.py'
python -m unittest discover -s tests -p 'test_solana_rpc_compat.py'
python -m unittest discover -s tests -p 'test_onchain_indexer.py'
python -m unittest discover -s tests -p 'test_onchain_stream_candles.py'
python -m unittest discover -s tests -p 'test_observer_migration_stress.py'
SOLANA_NATIVE_LOOPBACK_STRESS=1 python -m unittest discover -s tests -p 'test_rpc_transport_complete.py'
SOLANA_NATIVE_LOOPBACK_STRESS=1 python -m unittest discover -s tests -p 'test_native_transport_stress.py'
python benchmark_rpc_transport.py --allow-loopback
nix build .#checks.x86_64-linux.solana-network-lifecycle
nix flake show
```

## Remaining limitations and readiness

Tests executed here: **0**; new failures/skips and performance values: **unknown**.
Existing user-reported passes precede this change. The written tests are not
proof of correct execution; passing tests/measured results require the commands.

No known missing observer-required method remains in this audited contract. This
is an implementation assessment, not an exhaustive correctness guarantee. Real TLS,
DNS, 429s, source history completeness, outages, long-running cancellation/RSS,
provider-specific content encoding and source rights require authorized shadow
validation. Native HTTP deliberately requests identity and rejects unexpected
compression; no gzip-expansion bypass is introduced. Live Rust transport on macOS
remains denied until an equivalent approved kernel boundary exists, while portable
crate builds/offline parsing remain declared for all four platforms.

The 120GB normal /150GB absolute allocation and 5GB margin, short manual network
sessions, no automatic renewal, and ISP-level accounting limitations are unchanged.
A provider lacking required history is an operational blocker, not permission to
skip forward. Pump layout quarantine and incomplete historical USD data remain
outside RPC transport completion and may still close market gates. No profitable,
production-ready or independently verifying Solana system is claimed.
