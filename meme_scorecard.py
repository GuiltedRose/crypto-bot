"""Equal-family signal diagnostic. Scores are not calibrated win probabilities.

This records a challenger hypothesis without changing funded entry decisions.
Correlated inputs stay inside one family. Missing inputs stay unknown; weights
are never redistributed to make a sparse candidate look stronger.
"""
from meme_evidence import number
from trend_pullback import assess_trend_pullback,social_interest

FAMILIES=('price_structure','buying_demand','market_depth','community_interest')


def scorecard(snapshot,now,rules):
    groups={name:{'weight':.25,'score':None,'status':'unknown'} for name in FAMILIES}
    try:
        fresh=0<=now-number(snapshot['observed_at'])<=30
    except (KeyError,TypeError,ValueError): fresh=False
    if fresh:
        trend=assess_trend_pullback(snapshot,now,rules,require_social=False)
        # Uses the existing shape check so a shadow score can be compared to
        # actual reasons for rejecting/waiting, with no invented OHLC values.
        history=snapshot.get('candle_history')
        if isinstance(history,dict) and isinstance(history.get('candles'),list) and len(history['candles'])>=rules.trend_min_bars:
            from candle_quality import CandleQualityPolicy
            chart=CandleQualityPolicy(require_uptrend=False,min_bars=min(12,len(history['candles'])),
                    max_bar_range_fraction=rules.max_candle_range_fraction,max_wicky_bar_fraction=rules.max_wicky_bar_fraction).assess(snapshot,now)
            # An invalid/gapped/stale chart is unavailable evidence, rather
            # than a low but fully observed price signal.
            invalid=('missing','malformed','invalid','partial','stale','wrong candle','numeric','prices violate')
            if not any(any(tag in reason for tag in invalid) for reason in chart['reasons']):
                groups['price_structure'].update(score={'buy':1.,'watch':.5,'reject':0.}[trend['action']],status='observed',
                    detail=trend.get('reason') or trend.get('reasons',[]),metrics=trend.get('metrics',{}))
        try:
            buy=number(snapshot['buy_volume_usd']);sell=number(snapshot['sell_volume_usd'])
            if number(snapshot['buy_volume_window_seconds'])!=rules.buy_window_seconds or number(snapshot['buy_volume_coverage_pct'],0,100)<95:
                raise ValueError('Wrong or incomplete trade window')
            score=((min(1.,buy/rules.min_buy_volume_usd) if rules.min_buy_volume_usd else 1.)+(buy/(buy+sell) if buy+sell else 0))/2
            groups['buying_demand'].update(score=score,status='observed',buy_usd=buy,sell_usd=sell,
                note='Provider aggregate; not independent human or wash-volume verification')
        except (KeyError,ValueError,TypeError): pass
        try:
            liquidity=number(snapshot['liquidity_usd'],1e-15)
            full_size=rules.max_position_usd or 0
            if full_size<=0: raise ValueError('Absolute position cap required for this diagnostic')
            depth=liquidity*rules.max_liquidity_allocation_fraction/full_size
            groups['market_depth'].update(score=min(1.,depth),status='observed',liquidity_usd=liquidity,
                note='Provider liquidity supports configured size; executable impact and fees remain separate guards')
        except (KeyError,ValueError,TypeError): pass
        if isinstance(snapshot.get('research_context'),dict):
            interest=social_interest(snapshot,now,rules)
            context=snapshot['research_context']
            if interest['present'] or context.get('source_status')=='bounded_sample':
                groups['community_interest'].update(score=float(interest['present']),status='observed',metrics=interest,
                    note='Popularity evidence; no human identity claim')
    observed=sum(group['score'] is not None for group in groups.values())
    contribution=sum(group['weight']*group['score'] for group in groups.values() if group['score'] is not None)
    return {'version':'equal-family-shadow-v1','mint':snapshot.get('mint'),'pool':snapshot.get('pool'),
            'observed_at':now,'mode':'shadow_only','groups':groups,'coverage_fraction':observed/len(FAMILIES),
            'score':contribution if observed==len(FAMILIES) else None,'known_contribution':contribution,
            'unmeasured':['full_launch_bundle_coverage','human_buyer_identity','calibrated_profit_probability'],
            'automatic_entry_authorization':False}
