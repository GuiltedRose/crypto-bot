"""Minimal market entry policy: reported buy volume + real completed candles.

Does not infer momentum, direction, social support, or absence of manipulation.
Transaction identity, fees, exact output bounds and position limits live in the
execution layer and remain mandatory.
"""
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
        bars=bars[-rules.trend_min_bars:]
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
    if len(bars)<3: return watch('need impulse, pullback and reclaim candles')
    # Reserve the final two completed candles for pullback and confirmation.
    impulse=bars[:-2];dip,reclaim=bars[-2:]
    if any(b['timestamp']-a['timestamp']!=60 for a,b in zip(bars,bars[1:])):
        return watch('waiting for contiguous momentum candles')
    peak=max(b['high'] for b in impulse)
    gain=impulse[-1]['close']/impulse[0]['open']-1
    if gain<rules.trend_min_gain_fraction or impulse[-1]['close']<=impulse[0]['open']:
        return watch('waiting for an upward impulse')
    depth=1-dip['low']/peak
    if dip['close']>=dip['open'] or dip['low']>=impulse[-1]['low'] and dip['close']>=impulse[-1]['close']:
        return watch('waiting for a completed pullback')
    if not rules.trend_pullback_min_fraction<=depth<=rules.trend_pullback_max_fraction:
        return watch('pullback outside the configured setup range',pullback_fraction=depth)
    if reclaim['low']<dip['low']:
        return watch('pullback low broke; setup invalidated')
    if reclaim['close']<=dip['high'] or reclaim['close']<=reclaim['open']:
        return watch('waiting for a completed reclaim above pullback high')
    if reclaim['volume_usd']<dip['volume_usd'] or reclaim['volume_usd']<=0:
        return watch('reclaim lacks renewed trading activity')
    price=snapshot['price_usd']
    if price<dip['high'] or price>reclaim['close']*(1+rules.slippage_per_side):
        return watch('reclaim lost or entry already moved; do not chase')
    return {'action':'buy','reasons':[],'reason':'upward impulse, pullback and volume-backed reclaim',
            'metrics':{'peak_price':peak,'pullback_low':dip['low'],'pullback_fraction':depth,
                       'runup_gain_fraction':gain,'completed_bars':len(bars),
                       'human_buyers_verified':False}}
