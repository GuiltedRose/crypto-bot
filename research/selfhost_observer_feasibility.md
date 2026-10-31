# Self-hosted Solana observer: feasibility and unexecuted prototype

2026-10-30. **Tests, benchmarks, connectivity probes, prototype programs and validator
startup were NOT run, at the user's explicit request.** Code below is unvalidated.
Official documentation was researched; no result is represented as a measured
server capability. Existing trading/learning records, risk settings, frozen
challengers and ten-promotion eligibility were not changed.

## Decision

A lightweight shred/P2P listener cannot establish executed successful swaps,
inner instructions or finalized canonical coverage by packet reception alone.
Do not build a custom stripped validator on the assumption it removes global
state/replay costs. The existing dual E5-2630 v4 / 64GB server is a plausible
**indexing/cache/archive host**, not a demonstrated viable mainnet replay node.
The minimum credible independent execution source is a properly synchronized,
non-voting Agave replay node, optionally with Geyser, on adequate hardware.

The provided prototype is a **local replay-node bridge**, not a validator, P2P
listener or implemented Geyser plugin. It reuses our finalized indexer and candles
through a loopback-only RPC. It becomes useful only when an operator supplies a
real replaying node. A local proxy to external RPC is still external dependence.
No validator binary, staking/vote identity, keys or paid service was created.

## Architecture and essential components

| Approach | Information available | Feasibility / limitation |
| --- | --- | --- |
| Shred/non-validating transport listener | Candidate ledger fragments and transaction payloads; possible missing fragments/forks | Cannot infer execution success, account effects, CPI/fees or finalized root. Gossip participation is not entitlement to complete Turbine delivery. **NO-GO** as a verified swap feed. |
| Modified/stripped Agave | Depends on what remains | Removing accounts/runtime/fork validation destroys independently reconstructed execution. Maintaining a fork adds protocol/upgrade/security obligations. No proven low-RAM shortcut established. **NO-GO** for a custom fork now. |
| Non-voting Agave + local read-only RPC | Executed transaction metadata, ledger ordering, commitment and retained history, if configured and synchronized | Smallest practical source compatible with our existing ingestion contract. Still replays global state. **CONDITIONAL**, hardware upgrade and operational proof required. |
| Agave + version-matched Geyser/Yellowstone | Transaction/account/slot/block callbacks, potentially lower RPC overhead | Needs the replay node underneath, matching plugin ABI/protobuf and explicit root/gap handling. A remote service remains an external execution-data source. **CONDITIONAL**, not implemented here. |

