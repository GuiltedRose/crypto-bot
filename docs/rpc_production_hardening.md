# RPC production hardening — validation pending

No builds, tests, benchmarks, sockets, external requests or deployments were run.
The user's reported baseline is 487 Python tests (479 passed, 8 skipped), 9 Rust,
3 transport and 3 native stress tests passing. Those results predate this change.
The attachment's instruction to run tests/benchmarks is superseded by the user's
explicit no-execution restriction. New pass/fail/skip counts are unavailable.

## Root causes and changes

**Failover:** hint tables and pending-signature queue are shared across sources;
the original primary handle could still delete shared rows. The defect was stale
handle ownership and missing explicit source reporting, not proof that backup rows
were ignored by SQLite. The supervisor now atomically selects the active hints
handle/endpoint before listening. Recovery, budget checks and reporting use that
selection. Reports expose active_source_id from persisted state. A reopened DB
retains the last selected source; a new primary connection updates it explicitly.
All-source dedup/recovery stays single-worker and checkpoint-driven. Raw observations
retain their source identity. Same signature/same slot from another approved source
no longer creates a false fork gap; different-slot conflicts still do.

Regression software delivers real hint notifications through a scripted backup,
recovers only the finalized prefix, verifies pending backup data, reopens storage,
switches to primary and checks source identities, gaps, dedup and unchanged events/
checkpoints. Scripted tests do not validate native socket behavior themselves.

**Response contract:** Python previously accepted envelopes that Rust rejected.
Both now use one strict parser/result/error contract. Version 2.0, integer ID 1,
result/error exclusivity, integer error codes, existing result-type checks, duplicate
keys and nonfinite JSON values are validated equally. Malformed evidence is terminal,
not an automatic provider fallback. Authorization/throttle/history error behavior
and shared governor accounting remain enforced. The existing denial fixture now
contains a valid JSON-RPC envelope so it continues testing persistent denial.

**Other consistency:** approved read-only session status works for both transports,
including Python rollback and mixed-backend stream failover; it does not expose or
relax the private accounting ledger. Future-dated as well as stale session files
fail closed. An explicit client close during an active request no longer records
a false provider outage/backoff; it reports local client shutdown. No budgets, kernel rules, read-only allowlists, source rights, event
decoding, wallets, trading or learning state were changed.

## Latency: known facts versus hypotheses

User-reported historical benchmark, 160 measured calls:

| Metric | Python | Rust |
| --- | ---: | ---: |
| Requests/s | 43.18 | 23.80 |
| Median | 20.65ms | 42.00ms |
| Connections including warmup | 161 | 1 |
| Peak RSS | 83.94MB | 46.20MB |

After-change results: **NOT MEASURED**. Connection reuse alone did not prove better
latency. Static inspection found the fixture sends HTTP headers and body in separate
socket writes. A roughly 40ms delay is consistent with Nagle/delayed-ACK interaction
on a persistent connection, but this is NOT an established root cause. Scheduling,
GIL/FFI, allocation/strict parsing and lock contention remain alternative contributors.
No production optimization is justified or enabled without measurements.

`benchmark_rpc_transport.py --ab` compares separate worker processes with identical
response-body hashes, serial concurrency and reported TCP connection counts:

| Case | Single variable versus baseline |
| --- | --- |
| baseline | Existing split fixture writes, unchanged server/client TCP defaults |
| server-nodelay | Server TCP_NODELAY=true only |
| server-coalesced | One fixture header+body write only |
| client-nodelay-off | Rust client TCP_NODELAY=false only |
| client-nodelay-on | Rust client TCP_NODELAY=true only |

Server socket NODELAY is observed and reported; native override=None means unchanged
library default, not a measured default. Client-only cases run Rust only. Native
constructor gained an optional benchmark override; the compatibility bridge continues
using the old three arguments, preserving production defaults. Default fixture
packetization is unchanged. The coalesced path uses fixture-only HTTP internals.
Results include latency median/p95, throughput, connections and process RSS (including
local fixture memory). No reliable allocation metric is available. Provider pacing
is disabled equally only inside the local harness, not production.

Run multiple independent A/B trials. If changing only server packetization/NODELAY
collapses the 42ms mode, that supports a fixture/TCP interaction; it does not prove
an Internet speedup. If it persists, compare binding-only/parser benchmarks and
profile scheduler/locks before changing production tuning. No live tuning change
is authorized by the historical figures alone.

## Modified files

`onchain_stream.py`, `rpc_contract.py`, `onchain_ingest.py`, `solana_rpc_compat.py`,
`solana_observer.py`, `native/solana_binary/src/rpc.rs`, `benchmark_rpc_transport.py`,
`tests/rpc_loopback_fixture.py`, `tests/test_rpc_stream_failover.py`,
`tests/test_onchain_indexer.py`, `tests/test_solana_rpc_compat.py`, `tests/test_rpc_response_parity.py`,
`tests/test_rpc_latency_experiments.py`, `docs/rpc_production_hardening.md`.

## Operator build/test/benchmark commands — not executed

