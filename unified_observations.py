"""UNTESTED isolated append-only ML evidence/retrieval cache. Never a trade DB.

Existing ledgers remain authoritative and read-only. Unknown provenance/rights or
late features may be stored with storage approval but never trained/retrieved.
"""
from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time
from data_policy import require_learning_rights, SourceAccessBlocked

APP_ID=0x554D4C31


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)
def digest(value):return hashlib.sha256(canonical(value).encode()).hexdigest()
def number(value):return type(value) in (int,float) and math.isfinite(value)


def validate(row):
    for field in ('identity','position_id','group_id','source','mint','feature_schema'):
        if not isinstance(row.get(field),str) or not 0<len(row[field])<=256:raise ValueError('Explicit '+field+' required')
    for field in ('feature_at','known_at'):
        if not number(row.get(field)) or row[field]<0:raise ValueError('Explicit timestamp required')
    features=row.get('features')
    if not isinstance(features,list) or not 1<=len(features)<=64 or any(v is not None and (not number(v) or abs(v)>8) for v in features):raise ValueError('Invalid bounded feature vector')
    if row['source']=='followed_wallet':
        proof=row.get('provenance',{})
        group=proof.get('wallet_cluster_id') if proof.get('wallet_cluster_verified') is True else 'unverified_wallet_cluster'
        if not group or row['group_id']!=group:raise ValueError('Wallet observations require explicit correlation grouping')
    rights=row.get('rights',{})
    if rights.get('storage_verified') is not True or not rights.get('evidence'):raise ValueError('Reviewed storage rights required')
    for field in ('net_pnl_usd','fees_usd','risk_label_at','label_known_at'):
        if row.get(field) is not None and not number(row[field]):raise ValueError('Invalid outcome/provenance number')
    if row.get('risk_label') is not None and (type(row['risk_label']) is not int or row['risk_label'] not in (0,1)):raise ValueError('Invalid risk label')
    if len(canonical(row).encode())>65536:raise ValueError('Observation byte budget exceeded')
    return row


def eligible(row):
    rights=row.get('rights',{});proof=row.get('provenance',{})
    if (rights.get('ml_verified') is not True or rights.get('evaluation_verified') is not True
        or proof.get('verified') is not True or not proof.get('evidence')
        or row.get('training_eligible') is not True or row.get('synthetic') is True
        or row['known_at']>row['feature_at']):return False
    try:require_learning_rights(row)
    except SourceAccessBlocked:return False
    return True


def feature_schema():
    from meme_learning import MODEL_FEATURES, CONTEXT_SCALES
    return digest([list(MODEL_FEATURES),CONTEXT_SCALES])


def tensor(row):
    result=[]
    for value in row['features']:result.extend([0. if value is None else value/8,0. if value is None else 1.])
    return result


