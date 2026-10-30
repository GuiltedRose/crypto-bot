"""Read-only finalized Pump indexer and local market shadow report."""
import argparse
import json
from pathlib import Path
from onchain_store import EventStore
from onchain_ingest import IndexRpc, Ingester, follow
from onchain_market import OnchainMarket


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('onchain_indexer.example.json'))
    parser.add_argument('--db', type=Path, default=Path('state/onchain/index.sqlite'))
    parser.add_argument('--start-slot', type=int)
    parser.add_argument('--batch-slots', type=int, default=8)
    parser.add_argument('--loop', action='store_true')
    parser.add_argument('--interval', type=float, default=5)
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--mint')
    parser.add_argument('--pool')
    args = parser.parse_args()
    store = EventStore(args.db)
    if args.status or args.mint:
        watermark = store.watermark()
        coverage = store.coverage(watermark-3600, watermark) if watermark is not None else {
            'complete': False, 'coverage_pct': 0, 'reason': 'no finalized blocks indexed'}
        result = {'mode': 'read_only_shadow', 'metrics': store.metrics(),
                  'finalized_hour_coverage': coverage}
        if args.mint:
            try:
                result['market'] = OnchainMarket(store).observation(args.mint, pool=args.pool)
            except ValueError as error:
                result['market_unavailable'] = str(error)
        print(json.dumps(result, allow_nan=False))
        return
    config = json.loads(args.config.read_text())
    endpoints = []
    for endpoint in config['endpoints']:
        if endpoint.get('enabled') is not True:
            continue
        store.register_source(endpoint['source_id'], endpoint['policy'])
        endpoints.append(IndexRpc(endpoint))
    ingester = Ingester(store, endpoints, config.get('reviewed_program_versions'))
    if args.loop:
        for result in follow(ingester, args.start_slot, args.batch_slots, args.interval):
            print(json.dumps(result), flush=True)
    else:
        print(json.dumps(ingester.step(args.start_slot, args.batch_slots)))


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('Stopped. Index checkpoint preserved; no transactions submitted.')
