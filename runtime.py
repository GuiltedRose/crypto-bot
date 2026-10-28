"""Durable strategy positions and fee-aware promotion after five straight wins."""
import json
import math
import sqlite3
from dataclasses import asdict, dataclass
from strategy import Position, decide, entry_budget, DONCHIAN_ID
from bot import SOL_MINT


@dataclass(frozen=True)
class Fill:
    order_id: str
    side: str
    quantity: float
    price: float
    timestamp: int
    fee_usd: float
    mint: str = SOL_MINT


def record_outcome(state,strategy_id,net):
    won=net>1e-9
    stats=state['performance'].setdefault(strategy_id,{'consecutive_net_wins':0,
        'multi_position_unlocked':False,'closed_trades':0})
    stats['closed_trades']+=1
    stats['consecutive_net_wins']=stats['consecutive_net_wins']+1 if won else 0
    lost=net < -1e-9
    stats['consecutive_net_losses']=stats.get('consecutive_net_losses',0)+1 if lost else 0
    if stats['consecutive_net_losses']>=2:
        if stats['multi_position_unlocked']:
            # Promotion buys one demotion step, not an immediate
            # shutdown. Count a fresh pair of losses at one slot.
            stats['multi_position_unlocked']=False
            stats['consecutive_net_losses']=0
            stats['demotions']=stats.get('demotions',0)+1
        else:
            state['review_halted']=True
            state['review_strategy']=strategy_id
    if stats['consecutive_net_wins']>=5:
        stats['multi_position_unlocked']=True
    return won



