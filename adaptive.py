"""Bounded, auditable strategy selection. No signing or wallet configuration.

Chronological training selects ONE challenger; validation and a sealed fresh
holdout can only accept/reject it. Live risk settings are never optimized.
"""
import hashlib
import json
import math
import random
import sqlite3
import statistics
from dataclasses import asdict
from pathlib import Path
from evaluate import Costs,_window
from strategy import Candle,DonchianRules,validate,DONCHIAN_ID

from strategies import PROFILES

TRAIN=672
VALIDATION=336
HOLDOUT=672
WARMUP=201
MIN_BARS=WARMUP+TRAIN+VALIDATION+HOLDOUT
MIN_TRADES=20


def profile_id(rules):
    for key,value in PROFILES.items():
        if rules==value:return key
    raise ValueError('Unapproved adaptive parameters')


def rules_for(strategy_id):
    return PROFILES.get(strategy_id)


def active_profile(mint,path='state/adaptation.sqlite'):
    """Unavailable adaptive state defaults to the original fixed strategy."""
    if not Path(path).exists():return DONCHIAN_ID,PROFILES[DONCHIAN_ID]
    try:
        db=sqlite3.connect('file:'+str(Path(path).resolve())+'?mode=ro',uri=True,timeout=1)
        try:row=db.execute('SELECT profile FROM active WHERE mint=?',(mint,)).fetchone()
        finally:db.close()
        if row and row[0] in PROFILES:return row[0],PROFILES[row[0]]
    except sqlite3.Error:
        pass
    return DONCHIAN_ID,PROFILES[DONCHIAN_ID]


def window(candles,start,end,costs,rules):
    result=_window(candles,start,end,costs,rules,enforce_cost_gate=True)
    # Charge liquidation of unfinished inventory, never hide an open loser.
    result['liquidation_adjusted_pnl']=result['net_pnl_usd']+(result['estimated_open_liquidation_pnl_usd'] or 0)
    return result