```bash
nix build .#native-rpc .#native-rpc-app
nix develop .#native-rpc
cargo test --locked --features rpc-engine --manifest-path native/solana_binary/Cargo.toml
python -m unittest discover -s tests -p 'test_rpc_stream_failover.py'
python -m unittest discover -s tests -p 'test_rpc_response_parity.py'
python -m unittest discover -s tests -p 'test_rpc_latency_experiments.py'
python -m unittest discover -s tests -p 'test_solana_rpc_compat.py'
python -m unittest discover -s tests -p 'test_observer_start_slot.py'
python -m unittest discover -s tests -p 'test_onchain_indexer.py'
SOLANA_NATIVE_LOOPBACK_STRESS=1 python -m unittest discover -s tests -p 'test_rpc_transport_complete.py'
SOLANA_NATIVE_LOOPBACK_STRESS=1 python -m unittest discover -s tests -p 'test_native_transport_stress.py'
python benchmark_rpc_transport.py --allow-loopback --ab --iterations 10 > /tmp/rpc-ab-01.json
nix build .#checks.x86_64-linux.solana-network-lifecycle
```

Use fresh filenames for repeat trials. Default unit discovery skips privileged/
opt-in socket cases; inspect every skip. These commands do not authorize external
RPC use. Real-provider validation requires the existing approved source, manual
network session and quota boundary.

## Controlled shadow deployment and rollback

1. Stop listed workloads before updating. Back up the external budget ledger and
   observer DB plus WAL safely while stopped; preserve archives/catalog together.
   Never roll accounting back with a Nix generation or replace it with an old backup.
2. Update the host's existing pinned application input/module; review dry activation
   and run the above isolated validation. No service is enabled automatically here.
3. Use an external reviewed config with explicit `backend=rust`, `ws_backend=rust`,
   existing storage/stream rights, and `network_budget.status_file` set to
   `/run/solana-budget-status.json`. Leave event_backend=python and stream failover
   off initially. Keep URLs/credentials outside the Nix store. Use a NEW shadow DB,
   e.g. `state/rpc-rust-shadow-001/observer.sqlite`; never a trade/learning ledger.
4. Run the observer as an explicitly listed non-root systemd workload in the existing
   approved namespace, using the native-rpc-app wrapper. Start that workload manually;
   its Requires/BindsTo lifecycle charges a bounded session before opening the veth.
   Do not start an unrestricted shell observer outside the boundary. Suggested initial
   arguments (through the configured service's ExecStart):

```bash
crypto-bot-observer --config /private/reviewed-observer-rust.json \
  --db /persistent/rpc-rust-shadow-001/observer.sqlite \
  --batch-slots 1 --interval 5 --max-cycles 1
```

5. Inspect `journalctl -u solana-budget-network -u YOUR_OBSERVER_UNIT`, budget report,
   active_source_id, pending-hint age, gaps, lag, quarantine counts and process RSS.
   Read-only status: `crypto-bot-observer --db PATH --status`. Optional diagnostics
   getHealth/getVersion/minimumLedgerSlot must be explicit approved calls, not a new
   background poller. Expect current Pump layout quarantines/coverage gates to remain.
6. Stop the observer/controller deliberately; verify the veth disappears. Restart
   only with an explicitly authorized new session and the SAME DB. Its checkpoint
   wins over discovery/start-slot; no automatic unlimited catch-up. Continue bounded
   shadow sessions and compare restored source/checkpoint/error behavior.
7. Bound logs using host systemd-journald retention settings and logrotate for the
   external network-events JSONL (reopen-per-write supports rotation). Set quotas
   appropriate to other host workloads; do not truncate SQLite/accounting/evidence.
   Monitor free disk, DB/WAL/archive caps and cgroup/process RSS. No host logging
   policy or service configuration is changed by this code.
8. Rollback: stop the workload and controller, switch the reviewed config's backend
   and ws_backend to python, keep event_backend=python, preserve source permissions,
   status-file budget configuration, namespace service inventory and the SAME DB.
   Select the Python app wrapper if needed. Start a fresh bounded manual session.
   Never restore obsolete quota rules, zero the ledger, delete state or move the
   checkpoint to the latest tip to make rollback appear healthy.

## Acceptance matrix

| Condition | Evidence class | Current status |
| --- | --- | --- |
| Previous unit/socket baseline | User-reported monolith measurement | Passed prior revision |
| Active backup recovery/restart/switch-back | New scripted regression | Written; NOT RUN |
| Cross-backend malformed/error parity | New socket-free regression | Written; NOT RUN |
| Actual socket reuse/cancellation/quiet WS | Existing opt-in real loopback tests | New revision NOT RUN |
| TCP latency explanation | Static hypothesis plus new A/B controls | Unresolved pending measurements |
| Namespace/lease/quota lifecycle | Existing isolated VM/kernel tests | New revision NOT RUN |
| Production-provider history/TLS/429/DNS behavior | Real network | NOT VALIDATED |
| Sustained RSS/latency/resource cleanup | Operator stress/monitoring | NOT MEASURED |
| Trading/event-data eligibility | Existing strict validation | Not relaxed; no new approval |

Promotion requires all applicable tests actually passing without unexplained skips,
measured A/B explanation, equivalent accepted/quarantined output, unchanged durable
checkpoints across interruption/failover, bounded stable resource use, and authorized
shadow/provider evidence. Production readiness is blocked pending those results;
loopback tests alone cannot validate real-network behavior or a profitable bot.
