"""Durable paper-only session ledger. Never imports wallet or transaction code."""
from dataclasses import asdict, replace
import fcntl
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import uuid
from meme_dip import DipRules, PaperDipTrader


SCHEMA = 1


def encode_state(trader):
    return {'schema': SCHEMA, 'rules': asdict(trader.rules), 'cash': trader.cash,
            'native_sol': trader.native_sol, 'sol_price_usd': trader.sol_price_usd,
            'peak': trader.peak, 'position': trader.position, 'halted': trader.halted,
            'last_timestamp': trader.last_timestamp,
            'account_rent':[[list(key),value] for key,value in trader.account_rent.items()],
            'last_entry_bars':[[list(key),value] for key,value in trader.last_entry_bars.items()],
            'armed': [[list(key), value] for key, value in trader.armed.items()],
            'last_seen': [[list(key), value] for key, value in trader.last_seen.items()]}


def decode_state(state):
    if state['schema'] != SCHEMA:
        raise ValueError('Unsupported paper checkpoint schema')
    trader = PaperDipTrader(max(.01, state['cash']), DipRules(**state['rules']),
                            state['native_sol'], state['sol_price_usd'])
    for field in ('cash', 'peak', 'position', 'halted', 'last_timestamp'):
        setattr(trader, field, state[field])
    trader.account_rent={tuple(key):value for key,value in state.get('account_rent',[])}
    trader.last_entry_bars={tuple(key):value for key,value in state.get('last_entry_bars',[])}
    trader.armed = {tuple(key): value for key, value in state['armed']}
    trader.last_seen = {tuple(key): value for key, value in state['last_seen']}
    return trader