def daily_pnl(result,candles,start,end):
    days={candles[i].timestamp//86400:0. for i in range(start,end)}
    for trade in result['completed_trades']:
        days[candles[trade['exit_bar']].timestamp//86400]+=trade['net_pnl_usd']
    days[candles[end-1].timestamp//86400]+=result['estimated_open_liquidation_pnl_usd'] or 0
    return days


def improvement_bound(challenger,incumbent,candles,start,end,seed=0):
    """99% lower moving-seven-day-block bootstrap bound on mean daily PnL.

    A robustness filter, not a proof of statistical independence or future edge.
    Zero-trade days remain in the comparison. Fixed seed makes audits repeatable.
    """
    a=daily_pnl(challenger,candles,start,end)
    b=daily_pnl(incumbent,candles,start,end)
    values=[a[day]-b[day] for day in sorted(a)]
    if len(values)<28:return -math.inf
    rng=random.Random(seed)
    means=[]
    for _ in range(2000):
        sample=[]
        while len(sample)<len(values):
            index=rng.randrange(len(values)-6)
            sample.extend(values[index:index+7])
        means.append(statistics.mean(sample[:len(values)]))
    return sorted(means)[19]


def experiment(candles,costs,incumbent=DONCHIAN_ID):
    validate(candles,candles[-1].timestamp+3600 if candles else 0)
    if len(candles)<MIN_BARS:
        return {'accepted':False,'reason':'insufficient history','bars':len(candles),'required_bars':MIN_BARS}
    if incumbent not in PROFILES:raise ValueError('Unknown incumbent')
    hold_start=len(candles)-HOLDOUT
    val_start=hold_start-VALIDATION
    from extra_strategies import data_ready
    eligible={key:rules for key,rules in PROFILES.items() if key==incumbent or data_ready(rules,candles)[0]}
    excluded={key:data_ready(rules,candles)[1] for key,rules in PROFILES.items() if key not in eligible}
    training={key:window(candles,WARMUP,val_start,costs,rules) for key,rules in eligible.items()}
    # Deterministic training-only selection. No fallback challenger is tried on
    # validation/holdout after rejection; that would reuse the test for tuning.
    challenger=max(training,key=lambda key:(training[key]['liquidation_adjusted_pnl']-
                    training[key]['max_closed_trade_drawdown_usd'],key==incumbent,key))
    report={'accepted':False,'incumbent':incumbent,'challenger':challenger,
        'excluded_profiles':excluded,'cost_assumptions':asdict(costs),'train_end':candles[val_start-1].timestamp,
        'holdout_start':candles[hold_start].timestamp,'holdout_end':candles[-1].timestamp,
        'training_scores':{key:r['liquidation_adjusted_pnl']-r['max_closed_trade_drawdown_usd'] for key,r in training.items()},
        'limitations':'Fixed-notional per-token simulation, not a multi-token Kelly portfolio or execution guarantee. Block bootstrap is a robustness filter, not a PBO calculation.'}
    if challenger==incumbent:
        return {**report,'reason':'incumbent wins training selection'}
    c=window(candles,val_start,hold_start,costs,PROFILES[challenger])
    b=window(candles,val_start,hold_start,costs,PROFILES[incumbent])
    report['validation']={'challenger':c,'incumbent':b}
    if c['trades']<MIN_TRADES or c['liquidation_adjusted_pnl']<=max(0,b['liquidation_adjusted_pnl']):
        return {**report,'reason':'validation sample/after-cost improvement insufficient'}
    c=window(candles,hold_start,len(candles),costs,PROFILES[challenger])
    b=window(candles,hold_start,len(candles),costs,PROFILES[incumbent])
    lower=improvement_bound(c,b,candles,hold_start,len(candles))
    stress=Costs(costs.notional_usd,min(.99,costs.fee_per_side*2),min(.99,costs.slippage_per_side*2),costs.fixed_per_side_usd*2)
    stressed=window(candles,hold_start,len(candles),stress,PROFILES[challenger])
    report['holdout']={'challenger':c,'incumbent':b,'daily_improvement_lower_bound':lower if math.isfinite(lower) else None,
                       'double_cost_liquidation_pnl':stressed['liquidation_adjusted_pnl']}
    accepted=(c['trades']>=MIN_TRADES and c['liquidation_adjusted_pnl']>max(0,b['liquidation_adjusted_pnl']) and
              lower>0 and stressed['liquidation_adjusted_pnl']>0 and
              c['max_closed_trade_drawdown_usd']<=max(b['max_closed_trade_drawdown_usd'],costs.notional_usd*.1))
    return {**report,'accepted':accepted,'reason':'all evidence gates passed' if accepted else 'holdout/cost-stress/drawdown evidence insufficient'}


class AdaptationStore:
    def __init__(self,path='state/adaptation.sqlite'):
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(path,timeout=5)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS candles(mint TEXT, timestamp INTEGER, bar TEXT, PRIMARY KEY(mint,timestamp));
          CREATE TABLE IF NOT EXISTS experiments(id INTEGER PRIMARY KEY,mint TEXT,end INTEGER,report TEXT,UNIQUE(mint,end));
          CREATE TABLE IF NOT EXISTS active(mint TEXT PRIMARY KEY,profile TEXT,activated INTEGER,experiment INTEGER);
          CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,at INTEGER,mint TEXT,action TEXT,detail TEXT);
        ''')
        columns={row[1] for row in self.db.execute('PRAGMA table_info(active)')}
        with self.db:
            if 'previous_profile' not in columns:
                self.db.execute("ALTER TABLE active ADD COLUMN previous_profile TEXT DEFAULT '"+DONCHIAN_ID+"'")
            if 'rolled_back' not in columns:
                self.db.execute('ALTER TABLE active ADD COLUMN rolled_back INTEGER DEFAULT 0')
            self.db.execute('CREATE TABLE IF NOT EXISTS backfills(mint TEXT PRIMARY KEY,attempted INTEGER,detail TEXT)')

    def archive(self,mint,candles,now):
        validate(candles,now)
        with self.db:
            for bar in candles:
                # Keep the first observed completed candle. Repeated provider
                # revisions cannot rewrite a sealed experiment's observations.
                self.db.execute('INSERT OR IGNORE INTO candles VALUES(?,?,?)',(mint,bar.timestamp,json.dumps(asdict(bar))))
        rows=self.db.execute('SELECT bar FROM (SELECT timestamp,bar FROM candles WHERE mint=? ORDER BY timestamp DESC LIMIT 8760) ORDER BY timestamp',(mint,)).fetchall()
        result=[Candle(**json.loads(row[0])) for row in rows]
        start=0
        for i,(a,b) in enumerate(zip(result,result[1:])):
            if b.timestamp-a.timestamp!=3600:start=i+1
        return result[start:]

    def research(self,mint,candles,costs):
        if len(candles)<MIN_BARS:return experiment(candles,costs)
        end=candles[-1].timestamp
        previous=self.db.execute('SELECT MAX(end) FROM experiments WHERE mint=?',(mint,)).fetchone()[0]
        if previous is not None and end-previous<HOLDOUT*3600:
            return {'accepted':False,'reason':'waiting for fresh non-overlapping holdout','next_eligible_end':previous+HOLDOUT*3600}
        incumbent=self.db.execute('SELECT profile FROM active WHERE mint=?',(mint,)).fetchone()
        report=experiment(candles,costs,incumbent[0] if incumbent else DONCHIAN_ID)
        report['data_sha256']=hashlib.sha256(json.dumps([asdict(c) for c in candles],sort_keys=True).encode()).hexdigest()
        with self.db:self.db.execute('INSERT INTO experiments(mint,end,report) VALUES(?,?,?)',(mint,end,json.dumps(report,allow_nan=False)))
        return report

    def deploy_or_rollback(self,state,now):
        """Read-only live state input. Never clear halt flags or mutate positions."""
        with self.db:
            for mint,profile,activated,experiment_id,previous,rolled_back in self.db.execute('SELECT mint,profile,activated,experiment,previous_profile,rolled_back FROM active').fetchall():
                if rolled_back or experiment_id is None:continue
                trades=[t for t in state.get('closed_trades',[]) if t.get('mint')==mint and t['strategy_id']==profile and t['closed_at']>=activated]
                if ((len(trades)>=2 and all(t['net_pnl_usd']<0 for t in trades[-2:])) or
                    (len(trades)>=5 and sum(t['net_pnl_usd'] for t in trades[-20:])<=0)):
                    restored=previous if previous in PROFILES else DONCHIAN_ID
                    self.db.execute('UPDATE active SET profile=?,activated=?,rolled_back=1 WHERE mint=?',(restored,now,mint))
                    self.db.execute('INSERT INTO events(at,mint,action,detail) VALUES(?,?,?,?)',(now,mint,'rollback','restored '+restored+' after live net deterioration; halt flags unchanged'))
            if state.get('pending') or state.get('positions') or state.get('position') or state.get('halted') or state.get('review_halted'):
                return
            for mint, in self.db.execute('SELECT DISTINCT mint FROM experiments').fetchall():
                row=self.db.execute('SELECT id,report FROM experiments WHERE mint=? ORDER BY end DESC LIMIT 1',(mint,)).fetchone()
                report=json.loads(row[1])
                if not report.get('accepted'):continue
                if not 3600<=now-report.get('holdout_end',0)<=48*3600:continue
                current=self.db.execute('SELECT profile,experiment FROM active WHERE mint=?',(mint,)).fetchone()
                if current and current[1]>=row[0]:continue  # rolled-back experiments cannot redeploy
                if (current[0] if current else DONCHIAN_ID)!=report['incumbent']:continue
                if report['challenger'] not in PROFILES:raise ValueError('Unapproved challenger')
                self.db.execute('INSERT OR REPLACE INTO active(mint,profile,activated,experiment,previous_profile,rolled_back) VALUES(?,?,?,?,?,0)',
                                (mint,report['challenger'],now,row[0],report['incumbent']))
                self.db.execute('INSERT INTO events(at,mint,action,detail) VALUES(?,?,?,?)',(now,mint,'activate',str(row[0])))
