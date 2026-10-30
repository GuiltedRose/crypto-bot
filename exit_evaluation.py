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
from evaluation_state import load as load_state,save as save_state
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


def comparison_row(model,reference,quotes,baseline):
    trial=position_score(model,quotes,baseline)
    base=position_score(reference,quotes,baseline)
    return {'delta':trial['net']-base['net'],'neural':trial['net'],'reference':base['net'],
            'rule':baseline['net_pnl_usd'],'reference_accuracy':base['accuracy'],
            'reference_brier':base['brier'],**{k:trial[k] for k in ('changed','accuracy','brier')}}


def compare(model,reference,groups,closed,keys,saved_rows=None):
    rows=[]
    for key in sorted(keys,key=lambda k:(closed[k]['exit_at'],k)):
        rows.append(saved_rows[json.dumps(key)] if saved_rows is not None else
                    comparison_row(model,reference,groups[key],closed[key]))
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


def baseline_signature(row):
    return {key:row.get(key) for key in ('entry_at','exit_at','exit_signature','exit_reason','net_pnl_usd')}


def capture(candidate,key,quotes,baseline,reference):
    encoded=json.dumps(key)
    candidate['validation_ids'].append(encoded)
    candidate['validation_rows'][encoded]=comparison_row(candidate['model'],reference,quotes,baseline)
    candidate['validation_inputs'][encoded]={'quotes':copy.deepcopy(quotes),'baseline':baseline_signature(baseline)}


def event_summary(event):
    if not event:return None
    return {key:event[key] for key in ('status','reason','candidate_id') if key in event}


def pause(state,path,reason):
    if state.get('recovery',{}).get('reason')!=reason:
        state['history'].append({'status':'evaluation_paused','reason':reason})
    state['recovery']={'status':'paused','reason':reason}
    chosen=state.get('champion') or state.get('candidate')
    model=copy.deepcopy(state.get('result') or (chosen['model'] if chosen else new_weighted_model()))
    evaluation=model.setdefault('evaluation',{})
    evaluation.update(approved=False,status='paused',recovery=state['recovery'],
                      last_event=event_summary(state['history'][-1]))
    # The original challenger, metrics, cutoff, and history remain in state.
    save_state(path,state)
    return model


REQUIRED_CONSECUTIVE_PROMOTIONS=10


def promotion_failures(metrics):
    tests=(
        ('prediction_metrics_missing',metrics.get('accuracy') is not None),
        ('nonpositive_neural_net',metrics.get('neural_net_usd',0)>0),
        ('accuracy_worse_than_reference',metrics.get('accuracy') is not None and metrics.get('reference_accuracy') is not None and metrics['accuracy']>=metrics['reference_accuracy']),
        ('brier_worse_than_reference',metrics.get('brier') is not None and metrics.get('reference_brier') is not None and metrics['brier']<=metrics['reference_brier']),
        ('fewer_than_three_changed_exits',metrics.get('changed_positions',0)>=3),
        ('no_positive_improvement_lower_bound',metrics.get('improvement_lower_usd') is not None and metrics['improvement_lower_usd']>0),
        ('drawdown_worse_than_reference',metrics.get('neural_drawdown_usd',0)<=metrics.get('reference_drawdown_usd',0)))
    return [name for name,passed in tests if not passed]


def live_eligibility(state):
    streak=0
    for event in (state or {}).get('history',[]):
        if event['status']=='promoted':streak+=1
        elif event['status'] in ('rejected','rolled_back'):streak=0
    eligible=bool(streak>=REQUIRED_CONSECUTIVE_PROMOTIONS and (state or {}).get('champion')
                  and not (state or {}).get('recovery')
                  and (state or {}).get('version')==new_weighted_model()['version'])
    return {'live_eligible':eligible,'consecutive_promotions':streak,
            'required_consecutive_promotions':REQUIRED_CONSECUTIVE_PROMOTIONS,
            'reason':'Ten consecutive successful challenger promotions required' if not eligible else 'Promotion history requirement met; all trade controls still apply'}


