"""Synthetic end-to-end example. NOT market evidence, backtest results or profit claims."""
import argparse
import json
from pathlib import Path
import time


def synthetic_history(now,price):
    end=int(now)//60*60
    return {'mint':'SYNTHETIC_MINT','pool':'SYNTHETIC_POOL','source_url':'https://example.invalid/synthetic',
            'retrieved_at':now,'interval_seconds':60,
            'candles':[{'timestamp':end-(6-i)*60,'open':price*(.95+.01*i)*.995,
                        'high':price*(.95+.01*i)*1.003,'low':price*(.95+.01*i)*.992,
                        'close':price*(.95+.01*i),'volume_usd':100} for i in range(6)]}


def snapshots():
    base = int(time.time())-60
    for index, price in enumerate((.63, .60, .63, .90)):
        now = base+index*10
        yield {'synthetic': True, 'mint': 'SYNTHETIC_MINT', 'pool': 'SYNTHETIC_POOL',
               'observed_at': now, 'price_usd': price,'candle_history':synthetic_history(now,price), 'ath_price_usd': 1,
               'ath_checked_at': now, 'ath_source_url': 'https://example.invalid/synthetic',
               'ath_history_complete_since_launch': True, 'token_safety_passed': True,
               'safety_checked_at': now, 'buy_volume_usd': 5000,
               'buy_volume_window_seconds': 3600, 'buy_volume_coverage_pct': 100,
               'liquidity_usd': 100000, 'fixed_cost_per_side_usd': .001,
               'execution': {'mint': 'SYNTHETIC_MINT', 'pool': 'SYNTHETIC_POOL',
                             'observed_at': now, 'sol_price_usd': 100,
                             'network_fee_sol': .000005, 'entry_rent_sol': .00001,
                             'exit_rent_sol': 0, 'buy_route_available': True, 'sell_route_available': True},
               'evidence': {'mint': 'SYNTHETIC_MINT', 'pool': 'SYNTHETIC_POOL', 'observed_at': now,
                            'narrative': {'topic': 'SYNTHETIC ONLY', 'observed_at': now, 'source_url': 'https://example.invalid/synthetic'},
                            'distribution': {'observed_at': now, 'source_url': 'https://example.invalid/synthetic',
                                             'launch_history_complete': True, 'supply_coverage_pct': 100,
                                             'bundled_supply_pct': 0, 'linked_supply_pct': 0},
                            'trading': {'observed_at': now, 'source_url': 'https://example.invalid/synthetic',
                                        'buyer_analysis_coverage_pct': 100, 'independent_buyers': 30,
                                        'suspected_bot_buy_volume_pct': 0, 'wash_volume_pct': 0,
                                        'price_change_1h_pct': 0, 'liquidity_drop_1h_pct': 0}}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True, help='Must be a new directory, separate from real observations')
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=False)
    path = args.directory/'synthetic.jsonl'
    path.write_text(''.join(json.dumps(row)+'\n' for row in snapshots()))
    print(json.dumps({'synthetic_only': True, 'feed': str(path), 'starting_usdc': 10,
                      'native_sol': .006, 'sol_price_usd': 100}))


if __name__ == '__main__':
    main()
