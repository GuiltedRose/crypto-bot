# Optional Rust binary decoder — UNTESTED

No builds, tests, benchmarks, dependency resolution or network operations were run.
The crate contains safe Rust only (`forbid(unsafe_code)`). PyO3 itself manages its
CPython boundary; its upstream implementation is outside that crate's safety lint.

`solana_binary.parse_transaction(bytes, backend='auto'|'python'|'rust')` parses
legacy and v0 wire transactions with canonical compact-u16 lengths, signatures,
headers, static keys, blockhashes, instructions and lookup-table references.
It rejects oversized packets (>1232 bytes), truncation, invalid indices, trailing
data and unsupported versions. Input limits bound allocations. This does not
support larger future packet formats or every semantic transaction sanitizer rule.

The result preserves bytes as integer lists and canonical ordering. It NEVER
verifies signatures, successful execution, roots, loaded address values, source
rights or program versions. Trust fields remain false. Unsupported/malformed data
raises an error for quarantine; native errors never trigger a permissive retry.
Missing native installation falls back to Python; explicit `backend='rust'` fails.

The existing RPC/JSON decoder, its Pump/PumpSwap layouts, program-version checks,
quarantine and trading API are unchanged. No JSON-to-binary adapter is inserted.
This foundation is not yet an active indexer backend: an actual binary source with
execution metadata/provenance must be integrated and reviewed separately. No new
network access, quotas, wallets, NN changes or trade authorization are introduced.

## Dependency lock prerequisite

PyO3 is pinned to 0.23.5. A complete transitive Cargo.lock could not be generated
under the prohibition on dependency resolution/network operations. No checksums or
vendor hashes were fabricated. Consequently optional `native-decoder` and `native`
shell outputs are conditional on a real Cargo.lock; existing outputs remain usable.
Full dependency reproducibility is **blocked until that lock is generated,
reviewed and committed**. Nix then vendors via importCargoLock and uses the existing
pinned nixpkgs' maturin/Python toolchain. There is no architecture-specific assembly
or separate flake. The package is declared for all four existing platforms but
has not been built on any of them.

## Monolith validation commands — operator runs these

Dependency resolution is a separate, authorized development download; do not run
it through or charge it to an unreviewed node startup process. Respect the shared
network budget when fetching crates/Nix inputs.

```bash
nix develop
cargo generate-lockfile --manifest-path native/solana_binary/Cargo.toml
# Review and track the crate, packaging files and generated Cargo.lock before Nix
# evaluation: git-backed flakes ignore untracked files.
nix build .#native-decoder
nix develop .#native
python -m unittest discover -s tests -p 'test_solana_binary.py'
cargo test --locked --manifest-path native/solana_binary/Cargo.toml
```

The extension-module feature is enabled by maturin, not ordinary Cargo tests,
so Rust unit-test binaries can use the normal Python linkage.
The suite uses synthetic wire fixtures, solders as an independent parsing oracle,
all truncations of a valid packet, program identities and native/reference
comparison. Native cases skip when the extension is absent; check they actually
run inside the native shell. No synthetic fixture establishes valid execution.

```bash
python benchmark_solana_binary.py tests/fixtures/onchain/curve.json tests/fixtures/onchain/pumpswap.json
python benchmark_solana_binary.py /isolated/verified-wire-transaction.bin
nix build .#checks.x86_64-linux.solana-network-lifecycle
```

Existing JSON fixtures without original base64 wire bytes are explicitly reported
as unsupported, not converted or used to invent throughput. Supply verified binary
fixtures for representative real benchmarks. The benchmark uses separate processes
for Python/Rust, serial batches of 1/64, throughput, latency and process peak RSS.
RSS includes interpreter/imports; performance results remain unavailable. No
speedup, memory savings, production readiness or mainnet independence is claimed.

## Binary ingestion and installed application

The current safety lint is `deny(unsafe_code)`: safe Rust remains the default;
no unsafe code was added. Any future local exception needs specific invariants,
justification and targeted tests. Neither unsafe Rust nor a language change is an
OOM-prevention guarantee.

`solana_binary_ingest.ingest_rpc_response` accepts original base64 wire bytes from
an already obtained getTransaction response; `ingest_binary` accepts an approved
local source's bytes plus explicit slot, canonical transaction index, commitment,
execution metadata and reviewed source policy. They perform no requests or DB
writes. `binary_ingestion_verified=true` plus storage rights/evidence is required;
this is operator approval, not a new grant of data rights. Malformed evidence is
returned as a quarantined envelope with its original context. JSON transactions
are never re-serialized. Execution errors are preserved as source-reported failures.
Lookup-table addresses require matching source metadata and remain source claims.

Even complete input stays `market_eligible=false`, `execution_verified=false` and
`consensus_verified=false`. This API is ready for an explicitly approved binary
collector, not silently wired into the existing JSON ingester or Pump decoder.
Actual finalized verification, source/program-version checks and reproducible
Pump event validation are still mandatory before market/learning use. No authentic
binary capture or collector has been demonstrated here.

After Cargo.lock review/commit, `nix build .#native-app` produces existing app
wrappers with the native extension on PYTHONPATH. The default package retains its
Python fallback and all four platforms remain declared. Packaging is unbuilt;
PyO3/maturin dependencies are not available/verified here. Do not mistake a skipped
native test for a successful native build.

### Exact operator commands

```bash
nix develop
cargo generate-lockfile --manifest-path native/solana_binary/Cargo.toml
# Review Cargo.lock and git-add the new source/lock/packaging files before Nix.
nix build .#native-decoder .#native-app
nix develop .#native
cargo test --locked --manifest-path native/solana_binary/Cargo.toml
python -m unittest discover -s tests -p 'test_solana_binary*.py'
python benchmark_solana_binary.py --scope adapter /isolated/authentic-wire.bin
python benchmark_solana_binary.py --scope parser /isolated/authentic-wire.bin
python benchmark_solana_binary.py --scope binding /isolated/authentic-wire.bin
nix flake show
```

Adapter timing includes binding/output normalization; parser timing separates the
Python reference loop and Rust-internal parser loop (input conversion and result
marshalling outside Rust timing). Binding-only timing is a no-op input-length
baseline, not a subtraction that proves exact FFI overhead. Same authentic inputs
and separate processes are used, batches 1/64. No RPC/decompression/DB writes occur
inside timing. Allocation metrics remain null: no reliable cross-language allocator
instrumentation is installed. Peak RSS is whole-process, not per-transaction or
native-only allocation. No speed/memory results or production readiness are claimed.
