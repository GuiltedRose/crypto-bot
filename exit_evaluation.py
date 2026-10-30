"""Frozen, chronological position-level exit comparisons against rule exits.

Replay net quotes are estimates, not executable counterfactual fills. Positions
closed by neural exits have no known rule-only outcome and are not compared.
"""
import copy
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import statistics
from meme_learning import live_outcomes,paper_outcomes,model_features,weighted_prediction,new_weighted_model
from neural_exits import load_groups,train_groups,samples_for

MIN_EVALUATION_POSITIONS=10
MIN_ROLLBACK_POSITIONS=5


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,allow_nan=False).encode()).hexdigest()


def baselines(paths,groups):
    result={}
    for path in dict.fromkeys(str(Path(p).resolve()) for p in paths if p and Path(p).exists()):
        db=sqlite3.connect('file:'+path+'?mode=ro',uri=True)
        try:tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:db.close()
        if 'paper_events' in tables:
            for row in paper_outcomes(path)[0]:
                if not row.get('training_eligible',True) or 'neural' in (row.get('exit_reason') or ''):continue
                for key in groups:
                    if key[0]!=path:continue
                    parts=key[1].rsplit(':',2)
                    if len(parts)!=3:continue
                    if parts[:2]!=[row['run_id'],row['mint']]:continue
                    try:matches=float(parts[2])==row['entry_at']
                    except ValueError:matches=False
                    if matches:result[key]=row
        elif 'events' in tables:
            for row in live_outcomes(path)[0]:
                if 'neural' not in (row.get('exit_reason') or ''):
                    result[(path,row['entry_signature'])]=row
    return result


def split_positions(groups,closed):
    ordered=sorted(groups,key=lambda key:(closed.get(key,{}).get('entry_at',groups[key][0][0]),key))
    training=ordered[:max(1,int(len(ordered)*.7))]
    cutoff=max(max(groups[key][-1][0],closed.get(key,{}).get('exit_at',0)) for key in training)
    heldout=[key for key in ordered if key not in training and
             closed.get(key,{}).get('entry_at',groups[key][0][0])>cutoff]
    return training,heldout,cutoff


def position_score(model,quotes,baseline):
    predictions=[];exit_net=baseline['net_pnl_usd'];changed=False
    for i,(at,context,net) in enumerate(quotes):
        if at>=baseline['exit_at']:continue
        score=weighted_prediction(model,context)['score'] if model else None
        future=next((r for r in quotes[i+1:] if 60<=r[0]-at<=180 and r[0]<=baseline['exit_at']),None)
        if future:
            label=int(future[2]>net)
            value=score if model else 1.
            predictions.append((int(value>=1/3)==label,(value-label)**2))
        if model and not changed and score<1/3:
            exit_net=net;changed=True
    return {'net':exit_net,'delta':exit_net-baseline['net_pnl_usd'],'changed':changed,
            'accuracy':sum(p[0] for p in predictions)/len(predictions) if predictions else None,
            'brier':sum(p[1] for p in predictions)/len(predictions) if predictions else None}


def compare(model,reference,groups,closed,keys):
    rows=[]
    for key in sorted(keys,key=lambda k:(closed[k]['exit_at'],k)):
        trial=position_score(model,groups[key],closed[key])
        base=position_score(reference,groups[key],closed[key])
        rows.append({'delta':trial['net']-base['net'],'neural':trial['net'],'reference':base['net'],
                     'rule':closed[key]['net_pnl_usd'],'reference_accuracy':base['accuracy'],'reference_brier':base['brier'],**{k:trial[k] for k in ('changed','accuracy','brier')}})
    deltas=[r['delta'] for r in rows]
    lower=(statistics.mean(deltas)-1.96*statistics.stdev(deltas)/math.sqrt(len(deltas))) if len(deltas)>1 else None
    def drawdown(field):
        equity=peak=worst=0.
        for row in rows:
            equity+=row[field];peak=max(peak,equity);worst=max(worst,peak-equity)
        return worst
    labeled=[r for r in rows if r['accuracy'] is not None]
    return {'positions':len(rows),'changed_positions':sum(r['changed'] for r in rows),
            'accuracy':statistics.mean(r['accuracy'] for r in labeled) if labeled else None,
            'brier':statistics.mean(r['brier'] for r in labeled) if labeled else None,
            'reference_accuracy':statistics.mean(r['reference_accuracy'] for r in labeled) if labeled else None,
            'reference_brier':statistics.mean(r['reference_brier'] for r in labeled) if labeled else None,
            'net_improvement_usd':sum(deltas),'improvement_lower_usd':lower,
            'neural_net_usd':sum(r['neural'] for r in rows),'rule_net_usd':sum(r['rule'] for r in rows),
            'neural_vs_rule_usd':sum(r['neural']-r['rule'] for r in rows),
            'neural_drawdown_usd':drawdown('neural'),'reference_drawdown_usd':drawdown('reference'),
            'basis':'position_grouped_out_of_sample_quote_replay_not_actual_fills'}


