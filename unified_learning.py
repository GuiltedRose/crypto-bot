"""UNTESTED frozen-model research/evaluation; no order authorization or old-state edits."""
import copy
import json
import math
import time
from neural_autonomy import MIN_TRAINING, qualification
from neural_network import initialize, fit_balanced, forward
from unified_observations import canonical, digest, eligible, tensor, number
from shadow_ml_models import fit_svm, calibrate_svm, fit_lstm, predict, MAX_STEPS


def grouped_split(records):
    positions={}
    for row in records:
        if row['position_id'] in positions and positions[row['position_id']]!=row['group_id']:raise ValueError('Position correlation group changed')
        positions[row['position_id']]=row['group_id']
    groups={}
    for row in records:groups.setdefault(row['group_id'],[]).append(row)
    ordered=sorted(groups.values(),key=lambda rows:(min(r['feature_at'] for r in rows),rows[0]['group_id']))
    first=int(len(ordered)*.6);second=int(len(ordered)*.8)
    def flatten(grouped):return [row for group in grouped for row in group]
    train=flatten(ordered[:first])
    cutoff=max((max(r.get('label_known_at') or 0,r.get('risk_label_at') or 0,r['known_at']) for r in train),default=0)
    validation=[r for r in flatten(ordered[first:second]) if r['feature_at']>cutoff]
    cutoff=max([cutoff]+[max(r.get('label_known_at') or 0,r.get('risk_label_at') or 0,r['known_at']) for r in validation])
    test=[r for r in flatten(ordered[second:]) if r['feature_at']>cutoff]
    return train,validation,test


def windows(records):
    groups={}
    for row in records:groups.setdefault(row['position_id'],[]).append(row)
    result=[]
    for group in groups.values():
        ordered=sorted(group,key=lambda row:(row['feature_at'],row['identity']))
        labels={r['net_pnl_usd'] for r in ordered if r.get('net_pnl_usd') is not None}
        if len(labels)>1:raise ValueError('Conflicting completed-position outcomes')
        last_end=0
        for end in range(MAX_STEPS,len(ordered)+1):
            if end-last_end<MAX_STEPS:continue
            sequence=ordered[end-MAX_STEPS:end]
            if any(sequence[i]['feature_at']>=sequence[i+1]['feature_at'] for i in range(len(sequence)-1)):continue
            last=sequence[-1]
            if last.get('net_pnl_usd') is None or last.get('net_of_costs_verified') is not True:continue
            if last.get('label_known_at',0)<=last['feature_at']:continue
            result.append(([tensor(row) for row in sequence],int(last['net_pnl_usd']>0),last))
            last_end=end
    return result


def metrics(predictions):
    grouped={}
    for probability,label,row in predictions:grouped.setdefault(row['position_id'],[]).append((probability,label,row))
    if not grouped:return {'positions':0,'brier':None,'accuracy':None,'estimated_selected_net_usd':None}
    aggregated=[]
    for values in grouped.values():
        if len({v[1] for v in values})!=1:raise ValueError('Conflicting position targets')
        aggregated.append((sum(v[0] for v in values)/len(values),values[0][1],values[0][2]))
    ordered=sorted(aggregated,key=lambda value:value[2].get('label_known_at',0))
    cumulative=peak=drawdown=0.
    for probability,_,row in ordered:
        if probability>=2/3 and row.get('net_pnl_usd') is not None:cumulative+=row['net_pnl_usd']
        peak=max(peak,cumulative);drawdown=max(drawdown,peak-cumulative)
    return {'positions':len(aggregated),'windows':len(predictions),
            'brier':sum((p-y)**2 for p,y,_ in aggregated)/len(aggregated),
            'accuracy':sum((p>=.5)==bool(y) for p,y,_ in aggregated)/len(aggregated),
            'estimated_selected_net_usd':cumulative,'max_net_drawdown_usd':drawdown,
            'scope':'position-aggregated historical labels, not executable counterfactual improvement'}


