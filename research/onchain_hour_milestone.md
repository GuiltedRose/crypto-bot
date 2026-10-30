# Real-hour milestone: blocked, with verifiable oracle and replay tooling

Reviewed 2026-10-30. **Complete independently verified real hours: 0.** No live
trades, operational ledger/model/evaluation writes, parameter/risk changes or
challenger-criteria changes. All probes used read-only public RPC; all temporary
limiter/snapshot state was isolated under `/tmp`. New fixtures are regression data,
not admitted NN samples. [Machine-readable measurements](onchain_hour_validation.json).

## Serialization findings

Rechecked official Pump specs at immutable commit
`639b87fb45c3adeffa6f7c4a1bf0cb85e392bf22`. Event types are unchanged from the
existing decoder: [Pump IDL](https://raw.githubusercontent.com/pump-fun/pump-public-docs/639b87fb45c3adeffa6f7c4a1bf0cb85e392bf22/idl/pump.json),
[AMM IDL](https://raw.githubusercontent.com/pump-fun/pump-public-docs/639b87fb45c3adeffa6f7c4a1bf0cb85e392bf22/idl/pump_amm.json).
The retained real TradeEvent/SellEvent fixtures still exceed those definitions;
`creator_fee_unclaimed` is already included. The [serialization issue](https://github.com/pump-fun/pump-public-docs/issues/54)
has no authoritative resolution in the reviewed material. Bytes remain intact;
no guessed field, truncation or inferred fee/reserve interpretation was added.

Offline recheck of real slots 463447225–463447226 (549 transactions) accepted
one InitUserVolumeAccumulatorEvent, one CloseUserVolumeAccumulatorEvent and six
DistributeFeeToHoldersEvents. These are **not verified token creations, swaps or
migrations**. Real creation/migration verification remains incomplete; existing
synthetic regression cases test known schemas but cannot establish deployment
compatibility. One retained AMM fixture has native SOL as its declared base and
a different quote token: venue identity alone does not establish a canonical
SOL-quoted graduated Pump token. Account ordering/orientation must be independently
validated; do not invert or rename tokens to force a pass.

## Full blocks versus selective indexing

| Measurement | Result / implication |
| --- | --- |
| Two retained full blocks | 549 transactions, 3,918,026 canonical JSON bytes, 786,841 zlib bytes. Two block reads, excluding common head/version/manifest overhead. |
| Same blocks, standard program-selective approach | 239 transactions reference either program; 119 are successful candidates after signature deduplication. At least **123 requests**: 119 transaction reads, two address pages and two block-signature lists for transaction ordering. Successful candidate JSON totals 1,436,109 bytes. Fewer bytes, far more RPC operations. |
| New Pump signature page | 1,000 rows within a **3-second timestamp span**, 798 failed. A full page is not an hour or a complete historical dataset. |
| New PumpSwap signature page | 1,000 rows within a **3-second timestamp span**, 439 failed. Fetching only successful receipts still overwhelms the tested public budget at this sampled workload. |
| One previously observed pool | 1,000 rows over 2,362 seconds; three failed. Historical boundary not proven. Selected low-activity pools may be feasible; this pool page does not certify an hour. |

These short page samples are not sustained-rate estimates. Standard
[getSignaturesForAddress](https://solana.com/docs/rpc/http/getsignaturesforaddress)
provides newest-first pagination and execution status, not a portable guarantee of
transaction index or ledger completeness. Provider-specific ordering extensions
must be separately verified. Recovery must paginate to an older-than-window
boundary, deduplicate overlaps, retain cursor/bounds, reject null/pruned history
and recover canonical order. An empty/truncated page is not proof of zero activity.

Current full-block default pacing has an ideal ceiling around **0.46 blocks/s**
for 64-slot batches (70 calls at two seconds each), before decode/transfer costs.
Existing shared pacing remains unchanged. Under observed load, global selective
program indexing is worse in request demand. A fixed low-activity mint/pool
universe is a conditional alternative, with explicitly scoped coverage and
selection bias; it cannot replace global discovery or prove market-wide coverage.
No incomplete selective stream was wired into trading.

The existing two-second default / half-second shared floor are conservative
**implementation settings, not the provider's maximum entitlement**. Official
[public RPC limits](https://solana.com/docs/references/clusters) currently list
100 requests/10s/IP, 40/method/10s and 100 MB/30s, warn that limits vary, and say the
public service is not production infrastructure. We did not raise request rates.
Two additional block reads with standard gzip negotiation returned 646,333 and
497,187 wire-body bytes versus 3,918,108 combined uncompressed response bytes:
about **71% less transfer**, at roughly 0.46–0.50 seconds/request in this sample.
`IndexRpc` now negotiates gzip with bounded decompression and rejects unknown,
trailing, oversized or incomplete compressed content. No quota bypass was added.

A future faster schedule might fit the published request ceilings, particularly
with compression, but needs shared per-method/byte accounting, confirmation of the
operator's actual limits and a sustained authorized test. The current conservative
pipeline is insufficient; this is **not proof that every free configuration is
intrinsically infeasible**. Serialization and history/rights blockers prevent a
credible complete-hour experiment now. No rate cap was weakened to produce an
optimistic benchmark.

At an illustrative 2.5 produced blocks/s, the two-block compression sample implies
roughly **3.54 GB/hour retained** (about 85 GB/day), versus 17.63 GB/hour raw JSON.
This is a planning extrapolation, not measured hourly disk growth. Paid archive,
Geyser/indexed stream or self-operated node costs remain **UNKNOWN**; no service
was purchased. An authorized transaction stream plus archive repair could reduce
HTTP demand, but needs measured continuity, terms and recovery guarantees.

## Independently inspectable SOL/USD snapshots

Added `onchain_oracle.py`, pinned to official Pyth SDK commit
`c875af6c87b08b507664c76d839e82d6167c9356`; URLs/hashes are in
`vendor/pyth_receiver.manifest.json`. Layout:
[PriceUpdateV2](https://raw.githubusercontent.com/pyth-network/pyth-crosschain/c875af6c87b08b507664c76d839e82d6167c9356/target_chains/solana/pyth_solana_receiver_sdk/src/price_update.rs),
[feed message](https://raw.githubusercontent.com/pyth-network/pyth-crosschain/c875af6c87b08b507664c76d839e82d6167c9356/pythnet/pythnet_sdk/src/messages.rs).
The [official push-oracle seeds](https://github.com/pyth-network/pyth-crosschain/blob/c875af6c87b08b507664c76d839e82d6167c9356/target_chains/solana/programs/pyth-push-oracle/src/lib.rs)
bind shard/feed ID to the account. Program-owned Full verification is checked;
partial verification, wrong owner/feed/PDA/write authority, unrecognized lengths,
nonzero spare allocation byte, stale/future times and invalid context slots reject.
The SDK explicitly allocates space for its largest enum variant; its single spare
zero byte for Full is accounted for, retained and hashed, not an unidentified
Pump-event tail.

A real finalized read of account `7UVimffxr9ow1uXYxsr4LHAcV58mLzhmwaeKvJ1pjLiE`
yielded SOL/USD **95.86583214**, publish time **1793401602**, posted slot
**463463971**, context slot **463463976**, block time **1793401605**. It decoded
with Full verification and approximately 3.43 bps reported confidence. Raw snapshot
and SHA256 manifest are regression fixtures. This is one RPC-attested account
observation, **not an independently verified historical hour or a consensus proof**.
It was not imported into operational conversions or training.

Historical conversions now also exclude oracle observations whose posting slot
is later than the trade, even when publish time is earlier: a backdated publication
time must not introduce look-ahead. Existing 60-second conversion freshness and
all trading limits remain unchanged. Import additionally requires reviewed oracle
rights, USD-method approval and an explicit source confidence policy. No production
approval flags were enabled.

Current `getAccountInfo` is **not historical account-state access**; minContextSlot
is not a historical selector. Captured finalized snapshots can form future history
once retention rights are reviewed. Existing older hours need independently
verified archive snapshots or signed/verified historical updates. Pyth's current
[History API](https://docs.pyth.network/pro/api/history) and
[REST/Hermes documentation](https://docs.pyth.network/pro/api/rest) require keys
for historical prices; no account, key, unauthenticated bypass or paid request was
attempted. SDK source licensing does not establish price-data retention/ML rights.
These rights remain UNKNOWN.

## Minimal implementation and isolated usage

- `onchain_ingest.py`: tested standard gzip negotiation, bounded wire/decompressed
  response handling; existing request pacing and denial/backoff remain unchanged.
- `onchain_store.py`: opt-in compressed full-block retention; defaults unchanged.
  Prior hashes plus selected raw transactions alone cannot replay all activity.
- `onchain_hour.py`: read-only exact UTC-aligned hour audit; strict retained-block
  hashes/headers, re-decoding and canonical-event comparison, minute/gap states,
  directional causal USD totals and deterministic replay digest. It explicitly
  reports **independently_verified=false** until external verification exists.
- `onchain_oracle.py`: validated capture/import API; explicit
  `run_onchain_indexer.py --capture-sol-usd` reuses reviewed endpoints and pacing.
  It does not backfill past values or enable NN training.

After rights/schema/infrastructure review, collect into a **new isolated path**:

```bash
python run_onchain_indexer.py --config /path/to/reviewed-config.json \
  --db /path/to/isolated/hour.sqlite --start-slot <REVIEWED_SLOT> --retain-blocks --loop

# Separate invocation; requires reviewed oracle policy in the source record:
python run_onchain_indexer.py --config /path/to/reviewed-config.json \
  --db /path/to/isolated/hour.sqlite --capture-sol-usd

python -m onchain_hour --db /path/to/isolated/hour.sqlite \
  --start <ALIGNED_UNIX_SECOND> --end <START_PLUS_3600> --mint <MINT> --pool <POOL>
```

Oracle import requires `usd_conversion_verified`, `oracle_data_rights_verified`
and a reviewed integer `oracle_max_confidence_bps` in the immutable source policy.
These are evidence records, not flags to set merely to bypass a gate. Repeated
captures must be paced and archived before trades to produce causal history.
Keep all existing paper, live, learning and evaluation directories untouched.
Audit exit 0 means internally complete and ready for independent review, **not**
independent verification, paper profitability, ML permission or live eligibility.

**41 focused tests passed**: 30 existing indexer tests and eleven new hour/oracle tests.
They cover real oracle decoding, retained-block corruption/omission, deterministic
synthetic-hour replay, restart, missing USD, slot-based look-ahead, stale/future
prices, wrong identities, partial verification, source rights selective request counting and bounded gzip decoding. No full-suite run. Synthetic creation/migration and complete-hour tests
remain explicitly synthetic. No complete real-hour audit was claimed.

## Acceptance still required

Obtain authoritative deployed Pump layouts/version boundaries; demonstrate an
adequate authorized acquisition/archive budget; acquire a complete causal USD
history with reviewed retention/ML rights; retain and replay a full real window;
compare finalized slot/transaction ordering and decoded values against a second
independently operated authorized ledger source. RPC aliases of the same operator
are not independent sources. Compare normalized chain facts, not provider-specific
JSON extensions. Then review the hour's scope, gaps and conversions before any
paper collection rollout or NN ingestion. The ten-promotion requirement is intact.
