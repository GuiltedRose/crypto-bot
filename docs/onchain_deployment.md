# Local indexer and paper deployment

Incremental candle/WSS additions: [real-time engine status](realtime_market_engine.md).

Reviewed 2026-10-30. Latest hour/oracle evidence: [real-hour milestone](../research/onchain_hour_milestone.md). **Not production-ready:** real swap layouts, a reviewed USD
source and sufficient full-chain throughput remain blockers. No live service is
provided. Existing paper ledgers and frozen NN evaluations are not migrated or
reset. The ten-promotion live eligibility requirement is unchanged.

## Validation and measured limits

The existing 49 indexer/learning/market/compliance/chart tests passed before changes.
New deployment tests exercise read-only adapters, no HTTP fallback, growing lag,
bounded catch-up, interrupted-batch resume, external quota state, three closed
candles, complete-hour volume and unsigned-only live source selection. **102 focused tests passed**: 30 indexer,
12 meme-learning, five Pump market, eight compliance, four chart-learning,
36 live-engine and seven evaluation-recovery tests. No full-suite run.

[Measured probe](../research/onchain_deployment_validation.json): two finalized
blocks, slots 463447225–463447226, contained **549 transactions** and approximately
3.92 MB of JSON. One shared decoder took about **27 ms** in offline recheck. Eight
lifecycle events decoded; no swaps passed the current schemas. The two original
hash-verified curve/AMM transaction fixtures also remain quarantined.