def build_candidate(store,family,now=None):
    now=time.time() if now is None else now
    records=[row for row in store.observations() if eligible(row) and row['feature_at']<=now and row['known_at']<=now and max(row.get('label_known_at') or 0,row.get('risk_label_at') or 0)<=now]
    fingerprint=digest(['unified-v1',family,[digest(row) for row in records]])
    with store.connect(readonly=True) as db:
        previous=db.execute('SELECT body FROM models WHERE fingerprint=?',(fingerprint,)).fetchone()
    if previous:return json.loads(previous[0])
    train,validation,test=grouped_split(records)
    feature_sizes={len(tensor(row)) for row in records}
    schemas={row['feature_schema'] for row in records}
    if len(schemas)>1:raise ValueError('Feature schema mixing prohibited')
    if len(feature_sizes)>1:raise ValueError('One explicit feature schema per dataset required')
    model=None;report={}
    if family=='svm':
        valid=lambda rows:[r for r in rows if r.get('risk_label_verified') is True and r.get('risk_label') in (0,1) and number(r.get('risk_label_at')) and r['risk_label_at']>=r['feature_at']]
        train,validation,test=map(valid,(train,validation,test))
        if len({r['group_id'] for r in train})<MIN_TRAINING or len({r['group_id'] for r in validation})<10 or len({r['risk_label'] for r in train})<2:
            return {'status':'blocked','reason':'Verified two-class risk labels and independent calibration data required'}
        model=calibrate_svm(fit_svm([(tensor(r),r['risk_label']) for r in train]),[(tensor(r),r['risk_label']) for r in validation])
        report=metrics([(predict(model,tensor(r)),r['risk_label'],r) for r in test])
        report.pop('estimated_selected_net_usd',None);report.pop('max_net_drawdown_usd',None)
        report['target']='verified_risk; not ordinary trade losses'
        rules=[r for r in test if number(r.get('rule_risk_score')) and 0<=r['rule_risk_score']<=1]
        report['rule_baseline']=metrics([(r['rule_risk_score'],r['risk_label'],r) for r in rules])
    elif family=='lstm':
        training=windows(train);testing=windows(test)
        if len({r['group_id'] for _,_,r in training})<MIN_TRAINING:return {'status':'blocked','reason':'Insufficient non-overlapping verified chronological sequences'}
        model=fit_lstm([(x,y) for x,y,_ in training])
        baseline=fit_balanced(initialize(len(training[0][0][-1])),[(x[-1],y) for x,y,_ in training])
        report=metrics([(predict(model,x),y,r) for x,y,r in testing])
        report['feedforward_baseline']=metrics([(forward(baseline,x[-1])[0],y,r) for x,y,r in testing])
    elif family=='rag':
        train=[r for r in train if r.get('net_pnl_usd') is not None and r.get('net_of_costs_verified') is True]
        if len(train)<MIN_TRAINING:return {'status':'blocked','reason':'Insufficient eligible frozen retrieval evidence'}
        train=train[:512]
        model={'family':'rag','corpus_ids':[digest([r['source'],r['identity']]) for r in train]}
        predictions=[]
        for row in test:
            if row.get('net_pnl_usd') is None or row.get('net_of_costs_verified') is not True:continue
            found=store.retrieve(tensor(row),row['feature_at'],row['group_id'],allow_ids=set(model['corpus_ids']),feature_schema_id=next(iter(schemas),None))
            if len(found['neighbors'])>=3:predictions.append((found['score'],int(row['net_pnl_usd']>0),row))
        report=metrics(predictions)
        report['retrieval_scope']='frozen training corpus only; chronological labels known at inference'
    else:raise ValueError('Unknown ML family')
    artifact={'family':family,'version':'unified-shadow-v1','model':model,'created_at':now,
              'training_ids':[digest([r['source'],r['identity']]) for r in train+validation],
              'training_fingerprints':{digest([r['source'],r['identity']]):digest(r) for r in train+validation},
              'feature_schema':next(iter(schemas),None),'feature_size':next(iter(feature_sizes),0),'historical_evaluation':report,
              'status':'shadow','live_eligible':False}
    artifact['id']=digest(artifact)
    with store.connect() as db:db.execute('INSERT OR IGNORE INTO models VALUES(?,?,?)',(artifact['id'],fingerprint,canonical(artifact)))
    return artifact


def load_candidate(store,identifier):
    with store.connect(readonly=True) as db:row=db.execute('SELECT body FROM models WHERE id=?',(identifier,)).fetchone()
    if not row:raise ValueError('Unknown frozen candidate')
    candidate=json.loads(row[0]);body={k:v for k,v in candidate.items() if k!='id'}
    if digest(body)!=identifier:raise ValueError('Frozen model integrity mismatch')
    return candidate


