"""Shared entry regimes/experiments; never changes dollar risk or slippage."""
from dataclasses import replace
import json
from pathlib import Path

ALLOWED={'max_candle_range_fraction','min_buy_volume_usd','trend_pullback_max_fraction'}
DEFAULT=Path(__file__).with_name('paper_profiles.json')


def profiles(base,path=DEFAULT):
    overrides=json.loads(Path(path).read_text())
    if set(overrides)!={'current','moderate','aggressive'}: raise ValueError('Exactly current, moderate and aggressive profiles required')
    result={}
    for name,values in overrides.items():
        if not isinstance(values,dict) or set(values)-ALLOWED: raise ValueError('Comparison cannot change risk, exits or slippage')
        checked=replace(base,entry_profile_mode='fixed',**values)
        result[name]=replace(base,entry_profile_mode='fixed') if base.entry_style=='volume_candles' else checked
    return result


def selected(base,name):
    if name=='adaptive': return replace(base,entry_profile_mode='adaptive')
    return profiles(base)[name] if name else base


def assess_profiles(snapshot,now,base):
    """Trend: narrowest qualifying profile; minimal mode: volatility regime.

    Not a learned profit prediction, and never selects from future outcomes.
    """
    if base.entry_style=='volume_candles':
        from volume_candles import assess_volume_candles
        signal=assess_volume_candles(snapshot,now,base)
        span=None;chosen=None
        if signal['action']=='buy':
            candles=snapshot['candle_history']['candles'][-base.trend_min_bars:]
            span=max((row['high']-row['low'])/row['open'] for row in candles)
            chosen='current' if span<=.30 else 'moderate' if span<=.45 else 'aggressive'
        assessments={name:{'action':signal['action'],'reasons':signal['reasons'],
            'reason':signal['reason'],'metrics':signal['metrics']} for name in ('current','moderate','aggressive')}
        return {'mint':snapshot.get('mint'),'pool':snapshot.get('pool'),'selected_profile':chosen,
            'action':signal['action'],'reasons':signal['reasons'] or ([signal['reason']] if signal['action']=='watch' else []),'profiles':assessments,
            'recent_candle_range_fraction':span,'method':'volume_candles_regime_v1','learned_model':False,
            'scope':'Shared volume/candle entry rules; profile controls response cadence, not additional entry vetoes or larger stakes'}
    from meme_dip import entry_screen
    from trend_pullback import assess_trend_pullback
    assessments={}
    for name,rule in profiles(base).items():
        reasons=entry_screen(snapshot,now,rule)
        signal=assess_trend_pullback(snapshot,now,rule)
        assessments[name]={'action':'reject' if reasons else signal['action'],
            'reasons':reasons,'reason':signal.get('reason'),'metrics':signal.get('metrics',{})}
    selected_profile=next((name for name in ('current','moderate','aggressive') if assessments[name]['action']=='buy'),None)
    waiting=any(a['action']=='watch' for a in assessments.values())
    reasons=[] if selected_profile else list(dict.fromkeys(reason for a in assessments.values() for reason in a['reasons']))
    if not selected_profile and waiting: reasons=['awaiting a qualifying pullback under at least one profile']
    return {'mint':snapshot.get('mint'),'pool':snapshot.get('pool'),'selected_profile':selected_profile,
            'action':'buy' if selected_profile else 'watch' if waiting else 'reject','reasons':reasons,
            'profiles':assessments,'method':'narrowest_qualifying_rules_v1','learned_model':False,
            'scope':'market filters only; on-chain safety, fees, liquidity and execution checks still required'}


def response_rest(profile,holding=False):
    return ({'current':15,'moderate':12,'aggressive':10} if holding else
            {'current':30,'moderate':20,'aggressive':10}).get(profile,15 if holding else 30)