Both programs still emit event bytes beyond the published fields. Unreviewed-block events are retained for audit but cannot supply even a spot
price to paper or unsigned live. The discrepancy
is not fixed by adding `creator_fee_unclaimed`: the pinned schema already has it.
We do not strip bytes or infer a layout. [Official repository issue #54](https://github.com/pump-fun/pump-public-docs/issues/54)
remains unresolved in the material reviewed. Documented Borsh options/enums and
omitted trailing `partial_fill` for the two v3 buy instructions are now handled;
undocumented legacy omissions remain quarantined.

A separate correctness fix counts `PostCompleteBuyEvent` as an additional swap
component, rather than lifecycle-only metadata. The official
[synthetic-migration specification](https://github.com/pump-fun/pump-public-docs/blob/main/docs/SYNTHETIC_MIGRATION.md)
requires both the curve and post-completion buy amounts. Orphan/duplicate
components fail coverage. This is on-chain execution terminology, not a fabricated
paper trade. Unknown multi-hop layouts remain unsupported and invalidate coverage.

The short (~8-second) probe observed finalized head advancement of **4.75 slots/s**;
this includes publication jitter and is not a sustained production-rate estimate.
At the default two-second request interval, a 64-slot batch needs 70 calls:
64 blocks, one head, four deployment reads and one manifest. Its ideal ceiling is
**0.46 blocks/s**, before CPU/transfer costs. Even the existing 0.5-second shared
limiter floor permits only **1.83 blocks/s** under those assumptions. Catch-up
cannot overcome an insufficient authorized RPC budget. A complete live hour has
**not** been demonstrated; complete real hourly windows remain **zero**.

## Catch-up and recovery behavior

- `--batch-slots` defaults to 8. When behind, batches double up to
  `--max-catchup-slots` (default 64, hard maximum 64), with no redundant idle rest.
  Every RPC still goes through the existing shared provider limiter. No quota or
  access restriction was relaxed.
- Each downloaded block and its preceding manifest-verified skipped slots commit
  atomically. An interruption retains that prefix and resumes at the next slot;
  the in-flight block is retried. Memory is bounded to one block, not 64 blocks.
- Access denial and throttling never rotate endpoints. Transport failover resumes
  the same saved cursor through another explicitly authorized endpoint. Exponential
  retry and systemd restart loops are bounded.
- Status reports durable progress: finalized head, cursor, lag slots, lag growth,
  indexed slots/s, sampled production slots/s and throughput warnings. A stale or
  incomplete feed never becomes a passing market window. Short rate samples are
  diagnostics, not an SLA.

```bash
.venv/bin/python run_onchain_indexer.py --db /path/to/index.sqlite --status
```

## Portable Nix development

`flake.nix` pins Nixpkgs by immutable commit and solders 0.29.0/websockets 15.0.1
by official PyPI artifact hashes. It defines Linux x86_64/aarch64 and Darwin
x86_64/aarch64 environments. Native services are NixOS/Linux only. Architecture
support is declared; builds on each platform still require validation.

Use a **clean Git checkout**. Keep private configuration, `.env`, keys and state
outside it. The derivation uses an explicit public-code/specification allowlist.
Never invoke a `path:` flake against a directory containing secrets: Nix can copy
flake inputs before the derivation's source filter runs. Do not use `builtins.readFile`
or a Nix path literal for a secret. Only the runtime path string belongs in Nix.

```bash
nix develop
python -m unittest discover -s tests -p test_onchain_indexer.py
nix flake check
nix build
```

The generated `flake.lock` pins the input content hash as well as its commit.
Official Nix 2.31.2 parsed both Nix files and evaluated packages on all four
platforms. A minimal Linux host evaluated both services with no failed assertions,
and the generated paper service script passed `sh -n`. Full package builds and
`nix flake check` builds have not run: this workspace has no installed Nix build
environment. Validate them on the target host before deployment; nothing was installed
or started here.

Runtime state must be external and writable. All processes sharing a provider
must use the same external pacing database:

```bash
export CRYPTO_BOT_PROVIDER_LIMITS=/path/to/private-state/provider_limits.sqlite
nix run .#indexer -- --config /path/to/private-config/indexer.json \
  --db /path/to/private-state/index.sqlite --start-slot <REVIEWED_SLOT> \
  --loop --max-catchup-slots 64
```

`onchain_indexer.example.json` intentionally has no enabled/approved endpoint.
Review storage rights, endpoints, program deployment ranges and historical access
before enabling collection. A configured initial slot is used only for a new
index; restarts use the saved cursor, including systemd restarts. Existing index
databases acquire the additive telemetry table when the writer opens them.

## Separate NixOS services

Import the flake's module in your host configuration. Keep the indexer configuration
at a root-managed runtime path; systemd copies it into its credential directory.
The secret contents never become a derivation input or command-line argument.
Example (replace the slot only after schema/deployment review):

```nix
{
  imports = [ inputs.crypto-bot.nixosModules.default ];
  services.crypto-bot-paper = {
    enable = true;
    stateDirectory = "/var/lib/crypto-bot-paper";
    indexerConfigFile = "/run/secrets/crypto-bot-indexer.json"; # string, not a path literal
    startSlot = 463447225; # example only; verify schema and archive access first
    batchSlots = 8;
    maxCatchupSlots = 64;
    paper.enable = false; # enable only after real coverage/valuation acceptance
  };
}
```

The module creates separate `crypto-bot-indexer` and optional `crypto-bot-paper`
services under a dedicated unprivileged user. External state is owner-only;
services share the provider budget, not a signer. Paper uses a separate persistent
ledger under `stateDirectory/paper` and opens the index in SQLite read-only mode.
Working directories, credentials, WAL files, outcomes and model/evaluation state
remain outside `/nix/store`. Hardening prevents system/home writes and privilege
escalation. The module does not start or provide a live-execution service.

Existing paper state should be backed up using SQLite's backup API before any
operator-directed relocation; preserve its outcome/evaluation sidecars too. Do
not copy an active SQLite database alone, delete history, or point this new test
service at an unrelated existing paper directory.

```bash
journalctl -u crypto-bot-indexer -f
journalctl -u crypto-bot-paper -f
```

## Paper and unsigned live integration

The paper adapter reads only local SQLite, including discovery, SOL cost valuation
and token observations. Missing DB/conversion/history is explicit unavailability,
not a request to Pump.fun. The legacy URL-shaped calls used by valuation helpers
are routed to local queries; there is no HTTP fallback. Current paper costs/rent
assumptions and every entry/risk limit remain unchanged.

```bash
nix run .#paper -- --state-dir /path/to/private-state/paper-shadow \
  --rules /path/to/reviewed-market-only-rules.json --exploratory \
  --balance 6 --assume-usd-costs --onchain-db /path/to/private-state/index.sqlite \
  --discover --loop --interval 30
```

This illustrates virtual costs, not a market USD conversion. The index still needs
verified historical USD rates. The current full-hour gate uses the finalized
watermark, three or more genuinely completed minute bars as configured, and the
unchanged $5,000 buy-volume minimum. Missing intervals and absent conversions fail;
zero-trade intervals do not fabricate OHLC bars.

`run_meme_live.py --onchain-db ... --wallet <PUBLIC_ADDRESS>` now uses the same
local data adapter for **unsigned** simulation, including portfolio valuations.
Combining `--onchain-db` with `--execute` is rejected before signer/config loading.
Existing execution/reconciliation code and the ten-promotion gate are untouched.
No signed live run was executed. Existing explicit live CLI behavior without this
new source is unchanged; no deployment service invokes it.

## Rights and rollout blockers

The [official public RPC documentation](https://solana.com/docs/references/clusters)
documents chain access and variable provider limits, not an express perpetual
storage/ML/derived-model license. No custom provider contract was supplied.
Storage, ML and evaluation rights therefore remain **UNKNOWN**, not permitted by
assumption. The example flags remain false; operational training was not enabled.
Policy versions/evidence are persisted, and unreviewed source contexts cannot
update NN weights or enter challenger evaluation. Existing valid data is preserved.

Before paper rollout: resolve exact deployed schemas/version boundaries; provide a
reviewed, independently validated event-time USD source; establish an authorized
infrastructure budget that keeps up; prove a complete real hourly window and
replay its candles/volume; then demonstrate paper processing of those observations.
Synthetic fixture tests and passing regression tests do not establish these facts.
