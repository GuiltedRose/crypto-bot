"""Deterministic hourly spot strategy shared by live adapters and research.

Consumes completed, contiguous hourly candles; never signs or submits orders.
All prices and cost estimates must be USD per base token / USD respectively.
"""
from dataclasses import dataclass
import math
from bot import Risk, Universe, size


@dataclass(frozen=True)
class Candle:
    timestamp: int  # UTC candle-open Unix seconds
    open: float
    high: float
    low: float
    close: float
    volume: float  # units depend on provenance; verified feeds use base tokens
    quote_volume: float | None = None
    volume_verified: bool = False
    liquidity_usd: float | None = None


@dataclass(frozen=True)
class Position:
    entry_price: float
    stop_price: float
    target_price: float | None
    opened_at: int  # actual fill timestamp
    strategy_id: str | None = None


@dataclass(frozen=True)
class Signal:
    action: str
    reason: str
    stop_distance: float = 0.0
    target_distance: float = 0.0
    take_profit: bool = True
    strategy_id: str = 'legacy-pullback-v1'


@dataclass(frozen=True)
class EntryRules:
    require_slow_trend: bool = False
    pullback_bars: int = 8
    require_prior_high_break: bool = False
    volume_ratio: float = 0.8

    def __post_init__(self):
        if not 1 <= self.pullback_bars <= 50 or not math.isfinite(self.volume_ratio) or self.volume_ratio < 0:
            raise ValueError('Invalid entry rules')


BASELINE_RULES = EntryRules(True, 3, True, 1.0)


def ema(values, period):
    result = values[0]
    alpha = 2 / (period + 1)
    for value in values[1:]:
        result += alpha * (value-result)
    return result


def validate(candles, now):
    if not math.isfinite(now):
        raise ValueError('Invalid clock')
    for i, bar in enumerate(candles):
        values = (bar.open, bar.high, bar.low, bar.close, bar.volume)
        if (not math.isfinite(bar.timestamp) or bar.timestamp % 3600 or
                any(not math.isfinite(v) for v in values) or
                min(values[:4]) <= 0 or bar.volume < 0 or
                bar.low > min(bar.open, bar.close) or
                bar.high < max(bar.open, bar.close)):
            raise ValueError('Invalid hourly OHLCV candle')
        if (type(bar.volume_verified) is not bool or
            (bar.quote_volume is not None and (not math.isfinite(bar.quote_volume) or bar.quote_volume<0)) or
            (bar.volume_verified and bar.quote_volume is None) or
            (bar.liquidity_usd is not None and (not math.isfinite(bar.liquidity_usd) or bar.liquidity_usd<0))):
            raise ValueError('Invalid volume/liquidity provenance')
        if i and bar.timestamp-candles[i-1].timestamp != 3600:
            raise ValueError('Candle history must be contiguous and ascending')
        if bar.timestamp+3600 > now:
            raise ValueError('Incomplete candle cannot generate a signal')


def decide_pullback(candles, now, quote_price, position=None, rules=EntryRules()):
    """Fixed candidate rules, not a claim of profitability.

    Live caller must supply a fresh executable quote. Exits are evaluated
    before candle freshness/entry gates, so stale feeds do not disable stops.
    """
    if not math.isfinite(quote_price) or quote_price <= 0:
        raise ValueError('Invalid executable quote')
    if position:
        if quote_price <= position.stop_price:
            return Signal('sell', 'stop reached')
        if position.target_price is not None and quote_price >= position.target_price:
            return Signal('sell', 'target reached')
        if now-position.opened_at >= 72*3600:
            return Signal('sell', '72-hour time exit')
    validate(candles, now)
    if len(candles) < 250:
        return Signal('hold', 'need 250 completed hourly candles')
    if now-(candles[-1].timestamp+3600) > 300:
        return Signal('hold', 'stale candles; block entries')
    closes = [b.close for b in candles]
    fast, slow = ema(closes, 50), ema(closes, 200)
    if position:
        return Signal('sell', 'trend failed') if closes[-1] < fast else Signal('hold', 'manage position')
    prior = candles[:-1]
    previous_fast = ema(closes[:-6], 50)
    if not (closes[-1] > fast and fast > previous_fast and
            (not rules.require_slow_trend or fast > slow)):
        return Signal('hold', 'no rising trend')
    pullback = any(b.low <= ema(closes[:i+1], 20) for i, b in enumerate(candles)
                   if len(candles)-rules.pullback_bars-1 <= i < len(candles)-1)
    if not pullback:
        return Signal('hold', 'waiting for pullback')
    recovery_level = prior[-1].high if rules.require_prior_high_break else max(prior[-1].close, ema(closes,20))
    if closes[-1] <= recovery_level or (not rules.require_prior_high_break and closes[-1] <= candles[-1].open):
        return Signal('hold', 'waiting for recovery')
    mean_volume = sum(b.volume for b in candles[-21:-1])/20
    if mean_volume <= 0 or candles[-1].volume < mean_volume*rules.volume_ratio:
        return Signal('hold', 'recovery volume too low')
    tr = [max(candles[i].high-candles[i].low,
              abs(candles[i].high-candles[i-1].close),
              abs(candles[i].low-candles[i-1].close)) for i in range(len(candles)-14, len(candles))]
    # Explicit SMA ATR variant, rather than Wilder's smoothing.
    atr = sum(tr)/14
    if not 0.002 <= atr/closes[-1] <= 0.04:
        return Signal('hold', 'volatility outside allowed range')
    if abs(quote_price/closes[-1]-1) > 0.005:
        return Signal('hold', 'quote moved too far from signal')
    return Signal('buy', 'uptrend pullback recovery', 2*atr, 4*atr)


