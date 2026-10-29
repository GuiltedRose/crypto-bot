"""Paired paper experiments: one durable observation stream, three portfolios.

No wallet, signing or submission imports. Recorded observations are not refetched
for individual profiles. Idempotent fan-out recovers partial processing safely.
"""
from dataclasses import asdict,replace
import fcntl
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from meme_dip import DipRules
from meme_evidence import number
from paper_runtime import PaperSession
from meme_scorecard import scorecard

from meme_profiles import profiles


def volatility(snapshot):
    try:
        rows=snapshot['candle_history']['candles'][-12:]
        if not rows: return None
        ranges=[]
        for row in rows:
            o=number(row['open'],1e-15);h=number(row['high'],1e-15);l=number(row['low'],1e-15)
            if h<l: return None
            ranges.append((h-l)/o)
        return max(ranges)
    except (TypeError,KeyError,ValueError): return None


def band(value):
    if value is None: return 'unknown'
    return '<=30%' if value<=.30 else '30-45%' if value<=.45 else '45-60%' if value<=.60 else '>60%'


class Comparison:
    def __init__(self,directory,variants=None,balance=None,native_sol=None,sol_price=None):
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True)
        if any((self.directory/p).exists() for p in ('meme_live.sqlite','paper.sqlite','mode.json')):
            raise ValueError('Use an isolated comparison directory, not existing live/paper state')
        self.lock=(self.directory/'comparison.lock').open('a')
        try: fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.lock.close();raise ValueError('Another comparison process is active') from None
        self.sessions={};self.db=None
        try:
            manifest_path=self.directory/'manifest.json'
            if manifest_path.exists():
                manifest=json.loads(manifest_path.read_text())
                expected=manifest['initial']
                stored_profiles={k:asdict(DipRules(**v)) for k,v in manifest['profiles'].items()}
                if variants is not None and {k:asdict(v) for k,v in variants.items()}!=stored_profiles:
                    raise ValueError('Comparison profiles changed; choose a new experiment directory')
                for key,value in [('balance',balance),('native_sol',native_sol),('sol_price',sol_price)]:
                    if value is not None and value!=expected[key]: raise ValueError('Comparison starting inventory changed')
                variants={k:DipRules(**v) for k,v in manifest['profiles'].items()}
                balance,native_sol,sol_price=(expected[k] for k in ('balance','native_sol','sol_price'))
            else:
                if variants is None or balance is None: raise ValueError('New comparison needs explicit profiles and inventory')
                # Validate before recording the experiment manifest.
                from meme_dip import PaperDipTrader
                for rule in variants.values(): PaperDipTrader(balance,rule,native_sol,sol_price)
                manifest={'schema':1,'created_at':time.time(),'initial':{'balance':balance,'native_sol':native_sol,'sol_price':sol_price},
                          'profiles':{k:asdict(v) for k,v in variants.items()},'live_qualified':False}
                temp=self.directory/'manifest.tmp';temp.write_text(json.dumps(manifest,indent=2,allow_nan=False)+'\n');temp.replace(manifest_path)
            self.manifest=manifest
            self.db=sqlite3.connect(self.directory/'comparison.sqlite')
            self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=FULL')
            self.db.executescript('''CREATE TABLE IF NOT EXISTS observations(id INTEGER PRIMARY KEY,fingerprint TEXT UNIQUE,snapshot TEXT,completed INTEGER DEFAULT 0);
              CREATE TABLE IF NOT EXISTS samples(observation INTEGER,profile TEXT,event TEXT,scorecard TEXT,marked REAL,liquidation REAL,PRIMARY KEY(observation,profile));
              CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT);''')
            for name,rule in variants.items():
                self.sessions[name]=PaperSession(self.directory/name,balance,rule,native_sol,sol_price)
            self.recover()
        except Exception:
            self.close();raise

    def recover(self):
        for obs,raw in self.db.execute('SELECT id,snapshot FROM observations WHERE completed=0 ORDER BY id').fetchall():
            self._fanout(obs,json.loads(raw))

    def process(self,snapshot):
        self.recover()
        raw=json.dumps(snapshot,sort_keys=True,allow_nan=False);digest=hashlib.sha256(raw.encode()).hexdigest()
        with self.db: self.db.execute('INSERT OR IGNORE INTO observations(fingerprint,snapshot) VALUES(?,?)',(digest,raw))
        obs,done=self.db.execute('SELECT id,completed FROM observations WHERE fingerprint=?',(digest,)).fetchone()
        if done: return None
        return self._fanout(obs,snapshot)

    def _fanout(self,obs,snapshot):
        events={}
        for name,session in self.sessions.items():
            if self.db.execute('SELECT 1 FROM samples WHERE observation=? AND profile=?',(obs,name)).fetchone(): continue
            event=session.process(snapshot)
            if event is None:
                fingerprint=hashlib.sha256(json.dumps(snapshot,sort_keys=True,allow_nan=False).encode()).hexdigest()
                event=json.loads(session.db.execute('SELECT event FROM paper_events WHERE run_id=? AND fingerprint=?',(session.run_id,fingerprint)).fetchone()[0])
            report=session.report();position=session.trader.position
            liquidation=report['marked_equity_usd']
            if position:
                marked=position['quantity']*position.get('last_price_usd',position['entry_price'])
                fee=position.get('entry_fee_per_side',session.trader.rules.fee_per_side)
                proceeds=max(0,marked*(1-session.trader.rules.slippage_per_side)*(1-fee)-position['exit_fixed_cost'])
                exit_network=None
                # Use the latest recorded cost assumption for THIS held mint.
                for raw, in session.db.execute('SELECT snapshot FROM paper_events WHERE run_id=? ORDER BY sequence DESC',(session.run_id,)):
                    quote=json.loads(raw)
                    if isinstance(quote,dict) and quote.get('mint')==position['mint']:
                        execution=quote.get('execution',{})
                        if isinstance(execution,dict) and execution:
                            try: exit_network=(number(execution['network_fee_sol'])+(0 if position.get('rent_is_refundable') else number(execution['exit_rent_sol'])))*(session.trader.sol_price_usd or 0)
                            except (KeyError,ValueError,TypeError): pass
                        break
                liquidation=report['marked_equity_usd']-marked+proceeds
                if session.trader.native_sol is not None:
                    liquidation=None if exit_network is None else liquidation-exit_network
            assessment=None
            if isinstance(snapshot,dict):
                assessment=scorecard(snapshot,snapshot.get('observed_at',0),session.trader.rules)
            with self.db:
                self.db.execute('INSERT INTO samples VALUES(?,?,?,?,?,?)',
                    (obs,name,json.dumps(event),json.dumps(assessment),report['marked_equity_usd'],liquidation))
            events[name]=event
        with self.db: self.db.execute('UPDATE observations SET completed=1 WHERE id=?',(obs,))
        return events

    def metrics(self,name):
        session=self.sessions[name];report=session.report();initial=self.manifest['initial']
        start=initial['balance']+(initial['native_sol'] or 0)*(initial['sol_price'] or 0)
        peak=start;drawdown=0.;drawdown_fraction=0.;liquidation=start;cohorts={};project_results={};open_band=None;last_buy=None
        trades=[];modeled_slippage=0.;entry_attempts=0;rejected=0;blocked_exits=0
        for event_raw,snapshot_raw,marked,liquidation in self.db.execute('SELECT s.event,o.snapshot,s.marked,s.liquidation FROM samples s JOIN observations o ON o.id=s.observation WHERE profile=? ORDER BY observation',(name,)):
            event=json.loads(event_raw);snapshot=json.loads(snapshot_raw);action=event['action']
            peak=max(peak,marked);drawdown=max(drawdown,peak-marked)
            drawdown_fraction=max(drawdown_fraction,(peak-marked)/peak if peak else 0)
            bucket=band(volatility(snapshot));group=cohorts.setdefault(bucket,{'observations':0,'entries':0,'volatility_rejections':0,'closed_trades':0,'realized_pnl_usd':0.})
            group['observations']+=1
            reasons=event.get('reasons',[])+[event.get('reason','')]
            if 'extreme candle range' in reasons: group['volatility_rejections']+=1
            if action in ('paper_buy','reject','watch','invalid_snapshot'): entry_attempts+=1
            if action in ('reject','invalid_snapshot'): rejected+=1
            if action=='hold' and any('blocked' in r or 'insufficient' in r for r in reasons): blocked_exits+=1
            if action=='paper_buy':
                project=project_results.setdefault(event['mint'],{'entries':0,'closed_trades':0,'realized_pnl_usd':0.,'entry_profiles':{}})
                project['entries']+=1
                chosen=event.get('entry_profile') or name
                project['entry_profiles'][chosen]=project['entry_profiles'].get(chosen,0)+1
                group['entries']+=1;open_band=bucket;last_buy=event
                modeled_slippage+=event['quantity']*(event['entry_price']-snapshot['price_usd'])
            if action=='paper_sell':
                pnl=event['pnl_usd'];trades.append(pnl)
                entry_group=cohorts.setdefault(open_band or 'unknown',{'observations':0,'entries':0,'volatility_rejections':0,'closed_trades':0,'realized_pnl_usd':0.})
                entry_group['closed_trades']+=1;entry_group['realized_pnl_usd']+=pnl;open_band=None
                if last_buy:
                    project=project_results[last_buy['mint']];project['closed_trades']+=1;project['realized_pnl_usd']+=pnl
                if isinstance(snapshot,dict) and last_buy:
                    modeled_slippage+=last_buy['quantity']*snapshot['price_usd']*session.trader.rules.slippage_per_side
                last_buy=None
        return {'run_id':session.run_id,'observations':sum(v['observations'] for v in cohorts.values()),
            'entries':report['event_counts'].get('paper_buy',0),'closed_trades':len(trades),'wins':sum(p>0 for p in trades),
            'realized_pnl_usd':report['realized_pnl_usd'],'marked_equity_usd':report['marked_equity_usd'],
            'estimated_liquidation_equity_usd':liquidation,'estimated_liquidation_pnl_usd':None if liquidation is None else liquidation-start,
            'max_marked_drawdown_usd':drawdown,'max_marked_drawdown_fraction':drawdown_fraction,
            'entry_evaluations':entry_attempts,'rejected_evaluations':rejected,'blocked_exit_observations':blocked_exits,
            'modeled_slippage_usd':modeled_slippage,'assumed_slippage_per_side':session.trader.rules.slippage_per_side,
            'observed_execution_failures':None,'position':report['position'],'drawdown_halted':report['drawdown_halted'],
            'event_counts':report['event_counts'],'volatility_cohorts':cohorts,'projects':project_results,'execution_assumptions':report['latest_execution_assumptions'],
            'data_kinds':report['data_kinds']}

    def publish(self,source_status=None):
        result={'mode':'paired_paper_only','created_at':self.manifest['created_at'],'published_at':time.time(),
            'shared_observations':self.db.execute('SELECT COUNT(*) FROM observations WHERE completed=1').fetchone()[0],
            'profiles':{name:self.metrics(name) for name in self.sessions},'source_status':source_status or {},'live_qualified':False,
            'limitations':['Modeled fills, not actual execution or measured slippage','No automatic winner promotion',
              'Open positions are included only as marked/estimated liquidation values','Three entry tolerances vary together; causal effects are not isolated',
              'Three fixed exits are unchanged; trailing/volatility exits require a separate experiment']}
        bounds=self.db.execute("SELECT MIN(json_extract(snapshot,'$.observed_at')),MAX(json_extract(snapshot,'$.observed_at')) FROM observations WHERE completed=1 AND json_type(snapshot,'$.observed_at') IN ('integer','real')").fetchone()
        result['observation_time_range']={'first':bounds[0],'last':bounds[1]}
        hours=(bounds[1]-bounds[0])/3600 if bounds[0] is not None and bounds[1]>bounds[0] else None
        for name,metrics in result['profiles'].items():
            metrics['entries_per_hour_of_observations']=metrics['entries']/hours if hours else None
            metrics['entry_tolerances']={k:self.manifest['profiles'][name][k] for k in ('max_candle_range_fraction','min_buy_volume_usd','trend_pullback_max_fraction')}
        temp=self.directory/'report.tmp';temp.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n');temp.replace(self.directory/'report.json')
        return result

    def close(self):
        for session in self.sessions.values(): session.close()
        if self.db is not None: self.db.close()
        self.lock.close()
