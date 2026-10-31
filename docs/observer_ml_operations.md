# Observer rollout and unified ML operations — UNEXECUTED

The operator runs everything below. No build, tests, deployment, network requests,
model training or benchmark was executed here. No existing wallet, transaction,
training ledger, weights or promotion history was opened or modified. Services
and controls are implemented but cannot be called validated or deployed yet.

## Implementation

- New `nixosModules.observer-workflows` stays inside the application flake. It
  provides a real manually started observer service using native-rpc-app and the
  existing namespace/quota lifecycle. It is not a simulation. The ordinary default
  module and Python backend remain available. No trading/signing service is added.
- Optional offline learning service runs all three model families, has no network,
  CPUQuota=25%, MemoryHigh=512M/MemoryMax=1G, no swap, and a five-minute execution
  bound. No timer, automatic live promotion or training in transaction callbacks.
- `unified_observations.py` adds an isolated append-only, application-ID-checked
  SQLite evidence cache, explicit revocations, stable identities, timestamp/schema/
  rights checks, missing-value masks and SQLite historical similarity retrieval.
  No existing historical similarity index was found in the audited Python modules;
  this index reuses existing normalized feature/outcome readers instead of inventing
  another trading journal. Cache writes pause at a 512MB planning cap; no pruning.
- Read-only adapters reuse paper_outcomes/live_outcomes and existing quote groups.
  They preserve wins/losses and original context. Refills/corrections/synthetic fills
  do not become outcome labels. Quote P&L is never converted into USD implicitly.
  Wallet outcomes require receipt-backed input; unknown clusters share one group,
  and do not count as independent human proof. Generic market contexts have no labels.
- `shadow_ml_models.py` implements bounded linear soft-margin SVM with independent
  logistic calibration and a real four-unit LSTM trained by window BPTT. No NumPy,
  PyTorch, GPU, paid API or pretrained dependency. SVM needs verified risky/safe
  annotations: losing a trade is NOT a scam/risk label. LSTM windows are chronological,
  max eight observations, and nonoverlapping within positions.
- `unified_learning.py` groups/purges chronological splits, compares LSTM to the
  existing feedforward implementation, compares SVM to supplied rule risk scores,
  and evaluates RAG against a frozen eligible retrieval corpus. Metrics aggregate
  positions, not duplicate correlated windows. Net labels are already after costs;
  historical selection/drawdown metrics are not executable counterfactual profits.
- Frozen candidates keep weights, schema, corpus IDs and evidence hashes. Identical
  data does not replace a candidate on every worker restart. Revoked/altered evidence
  blocks inference/evaluation explicitly; artifacts remain preserved. Forecasts are
  immutable per candidate/position and use actual recording time.
- Prospective evaluation reuses neural_autonomy.qualification unchanged, with only
  later confirmed bot trades and genuinely pre-order forecasts; failures can carry
  existing network-cost labels. evaluation_state provides durable recovery, and
  evaluation snapshots are appended separately. Existing live ten-promotion history
  is NEVER edited or credited by these evaluations. New families cannot authorize
  orders from this service; they remain advisory while unqualified.

## Prerequisites and validation gates

Use the monolith and existing pinned host/application inputs. Dependency downloads
need separate approved accounting; prefer cached/offline builds. Do not broaden
network access or grant reserve automatically if a prerequisite is missing.

```bash
nix build --offline .#native-rpc .#native-rpc-app
nix develop --offline .#native-rpc
python -c 'import _solana_binary, importlib.metadata; print(_solana_binary.__file__); print(importlib.metadata.version("crypto-bot-solana-binary"))'
cargo test --locked --offline --features rpc-engine --manifest-path native/solana_binary/Cargo.toml
python -m unittest discover -s tests -p 'test_unified_learning.py'
python -m unittest discover -s tests -p 'test_rpc_stream_failover.py'
python -m unittest discover -s tests -p 'test_rpc_response_parity.py'
SOLANA_NATIVE_LOOPBACK_STRESS=1 python -m unittest discover -s tests -p 'test_rpc_transport_complete.py'
SOLANA_NATIVE_LOOPBACK_STRESS=1 python -m unittest discover -s tests -p 'test_native_transport_stress.py'
python benchmark_rpc_transport.py --allow-loopback --ab --iterations 10
nix build --offline .#checks.x86_64-linux.solana-network-lifecycle
nix flake show --offline
```

Run the remaining startup/history/recovery suites in rpc_production_hardening.md
and rpc_backend_completion_matrix.md. Inspect every skip. Offline/cache failure is
a gate: approve a bounded artifact transfer explicitly rather than removing isolation.
Capture build output/import path, test counts, A/B results and runtime logs as operator
evidence. Prior test passes are not measurements for these new ML/service changes.

## Declarative activation

Import `inputs.crypto-bot.nixosModules.observer-workflows` into the EXISTING host
configuration; do not create another flake. Example, adjust actual runtime paths:

```nix
services.crypto-bot-observer = {
  enable = true;
  configFile = "/private/reviewed-observer-rust.json";
  stateDirectory = "/var/lib/crypto-bot-observer";
  maxCycles = 1;
  learning = {
    enable = true;
    inputFile = "/private/reviewed-normalized-observations.jsonl";
  };
};
services.solana-budget-isolation.uplinkInterface = "YOUR_UPLINK";
# Session bytes and reserve remain governed by the existing reviewed settings.
```

Prepare the runtime JSON with explicit backend=rust, ws_backend=rust and
network_budget.status_file=/run/solana-budget-status.json. Keep actual source IDs,
reviewed rights, mint/pool/program versions; never enable or mark unreviewed sources
approved to make startup succeed. No .env or wallet keys are needed by these services.
Module credentials remain runtime-only; no config/data content enters the Nix store.

