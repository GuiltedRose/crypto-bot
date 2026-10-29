"""Research-only ATH dip strategy and paper fills. No live execution imports.

Input snapshots must come from independently validated collectors. The input
contract is deliberately strict: unknown ATH, buyers or safety means no entry.
Paper fills are estimates at sampled quotes, never evidence of executability.
"""
from dataclasses import dataclass, asdict
from urllib.parse import urlparse
from meme_evidence import MemeEvidencePolicy, number

SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


@dataclass(frozen=True)
class DipRules:
    evidence_mode: str = 'strict'
    entry_style: str = 'ath_dip'
    entry_profile_mode: str = 'fixed'
    trend_min_bars: int = 8
    trend_min_gain_fraction: float = .05
    trend_pullback_min_fraction: float = .02
    trend_pullback_max_fraction: float = .15
    social_min_views: int = 100
    max_liquidity_allocation_fraction: float = .0005
    max_holding_seconds: int = 900
    min_dip_fraction: float = .35
    max_dip_fraction: float = .50
    rebound_fraction: float = .05
    min_buy_volume_usd: float = 5000
    buy_window_seconds: int = 3600
    min_liquidity_usd: float = 50000
    min_rising_close_fraction: float = .6
    max_candle_range_fraction: float = .30
    max_wicky_bar_fraction: float = .34
    stop_fraction: float = .15
    target_r: float = 2
    take_profit_usd: float = 0
    max_loss_usd: float = 0
    reserve_usd: float = 1
    native_fee_reserve_sol: float = .005
    allocation_cap: float = .01
    max_position_usd: float = 0
    intended_risk_fraction: float = .0025
    drawdown_halt: float = .05
    drawdown_promotion_bankroll_usd: float = 0
    promoted_drawdown_halt: float = .30
    drawdown_bankroll_basis: str = 'bot'
    fee_per_side: float = .003
    slippage_per_side: float = .005

    def __post_init__(self):
        if self.drawdown_bankroll_basis not in ('bot','total_for_demotion'): raise ValueError('Unknown bankroll promotion basis')
        if self.evidence_mode not in ('strict','market_only'):
            raise ValueError('Unknown paper evidence mode')
        if type(self.trend_min_bars) is not int or not (2 if self.entry_style=='volume_candles' else 3)<=self.trend_min_bars<=12:
            raise ValueError('Trend history minimum must be 3 through 12 completed candles')
        if self.entry_profile_mode not in ('fixed','adaptive') or self.entry_profile_mode=='adaptive' and self.entry_style not in ('trend_pullback','volume_candles'):
            raise ValueError('Adaptive entry profiles require the trend/pullback strategy')
        if self.entry_style not in ('ath_dip','trend_pullback','volume_candles'):
            raise ValueError('Unknown paper entry style')
        if not 0 < self.trend_pullback_min_fraction < self.trend_pullback_max_fraction < 1 or self.max_holding_seconds<=0 or not 0<self.max_liquidity_allocation_fraction<1:
            raise ValueError('Invalid trend entry settings')
        for key,value in asdict(self).items():
            if key not in ('evidence_mode','entry_style','entry_profile_mode','drawdown_bankroll_basis'):
                number(value, 0)
        for value in (self.min_dip_fraction, self.max_dip_fraction, self.stop_fraction,
                      self.allocation_cap, self.intended_risk_fraction, self.drawdown_halt):
            if not 0 < value < 1:
                raise ValueError('Invalid fractional rule')
        if not 0 < self.min_rising_close_fraction <= 1 or not 0 < self.max_candle_range_fraction < 1 or not 0 <= self.max_wicky_bar_fraction <= 1:
            raise ValueError('Invalid candle-quality settings')
        if not 0<self.promoted_drawdown_halt<1 or self.drawdown_promotion_bankroll_usd>0 and self.promoted_drawdown_halt<self.drawdown_halt:
            raise ValueError('Invalid promoted drawdown schedule')
        if self.min_dip_fraction >= self.max_dip_fraction:
            raise ValueError('Pullback zone must have increasing bounds')
        if not 0 <= self.rebound_fraction < 1:
            raise ValueError('Rebound must be zero (disabled) or less than one')
        if self.fee_per_side + self.slippage_per_side >= 1 or self.target_r <= 0:
            raise ValueError('Invalid costs or reward multiple')


def reference_values(snapshot):
    if snapshot.get('ath_source_kind')=='pump_provider':
        return (number(snapshot['ath_market_cap_usd'],1e-15),number(snapshot['market_cap_usd'],1e-15))
    return (number(snapshot['ath_price_usd'],1e-15),number(snapshot['price_usd'],1e-15))


