# Local Pump indexer: implementation and validation

2026-10-30. **Working isolated pipeline, not production-ready market coverage.**

Current deployment/catch-up changes and measurements: [deployment guide](../docs/onchain_deployment.md).
No live execution, wallet-secret changes, paid services, existing ledger edits or
model/evaluation parameter changes. Ten consecutive challenger promotions remain
mandatory. New data is not approved for learning merely because it came from RPC.

## Implemented

- `onchain_decode.py`: checksum-pinned official program IDs/IDLs, raw/v0/v1 JSON
  transaction handling, loaded addresses, successful transactions, outer/inner
  instruction ordering and stack ownership, authenticated event-authority self-CPI,
  exact Borsh event/instruction decoding. Canonical curve/AMM buy/sell amounts,
  mint decimals from transaction balances, separate protocol/creator/LP fees,
  real/virtual reserves and lifecycle events. Standard SPL transfer/transferChecked
  details are transaction-scoped; wallet deltas never allocate routed swaps.
- `onchain_store.py`: independent SQLite WAL/FULL journal; source-policy records,
  finalized block hashes/parents/times, compressed relevant raw transactions and
  hashes, canonical event keys `(signature, outer_index, event_index)`, explicit
  slot manifests and transactional checkpoints. Conflicting duplicates fail;
  an interrupted batch preserves committed blocks and cannot advance past a failed block. Existing trading and frozen
  challenger databases are not opened by the indexer.
- `onchain_ingest.py`: bounded finalized `getBlocks`/`getBlock` polling, explicit
  mainnet verification, ProgramData deployment-slot checks, shared provider budget,
  rate-limit/backoff handling and checkpoint-based historical recovery. A skipped
  slot requires a successful finalized manifest; a missing block is never a skip.
  Resume uses the saved cursor. Backoff/retry is bounded; repeated failures stop.
- `onchain_market.py`: deterministic minute OHLCV and directional USD volume;
  missing/zero-trade/traded interval distinction; finalized-window completeness;
  event-time conversions with no forward lookup, assumed stablecoin peg, stale
  rate or silent conflicting conversion. Quote-side real reserves are explicitly
  labeled, not virtual liquidity or executable depth. Wallet queries use stored
  event identities. Prices/quotes are observations, never asserted fills.
- `run_onchain_indexer.py`, `onchain_indexer.example.json`: read-only deployment,
  status/completeness reporting, optional authorized RPC endpoints. Access denial,
  429 and provider cooldown never trigger endpoint rotation; only transport failure
  can try another explicitly configured endpoint under its own reviewed policy.
- `run_paper.py --onchain-db`: opt-in adapter for existing `market_only` paper rules,
  reusing conservative execution/rent/fee assumptions. No change to risk thresholds.
  Strict safety evidence is not supplied; the adapter rejects strict-mode usage.
  `meme_learning.py` propagates provenance/rights and refuses unapproved updates;
  shared `data_policy.py` protects entry training and exit train/evaluation paths.

Polling rather than WebSocket subscriptions supplies the recovery ground truth.
This implementation does not require an unreliable WS connection to certify
coverage. HTTP reconnections resume the transactional cursor without skipping data.

## Concrete real-data evidence and blockers

Two small successful finalized transactions were independently retrieved from the
public Solana RPC, without keys, accounts or signatures being submitted:

| Fixture | Slot/version | Decoder result |
| --- | --- | --- |
| `tests/fixtures/onchain/curve.json` | 463439519 / v1 | Quarantined: incomplete compatibility with current instruction arguments; TradeEvent has eight bytes beyond pinned fields. |
| `tests/fixtures/onchain/pumpswap.json` | 463439529 / v0 | Quarantined: SellEvent has eight bytes beyond pinned fields. |

Fixtures have SHA256/signature provenance in their manifest and are for regression
only, **not NN datasets**. Successful on-chain execution does not establish exact
schema compatibility. No trailing bytes are discarded, zero-padded, guessed or
turned into volume. Current official IDLs were rechecked at immutable commit
`639b87fb45c3adeffa6f7c4a1bf0cb85e392bf22`; event types are unchanged from the
repository's pinned revision. Existing execution IDLs remain untouched.

