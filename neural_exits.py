"""Learn one-minute continuation from recorded, net-of-cost position quotes.

These are quote-based labels, not proof of counterfactual executable profits.
Missing future quotes produce no label; entry and exit networks stay separate.
"""
import json
from pathlib import Path
import sqlite3
from collections import defaultdict
from meme_learning import decision_context,model_features,new_weighted_model,weighted_prediction,chart_features
from neural_network import fit_balanced


def record(db,trade_id,snapshot,rules,net_pnl,cost):
    if snapshot.get('synthetic') is True:return
    if not any(chart_features(snapshot)):return
    context=decision_context(snapshot,rules,notional_usd=cost)
    context['position_net_fraction']=net_pnl/max(cost,1e-9)
    with db:
        db.execute('''CREATE TABLE IF NOT EXISTS neural_position_quotes(
            trade_id TEXT,bar INTEGER,observed REAL,context TEXT,net REAL,
            PRIMARY KEY(trade_id,bar))''')
        db.execute('INSERT OR IGNORE INTO neural_position_quotes VALUES(?,?,?,?,?)',
            (trade_id,int(snapshot['observed_at']//60),snapshot['observed_at'],json.dumps(context,allow_nan=False),net_pnl))


def load_groups(paths):
    groups=defaultdict(list)
    for path in dict.fromkeys(str(Path(p).resolve()) for p in paths if p and Path(p).exists()):
        db=sqlite3.connect('file:'+path+'?mode=ro',uri=True)
        try:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='neural_position_quotes'").fetchone():continue
            invalid_runs=set()
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='paper_events'").fetchone():
                from meme_learning import _paper_correction
                invalid_runs={run for run,raw in db.execute('SELECT run_id,event FROM paper_events') if _paper_correction(json.loads(raw))}
            for trade,at,raw,net in db.execute('SELECT trade_id,observed,context,net FROM neural_position_quotes ORDER BY observed'):
                if any(trade.startswith(run+':') for run in invalid_runs):continue
                groups[(path,trade)].append((at,json.loads(raw),net))
        finally:db.close()
    return groups


def samples_for(groups):
    samples=[]
    for quotes in groups.values():
        for i,(at,context,net) in enumerate(quotes):
            future=next((row for row in quotes[i+1:] if 60<=row[0]-at<=180),None)
            if future:
                samples.append((list(model_features(context).values()),int(future[2]>net)))
    return samples


def train_groups(groups):
    from data_policy import require_learning_rights
    require_learning_rights(list(groups.values()))
    samples=samples_for(groups)
    model=new_weighted_model()
    model['weights']=fit_balanced(model['weights'],samples)
    model.update(samples=len(samples),wins=sum(label for _,label in samples),
                 losses=sum(not label for _,label in samples),trades=sum(bool(samples_for({key:quotes})) for key,quotes in groups.items()),task='one_minute_net_continuation')
    return model


def train(paths):
    return train_groups(load_groups(paths))


def decision(model,snapshot,rules,net,cost):
    if not model or model.get('task')!='one_minute_net_continuation' or model['samples']<30 or min(model['wins'],model['losses'])<5 or model.get('trades',0)<5:return None
    if not model.get('evaluation',{}).get('approved'):return None
    if not any(chart_features(snapshot)):return
    context=decision_context(snapshot,rules,notional_usd=cost)
    context['position_net_fraction']=net/max(cost,1e-9)
    try:prediction=weighted_prediction(model,context)
    except (ValueError,KeyError,TypeError):return None
    if prediction['score']>=1/3:return None
    return {'reason':'neural early profit exit' if net>0 else 'neural early loss exit',
            'continuation_score':prediction['score'],'estimated_net_pnl_usd':net}
