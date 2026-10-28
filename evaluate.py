"""Evaluate a fixed strategy on chronological, independent development/holdout windows.

No wallet access. Costs and notional are explicit assumptions, not live quotes.
"""
import argparse
import csv
import json
import math
from collections import Counter
from dataclasses import dataclass
from strategy import Candle, Position, validate, EntryRules, BASELINE_RULES, DonchianRules
from strategies import decide as registered_decide
from strategy import decide as legacy_decide

def decide(candles,now,quote,position=None,rules=None):
    return (legacy_decide if isinstance(rules,EntryRules) else registered_decide)(candles,now,quote,position,rules)


@dataclass(frozen=True)
class Costs:
    notional_usd: float = .20
    fee_per_side: float = .003  # illustrative direct non-canonical PumpSwap pool
    slippage_per_side: float = .005
    fixed_per_side_usd: float = .005

    def __post_init__(self):
        if (not all(math.isfinite(v) for v in (self.notional_usd, self.fee_per_side,
                self.slippage_per_side, self.fixed_per_side_usd)) or self.notional_usd <= 0 or
                not 0 <= self.fee_per_side < 1 or not 0 <= self.slippage_per_side < 1 or
                self.fixed_per_side_usd < 0):
            raise ValueError('Invalid cost assumptions')


def _window(candles, start, end, costs, rules=None, enforce_cost_gate=False):
    position = pending = None
    entry_bar = None
    trades, reasons = [], Counter()
    slippage = costs.slippage_per_side
    fee = costs.fee_per_side
    fixed = costs.fixed_per_side_usd
    debit = costs.notional_usd*(1+fee)+fixed

    def close(price, index, reason):
        exit_fill = price*(1-slippage)
        quantity = costs.notional_usd/position.entry_price
        proceeds = quantity*exit_fill*(1-fee)-fixed
        pnl = proceeds-debit
        trades.append({'entry_bar': entry_bar, 'exit_bar': index,
                       'entry_price': position.entry_price, 'exit_price': exit_fill,
                       'gross_return': price/(position.entry_price/(1+slippage))-1,
                       'net_pnl_usd': pnl, 'net_return': pnl/debit, 'reason': reason})

    for i in range(start, end):
        bar = candles[i]
        if pending:
            if pending.action == 'sell':
                close(bar.open, i, pending.reason)
                position = None
            elif abs(bar.open/candles[i-1].close-1) <= .005:
                fill = bar.open*(1+slippage)
                modeled_cost=2*costs.notional_usd*(fee+slippage)+2*fixed
                if enforce_cost_gate and costs.notional_usd*pending.target_distance/fill < 1.5*modeled_cost:
                    reasons['target cannot cover costs']+=1
                    pending=None
                    continue
                entry_bar=i
                position = Position(fill, fill-pending.stop_distance,
                                    fill+pending.target_distance if pending.take_profit else None, bar.timestamp,
                                    pending.strategy_id)
            else:
                reasons['entry gap too large'] += 1
            pending = None
        if position:
            if bar.low <= position.stop_price:
                close(min(bar.open, position.stop_price), i, 'stop')
                position = None
            elif position.target_price is not None and bar.high >= position.target_price:
                close(position.target_price, i, 'target')
                position = None
        signal = decide(candles[:i+1], bar.timestamp+3600, bar.close, position, rules)
        reasons[signal.reason] += 1
        if signal.action in ('buy', 'sell'):
            pending = signal
    values = [t['net_pnl_usd'] for t in trades]
    wins, losses = sum(max(v,0) for v in values), -sum(min(v,0) for v in values)
    cumulative = peak = drawdown = 0
    for value in values:
        cumulative += value
        peak = max(peak, cumulative)
        drawdown = max(drawdown, peak-cumulative)
    unrealized = None
    if position:
        quantity = costs.notional_usd/position.entry_price
        unrealized = quantity*candles[end-1].close*(1-slippage)*(1-fee)-fixed-debit
    return {'trades': len(trades), 'net_pnl_usd': sum(values),
            'mean_net_pnl_usd': sum(values)/len(values) if values else None,
            'win_rate': sum(v>0 for v in values)/len(values) if values else None,
            'profit_factor': wins/losses if losses else None,
            'max_closed_trade_drawdown_usd': drawdown,
            'open_position_at_end': position is not None,
            'estimated_open_liquidation_pnl_usd': unrealized,
            'pending_signal_at_end': pending.action if pending else None,
            'signal_reasons': dict(reasons), 'completed_trades': trades}


def evaluate(candles, costs=Costs(), rules=None):
    rules=rules or DonchianRules()
    warmup=250 if isinstance(rules,EntryRules) else max(rules.entry_bars,rules.exit_bars,rules.atr_bars)+1
    if len(candles) < warmup+2:
        raise ValueError('Insufficient hourly candles')
    validate(candles, candles[-1].timestamp+3600)
    split = max(warmup+1, int(len(candles)*.7))
    development = _window(candles, warmup, split, costs, rules)
    # Earlier candles warm indicators; no development positions/results carry over.
    holdout = _window(candles, split, len(candles), costs, rules)
    return {'development': development, 'holdout': holdout,
            'cost_assumptions': vars(costs), 'entry_rules': vars(rules), 'split_bar': split,
            'evidence': 'insufficient completed holdout trades' if holdout['trades'] < 100 else 'review cost sensitivity and independent periods before deployment',
            'limitations': 'Signal study at fixed notional; not a Kelly-sized wallet simulation. No measured pool liquidity, latency, rent, failed-transaction fees or market impact. Costs are assumptions. Closed-trade drawdown excludes intra-trade losses. No profitability certification.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv')
    parser.add_argument('--notional', type=float, default=.20)
    parser.add_argument('--fee-per-side', type=float, default=.003)
    parser.add_argument('--slippage-per-side', type=float, default=.005)
    parser.add_argument('--fixed-per-side', type=float, default=.005)
    parser.add_argument('--baseline', action='store_true', help='Compare the original restrictive rules')
    parser.add_argument('--take-profit-r',type=float,default=2.0,help='Profit target as a multiple of initial stop distance')
    args = parser.parse_args()
    with open(args.csv, newline='') as source:
        candles = [Candle(int(r['timestamp']), *(float(r[k]) for k in ('open','high','low','close','volume'))) for r in csv.DictReader(source)]
    print(json.dumps(evaluate(candles, Costs(args.notional, args.fee_per_side,
                                           args.slippage_per_side, args.fixed_per_side),
                              BASELINE_RULES if args.baseline else DonchianRules(take_profit_r=args.take_profit_r)), indent=2))
