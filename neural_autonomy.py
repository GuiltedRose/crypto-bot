"""Prospective qualification for a frozen neural entry veto.

Training can use paper and followed wallets. Qualification uses only later,
confirmed bot closes whose frozen score was recorded before order submission.
The incumbent rule strategy continues trading while candidates are in shadow.
"""
import copy
import hashlib
import json
import math
import random

MIN_TRAINING=30
MIN_VALIDATION=30
MIN_SELECTED=20
MIN_REJECTED=5
MIN_DAYS=14
THRESHOLD=.5
MAX_AGE=60*86400


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,allow_nan=False).encode()).hexdigest()


def qualification(candidate,trades,failures=()):
    eligible=[];seen=set()
    for row in trades:
        context=row.get('entry_context') or {}
        prediction=context.get('neural_selection') or {}
        score=prediction.get('score');pnl=row.get('net_pnl_usd');signature=row.get('exit_signature')
        if (row.get('source')!='bot_live' or prediction.get('candidate_id')!=candidate['id']
            or row.get('entry_at',0)<=candidate['created_at']
            or context.get('recorded_before_order') is not True
            or type(score) not in (int,float) or not math.isfinite(score) or not 0<=score<=1
            or type(pnl) not in (int,float) or not math.isfinite(pnl)
            or not signature or signature in seen): continue
        seen.add(signature);eligible.append(row)
    eligible.sort(key=lambda row:(row['exit_at'],row['exit_signature']))
    selected=[r for r in eligible if r['entry_context']['neural_selection']['score']>=THRESHOLD]
    days={}
    for row in eligible:
        day=int(row['exit_at']//86400)
        # Difference versus taking every incumbent trade: rejected trades earn zero.
        improvement=-row['net_pnl_usd'] if row not in selected else 0.
        days[day]=days.get(day,0.)+improvement
    failed_cost=0.
    for failure in failures:
        at=failure.get('observed_at',0);cost=failure.get('network_cost_usd')
        if at<=candidate['created_at']: continue
        if type(cost) not in (int,float) or not math.isfinite(cost) or cost<0:
            return {'status':'collecting','reason':'invalid failed-order costs'}
        failed_cost+=cost
        # Conservatively charge all observed failure fees to the filtered policy.
        day=int(at//86400);days[day]=days.get(day,0.)-cost
    lower=None
    if len(days)>=MIN_DAYS:
        values=list(days.values());rng=random.Random(42)
        samples=sorted(sum(rng.choice(values) for _ in values)/len(values) for _ in range(1000))
        lower=samples[24]
    pnl=sum(r['net_pnl_usd'] for r in selected)-failed_cost
    ready=(len(eligible)>=MIN_VALIDATION and len(selected)>=MIN_SELECTED
        and len(eligible)-len(selected)>=MIN_REJECTED and len(days)>=MIN_DAYS)
    return {'status':'qualified' if ready and pnl>0 and lower is not None and lower>0 else
            'rejected' if ready else 'collecting',
            'confirmed_validation_trades':len(eligible),'selected_trades':len(selected),
            'rejected_trades':len(eligible)-len(selected),'observed_days':len(days),
            'selected_net_pnl_usd':pnl,'failed_order_cost_usd':failed_cost,'daily_improvement_bootstrap_lower':lower}


def advance(previous,model,trades,binding,now,can_activate=True,failures=()):
    """Persist immutable candidate weights; never reuse its validation as training."""
    state=copy.deepcopy(previous or {})
    candidate=state.get('candidate')
    current_ids=set(model.get('training_ids',[]))
    binding_hash=digest(binding)
    if candidate and (candidate['binding_hash']!=binding_hash
        or not set(candidate['model'].get('training_ids',[])).issubset(current_ids)
        or now-candidate['created_at']>MAX_AGE):
        state={'status':'invalidated','reason':'training provenance, policy or model age changed'}
        candidate=None
    if candidate:
        if state.get('status')=='active':
            recent=sorted([row for row in trades if row.get('source')=='bot_live'
                and row.get('entry_at',0)>state['activated_at']
                and (row.get('entry_context') or {}).get('neural_selection',{}).get('candidate_id')==candidate['id']],
                key=lambda row:row['exit_at'])
            pnl=[row['net_pnl_usd'] for row in recent]
            if (len(pnl)>=2 and all(v<0 for v in pnl[-2:])) or (len(pnl)>=5 and sum(pnl[-5:])<=0):
                state={'status':'rolled_back','reason':'two losses or nonpositive last five closes'}
                candidate=None
            else:
                state['post_activation_closes']=len(pnl)
                return state
        else:
            metrics=qualification(candidate,trades,failures);state['qualification']=metrics
            if metrics['status']=='qualified' and can_activate:
                state.update(status='active',activated_at=now)
                return state
            if metrics['status']=='rejected':
                state={'status':'rejected','last_qualification':metrics};candidate=None
            else:
                return state
    if not candidate:
        if model.get('samples',0)<MIN_TRAINING:
            state.update(status='waiting_for_training',training_samples=model.get('samples',0))
            return state
        candidate={'id':digest({'model':model,'created_at':now,'binding':binding_hash}),
                   'created_at':now,'binding_hash':binding_hash,'model':copy.deepcopy(model)}
        state.update(status='collecting',candidate=candidate)
    return state


def entry_decision(state,context):
    candidate=(state or {}).get('candidate')
    if not candidate:
        return {'status':'unqualified','allow':True,'candidate_id':None}
    from meme_learning import weighted_prediction
    prediction=weighted_prediction(candidate['model'],context)
    active=state.get('status')=='active'
    return {'candidate_id':candidate['id'],'score':prediction['score'],
            'threshold':THRESHOLD,'features':prediction['features'],
            'status':'active' if active else 'shadow',
            'allow':not active or prediction['score']>=THRESHOLD}
