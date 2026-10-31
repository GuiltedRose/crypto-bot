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
