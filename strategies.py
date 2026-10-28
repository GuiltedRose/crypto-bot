"""Spot strategy plugins. All share ATR exits and fixed external risk limits.

Established indicator families; these specific parameterizations are research
candidates, not claims of a profitable edge.
"""
import math
from dataclasses import dataclass
from strategy import (DonchianRules,DONCHIAN_ID,Signal,validate,ema,wilder_atr,
                      decide as donchian_decide)


@dataclass(frozen=True)
class TrendRules:
    fast: int = 50
    slow: int = 200
    atr_bars: int = 20
    stop_atr: float = 2
    take_profit_r: float = 2


@dataclass(frozen=True)
class ReversionRules:
    rsi_bars: int = 14
    recovery: float = 30
    exit_rsi: float = 60
    trend_bars: int = 200
    atr_bars: int = 20
    stop_atr: float = 2
    take_profit_r: float = 2


from extra_strategies import EXTRA_PROFILES

PROFILES={
    **EXTRA_PROFILES,
    DONCHIAN_ID:DonchianRules(),
    'donchian-adaptive-v1-30-10':DonchianRules(entry_bars=30,exit_bars=10),
    'donchian-adaptive-v1-55-20':DonchianRules(entry_bars=55,exit_bars=20),
    'ema-trend-v1-50-200':TrendRules(),
    'rsi-recovery-v1-14-30-60-ema200':ReversionRules(),
}


def rsi(values,period=14):
    if len(values)<period+1:raise ValueError('Insufficient RSI history')
    changes=[b-a for a,b in zip(values,values[1:])]
    gain=sum(max(v,0) for v in changes[:period])/period
    loss=sum(max(-v,0) for v in changes[:period])/period
    for value in changes[period:]:
        gain=(gain*(period-1)+max(value,0))/period
        loss=(loss*(period-1)+max(-value,0))/period
    if not loss:return 100. if gain else 50.
    return 100-100/(1+gain/loss)


def decide(candles,now,quote_price,position=None,rules=None):
    if rules is None and position:
        rules=PROFILES.get(position.strategy_id)
        if rules is None:return donchian_decide(candles,now,quote_price,position,None)
    rules=rules or DonchianRules()
    if isinstance(rules,DonchianRules):return donchian_decide(candles,now,quote_price,position,rules)
    identity=next((key for key,value in PROFILES.items() if value==rules),None)
    if identity is None:raise ValueError('Unapproved strategy plugin parameters')
    if identity in EXTRA_PROFILES:
        from extra_strategies import decide as extra_decide
        return extra_decide(candles,now,quote_price,position,rules,identity)
    if not math.isfinite(now) or not math.isfinite(quote_price) or quote_price<=0:
        raise ValueError('Invalid clock/quote')
    def signal(action,reason,stop=0,target=0):return Signal(action,reason,stop,target,True,identity)
    # Stops/targets do not depend on fresh indicator history or entry eligibility.
    if position and quote_price<=position.stop_price:return signal('sell','ATR stop reached')
    if position and position.target_price is not None and quote_price>=position.target_price:
        return signal('sell','take-profit reached')
    validate(candles,now)
    warmup=max(rules.slow if isinstance(rules,TrendRules) else rules.trend_bars,rules.atr_bars)+1
    if len(candles)<warmup:return signal('hold',f'need {warmup} completed hourly candles')
    closes=[bar.close for bar in candles]
    if isinstance(rules,TrendRules):
        fast,slow=ema(closes,rules.fast),ema(closes,rules.slow)
        if position:return signal('sell' if fast<slow else 'hold','EMA bearish cross' if fast<slow else 'manage EMA trend position')
        entry=fast>slow and ema(closes[:-1],rules.fast)<=ema(closes[:-1],rules.slow)
        reason='EMA bullish cross' if entry else 'waiting for EMA bullish cross'
    else:
        current,previous=rsi(closes,rules.rsi_bars),rsi(closes[:-1],rules.rsi_bars)
        if position:return signal('sell' if current>=rules.exit_rsi else 'hold','RSI recovery exit' if current>=rules.exit_rsi else 'manage RSI recovery position')
        entry=previous<=rules.recovery<current and closes[-1]>=ema(closes,rules.trend_bars)
        reason='RSI recovery in uptrend' if entry else 'waiting for RSI recovery in uptrend'
    age=now-(candles[-1].timestamp+3600)
    if age>300:return signal('hold','outside five-minute hourly entry window' if age<3600 else 'stale candle feed; block entries')
    if abs(quote_price/closes[-1]-1)>.005:return signal('hold','quote moved too far from signal')
    if not entry:return signal('hold',reason)
    atr=wilder_atr(candles,rules.atr_bars)
    if atr<=0:return signal('hold','zero ATR; no entry')
    stop=rules.stop_atr*atr
    return signal('buy',reason,stop,rules.take_profit_r*stop)