def update(paths,state_path):
    groups=load_groups(paths);closed=baselines(paths,groups)
    path=Path(state_path)
    state,recovered=load_state(path)
    version=new_weighted_model()['version']
    if state is None:state={'version':version,'history':[],'consumed_until':0}
    if recovered:
        state['history'].append({'status':'state_recovered','reason':recovered})
    from data_policy import contains_unreviewed_learning_data
    if contains_unreviewed_learning_data(list(closed.values())) or contains_unreviewed_learning_data(list(groups.values())):
        return pause(state,path,'Data-source rights/storage review required; recorded provider data is not approved for model training/evaluation')
    if state.get('version')!=version:
        return pause(state,path,'Saved challenger feature version differs from current code; explicit migration required')
    state.setdefault('consumed_positions',[])
    was_paused=state.get('recovery')

    # Stable identity distinguishes a new experiment from a process restart.
    upgraded=bool(recovered or was_paused)
    for item in (state.get('candidate'),state.get('champion')):
        if item and 'id' not in item:
            item['id']=digest({'cutoff':item['cutoff'],'weights':item['model']['weights'],'training_rows':item['training_rows']})[:12]
            upgraded=True
    if state.get('candidate') and 'validation_ids' not in state['candidate']:
        upgraded=True
    fingerprint=digest({'groups':[(key,rows) for key,rows in sorted(groups.items())],
                        'closed':[(key,row) for key,row in sorted(closed.items())]})
    if state.get('fingerprint')==fingerprint and not state.get('pending_historical') and not upgraded:
        return state['result']
    state['pending_historical']=False
    champion=state.get('champion');candidate=state.get('candidate')
    # Validate only the observations actually used to fit frozen weights.
    # Late additions do not change the original training set.
    def validity(item):
        if not item:return None
        for encoded,rows in item['training_rows']:
            available={row[0]:row for row in groups.get(tuple(json.loads(encoded)),[])}
            for row in rows:
                if row[0] not in available:return ('missing',encoded,row[0])
                if digest(available[row[0]])!=digest(row):return ('changed',encoded,row[0])
        return None
    for label,item in (('champion',champion),('candidate',candidate)):
        problem=validity(item)
        if problem and problem[0]=='missing':
            return pause(state,path,f'{label} training observation unavailable: {problem[1]} at {problem[2]}; frozen state retained')
        if problem:
            reason=f'Previously used training observation corrected: {problem[1]} at {problem[2]}'
            state['history'].append({'status':'invalidated_'+label,'candidate_id':item['id'],
                                     'reason':reason,'snapshot':copy.deepcopy(item)})
            state.pop(label,None)
            state.pop('evaluation',None)
            if label=='champion':champion=None;state.pop('monitoring',None)
            else:candidate=None
            upgraded=True
    if candidate and 'reference_model' not in candidate:
        candidate['reference_model']=copy.deepcopy(champion['model']) if champion else None
        candidate['reference_id']=champion['id'] if champion else 'rules'
        upgraded=True
    if champion:
        fresh=[k for k in closed if k in groups and closed[k]['entry_at']>champion.get('monitor_after',champion['activated_after'])]
        fresh=sorted(fresh,key=lambda k:(closed[k]['exit_at'],k))[:MIN_ROLLBACK_POSITIONS]
        if len(fresh)>=MIN_ROLLBACK_POSITIONS:
            metrics=compare(champion['model'],champion.get('previous_model'),groups,closed,fresh)
            champion['monitor_after']=max(closed[k]['exit_at'] for k in fresh)
            champion['monitor_consumed_positions']=list(dict.fromkeys(
                champion.get('monitor_consumed_positions',[])+[json.dumps(k) for k in fresh]))
            state['monitoring']=metrics
            if metrics['net_improvement_usd']<0:
                previous=champion.get('previous_champion')
                state['history'].append({'status':'rolled_back','reason':'Negative matched net improvement on fresh monitoring block',
                    'candidate_id':champion['id'],'metrics':metrics,'snapshot':copy.deepcopy(champion),
                    'position_ids':[json.dumps(k) for k in fresh]})
                if candidate:
                    state['history'].append({'status':'candidate_replaced','candidate_id':candidate['id'],
                        'reason':'Champion rollback changed its comparison reference','snapshot':copy.deepcopy(candidate)})
                if previous:previous['monitor_after']=champion['monitor_after']
                state['champion']=previous;champion=previous
                state['consumed_until']=max(state['consumed_until'],max(closed[k]['exit_at'] for k in fresh))
                state.pop('candidate',None);candidate=None
    if candidate:
        reference=candidate['reference_model']
        if 'validation_ids' not in candidate:
            # Older state stored aggregates only: migrate only when the exact
            # same scores can be recovered. Never replace saved percentages.
            migrated=copy.deepcopy(candidate)
            migrated['validation_ids']=[];migrated['validation_rows']={};migrated['validation_inputs']={}
            previous=state.get('evaluation',{})
            completed_metrics=[row.get('metrics') for row in state['history'] if row.get('status') in ('rejected','promoted')]
            belongs_to_previous=any(digest(row)==digest(previous) for row in completed_metrics)
            same_candidate=previous.get('candidate_id')==candidate['id'] or previous.get('candidate_id') is None and not belongs_to_previous
            old_count=previous.get('positions',0) if same_candidate else 0
            legacy=sorted([k for k in closed if k in groups and closed[k]['entry_at']>candidate['cutoff']],
                          key=lambda k:(closed[k]['exit_at'],k))[:old_count]
            reconstructed=compare(candidate['model'],reference,groups,closed,legacy)
            for name in ('positions','accuracy','brier','net_improvement_usd'):
                old=previous.get(name);new=reconstructed.get(name)
                if old_count and (old is None)!=(new is None):
                    return pause(state,path,'Legacy validation scores cannot be recovered exactly; saved metrics retained')
                if old_count and old is not None and abs(old-new)>1e-10:
                    return pause(state,path,'Legacy validation scores differ from saved metrics; explicit review required')
            for key in legacy:capture(migrated,key,groups[key],closed[key],reference)
            candidate.update(migrated)
            state['history'].append({'status':'state_migrated','candidate_id':candidate['id'],
                'reason':'Saved partial validation reconstructed exactly; frozen weights and scores retained'})
            upgraded=True
        for encoded in candidate['validation_ids']:
            key=tuple(json.loads(encoded));saved=candidate['validation_inputs'][encoded]
            if key not in closed or key not in groups:
                return pause(state,path,'Previously evaluated position unavailable; validation progress retained: '+encoded)
            # Existing scores are immutable. Corrections require an explicit pause.
            available={row[0]:row for row in groups[key]}
            if any(row[0] not in available or digest(row)!=digest(available[row[0]]) for row in saved['quotes']):
                return pause(state,path,'Previously evaluated quote changed or unavailable; saved scores retained: '+encoded)
            if baseline_signature(closed[key])!=saved['baseline']:
                return pause(state,path,'Previously evaluated rule outcome changed; saved scores retained: '+encoded)
        new_keys=[k for k in closed if k in groups and closed[k]['entry_at']>candidate['cutoff']
                  and json.dumps(k) not in state['consumed_positions'] and json.dumps(k) not in candidate['validation_ids']]
        for key in sorted(new_keys,key=lambda k:(closed[k]['exit_at'],k)):
            if len(candidate['validation_ids'])>=MIN_EVALUATION_POSITIONS:break
            capture(candidate,key,groups[key],closed[key],reference)
            state['history'].append({'status':'validation_progressed','candidate_id':candidate['id'],
                'reason':'Added a newly completed rule-closed position','position_id':json.dumps(key)})
        keys=[tuple(json.loads(key)) for key in candidate['validation_ids']]
        metrics=compare(candidate['model'],reference,groups,closed,keys,candidate['validation_rows'])
        metrics['candidate_id']=candidate['id']
        metrics['reference']=candidate['reference_id']
        state['evaluation']=metrics
        if len(keys)>=MIN_EVALUATION_POSITIONS:
            failures=promotion_failures(metrics)
            accepted=not failures
            metrics['promotion_failures']=failures
            end=max(closed[k]['exit_at'] for k in keys)
            state['history'].append({'status':'promoted' if accepted else 'rejected','candidate_id':candidate['id'],'metrics':metrics,
                'reason':'Passed frozen position-grouped comparison' if accepted else 'Promotion blocked: '+', '.join(failures),
                'snapshot':copy.deepcopy(candidate)})
            state['consumed_positions']=list(dict.fromkeys(state['consumed_positions']+candidate['validation_ids']))
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
            candidate['id']=digest({'cutoff':cutoff,'weights':model['weights'],'training_rows':candidate['training_rows']})[:12]
            candidate.update(validation_ids=[],validation_rows={},validation_inputs={},
                reference_model=copy.deepcopy(champion['model']) if champion else None,reference_id=champion['id'] if champion else 'rules')
            state['candidate']=candidate
            state['history'].append({'status':'candidate_created','candidate_id':candidate['id'],
                'reason':'Initial frozen challenger' if not state['history'] else 'Fresh challenger after recorded lifecycle decision',
                'cutoff':cutoff})
            state['pending_historical']=any(row['entry_at']>cutoff for row in closed.values())
    chosen=champion or candidate
    model=copy.deepcopy(chosen['model']) if chosen else train_groups(groups)
    completed=[row for row in state['history'] if row['status'] in ('promoted','rejected')]
    current=state.get('evaluation',{})
    pending_positions=current.get('positions',0) if candidate and current.get('candidate_id')==candidate['id'] else 0
    model['evaluation']={'challenger_id':candidate['id'] if candidate else None,
        'live_eligibility':live_eligibility(state),
        'challengers_completed':len(completed),
        'total_evaluated_positions':sum(row['metrics']['positions'] for row in completed)+pending_positions,
        'current_challenger_positions':pending_positions,
        'last_decision':({**event_summary(completed[-1]),'metrics':completed[-1]['metrics']} if completed else None),
        'approved':champion is not None,'status':'champion' if champion else 'collecting',
        'oos':state.get('evaluation',{}),'monitoring':state.get('monitoring',{}),
        'rollbacks':sum(row['status']=='rolled_back' for row in state['history']),
        'corpus_positions':len(groups),'corpus_samples':len(samples_for(groups))}
    state['fingerprint']=fingerprint
    state['result']=model
    if was_paused:
        state['history'].append({'status':'evaluation_resumed','reason':'Required observations are available and unchanged; frozen challenger resumed'})
    state.pop('recovery',None)
    state['last_lifecycle_event']=state['history'][-1] if state['history'] else None
    model['evaluation']['last_event']=event_summary(state['last_lifecycle_event'])
    save_state(path,state)
    return model
