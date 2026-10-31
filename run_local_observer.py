"""Read-only bridge to an operator-provided local replaying Solana node.

Not a validator, shred decoder or Geyser implementation. No external fallback.
"""
import argparse
import ipaddress
import json
from pathlib import Path
from urllib.parse import urlparse
from onchain_ingest import IndexRpc, Ingester, follow
from onchain_store import EventStore
from onchain_candles import CandleEngine


def local_endpoint(endpoint):
    parsed = urlparse(endpoint['url'])
    try:
        local = ipaddress.ip_address(parsed.hostname or '').is_loopback
    except ValueError:
        local = False
    if not local or parsed.scheme not in ('http', 'https') or parsed.username or parsed.password or parsed.fragment:
        raise ValueError('Only explicit literal loopback RPC endpoints are allowed')
    if parsed.scheme == 'http' and endpoint.get('allow_loopback_http') is not True:
        raise ValueError('Loopback HTTP requires explicit configuration')
    return endpoint


def open_isolated(path, resume=False):
    path = Path(path)
    if path.exists():
        if not resume:
            raise ValueError('Use a new isolated database, or explicitly --resume an observer database')
        with EventStore(path,readonly=True).connect() as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'blocks', 'sources', 'checkpoints'}.issubset(tables):
            raise ValueError('Not an observer index; trading databases must remain separate')
    return EventStore(path, retain_blocks=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--start-slot', type=int)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--loop', action='store_true')
    parser.add_argument('--batch-slots', type=int, default=8)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    endpoints = [local_endpoint(row) for row in config['endpoints'] if row.get('enabled') is True]
    if not endpoints:
        parser.error('No reviewed local node enabled; no observer or validator was started')
    markets = config.get('markets', [])
    if len(markets) > 32:
        parser.error('At most 32 explicit markets')
    store = open_isolated(args.db, args.resume)
    for endpoint in endpoints:
        store.register_source(endpoint['source_id'], endpoint['policy'])
    engine = CandleEngine(store)
    for market in markets:
        engine.watch(market['mint'], market['pool'])
    ingester = Ingester(store, [IndexRpc(e) for e in endpoints], config.get('reviewed_program_versions'))
    start = None if store.next_slot() is not None else args.start_slot
    results = follow(ingester, start, args.batch_slots) if args.loop else [ingester.step(start, args.batch_slots)]
    for result in results:
        result.update(mode='local_replay_node_bridge', independent_feed_proven=False,
                      external_market_rpc_fallback=False, candles=engine.sync())
        print(json.dumps(result), flush=True)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('Stopped; local index checkpoint retained.')