class PaperSession:
    def __init__(self, directory, balance=None, rules=None, native_sol=None, sol_price_usd=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = (self.directory/'paper.lock').open('a')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock.close()
            raise ValueError('Another paper process holds this session lock') from None
        self.db = sqlite3.connect(self.directory/'paper.sqlite')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS paper_runs(
                id TEXT PRIMARY KEY, created REAL, archived REAL, initial TEXT, checkpoint TEXT);
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_paper_run ON paper_runs((1)) WHERE archived IS NULL;
            CREATE TABLE IF NOT EXISTS paper_source_cursors(run_id TEXT,source TEXT,cursor INTEGER,PRIMARY KEY(run_id,source));
            CREATE TABLE IF NOT EXISTS paper_events(
                sequence INTEGER PRIMARY KEY, run_id TEXT, fingerprint TEXT,
                observed REAL, event TEXT, snapshot TEXT, UNIQUE(run_id,fingerprint));
        ''')
        row = self.db.execute('SELECT id,initial,checkpoint FROM paper_runs WHERE archived IS NULL').fetchone()
        try:
            if row:
                self.run_id, initial, checkpoint = row
                self.initial = json.loads(initial)
                self.trader = decode_state(json.loads(checkpoint))
                # Never silently relabel an existing experiment with new settings.
                requested = {'balance': balance, 'native_sol': native_sol, 'sol_price_usd': sol_price_usd}
                if any(value is not None and value != self.initial[key] for key, value in requested.items()):
                    raise ValueError('Starting inventory changed; use reset for a new paper run')
                if rules is not None and asdict(rules) != asdict(self.trader.rules):
                    raise ValueError('Rules changed; use reset for a new paper run')
            else:
                if balance is None:
                    raise ValueError('First paper run requires an explicit --balance')
                self._create(balance, rules or DipRules(), native_sol, sol_price_usd)
        except Exception:
            self.close()
            raise

    def _create(self, balance, rules, native_sol, sol_price_usd):
        trader = PaperDipTrader(balance, rules, native_sol, sol_price_usd)
        initial = {'balance': balance, 'native_sol': native_sol, 'sol_price_usd': sol_price_usd}
        run_id = str(uuid.uuid4())
        self.db.execute('INSERT INTO paper_runs VALUES(?,?,?,?,?)',
                        (run_id, time.time(), None, json.dumps(initial), json.dumps(encode_state(trader), allow_nan=False)))
        self.run_id, self.initial, self.trader = run_id, initial, trader
        self.db.commit()

    def reset(self, balance, rules=None, native_sol=None, sol_price_usd=None):
        # Validate the replacement before archiving anything.
        replacement = PaperDipTrader(balance, rules or self.trader.rules, native_sol, sol_price_usd)
        initial = {'balance': balance, 'native_sol': native_sol, 'sol_price_usd': sol_price_usd}
        run_id = str(uuid.uuid4())
        with self.db:
            self.db.execute('UPDATE paper_runs SET archived=? WHERE id=?', (time.time(), self.run_id))
            self.db.execute('INSERT INTO paper_runs VALUES(?,?,?,?,?)',
                            (run_id, time.time(), None, json.dumps(initial), json.dumps(encode_state(replacement), allow_nan=False)))
        previous = self.run_id
        self.run_id, self.initial, self.trader = run_id, initial, replacement
        return previous

    def process(self, snapshot):
        raw = json.dumps(snapshot, sort_keys=True, allow_nan=False)
        fingerprint = hashlib.sha256(raw.encode()).hexdigest()
        if self.db.execute('SELECT 1 FROM paper_events WHERE run_id=? AND fingerprint=?', (self.run_id, fingerprint)).fetchone():
            return None
        # Work on a copy: exceptions and failed commits cannot partially apply a fill.
        candidate = decode_state(json.loads(json.dumps(encode_state(self.trader))))
        try:
            event = candidate.on_snapshot(snapshot)
            if candidate.rules.evidence_mode=='market_only':
                from meme_dip import entry_screen
                event['evidence_mode']='market_only'
                event['safety_qualified']=False
                event['strict_entry_blockers']=entry_screen(snapshot,snapshot['observed_at'],replace(candidate.rules,evidence_mode='strict'))
            if candidate.position and (snapshot['mint'], snapshot['pool']) == (candidate.position['mint'], candidate.position['pool']):
                candidate.position['last_price_usd'] = snapshot['price_usd']
                candidate.position['last_quote_at'] = snapshot['observed_at']
        except (KeyError, TypeError, ValueError, AttributeError):
            candidate = self.trader
            event = {'action': 'invalid_snapshot', 'reason': 'malformed, duplicate-time or out-of-order snapshot'}
        event['data_kind'] = 'synthetic' if isinstance(snapshot, dict) and snapshot.get('synthetic') is True else 'supplied_observation'
        observed = snapshot.get('observed_at') if isinstance(snapshot, dict) else None
        if not isinstance(observed, (int, float)):
            observed = None
        with self.db:
            self.db.execute('INSERT INTO paper_events(run_id,fingerprint,observed,event,snapshot) VALUES(?,?,?,?,?)',
                            (self.run_id, fingerprint, observed, json.dumps(event, allow_nan=False), raw))
            self.db.execute('UPDATE paper_runs SET checkpoint=? WHERE id=?',
                            (json.dumps(encode_state(candidate), allow_nan=False), self.run_id))
        self.trader = candidate
        return event

    def reclaim_rent(self):
        candidate=decode_state(json.loads(json.dumps(encode_state(self.trader))))
        event=candidate.reclaim_rent()
        event['data_kind']='paper_accounting'
        with self.db:
            self.db.execute('INSERT INTO paper_events(run_id,fingerprint,observed,event,snapshot) VALUES(?,?,?,?,?)',
                            (self.run_id,str(uuid.uuid4()),time.time(),json.dumps(event),'{}'))
            self.db.execute('UPDATE paper_runs SET checkpoint=? WHERE id=?',(json.dumps(encode_state(candidate)),self.run_id))
        self.trader=candidate
        return event

    def report(self):
        counts = {}
        realized = 0
        data_kinds = set()
        for (raw,) in self.db.execute('SELECT event FROM paper_events WHERE run_id=?', (self.run_id,)):
            event = json.loads(raw)
            data_kinds.add(event.get('data_kind', 'supplied_observation'))
            counts[event['action']] = counts.get(event['action'], 0)+1
            realized += event.get('pnl_usd', 0)
        position = self.trader.position
        value = 0 if position is None else position['quantity']*position.get('last_price_usd', position['entry_price'])
        assumptions={}
        for (snapshot_raw,) in self.db.execute('SELECT snapshot FROM paper_events WHERE run_id=? ORDER BY sequence DESC LIMIT 10',(self.run_id,)):
            snapshot=json.loads(snapshot_raw)
            execution=snapshot.get('execution',{}) if isinstance(snapshot,dict) else {}
            if isinstance(execution,dict) and execution:
                assumptions={key:execution.get(key) for key in ('model_only','fee_per_side','network_fee_sol','entry_rent_sol','exit_rent_sol','rent_is_refundable','rent_source')}
                break
        native = (self.trader.native_sol or 0)*(self.trader.sol_price_usd or 0)
        return {'mode': 'paper_only', 'run_id': self.run_id, 'initial_inventory': self.initial,
                'cash_usdc': self.trader.cash, 'native_sol': self.trader.native_sol,
                'marked_equity_usd': self.trader.equity(value),
                'liquid_cash_equity_usd':self.trader.cash+native,
                'locked_rent_sol':sum(self.trader.account_rent.values()),
                'valuation_as_of': self.trader.last_timestamp, 'realized_pnl_usd': realized,
                'position': position, 'drawdown_halted': self.trader.halted,
                'rules': asdict(self.trader.rules), 'event_counts': counts,
                'data_kinds': sorted(data_kinds),
                'safety_evidence_mode':self.trader.rules.evidence_mode,
                'live_qualified':False,
                'latest_execution_assumptions':assumptions,
                'execution_model': 'native_fee_ledger' if self.trader.native_sol is not None else 'assumed_USD_costs',
                'recent_events': [json.loads(row[0]) for row in self.db.execute(
                    'SELECT event FROM paper_events WHERE run_id=? ORDER BY sequence DESC LIMIT 20', (self.run_id,))],
                'archived_runs': self.db.execute('SELECT COUNT(*) FROM paper_runs WHERE archived IS NOT NULL').fetchone()[0]}

    def publish(self, source_status=None):
        report = self.report()
        report['source_status'] = source_status or {}
        report['published_at'] = time.time()
        temporary = self.directory/'report.tmp'
        temporary.write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
        temporary.replace(self.directory/'report.json')
        return report

    def close(self):
        self.db.close()
        self.lock.close()
