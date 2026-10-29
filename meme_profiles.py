"""Shared entry-tolerance experiments; never varies dollar risk or slippage."""
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
        result[name]=replace(base,entry_profile_mode='fixed',**values)
    return result


def selected(base,name):
    if name=='adaptive': return replace(base,entry_profile_mode='adaptive')
    return profiles(base)[name] if name else base


def assess_profiles(snapshot,now,base):
    """Narrowest qualifying MARKET profile. Safety/execution remain mandatory.

    Not a learned profit prediction, and never selects from future outcomes.
    """
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
