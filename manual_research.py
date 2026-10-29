"""Bounded, read-only archive of user-wallet activity for offline research.

Historical records are never fresh entry signals, never bot P&L, and never
validated profit labels. Known locally journaled bot signatures are excluded
from the manual-only subset while remaining in the complete research archive. Missing receipts stay pending.
"""
import json
from pathlib import Path
import sqlite3
import time
from wallet_watch import classify_transaction
from pump_narratives import ROOT,valid_mint


def verify_profile(wallet,username,fetch):
    if not valid_mint(wallet): raise ValueError('Valid public wallet required')
    profile=fetch(ROOT+'/users/'+wallet)
    if profile.get('address')!=wallet or str(profile.get('username','')).casefold()!=username.casefold():
        raise ValueError('Public wallet and requested profile do not match')
    return {'wallet':wallet,'username':profile['username'],'profile_verified':True,
            'source_url':ROOT+'/users/'+wallet,'profile_url':'https://pump.fun/profile/'+profile['username']+'?tab=closed'}


def bot_signatures(path):
    path=Path(path)
    if not path.exists(): return set()
    db=sqlite3.connect('file:'+str(path.resolve())+'?mode=ro',uri=True)
    try: return {row[0] for row in db.execute('SELECT signature FROM orders')}
    finally: db.close()