class ObservationStore:
    def __init__(self,path,create=False):
        self.path=Path(path)
        if create:
            self.path.parent.mkdir(parents=True,exist_ok=True)
            with self.path.open('xb'):pass
            self.path.chmod(0o600)
            with self.connect(check=False) as db:
                db.execute('PRAGMA application_id='+str(APP_ID))
                db.executescript('''CREATE TABLE observations(id TEXT PRIMARY KEY,position_id TEXT,group_id TEXT,feature_at REAL,known_at REAL,label_known_at REAL,body TEXT);
                  CREATE INDEX historical_neighbors ON observations(feature_at,known_at,label_known_at);
                  CREATE TABLE models(id TEXT PRIMARY KEY,fingerprint TEXT UNIQUE,body TEXT);
                  CREATE TABLE revocations(id TEXT PRIMARY KEY,reason TEXT,recorded REAL);
                  CREATE TABLE evaluations(id TEXT PRIMARY KEY,candidate_id TEXT,body TEXT);
                  CREATE TABLE forecasts(candidate_id TEXT,position_id TEXT,recorded REAL,body TEXT,PRIMARY KEY(candidate_id,position_id));''')
        elif not self.path.is_file():raise ValueError('Explicit new isolated ML cache required')
        with self.connect(readonly=True):pass # rejects unrelated DBs before writes

    @contextmanager
    def connect(self,readonly=False,check=True):
        if not readonly and check:
            used=sum(p.stat().st_size for p in [self.path,Path(str(self.path)+'-journal'),Path(str(self.path)+'-wal')] if p.exists())
            if used+1000000>512000000:raise ValueError('Unified cache planning cap reached; preserve data and review storage')
        db=sqlite3.connect(self.path.resolve().as_uri()+'?mode='+('ro' if readonly else 'rw'),uri=True,timeout=10)
        try:
            if check and db.execute('PRAGMA application_id').fetchone()[0]!=APP_ID:raise ValueError('Not an isolated unified ML cache')
            if readonly:db.execute('PRAGMA query_only=ON')
            else:db.execute('PRAGMA synchronous=FULL')
            with db:yield db
        finally:db.close()

    def add(self,row):
        row=validate(row);key=digest([row['source'],row['identity']]);body=canonical(row)
        with self.connect() as db:
            existing=db.execute('SELECT body FROM observations WHERE id=?',(key,)).fetchone()
            if existing and existing[0]!=body:raise ValueError('Conflicting evidence identity; preserve and investigate')
            db.execute('INSERT OR IGNORE INTO observations VALUES(?,?,?,?,?,?,?)',(key,row['position_id'],row['group_id'],row['feature_at'],row['known_at'],row.get('label_known_at'),body))
        return key

    def revoke(self,source,identity,reason):
        if not reason:raise ValueError('Explicit correction reason required')
        with self.connect() as db:
            db.execute('INSERT INTO revocations VALUES(?,?,?)',(digest([source,identity]),reason,time.time()))

    def observations(self,limit=1024):
        if type(limit) is not int or not 1<=limit<=1024:raise ValueError('Training read budget exceeded')
        with self.connect(readonly=True) as db:
            # Bounded historical replay reservoir plus recent evidence. All rows
            # remain stored; older training lineage is fetched by exact ID.
            count=max(1,limit//2)
            recent=db.execute('SELECT id,body FROM observations WHERE id NOT IN (SELECT id FROM revocations) ORDER BY feature_at DESC,id LIMIT ?',(count,)).fetchall()
            historical=db.execute('SELECT id,body FROM observations WHERE id NOT IN (SELECT id FROM revocations) ORDER BY id LIMIT ?',(limit-count,)).fetchall()
            chosen={row[0]:json.loads(row[1]) for row in recent+historical}
            return sorted(chosen.values(),key=lambda row:(row['feature_at'],row['identity']))

    def by_ids(self,identifiers):
        identifiers=sorted(set(identifiers))
        if len(identifiers)>1024:raise ValueError('Lineage read budget exceeded')
        result=[]
        with self.connect(readonly=True) as db:
            for start in range(0,len(identifiers),128):
                group=identifiers[start:start+128]
                sql='SELECT body FROM observations WHERE id IN ('+','.join('?' for _ in group)+') AND id NOT IN (SELECT id FROM revocations)'
                result.extend(json.loads(row[0]) for row in db.execute(sql,group))
        return result

    def retrieve(self,features,asof,exclude_group,k=8,allow_ids=None,feature_schema_id=None):
        from shadow_ml_models import vector
        vector(features)
        if type(k) is not int or not 1<=k<=8 or not number(asof) or asof<0:raise ValueError('Invalid bounded retrieval')
        args=[asof,asof,asof,exclude_group]
        clause=''
        if allow_ids is not None:
            if len(allow_ids)>512:raise ValueError('Frozen retrieval corpus exceeds budget')
            if not allow_ids:return {'neighbors':[],'score':None,'market_eligible':False}
            identifiers=sorted(allow_ids)
            clause=' AND id IN ('+','.join('?' for _ in identifiers)+')'
            args+=identifiers
        with self.connect(readonly=True) as db:
            rows=[json.loads(row[0]) for row in db.execute('SELECT body FROM observations WHERE feature_at<? AND known_at<=? AND label_known_at<=? AND group_id!=? AND id NOT IN (SELECT id FROM revocations)'+clause+' ORDER BY feature_at DESC,id LIMIT 512',args)]
        neighbors=[]
        for row in rows:
            if feature_schema_id is not None and row['feature_schema']!=feature_schema_id:continue
            if not eligible(row) or row.get('net_pnl_usd') is None or row.get('net_of_costs_verified') is not True:continue
            values=tensor(row)
            if len(values)!=len(features):continue
            distance=sum((a-b)**2 for a,b in zip(features,values))/len(values)
            neighbors.append({'identity':row['identity'],'position_id':row['position_id'],'group_id':row['group_id'],
                              'distance':distance,'net_pnl_usd':row['net_pnl_usd'],'source':row['source'],'provenance':row['provenance']})
        # Correlated evidence contributes at most one neighbor per group.
        result=[];seen=set()
        for row in sorted(neighbors,key=lambda r:(r['distance'],r['identity'])):
            if row['group_id'] in seen:continue
            seen.add(row['group_id']);result.append(row)
            if len(result)==k:break
        return {'neighbors':result,'score':sum(r['net_pnl_usd']>0 for r in result)/len(result) if result else None,
                'independent_human_verified':False,'market_eligible':False}


def import_bot_ledger(store,path,source,rights,provenance,offset=0,limit=128):
    from meme_learning import paper_outcomes, live_outcomes, model_features, _paper_correction
    if source not in ('bot_paper','bot_live'):raise ValueError('Explicit bot ledger type required')
    outcomes=(paper_outcomes if source=='bot_paper' else live_outcomes)(path)[0]
    if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=128:raise ValueError('Invalid import page')
    imported=0;at=time.time()
    for row in outcomes[offset:offset+limit]:
        if row.get('training_eligible',True) is not True or _paper_correction(row) or row.get('entry_data_kind')=='synthetic':continue
        context=row.get('entry_context') or {}
        if not number(row.get('net_pnl_usd')):continue
        identity=row.get('exit_signature')
        if not identity:continue
        position=(str(row['run_id'])+':'+row['mint']+':'+str(row['entry_at'])) if source=='bot_paper' else str(row.get('entry_signature',identity))
        record=dict(identity=identity,position_id=position,group_id=position,source=source,mint=row['mint'],
                    feature_at=row['entry_at'],known_at=context.get('observed_at',at),label_known_at=row['exit_at'],
                    features=list(model_features(context).values()),feature_schema=feature_schema(),net_pnl_usd=row['net_pnl_usd'],fees_usd=row.get('fees_usd'),
                    net_of_costs_verified=provenance.get('net_outcomes_verified') is True,label_kind=row.get('label_kind',source),rights=rights,provenance=provenance,training_eligible=True,
                    original_context=context,missing_data=context.get('missing_data',[]),model_version=context.get('model_version'))
        store.add(record);imported+=1
    return {'imported':imported,'next_offset':offset+limit if offset+limit<len(outcomes) else None,'source_rows':len(outcomes)}


def import_wallet_outcomes(store,outcomes,rights,provenance):
    """Receipt-backed normalized wallet outcomes only; quote P&L is never USD."""
    if len(outcomes)>128:raise ValueError('Wallet import batch exceeded')
    from meme_learning import model_features
    imported=0
    for row in outcomes:
        if row.get('status')!='closed' or row.get('receipt_backed') is not True:continue
        if not row.get('exit_signature') or not row.get('wallet'):continue
        context=row.get('entry_context') or {}
        group=provenance.get('wallet_cluster_id') if provenance.get('wallet_cluster_verified') is True else 'unverified_wallet_cluster'
        usd_verified=row.get('usd_conversion_verified') is True
        record=dict(identity=row['exit_signature'],position_id=row['entry_signature'],group_id=group,
                    source='followed_wallet',mint=row['mint'],feature_at=row['entry_at'],known_at=row['feature_known_at'],
                    label_known_at=row['exit_at'],features=list(model_features(context).values()),feature_schema=feature_schema(),
                    net_pnl_usd=row.get('net_pnl_usd') if usd_verified else None,fees_usd=row.get('fees_usd'),
                    net_of_costs_verified=row.get('net_of_costs_verified') is True and usd_verified,
                    rights=rights,provenance=provenance,training_eligible=True,wallet=row['wallet'],
                    original_context=context,label_kind='receipt_backed_wallet_net',usd_proof=row.get('usd_proof'))
        store.add(record);imported+=1
    return imported


def add_market_context(store,context,*,identity,position_id,group_id,mint,feature_at,known_at,rights,provenance):
    """Bridge existing decision_context/features without inventing outcome labels."""
    from meme_learning import model_features
    return store.add(dict(identity=identity,position_id=position_id,group_id=group_id,source='market',mint=mint,
                          feature_at=feature_at,known_at=known_at,label_known_at=None,features=list(model_features(context).values()),feature_schema=feature_schema(),
                          net_pnl_usd=None,fees_usd=None,rights=rights,provenance=provenance,training_eligible=True,
                          original_context=context,missing_data=context.get('missing_data',[])))


def import_position_quotes(store,paths,rights,provenance):
    """Reuse the existing quote loader; original quotes/net outcomes stay untouched."""
    from neural_exits import load_groups
    from meme_learning import model_features
    completed={r['position_id']:r for r in store.observations() if r.get('net_pnl_usd') is not None}
    count=0;now=time.time()
    for (path,position),quotes in load_groups(paths).items():
        target=completed.get(position)
        if target is None:continue
        for at,context,_ in quotes:
            if count>=1024:return {'imported':count,'status':'batch_limit; use reviewed source pages'}
            if at>=target['label_known_at']:continue
            store.add(dict(identity='quote:'+digest([path,position,at]),position_id=position,group_id=target['group_id'],
                           source=target['source'],mint=target['mint'],feature_at=at,
                           known_at=at if provenance.get('quote_timestamp_verified') is True else now,
                           label_known_at=target['label_known_at'],features=list(model_features(context).values()),feature_schema=feature_schema(),
                           net_pnl_usd=target['net_pnl_usd'],net_of_costs_verified=target.get('net_of_costs_verified') is True,
                           fees_usd=target.get('fees_usd'),rights=rights,provenance=provenance,training_eligible=True,
                           original_context=context,label_kind='completed_position_net; quote features, not counterfactual fills'))
            count+=1
    return {'imported':count,'status':'complete'}
