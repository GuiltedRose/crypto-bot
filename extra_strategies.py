"""Additional spot challengers; incomplete volume/liquidity data fails closed."""
import math
import statistics
from dataclasses import dataclass
from strategy import Signal,validate,ema,wilder_atr

@dataclass(frozen=True)
class BollingerRules:
    period:int=20
    deviations:float=2
    atr_bars:int=20

@dataclass(frozen=True)
class VwapRules:
    trend_bars:int=50
    atr_bars:int=20

@dataclass(frozen=True)
class SqueezeRules:
    period:int=20
    deviations:float=2
    keltner_atr:float=1.5
    relative_volume:float=1.5
    atr_bars:int=20

@dataclass(frozen=True)
class RangeRules:
    period:int=48
    atr_bars:int=20

@dataclass(frozen=True)
class MomentumRules:
    period:int=20
    momentum_bars:int=5
    minimum_momentum:float=.02
    relative_volume:float=2
    min_liquidity_usd:float=100000
    atr_bars:int=20

EXTRA_PROFILES={
 'bb-mean-reversion-v1-20-2':BollingerRules(),
 'vwap-pullback-v1-utc-ema50':VwapRules(),
 'squeeze-breakout-v1-bb20-kc20':SqueezeRules(),
 'range-structure-v1-48':RangeRules(),
 'volume-momentum-v1-20':MomentumRules(),
}


def bands(values,period=20,deviations=2):
    sample=values[-period:]
    if len(sample)<period:raise ValueError('Insufficient band history')
    middle=statistics.mean(sample)
    spread=deviations*statistics.pstdev(sample)
    return middle-spread,middle,middle+spread


def verified_volume(bars):
    return bool(bars) and all(b.volume_verified and b.quote_volume is not None and b.volume>0 for b in bars)


def data_ready(rules,candles):
    if isinstance(rules,(VwapRules,SqueezeRules,MomentumRules)) and not verified_volume(candles):
        return False,'requires continuous verified base/quote trading volume'
    if isinstance(rules,MomentumRules) and any(b.liquidity_usd is None for b in candles):
        return False,'requires time-aligned historical liquidity'
    return True,None


def session_vwap(bars):
    if not verified_volume(bars):raise ValueError('Verified trade volume required for VWAP')
    total=sum(b.volume for b in bars)
    return sum(b.quote_volume for b in bars)/total


def _touches(values,level,tolerance):
    indexes=[i for i,value in enumerate(values) if abs(value-level)<=tolerance]
    return len(indexes)>=2 and indexes[-1]-indexes[0]>=3


def decide(candles,now,price,position,rules,identity):
    def signal(action,reason,stop=0,target=0):return Signal(action,reason,stop,target,True,identity)
    if not math.isfinite(now) or not math.isfinite(price) or price<=0:raise ValueError('Invalid clock/quote')
    if position and price<=position.stop_price:return signal('sell','ATR stop reached')
    if position and position.target_price is not None and price>=position.target_price:return signal('sell','take-profit reached')
    validate(candles,now)
    warmup=51 if isinstance(rules,VwapRules) else 49 if isinstance(rules,RangeRules) else 41 if isinstance(rules,BollingerRules) else 22
    if len(candles)<warmup:return signal('hold',f'need {warmup} completed hourly candles')
    closes=[b.close for b in candles]
    atr=wilder_atr(candles,rules.atr_bars)
    if atr<=0:return signal('hold','zero ATR; no entry')
    stop,target=2*atr,4*atr
    entry=False
    if isinstance(rules,BollingerRules):
        lower,middle,upper=bands(closes,rules.period,rules.deviations)
        previous_lower,_,_=bands(closes[:-1],rules.period,rules.deviations)
        if position:return signal('sell' if price>=middle else 'hold','Bollinger mean exit' if price>=middle else 'manage Bollinger position')
        old_mean=statistics.mean(closes[-2*rules.period:-rules.period])
        sideways=abs(middle/old_mean-1)<=.01
        entry=sideways and candles[-2].low<=previous_lower and closes[-1]>lower and closes[-1]>closes[-2]
        target=middle-price
        reason='Bollinger lower-band recovery in range' if entry else 'waiting for sideways lower-band recovery'
    elif isinstance(rules,RangeRules):
        prior=candles[-rules.period-1:-1]
        support=min(b.low for b in prior);resistance=max(b.high for b in prior)
        stable=(_touches([b.low for b in prior],support,.5*atr) and
                _touches([b.high for b in prior],resistance,.5*atr))
        ceiling=resistance-.25*atr
        if position:return signal('sell' if price>=ceiling else 'hold','range resistance exit' if price>=ceiling else 'manage range position')
        entry=(stable and resistance-support>=4*atr and candles[-2].low<=support+.5*atr and
               closes[-1]>closes[-2] and support<=price<=support+.25*(resistance-support) and
               candles[-1].low>=support-.5*atr and candles[-1].high<=resistance+.5*atr)
        target=ceiling-price
        reason='validated support recovery' if entry else 'waiting for validated support range'
    elif isinstance(rules,VwapRules):
        day=candles[-1].timestamp//86400
        session=[b for b in candles if b.timestamp//86400==day]
        if len(session)<3 or session[0].timestamp%86400 or not verified_volume(session):
            return signal('hold','full UTC-session verified volume required for VWAP')
        vwap=session_vwap(session);previous_vwap=session_vwap(session[:-1])
        trend=ema(closes,rules.trend_bars)
        if position:return signal('sell' if price<vwap-.5*atr else 'hold','VWAP trend lost' if price<vwap-.5*atr else 'manage VWAP position')
        entry=(trend>ema(closes[:-1],rules.trend_bars) and price>trend and vwap>=previous_vwap and
               candles[-1].low<=vwap*1.002 and closes[-2]<=previous_vwap*1.002 and
               closes[-1]>vwap and closes[-1]>closes[-2])
        reason='VWAP pullback recovery in trend' if entry else 'waiting for VWAP trend pullback'
    else:
        if position:return signal('hold','manage volatility/momentum position')
        sample=candles[-rules.period-1:-1]
        if not verified_volume([*sample,candles[-1]]):return signal('hold','verified trading volume required')
        relative=candles[-1].volume/statistics.mean(b.volume for b in sample)
        if isinstance(rules,SqueezeRules):
            lower,_,upper=bands(closes[:-1],rules.period,rules.deviations)
            previous_atr=wilder_atr(candles[:-1],rules.atr_bars)
            center=ema(closes[:-1],rules.period)
            compressed=lower>center-rules.keltner_atr*previous_atr and upper<center+rules.keltner_atr*previous_atr
            entry=(compressed and closes[-1]>upper and atr>previous_atr and relative>=rules.relative_volume)
            reason='volume-confirmed volatility squeeze release' if entry else 'waiting for squeeze release'
        else:
            liquidity=candles[-1].liquidity_usd
            if liquidity is None or liquidity<rules.min_liquidity_usd:return signal('hold','verified liquidity threshold required')
            momentum=closes[-1]/closes[-rules.momentum_bars-1]-1
            entry=momentum>=rules.minimum_momentum and closes[-1]>max(b.high for b in sample) and relative>=rules.relative_volume
            reason='liquid volume-confirmed momentum' if entry else 'waiting for volume-confirmed momentum'
    age=now-(candles[-1].timestamp+3600)
    if age>300:return signal('hold','outside five-minute hourly entry window' if age<3600 else 'stale candle feed; block entries')
    if abs(price/closes[-1]-1)>.005:return signal('hold','quote moved too far from signal')
    if not entry or target<=0:return signal('hold',reason)
    return signal('buy',reason,stop,target)
