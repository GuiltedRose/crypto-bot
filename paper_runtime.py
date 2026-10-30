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
            'strategy_capital_usd':trader.strategy_capital_usd,'strategy_peak_pnl_usd':trader.strategy_peak_pnl_usd,
            'strategy_realized_pnl_usd':trader.strategy_realized_pnl_usd,'strategy_risk':trader.strategy_risk,'peak': trader.peak, 'position': trader.position,
            'positions':trader.all_positions(),'max_positions':trader.max_positions, 'halted': trader.halted,
            'last_timestamp': trader.last_timestamp,'consecutive_losses':trader.consecutive_losses,
            'loss_cooldown_until':trader.loss_cooldown_until,
            'paper_loss_cooldown_seconds':getattr(trader,'paper_loss_cooldown_seconds',trader.rules.loss_streak_cooldown_seconds),
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
    for field in ('strategy_capital_usd','strategy_peak_pnl_usd','strategy_realized_pnl_usd'):
        if field in state: setattr(trader,field,state[field])
    trader.positions=state.get('positions') or ([trader.position] if trader.position else [])
    trader.position=trader.positions[0] if trader.positions else None
    trader.max_positions=state.get('max_positions',1)
    trader.strategy_risk=state.get('strategy_risk',{})
    trader.consecutive_losses=state.get('consecutive_losses',0)
    trader.loss_cooldown_until=state.get('loss_cooldown_until',0)
    trader.paper_loss_cooldown_seconds=state.get('paper_loss_cooldown_seconds',trader.rules.loss_streak_cooldown_seconds)
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

    def adopt_entry_mode(self,rules):
        old=asdict(self.trader.rules);new=asdict(rules)
        changed={key for key in old if old[key]!=new[key]}
        if old['entry_style']!='volume_candles' or new['entry_style']!='volume_candles' or changed-{'momentum_reclaim_enabled','trend_pullback_max_fraction','position_fraction','take_profit_fraction','max_loss_fraction','entry_profile_mode','drawdown_halt','drawdown_promotion_bankroll_usd','promoted_drawdown_halt','drawdown_bankroll_basis'}:
            raise ValueError('Paper adoption permits profile, reclaim entry policy, drawdown and percentage limits only; existing positions retain their frozen exits')
        candidate=decode_state(encode_state(self.trader));candidate.rules=rules
        with self.db:
            self.db.execute('INSERT INTO paper_events(run_id,fingerprint,observed,event,snapshot) VALUES(?,?,?,?,?)',
                (self.run_id,str(uuid.uuid4()),time.time(),json.dumps({'action':'paper_policy_adopted','old_mode':old['entry_profile_mode'],
                 'new_mode':new['entry_profile_mode'],'old_drawdown_halt':old['drawdown_halt'],
                 'new_drawdown_halt':new['drawdown_halt'],'old_promotion_bankroll_usd':old['drawdown_promotion_bankroll_usd'],
                 'new_promotion_bankroll_usd':new['drawdown_promotion_bankroll_usd'],'promoted_drawdown_halt':new['promoted_drawdown_halt'],'drawdown_bankroll_basis':new['drawdown_bankroll_basis'],'halt_preserved':candidate.halted,'pnl_usd':0,'data_kind':'paper_accounting'}),'{}'))
            self.db.execute('UPDATE paper_runs SET checkpoint=? WHERE id=?',(json.dumps(encode_state(candidate),allow_nan=False),self.run_id))
        self.trader=candidate

    def process(self, snapshot):
        raw = json.dumps(snapshot, sort_keys=True, allow_nan=False)
        fingerprint = hashlib.sha256(raw.encode()).hexdigest()
        if self.db.execute('SELECT 1 FROM paper_events WHERE run_id=? AND fingerprint=?', (self.run_id, fingerprint)).fetchone():
            return None
        # Work on a copy: exceptions and failed commits cannot partially apply a fill.
        candidate = decode_state(json.loads(json.dumps(encode_state(self.trader))))
        try:
            candidate.neural_exit_model=getattr(self,'learning_model',{}).get('exit_model') if getattr(self,'neural_mode','shadow')=='learning' else None
            event = candidate.on_snapshot(snapshot)
            if event.get('action')=='paper_buy':
                from meme_learning import decision_context
                from neural_autonomy import learning_decision
                model=getattr(self,'learning_model',None)
                context=decision_context(snapshot,candidate.rules,signal={'metrics':event.get('signal_metrics',{})},weighted_model=model,notional_usd=event.get('budget_usd'))
                decision=learning_decision(model,context) if getattr(self,'neural_mode','shadow')=='learning' else {'allow':True,'status':'shadow'}
                if not decision['allow']:
                    # Keep an unbiased exploration stream without inventing fills.
                    explore=int(hashlib.sha256((snapshot['mint']+str(snapshot['observed_at'])).encode()).hexdigest()[:8],16)%4==0
                    if not explore:
                        candidate=self.trader
                        event={'action':'reject','reason':'paper neural entry veto','neural_selection':decision}
                    else: decision=dict(decision,allow=True,status='paper_exploration')
                if event.get('action')=='paper_buy':
                    context['adaptive_selection']=decision
                    event['entry_context']=context
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
        for position in candidate.all_positions():
            if position['mint']==snapshot.get('mint') and 'last_net_pnl_usd' in position:
                from neural_exits import record
                record(self.db,f"{self.run_id}:{position['mint']}:{position['opened_at']}",snapshot,candidate.rules,
                    position['last_net_pnl_usd'],position['cash_spent'])
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

    def configure_positions(self,count):
        if type(count) is not int or not 1<=count<=8:
            raise ValueError('Paper position limit must be 1 through 8')
        if count<len(self.trader.all_positions()):
            raise ValueError('Cannot reduce paper slots below open positions')
        if count==self.trader.max_positions: return
        self.trader.max_positions=count
        with self.db:
            self.db.execute('UPDATE paper_runs SET checkpoint=? WHERE id=?',
                (json.dumps(encode_state(self.trader)),self.run_id))
            self.db.execute('INSERT INTO paper_events(run_id,fingerprint,observed,event,snapshot) VALUES(?,?,?,?,?)',
                (self.run_id,str(uuid.uuid4()),time.time(),json.dumps({'action':'paper_slots_configured','max_positions':count,'pnl_usd':0,'data_kind':'paper_accounting'}),'{}'))

    def configure_loss_cooldown(self,seconds):
        if type(seconds) is not int or not 0<=seconds<=86400:
            raise ValueError('Paper cooldown must be 0 through 86400 seconds')
        trader=self.trader
        old=getattr(trader,'paper_loss_cooldown_seconds',trader.rules.loss_streak_cooldown_seconds)
        if old==seconds: return
        # Preserve the loss timestamp and history rather than restarting the wait.
        if trader.loss_cooldown_until:
            trader.loss_cooldown_until=max(0,trader.loss_cooldown_until-old+seconds)
        trader.paper_loss_cooldown_seconds=seconds
        with self.db:
            self.db.execute('INSERT INTO paper_events(run_id,fingerprint,observed,event,snapshot) VALUES(?,?,?,?,?)',
                (self.run_id,str(uuid.uuid4()),time.time(),json.dumps({'action':'paper_cooldown_configured',
                 'old_seconds':old,'new_seconds':seconds,'data_kind':'paper_accounting','pnl_usd':0}),'{}'))
            self.db.execute('UPDATE paper_runs SET checkpoint=? WHERE id=?',
                (json.dumps(encode_state(trader)),self.run_id))

    def refresh_learning(self,live_db=None,wallet_db=None,wallets=()):
        """Paper owns this artifact; live separately rebuilds the same shared model."""
        from meme_learning import paper_outcomes,live_outcomes,train_shared_model
        paper=paper_outcomes(self.directory/'paper.sqlite')[0]
        live=live_outcomes(live_db)[0] if live_db and Path(live_db).exists() else []
        external=[]
        if wallet_db and Path(wallet_db).exists() and wallets:
            from wallet_watch import followed_wallet_outcomes
            external=followed_wallet_outcomes(wallet_db,wallets)
        model=train_shared_model(external,live,paper)
        from exit_evaluation import update
        model['exit_model']=update([self.directory/'paper.sqlite',live_db],self.directory/'exit_evaluation.json')
        document={'model':model,'qualification':'paper_training_only_not_live_validation'}
        target=self.directory/'learning.json'
        raw=json.dumps(document,allow_nan=False,sort_keys=True)
        if not target.exists() or target.read_text()!=raw:
            temporary=self.directory/'learning.tmp'
            temporary.write_text(raw);temporary.replace(target)
        self.learning_model=model
        self.learning_summary={'version':model['version'],'samples':model['samples'],
            'wins':model['wins'],'losses':model['losses'],'source_samples':model['source_samples'],
            'class_balance':model.get('class_balance',{}),
            'exit_model':{**{key:model['exit_model'].get(key,0) for key in ('samples','wins','losses','trades')},
                'evaluation':model['exit_model'].get('evaluation',{})}}
        return self.learning_summary

    def auto_reset_if_exhausted(self):
        """Refill only virtual inventory, while flat, after actual paper activity."""
        trader=self.trader
        if trader.position is not None: return None
        closed=self.db.execute("SELECT 1 FROM paper_events WHERE run_id=? AND json_extract(event,'$.action')='paper_sell' LIMIT 1",(self.run_id,)).fetchone()
        if not closed and not trader.halted: return None
        liquid=trader.cash
        if trader.native_sol is not None:
            liquid+=max(0,trader.native_sol-trader.rules.native_fee_reserve_sol)*(trader.sol_price_usd or 0)
        exhausted=(liquid<=trader.rules.reserve_usd+1e-9 or
            trader.native_sol is not None and trader.native_sol<=trader.rules.native_fee_reserve_sol)
        if not exhausted and not trader.halted: return None
        reason='drawdown halt' if trader.halted else 'spendable paper balance exhausted'
        initial=dict(self.initial)
        previous=self.reset(initial['balance'],trader.rules,initial['native_sol'],initial['sol_price_usd'])
        event={'action':'paper_balance_reset','reason':reason,'archived_run':previous,
            'new_run':self.run_id,'initial_inventory':initial,'pnl_usd':0,'data_kind':'paper_accounting'}
        with self.db:
            self.db.execute('INSERT INTO paper_events(run_id,fingerprint,observed,event,snapshot) VALUES(?,?,?,?,?)',
                (self.run_id,str(uuid.uuid4()),time.time(),json.dumps(event),'{}'))
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
                'position': position,'positions':self.trader.all_positions(),'max_positions':self.trader.max_positions, 'drawdown_halted': self.trader.halted,
                'consecutive_losses':self.trader.consecutive_losses,
                'loss_cooldown_until':self.trader.loss_cooldown_until,
                'paper_loss_cooldown_seconds':getattr(self.trader,'paper_loss_cooldown_seconds',self.trader.rules.loss_streak_cooldown_seconds),
                'rules': asdict(self.trader.rules), 'event_counts': counts,
                'data_kinds': sorted(data_kinds),
                'safety_evidence_mode':self.trader.rules.evidence_mode,'drawdown_scope':'bot_positions' if self.trader.rules.entry_style=='volume_candles' else 'virtual_wallet',
                'strategy_realized_pnl_usd':self.trader.strategy_realized_pnl_usd,'strategy_risk':self.trader.strategy_risk,
                'live_qualified':False,'neural_mode':getattr(self,'neural_mode','shadow'),'neural_training':getattr(self,'learning_summary',None),
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
