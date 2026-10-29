"""Observable trend/candle rejection rules, not a fraud or profitability detector."""
from dataclasses import dataclass
from urllib.parse import urlparse
from meme_evidence import number


@dataclass(frozen=True)
class CandleQualityPolicy:
    require_uptrend: bool = True
    interval_seconds: int = 60
    max_history_age_seconds: int = 120
    min_bars: int = 6
    min_rising_close_fraction: float = .6
    max_wick_fraction: float = .8
    max_wicky_bar_fraction: float = .34
    max_bar_range_fraction: float = .30
    max_gap_fraction: float = .10
    max_quote_deviation_fraction: float = .10

    def __post_init__(self):
        if type(self.min_bars) is not int or self.min_bars < 3 or self.interval_seconds not in (60,3600):
            raise ValueError('Invalid candle window')
        for value in (self.min_rising_close_fraction,self.max_wick_fraction,self.max_wicky_bar_fraction,
                      self.max_bar_range_fraction,self.max_gap_fraction,self.max_quote_deviation_fraction):
            number(value,0,1)
        number(self.max_history_age_seconds,1)

    def assess(self, snapshot, now):
        reasons = []
        metrics = {}
        try:
            history = snapshot['candle_history']
            if history['mint'] != snapshot['mint'] or history['pool'] != snapshot['pool']:
                reasons.append('candle mint/pool mismatch')
            source = urlparse(history['source_url'])
            if source.scheme != 'https' or not source.hostname:
                raise ValueError('Candle source required')
            if not 0 <= now-number(history['retrieved_at']) <= self.max_history_age_seconds:
                reasons.append('stale candle retrieval')
            interval = history['interval_seconds']
            if type(interval) is not int or interval != self.interval_seconds:
                raise ValueError('Completed candles at configured interval required')
            rows = history['candles']
            if not isinstance(rows, list) or len(rows) < self.min_bars:
                raise ValueError('Insufficient candle history')
            rows = rows[-self.min_bars:]
            previous = None
            closes, lows, wicky = [], [], 0
            for row in rows:
                timestamp = number(row['timestamp'])
                if timestamp != int(timestamp) or timestamp % interval or timestamp+interval > now:
                    raise ValueError('Partial or invalid candle timestamp')
                o, h, l, c = [number(row[key], 1e-15) for key in ('open', 'high', 'low', 'close')]
                volume = number(row['volume_usd'])
                if not l <= min(o, c) <= max(o, c) <= h:
                    raise ValueError('Invalid candle OHLC')
                if volume <= 0 or h == l:
                    reasons.append('inactive or flat candle')
                if (h-l)/o > self.max_bar_range_fraction:
                    reasons.append('extreme candle range')
                if h > l and ((h-max(o,c))+(min(o,c)-l))/(h-l) > self.max_wick_fraction:
                    wicky += 1
                if previous is not None:
                    if timestamp-previous['timestamp'] != interval:
                        raise ValueError('Gapped candle history')
                    if abs(o/previous['close']-1) > self.max_gap_fraction:
                        reasons.append('large inter-candle price gap')
                closes.append(c)
                lows.append(l)
                previous = row
            if not 0 <= now-(rows[-1]['timestamp']+interval) <= self.max_history_age_seconds:
                reasons.append('stale completed candles')
            rising = sum(b > a for a,b in zip(closes,closes[1:]))/(len(closes)-1)
            gain = closes[-1]/closes[0]-1
            if self.require_uptrend and (gain <= 0 or rising < self.min_rising_close_fraction):
                reasons.append('recent closes do not show sustained upward momentum')
            if self.require_uptrend and lows[-1] <= lows[0]:
                reasons.append('recent lows do not show upward support')
            if wicky/len(rows) > self.max_wicky_bar_fraction:
                reasons.append('excessive wick-dominated candles')
            if abs(number(snapshot['price_usd'],1e-15)/closes[-1]-1) > self.max_quote_deviation_fraction:
                reasons.append('quote moved too far from completed candles')
            metrics = {'rising_close_fraction': rising, 'close_gain_fraction': gain,
                       'wicky_bar_fraction': wicky/len(rows), 'bars': len(rows)}
        except ValueError as error:
            known={'Candle source required':'candle source is missing',
                   'Completed candles at configured interval required':'wrong candle interval; completed 1m candles required',
                   'Insufficient candle history':f'fewer than {self.min_bars} completed candles available',
                   'Partial or invalid candle timestamp':'partial or invalid candle timestamp',
                   'Invalid candle OHLC':'candle prices violate low/open/close/high ordering',
                   'Gapped candle history':'missing one or more consecutive candle intervals'}
            reasons.append(known.get(str(error),'invalid candle numeric values'))
        except (KeyError, TypeError, AttributeError, OverflowError):
            reasons.append('required candle fields missing or malformed')
        return {'passed': not reasons, 'reasons': list(dict.fromkeys(reasons)), 'metrics': metrics,
                'note': 'Price structure only. Candles cannot establish absence of manipulation.'}


def assess_hourly(asset,pool,bars,source_url,retrieved_at,now):
    history={'mint':asset['mint'],'pool':pool['pool'],'source_url':source_url,
             'retrieved_at':retrieved_at,'interval_seconds':3600,
             'candles':[{'timestamp':bar.timestamp,'open':bar.open,'high':bar.high,'low':bar.low,
                         'close':bar.close,'volume_usd':bar.volume} for bar in bars[-6:]]}
    snapshot={'mint':asset['mint'],'pool':pool['pool'],'price_usd':bars[-1].close if bars else 0,
              'candle_history':history}
    return CandleQualityPolicy(interval_seconds=3600,max_history_age_seconds=3600).assess(snapshot,now)


def provider_history(payload,mint,pool,url,now):
    if payload['meta']['base']['address'] != mint:
        raise ValueError('Minute history token orientation mismatch')
    rows=[]
    seen=set()
    for row in payload['data']['attributes']['ohlcv_list']:
        if len(row)!=6:
            raise ValueError('Invalid minute candle row')
        stamp=number(row[0])
        if stamp in seen:
            raise ValueError('Duplicate minute candle')
        seen.add(stamp)
        if stamp+60 > now:
            continue
        rows.append(dict(zip(('timestamp','open','high','low','close','volume_usd'),row)))
    return {'mint':mint,'pool':pool,'source_url':url,'retrieved_at':now,
            'interval_seconds':60,'candles':sorted(rows,key=lambda row:row['timestamp'])}
