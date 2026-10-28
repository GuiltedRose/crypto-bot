"""Atomic arbitrage admission rules. No sequential execution or signing path.

A fully executable route needs independently reviewed local adapters. The first
leg buys an EXACT output quantity; the second sells that exact quantity. This
avoids leftover inventory from an exact-input first leg. Quote profit alone
cannot enable execution.
"""
from dataclasses import dataclass
from decimal import Decimal,ROUND_CEILING
import json
import math
import time
from pathlib import Path
from venue_policy import entry_allowed,REVIEWS

@dataclass(frozen=True)
class BuyQuote:
    venue:str
    mint:str
    token_units:int
    max_usdc_units:int
    quoted_at:float
    exact_output:bool=True

@dataclass(frozen=True)
class SellQuote:
    venue:str
    mint:str
    token_units:int
    min_usdc_units:int
    quoted_at:float
    exact_input:bool=True

@dataclass(frozen=True)
class AtomicPlan:
    buy:BuyQuote
    sell:SellQuote
    fee_reserve_usdc_units:int
    min_net_profit_usdc_units:int
    transaction_count:int=1


def admit(buy,sell,equity_usd,fee_reserve_usdc_units,rent_usdc_units=0,
          min_net_profit_usdc_units=100,now=None):
    now=time.time() if now is None else now
    values=(now,equity_usd,buy.quoted_at,sell.quoted_at)
    if not all(math.isfinite(v) for v in values) or equity_usd<=1:
        raise ValueError('Invalid equity/quote snapshot')
    amounts=(buy.token_units,sell.token_units,buy.max_usdc_units,sell.min_usdc_units,
             fee_reserve_usdc_units,rent_usdc_units,min_net_profit_usdc_units)
    if any(type(n) is not int or n<0 for n in amounts) or min(buy.token_units,buy.max_usdc_units,min_net_profit_usdc_units)<=0:
        raise ValueError('Invalid atomic amounts')
    if buy.venue==sell.venue or not all(entry_allowed(v) for v in (buy.venue,sell.venue)):
        raise ValueError('Two distinct eligible venues required')
    if buy.mint!=sell.mint or buy.token_units!=sell.token_units or not buy.exact_output or not sell.exact_input:
        raise ValueError('Atomic cycle requires exact-output buy and matching exact-input sell')
    if any(not 0<=now-at<=2 for at in (buy.quoted_at,sell.quoted_at)):
        raise ValueError('Arbitrage quotes stale or future-dated')
    # This strategy has no proved Kelly history: use the same 1% startup ceiling.
    cap=min(equity_usd*.01,equity_usd-1)
    if buy.max_usdc_units/1e6>cap:
        raise ValueError('Arbitrage exceeds startup allocation')
    costs=fee_reserve_usdc_units+rent_usdc_units
    # The hard encoded second-leg minimum must cover the maximum initial debit,
    # both network fees, any rent and a positive profit cushion.
    cushion=max(min_net_profit_usdc_units,int((Decimal(costs)*Decimal('0.5')).to_integral_value(rounding=ROUND_CEILING)))
    if sell.min_usdc_units<buy.max_usdc_units+costs+cushion:
        raise ValueError('Worst-case atomic return cannot cover costs and profit')
    return AtomicPlan(buy,sell,fee_reserve_usdc_units,cushion)


def status():
    eligible=[name for name in REVIEWS if entry_allowed(name)]
    return {'strategy':'cross-dex-atomic-arbitrage','atomic_required':True,'transaction_count':1,
            'eligible_venue_reviews':eligible,'live_execution_enabled':entry_allowed('orca') and Path('bin/orca-quote').exists(),
            'reason':'direct Orca exact-output buy and PumpSwap exact-input sell; only profitable, admitted, simulated cycles can be sent',
            'requirements':['exact-output first leg','matching exact-input second leg',
                'second-leg encoded minimum covers principal, fees, rent and profit',
                'one message, one signature, one RPC submission','no sequential fallback',
                'wallet inventory, portfolio slot and unresolved-order reconciliation'],
            'failure_cost':'A reverted transaction can still consume a network fee'}


if __name__=='__main__':
    report=status()
    Path('state').mkdir(exist_ok=True)
    Path('state/arbitrage_status.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report))