[Agave anatomy](https://docs.anza.xyz/validator/anatomy) distinguishes leader TPU
from validating TVU. [Turbine](https://docs.anza.xyz/consensus/turbine-block-propagation)
distributes ledger fragments through a stake-weighted topology; receiving fragments
is not proof of complete history, correct execution or finalization. Independent
execution requires snapshot/genesis trust checks, accounts state, runtime/program
execution, transaction verification, ledger/repair, fork processing and rooted
chain tracking. Other programs mutate state that Pump swaps depend on; filtering
out all non-Pump state changes before execution is unsafe.

Disable voting with the documented `--no-voting`; this removes vote submission,
not replay/account costs. [RPC node guidance](https://docs.anza.xyz/operations/setup-an-rpc-node)
says RPC nodes run the validator process and need no vote account. Use private,
loopback-bound RPC; `--private-rpc` alone is not a network firewall. Broad account
indexes, public HTTP service, and unnecessary RPC workloads can be omitted.
Retain the local history/extended execution metadata needed for full block and
inner-instruction recovery; enable only version-supported settings. Ledger pruning
must not remove unrecovered intervals. Do not disable verification or weaken
commitment to reduce load. This report does not prescribe unverified launch flags.

[Geyser's official interface](https://docs.anza.xyz/validator/geyser/) exports
post-execution information to external stores. Callback arrival order must not be
used as transaction order; use slot/transaction index plus canonical event index.
Transactions may precede completion/root notifications; keep forks provisional
until rooted and reconcile counts/parents. [Yellowstone](https://github.com/rpcpool/yellowstone-grpc)
is a stream baseline, not an independent lightweight execution engine. Pin node,
plugin and protobuf versions and validate recovery semantics before deployment.
A narrow transaction filter reduces export volume, not validator replay workload.

## Hardware assessment (specification comparison, not a benchmark)

[Intel's E5-2630 v4 specification](https://www.intel.com/content/www/us/en/products/sku/92981/intel-xeon-processor-e52630-v4-25m-cache-2-20-ghz/specifications.html)
lists Broadwell, ten cores/twenty threads, 2.20GHz base, 3.10GHz maximum turbo and
AVX2 per socket. Two sockets provide the stated 20/40 count, but not modern
single-thread throughput. Turbo maximum is not a sustained all-core guarantee;
NUMA/QPI and simultaneous archive work also matter. Verify actual CPU flags on the
server rather than assuming required SHA instruction support from AVX2.

Current [Anza recommendations](https://docs.anza.xyz/operations/requirements)
include modern CPU generations, 2.8GHz+ base and SHA support; 12 cores/24 threads
for validator work, 256GB+ RAM, with more for broad RPC indexes. They recommend
separate high-endurance NVMe accounts/ledger devices around 1TB each and snapshot
storage around 500GB. Unstaked networking guidance is 1Gb/s symmetric plus public
IPv4. These are recommendations, not proof of a minimum that every workload meets.
64GB is one quarter of the listed RAM baseline. The current server's CPU generation
and base clock also fall below the recommendation; thread count does not offset
those limitations. No evidence supports fitting mainnet replay into this machine.

BTRFS describes the filesystem, not disk type, IOPS, bandwidth, endurance or free
capacity. Devices, NIC/link, sync time and sustained fsync rates are UNKNOWN.
Startup needs a verified usable snapshot/genesis and sufficient space/bandwidth;
swap is not an equivalent replacement for accounts RAM. No mainnet synchronization
or validator benchmark was attempted. Smallest credible plan: keep this host for
analytics/archives; obtain sufficient replay hardware or an authorized external
executed-data feed. Exact hardware/service cost remains UNKNOWN; nothing purchased.

## Prototype and boundary-volume correction

`run_local_observer.py` permits only literal loopback IP endpoints. Plain HTTP is
allowed solely with explicit `allow_loopback_http`; ordinary indexer endpoints
still require HTTPS by default. No external fallback, DNS endpoint or execution
method is introduced. Source storage review, mainnet identity, pinned program
versions, finalized manifests, decoder strictness, idempotent canonical events and
checkpoints all reuse existing components. New DB required unless `--resume`
explicitly names an observer index; unrelated trading DBs are rejected.

`CandleEngine.rolling()` now requires complete coverage of the **entire exact
requested interval**, including partial boundary minutes, before returning volume.
Unknown coverage returns zero amounts **with an explicit error**, not a valid
zero-activity observation. Existing freshness, $5,000 hourly volume and completed
candle gates remain unchanged. This closes the path where known boundary trades
could contribute positive amounts despite an unverified window. Tests are written
for missing endpoints, interior gaps, and valid partial/full windows; **not run**.

No new raw transport bypasses the unresolved Pump serialization mismatch.
Undecodable evidence remains quarantined. No real successful swaps, creation,
migration, complete hour or independent comparison is demonstrated by this work.
The bridge does not automatically enable training, authorize oracle data or modify
execution/signing RPC settings. Execution infrastructure remains separate and
external where currently configured.

## BTRFS retention and bounded Zstandard

[Official BTRFS compression guidance](https://btrfs.readthedocs.io/en/latest/Compression.html)
explains transparent compression of new extents, with 128KiB chunks and a CPU/ratio
trade-off. Changes do not retroactively compress existing data. Do not blanket
apply NOCOW to evidence: it changes checksum/compression behavior. Do not run
filesystem-wide defragmentation or mount changes from this prototype. Precompressed
Zstandard frames may gain little from filesystem recompression; measure rather
than assume. SQLite consistency requires backup API/checkpoint coordination,
not copying an active main database file alone.

| Tier | Contents | Retention rule |
| --- | --- | --- |
| Hot | SQLite/WAL, current candles, checkpoints, recent full raw evidence, oracle proofs | Preserve live recovery and all referenced records. Capacity/age budgets need measurements. |
| Warm | Immutable per-slot `block.zst` plus hash/length/provenance manifest | Copy-only export; raw hashes and decoder failures survive. No source deletion. |
| Cold | Verified warm files plus consistent index backup, source/version manifests, pinned IDLs and oracle/conversion evidence | Raw block archives alone are not a complete cold restore bundle. Keep model/evaluation dependencies and reconciliation evidence. |

`observer_archive.py` exports only retained **full** blocks from a read-only index.
It checks existing raw hashes, writes a private staged file pair, fsyncs and
publishes atomically. Re-export is idempotent; conflicts fail. It never deletes
source evidence, archived slots or trading/NN data. Only its own unfinished staging
directory is cleaned up. No automated pruning or age-based deletion is present.

Codec limits: <=64MB raw input, bounded compressed input/output, one zstd worker,
levels 1–6, an explicit 8MiB compression window, 64MB decompression-window limit,
output capped to the declared bounded size, hashes/lengths checked and a deadline.
Child stdout is streamed with a bounded buffer; stderr cannot fill a pipe.
`zstd` is optional CLI tooling supplied by the Nix shell, not a new Python/validator
framework. Missing tooling fails explicitly. Archives remain separate from SQLite's
existing zlib representation. Codec safety/performance still needs operator testing.

## Operator-run validation (nothing below was run here)

Enter `nix develop` for Python dependencies and zstd. Use external isolated paths.
The example config remains disabled/unreviewed. First establish a synchronized,
non-voting replay node on suitable hardware and review its source rights; do not
simply flip approvals to make tests appear successful.

```bash
python -m unittest discover -s tests -p test_selfhost_observer.py
python -m unittest discover -s tests -p test_onchain_stream_candles.py
python -m unittest discover -s tests -p test_onchain_indexer.py
python -m unittest discover -s tests -p test_onchain_hour.py

python observer_diagnostics.py --scratch-directory /path/to/isolated/scratch
python observer_diagnostics.py --scratch-directory /path/to/isolated/scratch \
  --input /path/to/retained-full-block.json --iterations 3 --index-db /path/to/isolated/index.sqlite

python run_local_observer.py --config /path/to/reviewed-local-config.json \
  --db /path/to/new-isolated/index.sqlite --start-slot <REVIEWED_SLOT> --loop
# On restart use --resume; saved checkpoints take precedence over an initial slot.

python observer_archive.py --db /path/to/isolated/index.sqlite --slot <SLOT> \
  --output /path/to/isolated/warm
```

The diagnostics read hardware flags/RAM/free space and optionally measure bounded
compression, decompression, temporary file fsync and a warm SQLite header query.
Synthetic input is explicitly labeled and cannot establish real compression ratios.
It writes/deletes only its own temporary benchmark files. It does not measure
validator capacity or independently contact the network. For runtime validation,
use existing indexer RPC/lag/coverage counters and candle evidence, capture process
and NIC/disk telemetry on the server, simulate interruption, then independently
compare normalized rooted transactions with a second authorized source. Do not
benchmark against production trading state.

## Measurement status and remaining dependencies

All new connectivity, transaction/swap counts, finality/event-to-candle latency,
sustained throughput, CPU/RAM/disk performance and compression ratios are **NOT
MEASURED**. Test and benchmark software is delivered, not passing-test claims.
Inspect/execute it on the target server; no new program was run here.

Independent operation remains blocked by suitable replay hardware, synchronization,
version-compatible exports, history retention, exact Pump event layouts and causal
USD history. Initial snapshot/bootstrap and missing/pruned history may still need
external sources even with an owned node. Remote Yellowstone substitutes a provider,
not full independence. Wallet verification, simulation, submission, confirmation
and reconciliation remain with existing execution RPC. Data access and open-source
code licenses are not blanket permission for retained data/ML; rights stay UNKNOWN
until source-specific review. No training or live trading was enabled.