def entry_screen(snapshot, now, rules):
    if rules.entry_style=='volume_candles':
        from volume_candles import assess_volume_candles
        return assess_volume_candles(snapshot,now,rules)['reasons']
    from candle_quality import CandleQualityPolicy
    if rules.entry_style=='trend_pullback':
        from trend_pullback import assess_trend_pullback
        chart=assess_trend_pullback(snapshot,now,rules)
    else:
        chart = CandleQualityPolicy(min_rising_close_fraction=rules.min_rising_close_fraction,
                                   max_bar_range_fraction=rules.max_candle_range_fraction,
                                   max_wicky_bar_fraction=rules.max_wicky_bar_fraction).assess(snapshot,now)
    reasons = list(chart.get('reasons',[]))
    try:
        if rules.entry_style=='ath_dip':
            ath,current = reference_values(snapshot)
            pump_reference=snapshot.get('ath_source_kind')=='pump_provider'
            if not pump_reference and snapshot['ath_history_complete_since_launch'] is not True:
                reasons.append('launch-to-present ATH history required')
            if not 0 <= now-number(snapshot['ath_checked_at']) <= 300:
                reasons.append('stale ATH history')
            source = urlparse(snapshot['ath_source_url'])
            if source.scheme != 'https' or not source.hostname:
                reasons.append('ATH provenance required')
            if pump_reference and (source.netloc!='swap-api.pump.fun' or source.path!='/v1/coins/'+snapshot['mint']+'/ath'):
                reasons.append('Pump ATH source does not match mint')
            if ath < current:
                reasons.append('current value exceeds reported ATH')
        if number(snapshot['buy_volume_window_seconds']) != rules.buy_window_seconds:
            reasons.append('wrong buy volume window')
        if number(snapshot['buy_volume_coverage_pct'], 0, 100) < 95:
            reasons.append('incomplete buy volume coverage')
        if number(snapshot['buy_volume_usd']) < rules.min_buy_volume_usd:
            reasons.append('recent buy volume below threshold')
        liquidity=number(snapshot['liquidity_usd'],1e-15)
        if rules.entry_style=='ath_dip' and liquidity < rules.min_liquidity_usd:
            reasons.append('insufficient liquidity')
        number(snapshot['fixed_cost_per_side_usd'])
    except (KeyError, ValueError, TypeError, OverflowError):
        reasons.append('required market data missing/invalid')
    if rules.evidence_mode=='strict':
        try:
            if snapshot['token_safety_passed'] is not True:
                reasons.append('independent token safety screen required')
            if not 0 <= now-number(snapshot['safety_checked_at']) <= 300:
                reasons.append('stale token safety screen')
        except (KeyError,ValueError,TypeError):
            reasons.append('token safety evidence missing/invalid')
        evidence=MemeEvidencePolicy(require_social_corroboration=False).assess(snapshot.get('mint'),snapshot.get('pool'),snapshot.get('evidence',{}),now)
        reasons.extend(evidence['reasons'])
    return reasons


