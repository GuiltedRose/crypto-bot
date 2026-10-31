# Rust RPC optimization pass — UNTESTED

No build, test, network operation, benchmark or dependency resolution ran. Python
remains default; budget/kernel isolation, program versions and trading rules are
unchanged. No speedup or reduction in peak RSS is claimed.

## Changes

- WebSocket halves now have independent bounded send/receive permits. One pending
  read no longer excludes a subscription write. A watch signal interrupts pending
  I/O; close signals synchronously before returning an awaitable and drops both
  halves. The receive deadline covers the whole control-frame loop. Control-frame,
  message and write-buffer limits remain enforced. Actual socket concurrency and
  cancellation stress still require isolated monolith validation.
- The binary parser now borrows signature/key/hash/instruction/lookup slices from
  the packet during internal processing. It materializes owned data only for the
  existing Python return contract. No unsafe code, unbounded queues or speculative
  cache was introduced; Python output normalization still allocates.
- Native Borsh schemas decode pinned Pump/PumpSwap event payload fields, including
  bounded vectors, options, enums and lossless 128-bit integers. Explicit schema,
  input, recursion/node and output limits fail closed. Schemas are cached per
  Decoder instance/program/event. Pubkeys use a portable bounded base58 routine.
- `event_backend=python|rust|compare` selects field extraction for the existing
  Ingester (first configured source establishes its decoder mode). Python remains
  default. `compare` quarantines native/reference field differences. Missing native
  support in an explicit native mode does not silently fall back.
- CPI stack/authority binding, instruction args, deployed-version checks, amount
  normalization, transfer attribution and canonical storage remain Python-owned.
  Trailing/unknown layouts still quarantine. This is NOT full Rust Pump decoding.
- `solana_rpc_compat.extract_event_batch` returns column-oriented rows plus key
  presence and per-transaction issues, bounded to 64 receipts. Ambiguous transactions
  contribute no compact events. Its output remains unverified and market-ineligible
  outside the existing validated ingestion path. No trading API is replaced.

## Server commands (not executed)

Tokio's macro feature is now required for cancellation selection. Regenerate,
review and track Cargo.lock if needed; do not bypass locked builds.

```bash
nix develop
cargo generate-lockfile --manifest-path native/solana_binary/Cargo.toml
# Review/track source, packaging and lock changes.
nix build .#native-rpc .#native-rpc-app
nix develop .#native-rpc
cargo test --locked --features rpc-engine --manifest-path native/solana_binary/Cargo.toml
python -m unittest discover -s tests -p 'test_native_events.py'
python -m unittest discover -s tests -p 'test_solana_binary*.py'
python -m unittest discover -s tests -p 'test_solana_rpc_*.py'
python -m unittest discover -s tests -p 'test_onchain_indexer.py'
```

Start with `event_backend=compare` on isolated/shadow data, not a production default
switch. Ensure native tests run rather than skip. Differential coverage includes
both venues/directions, captured layout quarantines, lossless integers, bounds and
compact projections. Rust tests cover borrowed slices and independent permits/
persistent shutdown; these are not end-to-end live socket stress tests.

```bash
python benchmark_event_pipeline.py tests/fixtures/onchain/curve.json --slot 1 --block-time 1793396356
python benchmark_event_pipeline.py /isolated/real-json-block.json --slot <ACTUAL_SLOT> --block-time <ACTUAL_TIME>
```

The first command uses existing captured data for offline parity/performance only;
its synthetic slot context is not independently verified history. For real
end-to-end evidence supply the authentic slot/time. The benchmark compares complete
receipt-to-compact-projection work in separate workers, including Python context
checks/normalization and native schema compilation. Input is bounded and hashed.
It reports throughput, batch median/p95, peak process RSS, event and quarantine
counts. Batch size comes from the original file (1–64 receipts); use both single
and multi-receipt captures. Allocation counts remain null. Network, decompression,
DB and verified historical coverage are excluded, so this is not a full live
RPC/indexing benchmark. A capture producing only quarantine demonstrates no usable
market-data performance or edge. Full native context/extraction/storage migration
and sustained real memory/latency evidence remain unfinished.
