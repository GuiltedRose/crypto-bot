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
    return {'action':'reject' if reasons else 'buy','reasons':list(dict.fromkeys(reasons)),
            'reason':'buy volume and completed-candle minimums met' if not reasons else None,
            'metrics':{'completed_bars':len(bars) if isinstance(bars,list) else 0,'minimum_buy_volume_usd':rules.min_buy_volume_usd}}