@dataclass(frozen=True)
class DonchianRules:
    entry_bars: int = 20
    exit_bars: int = 10
    atr_bars: int = 20
    stop_atr: float = 2.0
    take_profit_r: float = 2.0

    def __post_init__(self):
        if (not all(isinstance(v,int) and v>1 for v in (self.entry_bars,self.exit_bars,self.atr_bars))
                or self.exit_bars>=self.entry_bars or not math.isfinite(self.stop_atr) or self.stop_atr<=0
                or not math.isfinite(self.take_profit_r) or self.take_profit_r<=0):
            raise ValueError('Invalid Donchian parameters')


DONCHIAN_ID = 'donchian-20-10-atr20-hourly-long-tp2r-cost1p5-v3'
WARMUP_BARS = 21


def wilder_atr(candles,period=20):
    if len(candles)<period+1:
        raise ValueError('Insufficient ATR history')
    ranges=[max(b.high-b.low,abs(b.high-a.close),abs(b.low-a.close))
            for a,b in zip(candles,candles[1:])]
    result=sum(ranges[:period])/period
    for value in ranges[period:]:
        result=(result*(period-1)+value)/period
    return result


def decide(candles,now,quote_price,position=None,rules=None):
    """Donchian channel trend following; legacy rules require explicit opt-in.

    20/10 Donchian channels with a 2-ATR stop and requested 2R target. This
    adaptation uses hourly, close-confirmed, long-only spot entries, no shorts,
    no pyramiding and separate Kelly/capital controls. Not the full Turtle
    system or a claim of crypto profitability. Adds a 2R take-profit exit;
    there is no fixed time exit.
    """
    if isinstance(rules,EntryRules):
        return decide_pullback(candles,now,quote_price,position,rules)
    if rules is None and position:
        from adaptive import rules_for
        rules=rules_for(position.strategy_id)
        if rules is None and (position.strategy_id or '').startswith('donchian-55-20-'):
            rules=DonchianRules(entry_bars=55,exit_bars=20)
    rules=rules or DonchianRules()
    if not isinstance(rules,DonchianRules):
        from strategies import decide as registered_decide
        return registered_decide(candles,now,quote_price,position,rules)
    from adaptive import PROFILES
    strategy_id=next((key for key,value in PROFILES.items() if value==rules),DONCHIAN_ID)
    if position and position.strategy_id:
        strategy_id=position.strategy_id
    if not math.isfinite(now) or not math.isfinite(quote_price) or quote_price<=0:
        raise ValueError('Invalid clock/quote')
    # Use explicit strategy identity: a Donchian profit target must not route
    # positions through the old EMA/time-exit logic.
    if (position and position.target_price is not None and
            not (position.strategy_id or '').startswith('donchian-')):
        return decide_pullback(candles,now,quote_price,position)
    if position and quote_price<=position.stop_price:
        return Signal('sell','ATR stop reached',strategy_id=strategy_id)
    if position and position.target_price is not None and quote_price>=position.target_price:
        return Signal('sell','take-profit reached',strategy_id=strategy_id)
    validate(candles,now)
    warmup=max(rules.entry_bars,rules.exit_bars,rules.atr_bars)+1
    if len(candles)<warmup:
        return Signal('hold',f'need {warmup} completed hourly candles',strategy_id=strategy_id)
    if position:
        exit_level=min(b.low for b in candles[-rules.exit_bars-1:-1])
        return Signal('sell' if quote_price<exit_level else 'hold',
                      f'{rules.exit_bars}-bar channel exit' if quote_price<exit_level else 'manage Donchian position',strategy_id=strategy_id)
    age=now-(candles[-1].timestamp+3600)
    if age>300:
        reason='outside five-minute hourly entry window' if age<3600 else 'stale candle feed; block entries'
        return Signal('hold',reason,strategy_id=strategy_id)
    if abs(quote_price/candles[-1].close-1)>.005:
        return Signal('hold','quote moved too far from signal',strategy_id=strategy_id)
    channel=max(b.high for b in candles[-rules.entry_bars-1:-1])
    if candles[-1].close<=channel:
        return Signal('hold',f'waiting for {rules.entry_bars}-bar breakout',strategy_id=strategy_id)
    atr=wilder_atr(candles,rules.atr_bars)
    if atr<=0:
        return Signal('hold','zero ATR; no entry',strategy_id=strategy_id)
    stop_distance=rules.stop_atr*atr
    return Signal('buy',f'{rules.entry_bars}-bar Donchian breakout',stop_distance,
                  rules.take_profit_r*stop_distance,True,strategy_id)