Sources: [official program docs](https://github.com/pump-fun/pump-public-docs),
[reported unresolved serialization mismatch](https://github.com/pump-fun/pump-public-docs/issues/54),
[official fee accounting](https://github.com/pump-fun/pump-public-docs/blob/main/docs/instructions/SWEEP_FEES.md),
[Solana getBlock](https://solana.com/docs/rpc/http/getblock) and
[public RPC limits](https://solana.com/docs/references/clusters).
A community issue corroborates the symptom; it is not authority to invent a layout.

Actual accepted real swaps: **0/2 sampled transactions**. Complete real hourly
windows demonstrated: **0**. No continuously collected operational block window
or reviewed USD source exists yet. Machine-readable diagnostics:
[onchain_indexer_validation.json](onchain_indexer_validation.json).
These are measured limitations, not an assertion that the chain had no trades.

Further blockers:

1. Obtain an authoritative complete deployed layout and explicit compatible
   instruction lengths/version boundaries. The current decoder supports exact
   pinned schemas only. Unknown layouts, old abbreviated args, multi-hop parent
   layouts, unsupported Token-2022 fee extensions, unavailable CPI stack heights
   and undecodable relevant instructions invalidate coverage. No log-only fallback
   guesses missing self-CPI events. Raw relevant records remain auditable.
2. Review ProgramData deployment boundaries against the decoder schema. Configure
   each program's `from_slot`, `through_slot`, `deployment_slot`, `idl_hash` under
   `reviewed_program_versions`. The current implementation checks the observed
   current deployment; historical deployments require a separate reviewed archive
   and decoder configuration. Unknown upgrades stay closed, even when fields look
   familiar. An empty registry deliberately marks every block unreviewed.
3. Configure an independently verified event-time USD source/method. No fiat peg,
   oracle decoder or external valuation service is enabled. `add_conversion`
   requires a reviewed source with `usd_conversion_verified: true`, a positive
   finite price, timestamp and immutable proof. Merely filling these fields does
   not prove a rate: evidence must be independently reviewed before operational
   insertion. Without rates, USD quotes/volume are unavailable. An oracle reader
   with validated account/program identity and historical observations is a
   remaining integration, not implemented by substituting today's SOL price.
4. Public RPC archive availability and global block-ingestion throughput are
   unproven. Default pacing is deliberately conservative (2s/request; shared
   limiter minimum 0.5s). Full-chain polling at that speed may not keep up with
   Solana. Historical block responses can be large (64MB cap); pruned/missing
   blocks and newer unsupported transaction formats pause the batch. Production
   may require authorized indexed/archive/gRPC infrastructure or a self-operated
   node; none was purchased. Do not claim unlimited free backfill.
5. Review operator storage, training, evaluation and derived-data rights, including
   any endpoint-specific restrictions. Config defaults to disabled/unverified;
   explicit evidence and versioned source IDs are required. Endpoint failover
   does not permit circumventing shared-account quotas across hosts. Configure
   only endpoints whose combined use is authorized; do not duplicate the same
   service to gain quota. Existing host-level cross-process pacing is reused.

## Deploy locally after resolving the applicable reviews

Inspect an isolated empty database; no RPC calls:

```bash
.venv/bin/python run_onchain_indexer.py --db state/onchain/index.sqlite --status
```

Copy the example to a local configuration, keep credential-bearing endpoint URLs
out of git/logs, and review each enabled endpoint's policy. No `.env` is read.
Do not set verification flags merely to bypass a gate. Choose a finalized starting
slot supported by the archive. No implicit restart from "now" is performed.

```bash
.venv/bin/python run_onchain_indexer.py \
  --config /path/to/private-indexer.json --db state/onchain/index.sqlite \
  --start-slot <REVIEWED_FINALIZED_START_SLOT> --batch-slots 8

# Resume the saved checkpoint, with bounded recovery:
.venv/bin/python run_onchain_indexer.py \
  --config /path/to/private-indexer.json --db state/onchain/index.sqlite --loop

# Shadow observation; no trading or NN import:
.venv/bin/python run_onchain_indexer.py --db state/onchain/index.sqlite \
  --mint <TOKEN_MINT> --pool <POOL_OR_CURVE_ADDRESS>
```

Hourly volume uses `[finalized_watermark-3600, finalized_watermark)`, explicitly
reported as `buy_volume_as_of`. It requires a complete bracketed hour, successful
schema/version checks and fresh event-time USD conversions for every included
swap. This avoids pretending the unfinalized wall-clock tail is covered. Old
quotes still fail existing freshness checks. Missing bars remain missing; confirmed
zero-trade minutes are distinguishable but do not become synthetic OHLC candles.
Scope is mint **and pool**; graduation does not silently combine two pools' volume.

After real coverage and valuation validation, use a separate paper state directory
and existing market-only rules (do not change the operational paper ledger):

```bash
.venv/bin/python run_paper.py --state-dir state/paper_onchain_shadow \
  --rules meme_live_rules.json --exploratory \
  --balance 6 --assume-usd-costs \
  --onchain-db state/onchain/index.sqlite --mint <TOKEN_MINT> --loop
```

This example illustrates modeled virtual costs, not a fiat conversion source for
market data. The source still needs genuine verified conversions. New data rights
and provenance survive decision-context recording. Unapproved samples may be
retained as quarantined trade records but cannot update weights; no new data was
fed to the operational NN. The local source is wired into **unsigned live simulation only**; combining it with `--execute` is blocked before signer loading.
The analytics interface is available for future reviewed integration without
changing wallet monitoring, transaction construction or the promotion gate.

## Focused validation

- Initial 20 indexer tests (now 30 after deployment review): exact known-layout synthetic curve/AMM buy/sell and fee
  decoding, real fixture quarantine, lifecycle/migration, SPL transfer identity,
  multiple swaps, duplicates and out-of-order insertion, skipped versus missing
  slots, incomplete windows, unknown deployments, stale/future/missing/conflicting
  USD, finalized-time windows, transport/failover and 429 behavior, atomic batch
  rollback, actual child-process death during an uncommitted write, restart,
  learning quarantine and isolated paper processing without a position opening.
- Existing relevant tests: 12 meme-learning, five Pump market, eight data-compliance
  and four chart-learning tests passed. No whole-suite run.
- CLI help and empty shadow status checked. Synthetic complete-window tests verify
  arithmetic and recovery only; they do not establish real coverage/profitability.

**Status: CONDITIONAL shadow infrastructure; NO-GO for operational trading or new
NN training on current real samples.** Resolve schemas, version review, conversions,
rights and throughput, then demonstrate a complete real hour and replay before
considering a paper collection rollout. Existing historical outcomes, capital
reset behavior, frozen challengers, rollback history and live gate are preserved.