def update(paths,state_path):
    groups=load_groups(paths);closed=baselines(paths,groups)
    path=Path(state_path)
    state=json.loads(path.read_text()) if path.exists() else {}
    version=new_weighted_model()['version']
    if state.get('version')!=version:state={'version':version,'history':[],'consumed_until':0}
    fingerprint=digest({'groups':[(key,rows) for key,rows in sorted(groups.items())],
                        'closed':[(key,row) for key,row in sorted(closed.items())]})
    if state.get('fingerprint')==fingerprint and not state.get('pending_historical'):
        return state['result']
    state['pending_historical']=False
    champion=state.get('champion');candidate=state.get('candidate')
    # Training corrections invalidate learned models; appending later quotes does not.
    def valid(item):
        if not item:return True
        original={key:rows for key,rows in item['training_rows']}
        for encoded,rows in original.items():
            current=[r for r in groups.get(tuple(json.loads(encoded)),[]) if r[0]<=item['cutoff']]
            if digest(current)!=digest(rows):return False
        return True
    if not valid(champion):
        state['history'].append({'status':'invalidated_champion'});state.pop('champion',None);champion=None
        state.pop('evaluation',None);state.pop('monitoring',None)
    if not valid(candidate):
        state.pop('candidate',None);state.pop('evaluation',None);candidate=None
    if champion:
        fresh=[k for k in closed if k in groups and closed[k]['entry_at']>champion.get('monitor_after',champion['activated_after'])]
        fresh=sorted(fresh,key=lambda k:(closed[k]['exit_at'],k))[:MIN_ROLLBACK_POSITIONS]
        if len(fresh)>=MIN_ROLLBACK_POSITIONS:
            metrics=compare(champion['model'],champion.get('previous_model'),groups,closed,fresh)
            champion['monitor_after']=max(closed[k]['exit_at'] for k in fresh)
            state['monitoring']=metrics
            if metrics['net_improvement_usd']<0:
                previous=champion.get('previous_champion')
                state['history'].append({'status':'rolled_back','metrics':metrics})
                if previous:previous['monitor_after']=champion['monitor_after']
                state['champion']=previous;champion=previous
                state['consumed_until']=max(state['consumed_until'],max(closed[k]['exit_at'] for k in fresh))
                state.pop('candidate',None);candidate=None
    if candidate:
        keys=[k for k in closed if k in groups and closed[k]['entry_at']>candidate['cutoff']]
        keys=sorted(keys,key=lambda k:(closed[k]['exit_at'],k))[:MIN_EVALUATION_POSITIONS]
        metrics=compare(candidate['model'],champion['model'] if champion else None,groups,closed,keys)
        metrics['reference']='champion' if champion else 'rules'
        state['evaluation']=metrics
        if len(keys)>=MIN_EVALUATION_POSITIONS:
            accepted=(metrics['accuracy'] is not None and metrics['neural_net_usd']>0
                and metrics['accuracy']>=metrics['reference_accuracy'] and metrics['brier']<=metrics['reference_brier']
                and metrics['changed_positions']>=3 and metrics['improvement_lower_usd']>0
                and metrics['neural_drawdown_usd']<=metrics['reference_drawdown_usd'])
            end=max(closed[k]['exit_at'] for k in keys)
            state['history'].append({'status':'promoted' if accepted else 'rejected','metrics':metrics})
            state['consumed_until']=max(state['consumed_until'],end)
            if accepted:
                activation=max([r['exit_at'] for r in closed.values()]+[rows[-1][0] for rows in groups.values()])
                candidate.update(activated_after=activation,previous_model=champion['model'] if champion else None,
                                 previous_champion=champion,monitor_complete=False)
                state['champion']=candidate;champion=candidate
            state.pop('candidate',None);candidate=None
    if not candidate and groups:
        if state['consumed_until']:
            training=list(groups)
            cutoff=max(max(rows[-1][0],closed.get(k,{}).get('exit_at',0)) for k,rows in groups.items())
        else:training,_,cutoff=split_positions(groups,closed)
        model=train_groups({key:groups[key] for key in training})
        if model['samples']>=30 and model['trades']>=5 and min(model['wins'],model['losses'])>=5:
            candidate={'model':model,'cutoff':cutoff,'training_rows':[
                (json.dumps(key),groups[key]) for key in training]}
            state['candidate']=candidate
            state['pending_historical']=any(row['entry_at']>cutoff for row in closed.values())
    chosen=champion or candidate
    model=copy.deepcopy(chosen['model']) if chosen else train_groups(groups)
    model['evaluation']={'approved':champion is not None,'status':'champion' if champion else 'collecting',
        'oos':state.get('evaluation',{}),'monitoring':state.get('monitoring',{}),
        'rollbacks':sum(row['status']=='rolled_back' for row in state['history']),
        'corpus_positions':len(groups),'corpus_samples':len(samples_for(groups))}
    state['fingerprint']=fingerprint
    state['result']=model
    path.parent.mkdir(parents=True,exist_ok=True)
    raw=json.dumps(state,sort_keys=True,allow_nan=False)
    if not path.exists() or path.read_text()!=raw:
        temporary=path.with_suffix('.tmp');temporary.write_text(raw);temporary.replace(path)
    return model