class PaperDipTrader:
    """One simulated position, global drawdown halt, exit-first processing.

    Feed snapshots chronologically. Use a separate state from every live bot.
    Equity is marked on sampled quotes; gaps can lose more than the stop budget.
    """
    def __init__(self, starting_usd=100, rules=None, native_sol=None, sol_price_usd=None):
        number(starting_usd, 0)
        self.rules = rules or DipRules()
        self.cash = starting_usd
        self.native_sol = None if native_sol is None else number(native_sol)
        self.sol_price_usd = None if native_sol is None else number(sol_price_usd, .000000001)
        self.peak = starting_usd + (self.native_sol or 0)*(self.sol_price_usd or 0)
        if self.peak < .01:
            raise ValueError('Starting total equity must be at least one cent')
        self.strategy_capital_usd=self.peak
        self.strategy_peak_pnl_usd=0.
        self.strategy_realized_pnl_usd=0.
        self.strategy_risk={}
        self.position = None
        self.armed = {}
        self.last_seen = {}
        self.last_entry_bars = {}
        self.account_rent = {}
        self.halted = False
        self.last_timestamp = -1

    def equity(self,token_value=0):
        return self.cash+token_value+((self.native_sol or 0)+sum(self.account_rent.values()))*(self.sol_price_usd or 0)

    def mark_risk(self,equity,open_pnl=0.):
        if self.rules.entry_style=='volume_candles':
            pnl=self.strategy_realized_pnl_usd+open_pnl
            self.strategy_peak_pnl_usd=max(self.strategy_peak_pnl_usd,pnl)
            from drawdown_schedule import drawdown_state
            self.strategy_risk=drawdown_state(self.strategy_capital_usd,pnl,self.strategy_peak_pnl_usd,self.rules.drawdown_halt,
                self.rules.drawdown_promotion_bankroll_usd,self.rules.promoted_drawdown_halt,
                total_bankroll=equity if self.rules.drawdown_bankroll_basis=='total_for_demotion' else None,
                previous_tier=self.strategy_risk.get('tier','base'))
            self.halted=self.halted or (self.strategy_risk['drawdown_usd']>0 and self.strategy_risk['drawdown_usd']>=self.strategy_risk['limit_usd'])
        else:
            self.peak=max(self.peak,equity)
            self.halted=self.halted or equity<=self.peak*(1-self.rules.drawdown_halt)

    def reclaim_rent(self):
        if self.native_sol is None:
            return {'action':'paper_rent_reclaim','returned_sol':0,'pnl_usd':0}
        active=None if self.position is None else (self.position['mint'],self.position.get('token_program','standard_spl_assumed'))
        keys=[key for key,value in self.account_rent.items() if key!=active and value>0]
        returned=sum(self.account_rent.pop(key) for key in keys)
        self.native_sol+=returned
        return {'action':'paper_rent_reclaim','returned_sol':returned,'pnl_usd':0,'accounts_closed':len(keys)}

    def on_snapshot(self, snapshot):
        mint, pool = snapshot['mint'], snapshot['pool']
        if not isinstance(mint, str) or not mint or not isinstance(pool, str) or not pool:
            raise ValueError('Mint and pool identity required')
        now = number(snapshot['observed_at'])
        price = number(snapshot['price_usd'], .000000000000001)
        key = (mint, pool)
        if now < self.last_timestamp or now <= self.last_seen.get(key, -1):
            raise ValueError('Duplicate or out-of-order market snapshot')
        self.last_seen[key] = now
        self.last_timestamp = now
        r = self.rules
        execution = snapshot.get('execution', {})
        trade_fee=number(execution.get('fee_per_side',r.fee_per_side),0,.5)
        if execution.get('model_only') and execution.get('quote_mint') not in (SOL_MINT,USDC_MINT,'11111111111111111111111111111111'):
            return {'action':'reject','reason':'unsupported quote asset in paper execution model'}
        if self.native_sol is not None:
            try:
                self.sol_price_usd = number(execution['sol_price_usd'], .000000001)
                if not 0 <= now-number(execution['observed_at']) <= 30:
                    raise ValueError('Stale execution estimates')
                if execution['mint'] != mint or execution['pool'] != pool:
                    raise ValueError('Execution identity mismatch')
                network = number(execution['network_fee_sol'])
                entry_rent = number(execution['entry_rent_sol'])
                exit_rent = number(execution['exit_rent_sol'])
                rent_key=(mint,snapshot.get('token_program','standard_spl_assumed'))
                refundable=execution.get('rent_is_refundable') is True
                if refundable and rent_key in self.account_rent:
                    entry_rent=0
                if execution['sell_route_available'] is not True:
                    raise ValueError('Sell route unavailable')
            except (KeyError, TypeError, ValueError):
                self.armed.pop(key, None)
                return {'action': 'reject', 'reason': 'fresh identity-bound execution estimates and sell route required'}
        source_quote=snapshot.get('quote_mint')
        execution_quote=execution.get('quote_mint')
        normalize_quote=lambda value:SOL_MINT if value=='11111111111111111111111111111111' else value
        if source_quote is not None and execution_quote is not None and normalize_quote(source_quote)!=normalize_quote(execution_quote):
            return {'action':'reject','reason':'execution quote asset mismatch'}
        quote_mint=execution.get('quote_mint',snapshot.get('quote_mint',USDC_MINT))
        if quote_mint=='11111111111111111111111111111111':
            quote_mint=SOL_MINT
        if quote_mint not in (SOL_MINT,USDC_MINT) or quote_mint==SOL_MINT and self.native_sol is None:
            return {'action':'reject','reason':'supported quote inventory required'}
        position = self.position
        if position:
            if key != (position['mint'], position['pool']):
                return {'action': 'hold', 'reason': 'one paper position already open'}
            if position.get('quote_mint',USDC_MINT)!=quote_mint:
                return {'action':'hold','reason':'paper exit quote asset changed; reconcile route'}
            equity = self.equity(position['quantity']*price)
            if r.entry_style!='volume_candles': self.mark_risk(equity)
            time_exit=position.get('entry_style') in ('trend_pullback','volume_candles') and now-position['opened_at']>=r.max_holding_seconds
            proceeds=max(0,position['quantity']*price*(1-r.slippage_per_side)*(1-trade_fee)-position['exit_fixed_cost'])
            projected_pnl=proceeds-position['cash_spent']
            if self.native_sol is not None:
                projected_pnl-=position['entry_native_cost_usd']+(network+(0 if position.get('rent_is_refundable') else exit_rent))*self.sol_price_usd
            if r.entry_style=='volume_candles': self.mark_risk(equity,projected_pnl)
            loss_limit=position.get('max_loss_usd',0)
            profit_target=position.get('take_profit_usd',0)
            stop_hit=projected_pnl<=-loss_limit if loss_limit>0 else price<=position['stop']
            target_hit=projected_pnl>=profit_target if profit_target>0 else price>=position['target']
            if stop_hit or target_hit or self.halted or time_exit:
                # Use current quote for gaps, never fill at an unavailable stop.
                if self.native_sol is not None:
                    if self.native_sol < network+exit_rent:
                        return {'action': 'hold', 'reason': 'paper exit blocked: insufficient native SOL'}
                    self.native_sol -= network+exit_rent
                    if position.get('rent_is_refundable') and exit_rent:
                        self.account_rent[rent_key]=self.account_rent.get(rent_key,0)+exit_rent
                if quote_mint==SOL_MINT:
                    self.native_sol += proceeds/self.sol_price_usd
                else:
                    self.cash += proceeds
                pnl = proceeds-position['cash_spent']
                if self.native_sol is not None:
                    pnl -= position['entry_native_cost_usd']+(network+(0 if position.get('rent_is_refundable') else exit_rent))*self.sol_price_usd
                self.position = None
                self.armed.pop(key, None)
                self.strategy_realized_pnl_usd+=pnl
                self.mark_risk(self.equity())
                return {'action': 'paper_sell', 'mint': mint, 'pnl_usd': pnl, 'cash_usd': self.cash,'native_sol':self.native_sol,
                        'reason': 'drawdown halt' if self.halted else 'stop' if stop_hit else 'time limit' if time_exit and not target_hit else 'target'}
            return {'action': 'hold', 'reason': 'manage paper position'}
        if self.halted:
            return {'action': 'reject', 'reason': 'paper drawdown halt'}
        profile_name=None
        if r.entry_profile_mode=='adaptive':
            from meme_profiles import assess_profiles,profiles
            selection=assess_profiles(snapshot,now,r)
            profile_name=selection['selected_profile']
            if profile_name is None:
                return {'action':selection['action'],'reasons':selection['reasons'],'profile_selection':selection}
            r=profiles(r)[profile_name]
        reasons = entry_screen(snapshot, now, r)
        if reasons:
            # A recovery must be observed again after an evidence outage.
            self.armed.pop(key, None)
            return {'action': 'reject', 'reasons': reasons}
        if r.entry_style in ('trend_pullback','volume_candles'):
            if r.entry_style=='volume_candles':
                from volume_candles import assess_volume_candles
                setup=assess_volume_candles(snapshot,now,r)
            else:
                from trend_pullback import assess_trend_pullback
                setup=assess_trend_pullback(snapshot,now,r)
            if setup['action']!='buy':
                return setup
            signal_bar=snapshot['candle_history']['candles'][-1]['timestamp']
            if self.last_entry_bars.get(key)==signal_bar:
                return {'action':'watch','reason':'already entered on this completed candle'}
            ath,reference_price=setup['metrics'].get('peak_price',price),price
        else:
            ath,reference_price = reference_values(snapshot)
            armed = self.armed.get(key)
            if armed and armed['ath'] != ath:
                self.armed.pop(key)
                armed = None
            lower_price = ath*(1-r.max_dip_fraction)
            upper_price = ath*(1-r.min_dip_fraction)
            # The range is an entry filter, not a prediction of a bottom. Leaving
            # either side clears the setup; a later visit must qualify afresh.
            if not lower_price <= reference_price <= upper_price:
                self.armed.pop(key, None)
                if reference_price < lower_price:
                    return {'action': 'reject', 'reason': 'pullback deeper than configured entry zone'}
                return {'action': 'reject' if armed else 'watch',
                        'reason': 'price above configured pullback zone'}
            if r.rebound_fraction:
                if armed is None:
                    self.armed[key] = {'low': reference_price, 'ath': ath}
                    return {'action': 'watch', 'reason': 'pullback zone reached; awaiting rebound'}
                armed['low'] = min(armed['low'], reference_price)
                if reference_price < armed['low']*(1+r.rebound_fraction):
                    return {'action': 'watch', 'reason': 'awaiting rebound from observed dip low'}
        fixed = snapshot['fixed_cost_per_side_usd']
        equity = self.equity()
        self.mark_risk(equity)
        if self.halted:
            return {'action': 'reject', 'reason': 'paper drawdown halt'}
        if self.native_sol is not None:
            if execution.get('buy_route_available') is not True:
                return {'action': 'reject', 'reason': 'paper entry route unavailable'}
            if self.native_sol < r.native_fee_reserve_sol+network*2+entry_rent+exit_rent:
                return {'action': 'reject', 'reason': 'paper native fee reserve'}
            fixed = (network+(0 if refundable else (entry_rent+exit_rent)/2))*self.sol_price_usd
        risk_available = max(0, (r.max_loss_usd if r.max_loss_usd>0 else equity*r.intended_risk_fraction)-fixed*2)
        risk_rate = (0 if r.max_loss_usd>0 else r.stop_fraction)+2*(trade_fee+r.slippage_per_side)
        risk_rate=max(risk_rate,1e-15)
        available=self.cash-r.reserve_usd
        if quote_mint==SOL_MINT:
            available=min(equity-r.reserve_usd,(self.native_sol-r.native_fee_reserve_sol-network*2-entry_rent-exit_rent)*self.sol_price_usd)
        budget = max(0, min(available, r.max_position_usd if r.max_position_usd>0 else equity*r.allocation_cap,
                           risk_available/risk_rate))
        if r.entry_style=='trend_pullback':
            budget=min(budget,snapshot['liquidity_usd']*r.max_liquidity_allocation_fraction)
        target_gain = r.take_profit_usd if r.take_profit_usd>0 else budget*r.stop_fraction*r.target_r
        round_trip_cost = fixed*2+budget*2*(trade_fee+r.slippage_per_side)
        if budget <= fixed or target_gain < 1.5*round_trip_cost:
            return {'action': 'reject', 'reason': 'paper allocation cannot cover modeled costs'}
        fill_price = price*(1+r.slippage_per_side)
        cash_fixed = fixed if self.native_sol is None else 0
        quantity = (budget-cash_fixed)/(fill_price*(1+trade_fee))
        if self.native_sol is not None:
            self.native_sol -= network+entry_rent
            if refundable:
                self.account_rent[rent_key]=self.account_rent.get(rent_key,0)+entry_rent
        if quote_mint==SOL_MINT:
            self.native_sol -= budget/self.sol_price_usd
        else:
            self.cash -= budget
        self.position = {'mint': mint, 'pool': pool, 'entry_profile':profile_name,'quantity': quantity, 'entry_price': fill_price,
                         'stop': fill_price*(1-r.stop_fraction), 'target': fill_price*(1+r.stop_fraction*r.target_r),
                         'cash_spent': budget,'entry_fee_per_side':trade_fee,'take_profit_usd':r.take_profit_usd,'max_loss_usd':r.max_loss_usd,'entry_style':r.entry_style,'opened_at':now, 'quote_mint':quote_mint,'exit_fixed_cost': cash_fixed,
                         'rent_is_refundable':self.native_sol is not None and refundable,
                         'token_program':snapshot.get('token_program','standard_spl_assumed'),
                         'entry_native_cost_usd': 0 if self.native_sol is None else (network+(0 if refundable else entry_rent))*self.sol_price_usd}
        exit_costs=cash_fixed+self.position['entry_native_cost_usd']
        if self.native_sol is not None:
            exit_costs+=(network+(0 if refundable else exit_rent))*self.sol_price_usd
        coefficient=quantity*(1-r.slippage_per_side)*(1-trade_fee)
        if r.take_profit_usd>0:
            self.position['target']=(budget+exit_costs+r.take_profit_usd)/coefficient
        if r.max_loss_usd>0:
            self.position['stop']=max(0,(budget+exit_costs-r.max_loss_usd)/coefficient)
        self.armed.pop(key, None)
        if r.entry_style in ('trend_pullback','volume_candles'):
            self.last_entry_bars[key]=signal_bar
        return {'action': 'paper_buy', 'mint': mint, 'budget_usd': budget,
                'pullback_fraction': 1-reference_price/ath,
                'entry_style':r.entry_style,'evidence_mode':r.evidence_mode, 'safety_qualified':r.evidence_mode=='strict',
                'pullback_measure': 'recent_price_peak' if r.entry_style=='trend_pullback' else 'market_cap_usd' if snapshot.get('ath_source_kind')=='pump_provider' else 'price_usd', **self.position}