class Coordinator:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS runtime (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)')
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO runtime VALUES (1,?)', (json.dumps({
                'position': None, 'positions': [], 'pending': None, 'last_entry_bar': None,
                'peak_equity': None, 'halted': False, 'history': [], 'fills': [],
                'performance': {}, 'closed_trades': [],'review_halted':False}),))

    def read(self):
        state=json.loads(self.db.execute('SELECT data FROM runtime WHERE id=1').fetchone()[0])
        if 'positions' not in state:
            state['positions']=[state['position']] if state.get('position') else []
        for record in state['positions']:
            record.setdefault('position_id','legacy-position')
        state['position']=state['positions'][0] if state['positions'] else None
        for fill in state['fills']:
            fill.setdefault('mint',SOL_MINT)
        state.setdefault('performance',{})
        state.setdefault('closed_trades',[])
        if 'review_halted' not in state:
            losses=[sid for sid,stats in state['performance'].items() if stats.get('consecutive_net_losses',0)>=2]
            state['review_halted']=bool(losses)
            if losses:
                state['review_strategy']=losses[0]
        return state

    def _write(self, state):
        # Keep the old singular field as a compatibility view, never as a
        # separate holding or an independent source of truth.
        state['position']=state['positions'][0] if state['positions'] else None
        self.db.execute('UPDATE runtime SET data=? WHERE id=1', (json.dumps(state),))

    def promotion_status(self, strategy_id=DONCHIAN_ID, state=None):
        state=self.read() if state is None else state
        stats=state.get('performance',{}).get(strategy_id,{})
        unlocked=bool(stats.get('multi_position_unlocked',False))
        return {'strategy_id':strategy_id,'consecutive_net_wins':stats.get('consecutive_net_wins',0),
                'consecutive_net_losses':stats.get('consecutive_net_losses',0),
                'wins_required':5,'losses_for_demotion':2,'max_open_positions':2 if unlocked else 1,
                'multi_position_unlocked':unlocked,'open_positions':len(state['positions']),
                'review_halted':state.get('review_halted',False),
                'next_two_loss_action':'demote' if unlocked else 'halt for review'}

    def plan(self, candles, now, quote, equity, cost_usd, wallet_position_quantity):
        """Reconcile aggregate inventory and atomically reserve one order.

        Positions are independent SOL lots, not independent wallet balances.
        Outstanding orders serialize globally; exits always precede entries.
        """
        if (not all(math.isfinite(v) and v>=0 for v in (equity,wallet_position_quantity,cost_usd))
                or not math.isfinite(quote) or quote<=0):
            raise ValueError('Invalid portfolio snapshot')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            state=self.read()
            state['short_signal']=None
            if state['pending']:
                return None,'reconcile pending intent before trading'
            records=state['positions']
            expected_quantity=sum(p['quantity'] for p in records)
            if records and not math.isclose(wallet_position_quantity,expected_quantity,rel_tol=1e-6,abs_tol=1e-9):
                return None,'wallet position mismatch; reconcile before trading'
            if not records and wallet_position_quantity>1e-9:
                return None,'unmanaged SOL inventory; reconcile before entry'
            peak=max(state['peak_equity'] or equity,equity)
            state['peak_equity']=peak
            state['halted']=state['halted'] or equity<=peak*.9
            exits=[]
            for record in records:
                levels=dict(record['levels'])
                levels.setdefault('strategy_id',record.get('strategy_id'))
                signal=decide(candles,now,quote,Position(**levels))
                if signal.action=='sell':
                    exits.append((record,signal))
            intent=None
            reason='manage open positions'
            if exits:
                record,signal=min(exits,key=lambda pair:0 if 'stop' in pair[1].reason else 1)
                intent={'side':'sell','quantity':record['quantity'],'position_id':record['position_id'],
                        'strategy_id':record.get('strategy_id','legacy-pullback-v1')}
                reason=signal.reason
            else:
                # Flat-market signal may open a second independently managed
                # lot after promotion. Never add a lot on the same entry bar.
                signal=decide(candles,now,quote)
                capacity=self.promotion_status(signal.strategy_id,state)['max_open_positions']
                reason=signal.reason
                if signal.action=='buy':
                    bar=candles[-1].timestamp
                    if state['review_halted']:
                        reason='two consecutive net losses; manual strategy review required'
                    elif len(records)>=capacity:
                        reason='open position limit reached'
                    elif state['halted']:
                        reason='drawdown halt'
                    elif state['last_entry_bar']==bar:
                        reason='entry candle already processed'
                    else:
                        state['last_entry_bar']=bar
                        history=state['history'] if state.get('history_strategy','legacy-pullback-v1')==signal.strategy_id else []
                        exposure=expected_quantity*quote
                        budget,reason=entry_budget(SOL_MINT,equity,history,signal,quote,cost_usd,
                            open_exposure_usd=exposure,existing_token_usd=exposure,
                            active_mints=(SOL_MINT,) if records else (),
                            max_open_positions=capacity,open_position_count=len(records))
                        if budget:
                            intent={'side':'buy','budget_usd':budget,'stop_distance':signal.stop_distance,
                                    'target_distance':signal.target_distance,'take_profit':signal.take_profit,
                                    'strategy_id':signal.strategy_id}
            if intent:
                import uuid
                intent.update(order_id=str(uuid.uuid4()),created_at=now)
                state['pending']=intent
            self._write(state)
            return intent,reason

    def apply_fill(self, fill):
        """Idempotently account a confirmed real fill, then update net wins."""
        if (fill.side not in ('buy','sell') or
                not all(math.isfinite(v) for v in (fill.quantity,fill.price,fill.fee_usd,fill.timestamp)) or
                min(fill.quantity,fill.price)<=0 or fill.fee_usd<0):
            raise ValueError('Invalid actual fill')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            state=self.read()
            previous=next((f for f in state['fills'] if f['order_id']==fill.order_id),None)
            if previous:
                if previous!=asdict(fill):
                    raise ValueError('Duplicate order has different fill data')
                return
            pending=state['pending']
            if not pending or pending['order_id']!=fill.order_id or pending['side']!=fill.side or pending.get('mint',SOL_MINT)!=fill.mint:
                raise ValueError('Fill does not match reserved intent')
            strategy_id=pending.get('strategy_id','legacy-pullback-v1')
            if fill.side=='buy':
                capacity=self.promotion_status(strategy_id,state)['max_open_positions']
                if len(state['positions'])>=capacity:
                    raise ValueError('Open position capacity exceeded')
                if fill.quantity*fill.price>pending['budget_usd']+1e-8:
                    raise ValueError('Fill exceeds reserved allocation; reconciliation required')
                levels=Position(fill.price,fill.price-pending['stop_distance'],
                    fill.price+pending['target_distance'] if pending.get('take_profit',True) else None,
                    fill.timestamp,strategy_id)
                state['positions'].append({'position_id':fill.order_id,'quantity':fill.quantity,
                    'entry_fee':fill.fee_usd,'levels':asdict(levels),'strategy_id':strategy_id,
                    'mint':fill.mint,'decimals':pending.get('decimals',9)})
            else:
                position=next((p for p in state['positions'] if p['position_id']==pending.get('position_id')),None)
                # Migrate an already-reserved exit from the old single-lot format.
                if position is None and 'position_id' not in pending and len(state['positions'])==1:
                    position=state['positions'][0]
                if not position or not math.isclose(fill.quantity,position['quantity'],rel_tol=1e-6,abs_tol=1e-9):
                    raise ValueError('Partial/unexpected exit; preserve slot for reconciliation')
                strategy_id=position.get('strategy_id','legacy-pullback-v1')
                if position.get('mint',SOL_MINT)!=fill.mint:
                    raise ValueError('Exit mint mismatch')
                state.setdefault('asset_history',{}).setdefault(fill.mint,{}).setdefault(strategy_id,[]).append(
                    fill.price/position['levels']['entry_price']-1)
                if state.get('history_strategy','legacy-pullback-v1')!=strategy_id:
                    state.setdefault('history_archive',{})[state.get('history_strategy','legacy-pullback-v1')]=state['history']
                    state['history']=[]
                state['history_strategy']=strategy_id
                state['history'].append(fill.price/position['levels']['entry_price']-1)
                # Pool fees/slippage are embedded in actual fill prices; native
                # execution costs for both legs are recorded separately.
                net=fill.quantity*(fill.price-position['levels']['entry_price'])-position['entry_fee']-fill.fee_usd
                won=record_outcome(state,strategy_id,net)
                state['closed_trades'].append({'position_id':position['position_id'],'exit_order_id':fill.order_id,
                    'mint':fill.mint,'strategy_id':strategy_id,'closed_at':fill.timestamp,'net_pnl_usd':net,'successful':won})
                state['positions'].remove(position)
            state['fills'].append(asdict(fill))
            state['pending']=None
            self._write(state)

    def clear_review_halt(self):
        """Explicit manual action after review; never called by the live loop."""
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            state=self.read()
            if state['positions'] or state['pending']:
                raise ValueError('Reconcile and close remaining positions before resuming')
            for stats in state['performance'].values():
                stats['consecutive_net_wins']=0
                stats['consecutive_net_losses']=0
                stats['multi_position_unlocked']=False
            state['review_halted']=False
            state.pop('review_strategy',None)
            self._write(state)

    def record_failed(self, order_id, executed=False):
        """Clear a proven failure; on-chain failures break the win streak.

        Local cost/prepare rejections are not executed trades. A failed chain
        transaction is not a win and may consume fees, so it resets the streak.
        """
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            state=self.read()
            if not state['pending'] or state['pending']['order_id']!=order_id:
                raise ValueError('Unknown pending intent')
            if executed:
                strategy=state['pending'].get('strategy_id','legacy-pullback-v1')
                if strategy in state['performance']:
                    state['performance'][strategy]['consecutive_net_wins']=0
            state['pending']=None
            self._write(state)
