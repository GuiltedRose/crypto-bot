"""Volume/candle entry policy with an optional reclaim and directional-flow gate.

Reported aggregates are not proof of independent demand or absence of wash
trading. Transaction identity, fees, exact output bounds and position limits
live in the execution layer and remain mandatory.
"""
import math
from urllib.parse import urlparse
from meme_evidence import number


def assess_volume_candles(snapshot,now,rules):
    reasons=[];bars=[]
    try:
        if not 0<=now-number(snapshot['observed_at'])<=30: reasons.append('stale market observation')
        if number(snapshot['buy_volume_window_seconds'])!=rules.buy_window_seconds: reasons.append('wrong buy volume window')
        if number(snapshot['buy_volume_coverage_pct'],0,100)<95: reasons.append('incomplete buy volume coverage')
        if number(snapshot['buy_volume_usd'])<rules.min_buy_volume_usd: reasons.append('recent buy volume below threshold')
        history=snapshot['candle_history']
        if history['mint']!=snapshot['mint'] or history['pool']!=snapshot['pool']: reasons.append('candle mint/pool mismatch')
        source=urlparse(history['source_url'])
        if source.scheme!='https' or not source.hostname: reasons.append('candle source unavailable')
        if history['interval_seconds']!=60 or type(history['interval_seconds']) is not int: reasons.append('one-minute candles required')
        if history.get('measure','price_usd')!='price_usd': reasons.append('USD-price candles required')
        if not 0<=now-number(history['retrieved_at'])<=120: reasons.append('stale candle retrieval')
        bars=history['candles']
        if not isinstance(bars,list): raise ValueError('Candle array required')
        bars=bars[-12:] if rules.momentum_reclaim_enabled else bars[-rules.trend_min_bars:]
        if len(bars)<rules.trend_min_bars: reasons.append(f'only {len(bars)} completed 1m candles available; need {rules.trend_min_bars}')
        previous=-1
        for row in bars:
            if row.get('final') is False: reasons.append('partial candle cannot count as completed')
            stamp=number(row['timestamp'])
            if stamp!=int(stamp) or stamp%60 or stamp+60>now or stamp<=previous: reasons.append('partial, duplicate or invalid candle timestamp')
            previous=stamp
            o,h,l,c=(number(row[k],1e-15) for k in ('open','high','low','close'))
            if not l<=min(o,c)<=max(o,c)<=h: reasons.append('malformed candle prices')
            number(row['volume_usd'])
        if bars and not 0<=now-(bars[-1]['timestamp']+60)<=120: reasons.append('stale completed candles')
        number(snapshot['price_usd'],1e-15)
    except (KeyError,TypeError,ValueError,AttributeError): reasons.append('required volume/candle data unavailable or invalid')
    if not reasons and rules.momentum_reclaim_enabled:
        return assess_reclaim(snapshot,bars,rules)
    return {'action':'reject' if reasons else 'buy','reasons':list(dict.fromkeys(reasons)),
            'reason':'buy volume and completed-candle minimums met' if not reasons else None,
            'metrics':{'completed_bars':len(bars) if isinstance(bars,list) else 0,'minimum_buy_volume_usd':rules.min_buy_volume_usd}}


def assess_reclaim(snapshot,bars,rules):
    """Short-window impulse/pullback/reclaim hypothesis, using closed bars only."""
    def watch(reason,**metrics):
        return {'action':'watch','reasons':[], 'reason':reason,'metrics':metrics}
    buy=snapshot['buy_volume_usd']
    sell=snapshot.get('sell_volume_usd')
    if isinstance(sell,bool) or not isinstance(sell,(int,float)) or not 0<=sell<float('inf'):
        return watch('hourly sell-volume evidence unavailable')
    ratio=buy/sell if sell>0 and math.isfinite(buy/sell) else None
    if buy<sell*rules.min_buy_sell_ratio:
        return watch('hourly buy pressure below configured minimum',
                     buy_volume_usd=buy,sell_volume_usd=sell,buy_sell_ratio=ratio)
    if len(bars)<3: return watch('need impulse, pullback and reclaim candles')
    # Search observed setups, keeping the impulse while later bars confirm it.
    reclaim=bars[-1]
    found=[]
    for index in range(1,len(bars)-1):
        impulse=bars[max(0,index-6):index];dip=bars[index]
        window=bars[max(0,index-6):]
        if any(b['timestamp']-a['timestamp']!=60 for a,b in zip(window,window[1:])): continue
        peak=max(b['high'] for b in impulse)
        gain=max(b['close'] for b in impulse)/impulse[0]['open']-1
        depth=1-dip['low']/peak
        if gain<rules.trend_min_gain_fraction: continue
        if dip['close']>=dip['open'] or dip['close']>=impulse[-1]['close']: continue
        if not rules.trend_pullback_min_fraction<=depth<=rules.trend_pullback_max_fraction: continue
        # A later low invalidates this setup, rather than moving its reference.
        if min(b['low'] for b in bars[index+1:])<dip['low']: continue
        found.append((dip,peak,gain,depth))
    if not found: return watch('no intact momentum/pullback setup in observed history')
    qualified=[x for x in found if reclaim['close']>x[0]['high'] and reclaim['close']>reclaim['open']
               and reclaim['volume_usd']>=x[0]['volume_usd'] and reclaim['volume_usd']>0]
    if not qualified: return watch('waiting for reclaim with renewed activity',setups_tracked=len(found))
    dip,peak,gain,depth=qualified[-1]
    price=snapshot['price_usd']
    if price<dip['high'] or price>reclaim['close']*(1+rules.slippage_per_side):
        return watch('reclaim lost or entry already moved; do not chase')
    return {'action':'buy','reasons':[],'reason':'upward impulse, pullback and volume-backed reclaim',
            'metrics':{'peak_price':peak,'pullback_low':dip['low'],'pullback_fraction':depth,
                       'runup_gain_fraction':gain,'completed_bars':len(bars),
                       'buy_volume_usd':buy,'sell_volume_usd':sell,'buy_sell_ratio':ratio,
                       'human_buyers_verified':False}}
