"""Solana pool trade events to candles; no centralized exchange dependency."""
import math
from bot import SOL_MINT
from strategy import Candle, validate

USDC_MINT = 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'


def pool_candles(events, pool_id, now):
    """Aggregate ZeroSlip JSONL events for one explicitly selected SOL/USDC pool.

    Requires complete archived/recorded event coverage from the caller; missing
    hours are rejected, never forward-filled. USDC is treated as $1, not a
    guarantee of redemption. Duplicate events are ignored. No remote requests.
    """
    if not pool_id:
        raise ValueError('Explicit SOL/USDC pool ID required')
    trades, seen = [], set()
    coverage_start = 0
    for event in events:
        if event.get('kind') == 'coverage_reset':
            reset = float(event['timestamp'])/1000
            if not math.isfinite(reset) or reset <= 0:
                raise ValueError('Invalid coverage reset')
            coverage_start = (int(reset)//3600+1)*3600
            trades, seen = [], set()
            continue
        if event.get('poolId') != pool_id or event.get('action') not in ('buy', 'sell'):
            continue
        mint, quote = event.get('mint'), event.get('quoteMint')
        if {mint, quote} != {SOL_MINT, USDC_MINT}:
            continue
        timestamp = float(event['timestamp'])/1000
        if timestamp < coverage_start:
            continue
        base, counter = float(event['tokenAmount']), float(event['quoteAmount'])
        if not all(math.isfinite(v) and v > 0 for v in (timestamp, base, counter)):
            raise ValueError('Invalid pool trade')
        if int(timestamp)//3600*3600+3600 > now:
            continue
        sol, usdc = (base, counter) if mint == SOL_MINT else (counter, base)
        key = (event.get('signature'), timestamp, base, counter, event['action'])
        if key in seen:
            continue
        seen.add(key)
        liquidity=event.get('liquidityUsdEstimate')
        if liquidity is not None and (not math.isfinite(liquidity) or liquidity<0):
            raise ValueError('Invalid event liquidity')
        trades.append((timestamp, usdc/sol, sol,liquidity))
    trades.sort(key=lambda trade: trade[0])
    buckets = {}
    for timestamp, price, quantity,liquidity in trades:
        buckets.setdefault(int(timestamp)//3600*3600, []).append((price, quantity,liquidity))
    candles = []
    for timestamp, fills in sorted(buckets.items()):
        prices = [p for p, _,_ in fills]
        candles.append(Candle(timestamp, prices[0], max(prices), min(prices), prices[-1], sum(q for _,q,_ in fills),
                              quote_volume=sum(p*q for p,q,_ in fills),volume_verified=True,
                              liquidity_usd=min(liq for _,_,liq in fills) if all(liq is not None for _,_,liq in fills) else None))
    validate(candles, now)
    return candles
