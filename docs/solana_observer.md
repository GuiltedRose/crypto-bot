# Python Solana observer

Implementation and tests are **UNTESTED**. No observer, tests, benchmarks, network
probes, or builds were executed for this change. No real coverage or throughput
is claimed. Existing trading/learning state and eligibility criteria are unchanged.

`solana_observer.py` extends the original `observe()`/async subscription starter.
It uses `onchain_stream`, the existing finalized ingester, strict decoder,
EventStore, and incremental CandleEngine. It does not trade or train models.

## Configuration

Copy `solana_observer.example.json` outside the repository, review the source's
storage/stream rights and quotas, and configure explicit verified mint/pool pairs.
The example is disabled and grants no rights. Never put authenticated URLs in
tracked files or the Nix store. Every recovery endpoint needs reviewed storage
rights. ML/evaluation rights remain separately gated; stream/storage authorization
never enables training. Public reachability does not establish authorization.

Only JSON-RPC sources (`solana_rpc`, `local_rpc`) are implemented. Explicit
`stream_mentions` may select one or two program, mint, or pool addresses; this
filters observations, not finalized block coverage. HTTPS/WSS are required except
explicitly opted-in literal loopback HTTP/WS for a local node. Review HTTP
`allow_loopback_http` and WS `allow_loopback_ws` separately. No local validator,
Yellowstone/Geyser adapter, shred verification, or independent consensus is supplied.

## Operator validation (run yourself)

Enter `nix develop` and use its `python`; no `.venv` is required. Build/evaluate the
flake yourself before relying on its observer wrapper. Focused offline tests:

```bash
python -m unittest discover -s tests -p 'test_solana_observer.py'
python -m unittest discover -s tests -p 'test_onchain_stream_candles.py'
python -m unittest discover -s tests -p 'test_onchain_indexer.py'
```

Start with a new isolated database, never a paper/live ledger:

```bash
python solana_observer.py --config /private/reviewed-observer.json \
  --db /isolated/observer.sqlite --start-slot <REVIEWED_FINALIZED_SLOT> --max-cycles 1
python solana_observer.py --db /isolated/observer.sqlite --status
```

`--max-cycles` bounds successful recovery batches, not wall time; rate/backoff
waits still apply. Use `--resume` explicitly to reopen the observer database.
The saved finalized checkpoint wins over a supplied start slot; there is no
implicit jump to the current chain head. Omit `--max-cycles` for ongoing collection.
After building, `nix run .#observer -- ...` supplies the same CLI.

## Data and failure semantics

Subscriptions validate JSON-RPC acknowledgments, subscription IDs, signatures,
slots and execution status. Confirmed notifications remain provisional, including
failed transactions. SQLite stores a notification hash, normalized error, source,
signature, slot and receipt time; raw notification log bodies are not retained.
Duplicates do not create swaps. Finalized blocks provide canonical execution,
ordering and receipts through the existing decoder; unknown bytes remain quarantined.
There is no per-notification HTTP fetch or separate provisional swap decoder.

Pending hints and retained observations have explicit budgets. Pending overflow,
conflicting notifications and disconnects record gaps. Observation retention
exhaustion stops collection without pruning evidence. Archive/review the isolated
database and provision a new reviewed budget before resuming; no automatic evidence
deletion occurs. Reconnection uses bounded backoff and shared provider budgets;
401/403 stops access, and 429 respects provider backoff. Cancellation waits for
in-flight finalized writes before shutdown.

Status reports notification/failure/duplicate/disconnection counts, pending age,
finalized relevant transaction and verified-swap counts, persisted gap states,
checkpoint lag and coverage. The block-time-to-receipt metric is a coarse timestamp
proxy, **not** true delivery, finalization, or strategy latency. Gap history is not
silently cleared when finalized ingestion catches up. Complete candles and hourly
volume still require finalized coverage, known layouts and valid event-time USD
conversions; a WebSocket observation never satisfies these requirements.

## Remaining deployment blockers

Real Pump/PumpSwap layout/version mismatches remain quarantined. An authorized
RPC budget must demonstrate sustainable finalized recovery; notification filtering
does not reduce this implementation's full-block recovery requirements. Historical
USD freshness, a complete independently verified hour, deterministic real replay,
provider retention/ML rights and hardware capacity remain unproven. A self-hosted
node on the available hardware is not assumed viable. No production-readiness,
independent-feed or profitability claim follows from these changes.