Initially use a NEW stateDirectory for isolated real-provider validation. This is
real ingestion, not synthetic market data. After acceptance, the same service can
operate on the selected production observer directory while retaining its checkpoint.
Stop existing workloads before activation. Review host dry activation, then operator:

```bash
sudo nixos-rebuild dry-activate --flake /path/to/host-flake#HOST
sudo nixos-rebuild switch --flake /path/to/host-flake#HOST
sudo systemctl start crypto-bot-observer.service
sudo journalctl -u crypto-bot-observer -u solana-budget-network --since today
```

No wantedBy starts a validator, observer or trader at activation. The manual observer
start pulls in the quota controller; source/session authorization and automatic
finalized-tip discovery apply. Session end terminates the observer, leaves a gap,
and preserves checkpoints. A restart requires a new charged bounded session and
resumes the SAME database; no unlimited catch-up. Check --status, actual source ID,
lag/coverage/quarantine, namespace membership, disk budgets, RSS and budget report.
Use read-only status through the native app wrapper with the same state path.
The 120GB normal, 150GB absolute and 5GB margin remain unchanged.

Run learning separately, after data review, without starting a network service:

```bash
sudo systemctl start crypto-bot-unified-learning.service
sudo journalctl -u crypto-bot-unified-learning --since today
```

The worker creates only its new isolated `STATE/ml/unified.sqlite` if absent. It
never resets an existing DB. It imports at most 1024 bounded JSONL rows and trains
eligible models; unavailable labels/rights/sequences yield explicit blocked results.
Input is idempotent by source/identity; conflicting corrections must be investigated
and revoked explicitly, never silently overwritten. Service resource limits are
planning controls, not proof of sufficient memory; OOM/time-limit failure must retain
source files/cache and be investigated before retrying.

## Input contract and inference/evaluation

Normalized rows require identity, position_id, group_id, source, mint, feature_schema,
feature_at, known_at, features, reviewed rights and provenance. Optional net_pnl_usd,
fees_usd, label_known_at, label_kind and verified risk_label/risk_label_at remain
separate. Unknown features are null with explicit masks. Rights require storage
approval; training/evaluation also require explicit ML/evaluation permission, verified
provenance, no synthetic/corrected labels and features known by their decision time.
All original context remains available for data-rights checks. A source approval is
not proof of executable fills. Missing fee/conversion proof must not be marked verified.

Use the read-only adapter functions import_bot_ledger (paged), import_wallet_outcomes,
import_position_quotes and add_market_context to connect existing sources. Give them
reviewed rights/provenance, not blanket true flags. Quote timestamps and net-cost
proof need explicit verification. Existing ledger/quote readers currently materialize
source histories; use bounded normalized exports for large ledgers. Automatic correction
propagation into the cache is NOT implemented: call store.revoke with a reason before
using changed upstream evidence. Original ledgers are untouched.

Train/infer/evaluate manually with the same installed wrapper, inside appropriate
service resource limits for large datasets:

```bash
crypto-bot-unified-learning --db STATE/ml/unified.sqlite --status
crypto-bot-unified-learning --db STATE/ml/unified.sqlite --train all
crypto-bot-unified-learning --db STATE/ml/unified.sqlite --candidate ID --forecast-input /private/decision.json
crypto-bot-unified-learning --db STATE/ml/unified.sqlite --candidate ID --evaluation-input /private/confirmed-results.json
python benchmark_unified_learning.py --db STATE/ml/unified.sqlite
```

Decision JSON contains observations and position_id. Evaluation JSON contains verified
outcomes (including source=bot_live, position_id, original pre-order context and net
cost provenance) and failures. API forecast logging must occur before orders; imported
historical predictions cannot qualify prospectively. Supplied input must never contain
wallet secrets. New model scores are auditable advice, not a replacement safety veto.

## Monitoring, restart and rollback

Keep network-events and service logs bounded with host journald/logrotate policy.
Watch ML blocked reasons, candidate IDs, frozen lineage, prospective results, source
rights, unknown/risk-label counts, memory pressure and SQLite size. The shared store
uses a bounded recent/historical training sample; all valid rows remain retained.
Retrieval has a bounded indexed candidate set. No old training or wallet histories
are deleted to satisfy these caps.

Stop observer/controller to cut connectivity; stop ML before cache backup. Back up
SQLite and evaluation_state checkpoint/JSON files consistently. Preserve the monthly
ledger across every Nix rollback. To revert RPC, use the same reviewed config/source
permissions with backend/ws_backend=python, the same status file and SAME observer DB.
Stop/restart manually; checkpoints are not reset. To revert ML, stop its service and
set learning.enable=false; existing NN/trading state never changed. Keep cache,
models, forecasts and evaluation snapshots. Frozen IDs can be selected explicitly;
never restore stale bandwidth quotas or count a capital refill as model improvement.

## Honest acceptance status and remaining work

Implemented: service wiring, bounded data/cache/retrieval, CPU SVM/LSTM, frozen
artifacts/forecast APIs and reuse of prospective qualification. Deployed: none.
Executed new tests/benchmarks: zero. Measured ingestion/model accuracy/RSS: unavailable.

Real source ML/storage rights, verified risk annotations, reliable sequence timestamps,
net-cost/conversion evidence, prospective predictions and independent later outcomes
remain data gates. Production transport rollout still requires the operator's full
acceptance sequence. No measured profitability or model superiority is established.

Automatic trading-loop forecast capture and family-specific live promotion/order
adapters are NOT implemented. The service does not bypass those missing integration
gates or reuse another model's ten-promotion streak to authorize a new family. The
existing NN stays authoritative. This is a runnable observer plus bounded ML worker
implementation, not a claim that all requested live ML integration is complete.
