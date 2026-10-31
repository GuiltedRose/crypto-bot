# Independent mainnet node: current decision

Status: **NO-GO on the declared hardware without contrary measured evidence**.
This is an engineering assessment, not a proof that every experimental client
must fail. No node, tests, benchmarks or hardware probes were run. No independent
execution or consensus implementation has been added.

The intended design retains required current chain state and prunes historical
ledger data after recovery. Bot-specific market evidence is retained separately,
compressed after execution verification. Selective historical retention is useful
and is not being rejected.

## Evidence and constraints

Official [Anza requirements](https://docs.anza.xyz/operations/requirements),
reviewed 2026-10-30, recommend 256GB RAM, a modern CPU at 2.8GHz+, SHA extensions,
and separate NVMe capacity for accounts (1TB), ledger (1TB), and snapshots
(500GB). Full account indexes increase RAM guidance to 512GB; we do not require
those indexes. An unstaked node needs a stable public IPv4 and symmetric 1Gbps.
These are recommendations, not measured minimum allocations for this server.

The supplied 64GB RAM is one quarter of base RAM guidance. The supplied 884GB free
space is below that disk provisioning guidance even before archive growth and
reserve space. Actual device topology, IOPS, endurance, instruction support and
network capacity are unverified. Combining older CPU sockets does not establish
adequate replay latency. Compression ratios and sustained replay are unmeasured.
No particular compressed chain-state size or throughput is assumed.

[Anza RPC setup](https://docs.anza.xyz/operations/setup-an-rpc-node) uses the
validator process without voting. Ledger-size limits reduce retained history;
they are not a replacement execution engine. A viable future deployment needs
verified bootstrap/genesis provenance, replay, fork/root verification and gap
recovery. No external market-data RPC fallback is proposed here.

Python remains suitable for the management/index/archive/interface layer, but it
does have automatic memory management and garbage collection. Building a new
compatible Python execution/consensus client is not an established way around
these resource constraints and is outside the implemented components.

## Independently useful components delivered

- `node_capacity.py`: pure declared-inventory assessment; no probing or startup.
  `monolith.inventory.json` records supplied figures and leaves unknowns unknown.
- `benchmark_observer_archive.py`: explicit operator-run, bounded copy-sample
  Zstandard benchmark using existing checked `pack`/`unpack`; levels 1/3/6,
  compression ratio and encode/decode time. No deletion or database changes.
- `solana_observer.local.example.json`: disabled loopback HTTP/WS configuration.
  `require_local_replay` rejects remote endpoints, including fallbacks, before
  database creation. Loopback alone is not evidence of independent verification:
  the operator must establish that it reaches an actual self-operated replay node.
- Existing strict decoding, retained block provenance, incremental candles and
  copy-only checksummed archives are reused. No alternative RPC server is
  misrepresented as an independent node.

No automated pruning is introduced: safe deletion needs verified restored archives,
recovery watermarks and explicit dependency protection. Never prune live accounts
or data needed for replay, quarantined evidence, missing intervals, learning
provenance or frozen evaluation snapshots. No operational data was deleted.

## Operator-only validation, on the server

The following commands are provided, **not executed**:

```bash
python node_capacity.py monolith.inventory.json
python -m unittest discover -s tests -p 'test_node_capacity.py'
python -m unittest discover -s tests -p 'test_solana_observer.py'
python benchmark_observer_archive.py --server-only /isolated/copied-evidence.json
```

The capacity command reads declarations, not physical hardware. The benchmark
requires `zstd` (available in the existing Nix development shell); one sample
cannot predict aggregate storage savings. Include incompressible evidence in
operator measurements. No validator launch command, auto-start service or new
network connection is included. Do not start a node on the laptop.

Reconsider node deployment only after proving adequate state/snapshot/ledger
capacity with headroom, sustained independently verified replay/finalization,
recoverable gaps, and complete real market windows. Until then the observer's
local connection remains disabled. Trading state, NN settings, risk limits,
transaction submission and ten-promotion policy remain unchanged.

Mandatory network constraints and the disabled, unexecuted enforcement components
are described in [network budget](solana_network_budget.md). The design is not
approved for independent mainnet operation within the available transfer budget.


## Rust components and RAM headroom

The development shell includes Rust tooling. No Rust replacement is implemented
or benchmarked yet. Existing streaming compression already uses native libzstd
through CPython; language changes alone do not establish lower peak memory.
Budget accounting is low-volume SQLite work, not a demonstrated RAM bottleneck.

A justified Rust target would be bounded streaming decode/aggregation that avoids
materializing full block JSON. Preserve canonical ordering, hashes, quarantine,
archive compatibility and existing byte limits across any language boundary.
Use bounded channels and fixed worker counts; never queue unlimited blocks behind
a slow compressor. Measure peak RSS, codec workspace, disk buffers and throughput
on the monolith before selecting a replacement or setting process memory limits.
Leave explicit host headroom; Rust does not remove Solana account-state costs,
prevent all allocation failures, or replace cgroup memory enforcement. No node,
benchmark, build or test was run for this tooling change.