def entry_budget(mint, equity, history, signal, price, round_trip_cost_usd,
                 open_exposure_usd=0, existing_token_usd=0, active_mints=(),
                 risk=Risk(), universe=Universe(),max_open_positions=1,open_position_count=None):
    """Include pending buys in exposure. Caller atomically reserves result.

    Stops limit intended loss to 0.5% equity; full-loss allocation remains 5%.
    Cost estimate must include entry AND exit at the candidate allocation.
    """
    values = (equity, price, round_trip_cost_usd, open_exposure_usd, existing_token_usd)
    if any(not math.isfinite(v) for v in values) or price <= 0 or min(values[2:]) < 0:
        raise ValueError('Invalid entry budget inputs')
    if signal.action != 'buy':
        return 0.0, 'no entry signal'
    if type(max_open_positions) is not int or not 1<=max_open_positions<=2:
        raise ValueError('Position capacity must be one or two')
    count=open_position_count if open_position_count is not None else len(active_mints)
    if type(count) is not int or count<0:
        raise ValueError('Invalid open position count')
    if not count and (active_mints or open_exposure_usd>0 or existing_token_usd>0):
        count=1
    if count>=max_open_positions:
        return 0.0, 'one active trade limit; close and reconcile before another entry' if max_open_positions==1 else 'two active position limit'
    if not all(math.isfinite(v) and v > 0 for v in (signal.stop_distance, signal.target_distance)):
        raise ValueError('Invalid stop or target distance')
    allowed, reason = universe.eligible(mint, equity, active_mints)
    if not allowed:
        return 0.0, reason
    if any(not math.isfinite(r) or r < -1 for r in history):
        raise ValueError('Invalid strategy history')
    if len(history) < risk.min_samples:
        # Explicit exploratory allocation, NOT a Kelly estimate or proven edge.
        amount = max(0, min(equity*.01, equity-risk.reserve_usd))
        reason = 'startup allocation capped at 1%; Kelly awaits completed observations'
    else:
        amount, reason = size(equity, history, risk)
    initial_amount = amount
    amount = max(0, min(amount, equity*0.10-open_exposure_usd,
                        equity*risk.position_cap-existing_token_usd,
                        equity-risk.reserve_usd-open_exposure_usd,
                        equity*0.005/(signal.stop_distance/price)))
    if not amount:
        return 0.0, reason if not initial_amount else 'portfolio exposure limit'
    # Target distance is not an expected return: this only rejects expensive setups.
    if amount*signal.target_distance/price < risk.min_target_cost_multiple*round_trip_cost_usd:
        return 0.0, f'target move cannot cover {risk.min_target_cost_multiple:g} times estimated costs'
    return amount, reason if len(history) < risk.min_samples else 'quarter Kelly with position, portfolio and stop-risk caps'
