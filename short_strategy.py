"""Mirrored Donchian short signals; execution requires a separate eligible venue.

No wallet access, borrowing, leverage, liquidation or derivative execution.
PumpSwap spot must never interpret a short entry as selling owned inventory.
"""
from dataclasses import dataclass
import math
from strategy import DonchianRules,Signal,validate,wilder_atr

SHORT_STRATEGY_ID='donchian-20-10-atr20-hourly-short-tp2r-research-v1'


@dataclass(frozen=True)
class ShortPosition:
    entry_price: float
    stop_price: float
    target_price: float
    opened_at: int

    def __post_init__(self):
        if (not all(math.isfinite(v) for v in (self.entry_price,self.stop_price,self.target_price,self.opened_at)) or
                not 0<self.target_price<self.entry_price<self.stop_price or self.opened_at<0):
            raise ValueError('Invalid short entry/stop/target')

    @classmethod
    def from_fill(cls,entry_price,opened_at,signal):
        if signal.action!='short':
            raise ValueError('Short entry signal required')
        return cls(entry_price,entry_price+signal.stop_distance,
                   entry_price-signal.target_distance,opened_at)


def decide_short(candles,now,quote_price,position=None,rules=None):
    """20-bar lower breakout entry, 10-bar upper exit, 2 ATR stop and 2R TP.

    Entries use completed hourly closes and the same freshness/quote gates as
    longs. An existing short is covered on a rise to its stop or a drop to its
    profit target, even if candles are unavailable. Channel exits need history.
    """
    rules=rules or DonchianRules()
    if not math.isfinite(now) or not math.isfinite(quote_price) or quote_price<=0:
        raise ValueError('Invalid clock/quote')
    def signal(action,reason,stop=0,target=0):
        return Signal(action,reason,stop,target,True,SHORT_STRATEGY_ID)
    if position:
        if quote_price>=position.stop_price:
            return signal('cover','short ATR stop reached')
        if quote_price<=position.target_price:
            return signal('cover','short take-profit reached')
    validate(candles,now)
    warmup=max(rules.entry_bars,rules.exit_bars,rules.atr_bars)+1
    if len(candles)<warmup:
        return signal('hold',f'need {warmup} completed hourly candles')
    if position:
        upper=max(b.high for b in candles[-rules.exit_bars-1:-1])
        return signal('cover' if quote_price>upper else 'hold',
                      f'{rules.exit_bars}-bar short channel exit' if quote_price>upper else 'manage short position')
    age=now-(candles[-1].timestamp+3600)
    if age>300:
        return signal('hold','outside five-minute hourly entry window' if age<3600 else 'stale candle feed; block short entries')
    if abs(quote_price/candles[-1].close-1)>.005:
        return signal('hold','quote moved too far from short signal')
    lower=min(b.low for b in candles[-rules.entry_bars-1:-1])
    if candles[-1].close>=lower:
        return signal('hold',f'waiting for {rules.entry_bars}-bar lower breakout')
    atr=wilder_atr(candles,rules.atr_bars)
    stop=rules.stop_atr*atr
    target=rules.take_profit_r*stop
    if atr<=0 or quote_price-target<=0:
        return signal('hold','invalid short volatility/target range')
    return signal('short',f'{rules.entry_bars}-bar Donchian lower breakout',stop,target)
