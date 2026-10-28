"""Evaluate recorded Solana pool events without a centralized exchange account."""
import argparse
import csv
import json
import time
from dataclasses import asdict
from pathlib import Path
from market import pool_candles
from evaluate import evaluate
from strategy import decide,WARMUP_BARS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('events', help='Decompressed ZeroSlip JSONL archive/recording')
    parser.add_argument('--pool-id', required=True, help='Explicit SOL/USDC pool address')
    args = parser.parse_args()
    now = int(time.time())
    with open(args.events) as source:
        candles = pool_candles((json.loads(line) for line in source if line.strip()), args.pool_id, now)
    if len(candles) < WARMUP_BARS+2:
        raise ValueError(f'Need at least {WARMUP_BARS+2} complete contiguous hours of pool events')
    path = Path('data/sol_usdc_pool_hourly.csv')
    path.parent.mkdir(exist_ok=True)
    with path.open('w', newline='') as target:
        writer = csv.DictWriter(target, fieldnames=list(asdict(candles[0])))
        writer.writeheader()
        writer.writerows(asdict(c) for c in candles)
    report = evaluate(candles)
    report.update(reference_signal=asdict(decide(candles, now, candles[-1].close)),
                  reference_only=True, candles=len(candles), pool_id=args.pool_id,
                  source='Recorded SOL/USDC pool trades',
                  note='Pool trade close is not an executable quote. Assumes USDC=$1. No orders submitted.')
    Path('data/pool_strategy_report.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k != 'trades'}, indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Pool inspection failed ('+type(error).__name__+'); no transaction submitted.')
        raise SystemExit(1)