def lineage_valid(store,candidate):
    actual={digest([r['source'],r['identity']]):digest(r) for r in store.by_ids(candidate['training_ids']) if eligible(r)}
    return actual==candidate['training_fingerprints']


def forecast(store,identifier,observations,position_id):
    candidate=load_candidate(store,identifier);now=time.time()
    if not observations or len(observations)>MAX_STEPS or any(not eligible(r) or r['known_at']>now or r['feature_at']>now for r in observations):
        return {'score':None,'reason':'Verified bounded inference evidence required','market_eligible':False}
    if any(r['feature_schema']!=candidate['feature_schema'] for r in observations):raise ValueError('Inference schema mismatch')
    ordered=sorted(observations,key=lambda r:r['feature_at'])
    if any(ordered[i]['feature_at']>=ordered[i+1]['feature_at'] for i in range(len(ordered)-1)):raise ValueError('Strict sequence chronology required')
    if not lineage_valid(store,candidate):return {'score':None,'reason':'Frozen evidence unavailable/revoked','market_eligible':False}
    if any(r['position_id']!=position_id for r in ordered) or len({r['mint'] for r in ordered})!=1:raise ValueError('Sequence must describe one position and mint')
    x=tensor(ordered[-1]);family=candidate['family'];neighbors=[]
    if family=='lstm' and len(ordered)!=MAX_STEPS:return {'score':None,'reason':'Complete sequence required','market_eligible':False}
    if family=='rag':
        result=store.retrieve(x,now,ordered[-1]['group_id'],allow_ids=set(candidate['model']['corpus_ids']),feature_schema_id=candidate['feature_schema'])
        neighbors=result['neighbors'];score=result['score'] if len(neighbors)>=3 else None
    elif family=='svm':score=1-predict(candidate['model'],x)
    else:score=predict(candidate['model'],[tensor(r) for r in ordered])
    result={'candidate_id':identifier,'score':score,'recorded_at':now,'position_id':position_id,
            'evidence_ids':[r['identity'] for r in ordered],'neighbors':neighbors,'market_eligible':False}
    if score is not None:
        with store.connect() as db:db.execute('INSERT INTO forecasts VALUES(?,?,?,?)',(identifier,position_id,now,canonical(result)))
    return result


def evaluate_candidate(store,identifier,outcomes,failures=()):
    """Reuse existing prospective qualification; never edits its promotion history."""
    candidate=load_candidate(store,identifier)
    if not lineage_valid(store,candidate):
        return {'status':'blocked','reason':'Training evidence unavailable/revoked; frozen candidate preserved','live_eligible':False}
    with store.connect(readonly=True) as db:predictions={r[0]:json.loads(r[1]) for r in db.execute('SELECT position_id,body FROM forecasts WHERE candidate_id=?',(identifier,))}
    rows=[]
    for original in outcomes:
        if original.get('source')!='bot_live' or original.get('provenance',{}).get('verified') is not True or original.get('rights',{}).get('evaluation_verified') is not True or original.get('net_of_costs_verified') is not True:continue
        from data_policy import require_learning_rights,SourceAccessBlocked
        try:require_learning_rights(original)
        except SourceAccessBlocked:continue
        prediction=predictions.get(original.get('position_id'))
        if not prediction or original.get('entry_context',{}).get('recorded_before_order') is not True or prediction['recorded_at']>original.get('entry_at',0):continue
        row=copy.deepcopy(original)
        row['entry_context']['neural_selection']={'candidate_id':identifier,'score':prediction['score']}
        rows.append(row)
    result=qualification(candidate,rows,failures)
    result.update(live_eligible=False,shadow_only=True,promotion_history_unchanged=True)
    snapshot={'candidate_id':identifier,'qualification':result,'evaluated_at':time.time()}
    key=digest([identifier,result])
    with store.connect() as db:db.execute('INSERT OR IGNORE INTO evaluations VALUES(?,?,?)',(key,identifier,canonical(snapshot)))
    from evaluation_state import load,save
    path=store.path.with_name('unified-evaluation.json')
    state,_=load(path);state=state or {'candidates':{}}
    state['candidates'][identifier]={'qualification':result,'candidate_id':identifier}
    save(path,state)
    return result
