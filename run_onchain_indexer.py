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
    parser.add_argument('--max-catchup-slots', type=int, default=64)
    parser.add_argument('--loop', action='store_true')
    parser.add_argument('--interval', type=float, default=5)
    parser.add_argument('--capture-sol-usd', action='store_true', help='Explicit reviewed Pyth account capture into the selected isolated index; no historical backfill')
    parser.add_argument('--retain-blocks', action='store_true', help='Keep compressed full blocks for independent offline replay; substantial disk cost')
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--mint')
    parser.add_argument('--pool')
    args = parser.parse_args()
    store = EventStore(args.db, retain_blocks=args.retain_blocks)
    if args.status or args.mint:
        watermark = store.watermark()
        coverage = store.coverage(watermark-3600, watermark) if watermark is not None else {
            'complete': False, 'coverage_pct': 0, 'reason': 'no finalized blocks indexed'}
        result = {'mode': 'read_only_shadow', 'metrics': store.metrics(),
                  'finalized_hour_coverage': coverage, 'progress': store.progress()}
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
    if args.capture_sol_usd:
        if not endpoints:
            parser.error('A reviewed enabled RPC endpoint is required')
        from onchain_oracle import capture_sol, import_sol
        print(json.dumps(import_sol(store, capture_sol(endpoints[0]))))
        return
    ingester = Ingester(store, endpoints, config.get('reviewed_program_versions'))
    start = args.start_slot if store.next_slot() is None else None
    if args.loop:
        for result in follow(ingester, start, args.batch_slots, args.interval, max_catchup_slots=args.max_catchup_slots):
            print(json.dumps(result), flush=True)
    else:
        print(json.dumps(ingester.step(start, args.batch_slots)))


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('Stopped. Index checkpoint preserved; no transactions submitted.')