class ManualResearch:
    def __init__(self,directory,identity,since):
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True)
        self.identity=identity;self.since=since
        self.db=sqlite3.connect(self.directory/'activity.sqlite')
        self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT);
          CREATE TABLE IF NOT EXISTS transactions(signature TEXT PRIMARY KEY,blocktime REAL,status TEXT,attempts INTEGER DEFAULT 0,raw TEXT,event TEXT,origin TEXT);''')
        binding={'wallet':identity['wallet'],'since':since}
        row=self.db.execute("SELECT value FROM metadata WHERE key='binding'").fetchone()
        if row and json.loads(row[0])!=binding:
            self.db.close();raise ValueError('Research database belongs to a different wallet/date window')
        with self.db: self.db.execute("INSERT OR REPLACE INTO metadata VALUES('binding',?)",(json.dumps(binding),))

    def meta(self,key,default=None):
        row=self.db.execute('SELECT value FROM metadata WHERE key=?',(key,)).fetchone()
        return json.loads(row[0]) if row else default

    def ingest(self,tx,signature,known_bot=()):
        if tx is None: return False
        observed=tx.get('blockTime')
        if type(observed) not in (int,float): return False
        if tx.get('transaction',{}).get('signatures',[None])[0]!=signature: raise ValueError('Research receipt signature mismatch')
        origin='bot_order' if signature in known_bot else 'user_wallet_activity'
        # Decode at the historical receipt time, not "now". The resulting
        # original timestamps are preserved and never sent to the signal cache.
        event=classify_transaction(tx,self.identity['wallet'],signature,observed+1)
        event.update(historical=True,seen_at=time.time(),automatic_trade_approval=False,profit_label_verified=False)
        status='outside_period' if observed<self.since else 'recorded'
        with self.db:
            self.db.execute('INSERT INTO transactions(signature,blocktime,status,attempts,raw,event,origin) VALUES(?,?,?,1,?,?,?) ON CONFLICT(signature) DO UPDATE SET blocktime=excluded.blocktime,status=excluded.status,raw=excluded.raw,event=excluded.event,origin=excluded.origin',
                (signature,observed,status,json.dumps(tx),json.dumps(event),origin))
        return True

    def collect(self,rpc,known_bot=(),budget=5):
        if type(budget) is not int or not 1<=budget<=8: raise ValueError('Research receipt budget must be 1 through 8')
        # Head refresh observes new activity; one older page continues backfill.
        params={'limit':20,'commitment':'confirmed'}
        pages=[rpc.call('getSignaturesForAddress',[self.identity['wallet'],params])]
        if not self.meta('reached_since',False) and not self.meta('available_history_exhausted',False):
            older={'limit':50,'commitment':'confirmed'};cursor=self.meta('cursor')
            if cursor: older['before']=cursor
            page=rpc.call('getSignaturesForAddress',[self.identity['wallet'],older]);pages.append(page)
            with self.db:
                if page:
                    self.db.execute("INSERT OR REPLACE INTO metadata VALUES('cursor',?)",(json.dumps(page[-1]['signature']),))
                    if any(type(r.get('blockTime')) in (int,float) and r['blockTime']<self.since for r in page):
                        self.db.execute("INSERT OR REPLACE INTO metadata VALUES('reached_since','true')")
                else: self.db.execute("INSERT OR REPLACE INTO metadata VALUES('available_history_exhausted','true')")
        with self.db:
            for page in pages:
                for row in page:
                    stamp=row.get('blockTime');status='outside_period' if type(stamp) in (int,float) and stamp<self.since else 'pending'
                    self.db.execute('INSERT OR IGNORE INTO transactions(signature,blocktime,status) VALUES(?,?,?)',(row['signature'],stamp,status))
            # Reclassify known bot signatures when an uncertain order later
            # becomes known; this changes research provenance, never live state.
            for signature in known_bot: self.db.execute("UPDATE transactions SET origin='bot_order' WHERE signature=?",(signature,))
        pending=self.db.execute("SELECT signature FROM transactions WHERE status='pending' ORDER BY attempts ASC,blocktime DESC LIMIT ?",(budget,)).fetchall()
        errors=0
        for signature, in pending:
            with self.db: self.db.execute('UPDATE transactions SET attempts=attempts+1 WHERE signature=?',(signature,))
            try:
                tx=rpc.call('getTransaction',[signature,{'encoding':'jsonParsed','commitment':'confirmed','maxSupportedTransactionVersion':1}])
                if not self.ingest(tx,signature,known_bot): errors+=1
            except Exception: errors+=1
        result=self.report();result['receipt_requests_this_cycle']=len(pending);result['unavailable_receipts_this_cycle']=errors
        temp=self.directory/'summary.tmp';temp.write_text(json.dumps(result,indent=2)+'\n');temp.replace(self.directory/'summary.json')
        return result

    def report(self):
        rows=self.db.execute('SELECT status,event,origin FROM transactions').fetchall()
        trades=buys=sells=bot=unknown=pending=bot_trades=0;mints=set()
        for status,event,origin in rows:
            if status=='pending': pending+=1;continue
            if status!='recorded': continue
            if origin=='bot_order': bot+=1
            item=json.loads(event)
            if item['classification']!='pump_trade_candidate':
                if origin!='bot_order': unknown+=1
                continue
            for signal in item['signals']:
                if origin=='bot_order': bot_trades+=1
                else: trades+=1;buys+=signal['action']=='buy';sells+=signal['action']=='sell'
                mints.add(signal['mint'])
        return {**self.identity,'published_at':time.time(),'since':self.since,'transactions_archived':sum(r[0]=='recorded' for r in rows),
            'known_pump_trades_nonbot':trades,'buys':buys,'sells':sells,'distinct_trade_mints':len(mints),
            'known_bot_orders_archived':bot,'known_pump_trades_bot':bot_trades,'research_trades_total':trades+bot_trades,
            'known_bot_orders_excluded_from_manual_subset':bot,'unclassified_user_transactions':unknown,'pending_receipts':pending,
            'requested_period_traversed':self.meta('reached_since',False) and pending==0,
            'available_history_exhausted':self.meta('available_history_exhausted',False),
            'complete_archive_independently_verified':False,'historical_market_features_available':False,'profit_labels_verified':False,
            'use':'offline_research_only','limitations':'Bounded provider history; transfers and ambiguous routes remain unclassified. Net wallet deltas may include rent and are not verified profit. Historical candles/liquidity not backfilled.'}

    def close(self): self.db.close()
