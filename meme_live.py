"""One-position meme strategy coordinator; fills require confirmed receipts.

The runner selects read-only versus signed execution. This module does not
load secrets, auto-promote paper rules, or retrain itself on its own fills.
"""
from dataclasses import asdict
import fcntl
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time
import uuid
from meme_dip import DipRules, entry_screen, SOL_MINT
from meme_execution import MemeAdapter, associated, TOKEN, WSOL, token_balance
from solders.pubkey import Pubkey


def positive(value):
    if type(value) not in (float,int) or not math.isfinite(value) or value<=0:
        raise ValueError('Positive finite valuation required')
    return value


class MemeLiveStore:
    def __init__(self,directory,user,rules,adopt_rules=False):
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True)
        self.lock=(self.directory/'runner.lock').open('a')
        try: fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.lock.close();raise ValueError('Another meme runner holds this state') from None
        self.db=sqlite3.connect(self.directory/'meme_live.sqlite')
        self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''CREATE TABLE IF NOT EXISTS state(id INTEGER PRIMARY KEY CHECK(id=1),body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,at REAL NOT NULL,kind TEXT NOT NULL,body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS bars(mint TEXT,pool TEXT,bar INTEGER,PRIMARY KEY(mint,pool));''')
        row=self.db.execute('SELECT body FROM state WHERE id=1').fetchone()
        binding={'wallet':str(user),'rules':asdict(rules),'schema':1}
        if row:
            self.state=json.loads(row[0])
            old=self.state['binding']
            # Newly introduced default fields do not change old policy semantics.
            normalized=dict(old,rules=asdict(DipRules(**old['rules'])))
            if normalized!=binding:
                can_adopt=(adopt_rules and old['wallet']==str(user) and old['schema']==binding['schema']
                           and self.state['position'] is None and self.state['pending'] is None)
                orders_path=self.directory/'orders.sqlite'
                if can_adopt and orders_path.exists():
                    orders=sqlite3.connect('file:'+str(orders_path)+'?mode=ro',uri=True)
                    try: can_adopt=not orders.execute("SELECT 1 FROM orders WHERE state IN ('pending','uncertain') LIMIT 1").fetchone()
                    finally: orders.close()
                if not can_adopt:
                    self.close();raise ValueError('Live state wallet/rules differ; use --adopt-rules only while flat with no unresolved orders')
                self.state['binding']=binding
                self.save('rules_adopted',{'previous_rules':old['rules'],'new_rules':binding['rules']})
            elif old!=binding:
                self.state['binding']=binding
                self.save('rule_defaults_normalized',{})
        else:
            self.state=dict(binding=binding,position=None,pending=None,peak_equity=None,halted=False,realized_pnl_usd=0)
            self.save('initialized',{})

    def save(self,kind,body):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO state VALUES(1,?)',(json.dumps(self.state,allow_nan=False),))
            self.db.execute('INSERT INTO events(at,kind,body) VALUES(?,?,?)',(time.time(),kind,json.dumps(body,allow_nan=False)))

    def close(self):
        self.db.close();self.lock.close()


def receipt_fill(transaction,user,mint,base_account,quote_account):
    """Confirmed transaction deltas, never a sampled-price paper fill.

    Includes native SOL + WSOL changes and excludes rent deposited into the
    user's own base/quote accounts. Other setup costs stay in cost basis.
    """
    meta=transaction['meta']
    if meta['err'] is not None: raise ValueError('Failed transaction is not a fill')
    message=transaction['transaction']['message']
    keys=[k['pubkey'] if isinstance(k,dict) else k for k in message['accountKeys']]
    loaded=meta.get('loadedAddresses',{})
    keys+=loaded.get('writable',[])+loaded.get('readonly',[])
    if len(keys)!=len(set(keys)): raise ValueError('Duplicate receipt account keys')
    index=keys.index(str(user))
    native=meta['postBalances'][index]-meta['preBalances'][index]
    def balance(rows,address,expected_mint):
        found=[row for row in rows if keys[row['accountIndex']]==str(address)]
        if not found: return 0
        if len(found)!=1 or found[0]['mint']!=str(expected_mint) or found[0].get('owner')!=str(user):
            raise ValueError('Receipt token account identity mismatch')
        return int(found[0]['uiTokenAmount']['amount'])
    before=meta.get('preTokenBalances',[]);after=meta.get('postTokenBalances',[])
    base_delta=balance(after,base_account,mint)-balance(before,base_account,mint)
    quote_delta=balance(after,quote_account,SOL_MINT)-balance(before,quote_account,SOL_MINT)
    rent=0
    for address,token in ((base_account,mint),(quote_account,SOL_MINT)):
        if str(address) not in keys: continue
        i=keys.index(str(address));pre=meta['preBalances'][i];post=meta['postBalances'][i]
        # WSOL amount is lamports; remove changes in its backing from the
        # lamport delta so that only account rent is excluded from P&L.
        backing=(quote_delta if token==SOL_MINT else 0)
        rent+=post-pre-backing
    return dict(base_delta=base_delta,quote_delta=quote_delta,native_delta=native,
                net_cash_lamports=native+quote_delta+rent,own_account_rent_delta=rent,fee_lamports=meta['fee'])


class MemeLiveEngine:
    def __init__(self,rpc,user,store,rules,orders=None,signer=None,adapter_factory=MemeAdapter,risk_assessor=None,fixed_profile=None):
        self.rpc,self.user,self.store,self.rules=rpc,str(user),store,rules
        self.orders,self.signer,self.adapter_factory=orders,signer,adapter_factory
        self.risk_assessor=risk_assessor
        self.fixed_profile=fixed_profile
        if orders and store.state['pending'] is None and orders.unresolved():
            raise ValueError('Unbound unresolved orders require manual reconciliation')
        if (orders is None)!=(signer is None): raise ValueError('Signer and order journal must be paired')

    @property
    def execute(self): return self.signer is not None

    def mark(self,equity,open_pnl_usd=0.):
        from meme_bot_risk import mark_bot_risk
        has_orders=bool(self.orders and self.orders.db.execute('SELECT 1 FROM orders LIMIT 1').fetchone())
        return mark_bot_risk(self.store,equity,self.rules.drawdown_halt,open_pnl_usd,has_orders,
            self.rules.drawdown_promotion_bankroll_usd,self.rules.promoted_drawdown_halt,
            total_bankroll=equity if self.rules.drawdown_bankroll_basis=='total_for_demotion' else None,
            total_complete=getattr(self,'bankroll_valuation',{}).get('complete',True))

    def resolve_expired(self,p,signature):
        """Release only expired buys with unchanged finalized pre-send inventory.

        Incomplete legacy wallet history, any balance change, receipt, RPC failure or
        observed status keep replacement blocked. Never resend signed bytes.
        """
        baseline=p.get('pre_inventory')
        if p['side']!='buy' or not p.get('last_valid_block_height'):
            return 'uncertain'
        height=self.rpc.call('getBlockHeight',[{'commitment':'finalized'}])
        if height<=p['last_valid_block_height']: return 'uncertain'
        result=self.rpc.call('getSignatureStatuses',[[signature],{'searchTransactionHistory':True}])
        if result['value'][0] is not None: return 'uncertain'
        receipt=self.rpc.call('getTransaction',[signature,{'encoding':'json','commitment':'finalized','maxSupportedTransactionVersion':0}])
        if receipt is not None: return 'uncertain'
        if baseline is None:
            if not self.absent_from_wallet_history(p,signature,result['context']['slot']): return 'uncertain'
            with self.orders.db:
                self.orders.db.execute("UPDATE orders SET state='expired_unfilled' WHERE order_id=? AND state='uncertain'",(p['id'],))
            return 'expired_unfilled'
        accounts=self.rpc.call('getMultipleAccounts',[[self.user,p['base_account'],p['quote_account']],
            {'encoding':'base64','commitment':'finalized','minContextSlot':result['context']['slot']}])['value']
        if len(accounts)!=3 or accounts[0] is None: return 'uncertain'
        current={'native_lamports':accounts[0]['lamports'],
                 'base_balance':token_balance(accounts[1],Pubkey.from_string(p['mint']),Pubkey.from_string(self.user),Pubkey.from_string(p['token_program'])),
                 'quote_balance':token_balance(accounts[2],WSOL,Pubkey.from_string(self.user),TOKEN)}
        if current!=baseline: return 'uncertain'
        with self.orders.db:
            self.orders.db.execute("UPDATE orders SET state='expired_unfilled' WHERE order_id=? AND state='uncertain'",(p['id'],))
        return 'expired_unfilled'

    def absent_from_wallet_history(self,p,signature,context_slot):
        """Bounded finalized wallet scan spanning the original submission time."""
        row=self.orders.db.execute('SELECT created FROM orders WHERE order_id=?',(p['id'],)).fetchone()
        if not row or not isinstance(row[0],(int,float)): return False
        cutoff=row[0]-120
        before=None;seen=set()
        for _ in range(5):
            options={'commitment':'finalized','limit':100,'minContextSlot':context_slot}
            if before: options['before']=before
            rows=self.rpc.call('getSignaturesForAddress',[self.user,options])
            if not isinstance(rows,list) or not rows: return False
            for item in rows:
                sig=item.get('signature');stamp=item.get('blockTime');slot=item.get('slot')
                if sig==signature: return False
                if not sig or sig in seen or type(stamp) is not int or type(slot) is not int: return False
                seen.add(sig)
            # Require actual history older than submission, not an empty page.
            if rows[-1]['blockTime']<=cutoff:
                first=self.rpc.call('getFirstAvailableBlock',[])
                return type(first) is int and first<=rows[-1]['slot']
            before=rows[-1]['signature']
        return False

    def recover(self):
        s=self.store.state;p=s['pending']
        if not p: return {'status':'idle'}
        if not self.orders: return {'status':'pending_live_order_requires_execution_journal'}
        # Crash between intent reservation and signed journal insertion has
        # provably never called sendTransaction; clear only that exact case.
        row=self.orders.db.execute('SELECT signature,state FROM orders WHERE order_id=?',(p['id'],)).fetchone()
        if not row:
            s['pending']=None;self.store.save('unsent_intent_released',{'id':p['id']})
            return {'status':'unsent_intent_released'}
        status=self.orders.reconcile(p['id'])
        if status=='uncertain':
            status=self.resolve_expired(p,row[0])
        if status in ('rejected','expired_unfilled'):
            s['pending']=None
            self.store.save('order_'+status,{'id':p['id'],'signature':row[0],'pnl_usd':0})
            return {'status':'order_'+status,'signature':row[0]}
        if status=='uncertain': return {'status':'uncertain_order_blocks_replacement','signature':row[0]}
        tx=self.rpc.call('getTransaction',[row[0],{'encoding':'json','commitment':'confirmed','maxSupportedTransactionVersion':0}])
        if tx is None: return {'status':'waiting_for_confirmed_receipt','signature':row[0]}
        if tx['transaction']['signatures'][0]!=row[0]: raise ValueError('Receipt signature mismatch')
        if status=='failed':
            fee=tx['meta']['fee']/1e9*p['sol_price_usd']
            s['realized_pnl_usd']-=fee;s['pending']=None
            self.store.save('transaction_failed',{'id':p['id'],'network_cost_usd':fee})
            return {'status':'transaction_failed'}
        fill=receipt_fill(tx,self.user,p['mint'],p['base_account'],p['quote_account'])
        if p['side']=='buy':
            if fill['base_delta']<p['minimum'] or fill['net_cash_lamports']>=0:
                raise ValueError('Confirmed buy violates intent; manual reconciliation required')
            s['position']=dict(entry_profile=p.get('entry_profile'),mint=p['mint'],pool=p['pool'],kind=p['kind'],units=fill['base_delta'],
                opened_at=tx.get('blockTime') or time.time(),cost_usd=-fill['net_cash_lamports']/1e9*p['sol_price_usd'],
                base_account=p['base_account'],quote_account=p['quote_account'],entry_signature=row[0],
                take_profit_usd=p.get('take_profit_usd',self.rules.take_profit_usd),max_loss_usd=p.get('max_loss_usd',self.rules.max_loss_usd))
            with self.store.db:
                self.store.db.execute('INSERT OR REPLACE INTO bars VALUES(?,?,?)',(p['mint'],p['pool'],p['bar']))
        else:
            if -fill['base_delta']!=p['input_units'] or fill['quote_delta']+fill['native_delta']+fill['fee_lamports']+fill['own_account_rent_delta']<p['minimum']:
                raise ValueError('Confirmed sell violates intent; manual reconciliation required')
            pnl=fill['net_cash_lamports']/1e9*p['sol_price_usd']-s['position']['cost_usd']
            s['realized_pnl_usd']+=pnl;s['position']=None;fill['pnl_usd']=pnl
        s['rent_accounts']=sorted(set(s.get('rent_accounts',[])+[p['base_account'],p['quote_account']]))
        s['pending']=None
        self.store.save('confirmed_'+p['side'],dict(signature=row[0],mint=p['mint'],**fill))
        return {'status':'confirmed_'+p['side'],**fill}

    def prepare(self,snapshot,sol_price,equity,now=None):
        """Evaluate a candidate or held asset and construct/simulate its swap."""
        self.last_profile_selection=None
        now=time.time() if now is None else now;positive(sol_price);positive(equity)
        s=self.store.state
        if s['pending']: return self.recover()
        position=s['position']
        if position and snapshot['mint']!=position['mint']: return {'status':'open_position_has_priority'}
        if position is None: self.mark(equity)
        if not 0<=now-snapshot['observed_at']<=30: return {'status':'stale_market_observation'}
        if snapshot.get('execution',{}).get('model_only'): raise ValueError('Paper execution assumptions cannot authorize live orders')
        side='sell' if position else 'buy'
        if side=='buy':
            if s['halted']: return {'status':'drawdown_halted','risk':s.get('strategy_risk',{})}
            if s.get('strategy_risk',{}).get('promotion_valuation_pending'): return {'status':'bankroll_valuation_incomplete','reason':'Cannot confirm total-bankroll promotion/demotion; new entries paused','risk':s['strategy_risk']}
            entry_rules=self.rules
            profile_name=self.fixed_profile
            if self.rules.entry_profile_mode=='adaptive':
                from meme_profiles import assess_profiles,profiles
                selection=assess_profiles(snapshot,now,self.rules)
                self.last_profile_selection=selection
                self.store.state['last_entry_regime']={'profile':selection['selected_profile'],'observed_at':now} if selection['selected_profile'] else None
                self.store.save('entry_profile_selection',selection)
                profile_name=selection['selected_profile']
                if profile_name is None:
                    from meme_scorecard import scorecard
                    assessment=scorecard(snapshot,now,self.rules)
                    self.store.save('entry_scorecard',assessment)
                    return {'status':'watch' if selection['action']=='watch' else 'entry_rejected',
                            'reasons':selection['reasons'],'profile_selection':selection,'scorecard':assessment}
                entry_rules=profiles(self.rules)[profile_name]
            from meme_scorecard import scorecard
            assessment=scorecard(snapshot,now,entry_rules)
            self.store.save('entry_scorecard',assessment)
            reasons=entry_screen(snapshot,now,entry_rules)
            if reasons: return {'status':'entry_rejected','reasons':reasons,'scorecard':assessment}
            from trend_pullback import assess_trend_pullback
            if entry_rules.entry_style=='volume_candles':
                from volume_candles import assess_volume_candles
                signal=assess_volume_candles(snapshot,now,entry_rules)
            else: signal=assess_trend_pullback(snapshot,now,entry_rules)
            if signal['action']!='buy': return {'status':'watch','signal':signal,'scorecard':assessment}
            bar=int(snapshot['candle_history']['candles'][-1]['timestamp'])
            used=self.store.db.execute('SELECT bar FROM bars WHERE mint=? AND pool=?',(snapshot['mint'],snapshot['pool'])).fetchone()
            if used and used[0]>=bar: return {'status':'already_entered_on_completed_candle'}
        else: bar=None;profile_name=position.get("entry_profile")
        adapter=self.adapter_factory(self.rpc,self.user,snapshot['mint'],snapshot['pool'],snapshot['market_kind'])
        market=adapter.snapshot(for_entry=side=='buy')
        if position:
            if market['base_balance']<position['units']: return {'status':'wallet_inventory_changed_manual_reconciliation_required'}
            units=position['units'];out=adapter.output('sell',units,market)
            net=(out*(1-self.rules.slippage_per_side))/1e9*sol_price-.00002*sol_price-position['cost_usd']
            self.mark(equity,net)
            profit_target=position.get('take_profit_usd',self.rules.take_profit_usd)
            loss_limit=position.get('max_loss_usd',self.rules.max_loss_usd)
            reason=('bot strategy drawdown' if s['halted'] else 'loss trigger' if net<=-loss_limit else
                    'profit target' if net>=profit_target else 'time limit' if now-position['opened_at']>=self.rules.max_holding_seconds else None)
            if reason is None: return {'status':'holding','estimated_net_pnl_usd':net}
        else:
            # Preserve the explicit dollar reserve and native exit reserve.
            available=(market['native_lamports']+market.get('quote_balance',0)-int(self.rules.native_fee_reserve_sol*1e9)-5040000)/1e9*sol_price-self.rules.reserve_usd
            budget=min(self.rules.position_cap(equity),available)
            if self.rules.entry_style!='volume_candles': budget=min(budget,snapshot['liquidity_usd']*self.rules.max_liquidity_allocation_fraction)
            if budget<=0: return {'status':'insufficient_spendable_SOL_after_reserves'}
            units=int(budget/sol_price*1e9);reason='volume and candle minimums' if self.rules.entry_style=='volume_candles' else 'trend pullback'
            if units<=0: return {'status':'position_rounds_to_zero'}
        if side=='buy' and self.rules.momentum_reclaim_enabled and self.risk_assessor is None:
            return {'status':'onchain_risk_unavailable'}
        risk_report=None
        if side=='buy':
            if self.risk_assessor and (self.rules.entry_style!='volume_candles' or self.rules.momentum_reclaim_enabled):
                risk=self.risk_assessor.assess(adapter,market)
                risk_report=risk
                self.store.save('onchain_entry_risk',risk)
                if not risk['passed']: return {'status':'onchain_risk_rejected','risk':risk}
                # Risk sampling may take time; never use its old reserve quote.
                market=adapter.snapshot(for_entry=True)
            reverse=adapter.simulate_round_trip(units)
            if reverse.get('simulation_passed') is not True:
                return {'status':'reverse_route_simulation_failed'}
            market=adapter.snapshot(for_entry=True)
            available=(market['native_lamports']+market.get('quote_balance',0)-int(self.rules.native_fee_reserve_sol*1e9)-5040000)/1e9*sol_price-self.rules.reserve_usd
            units=min(units,int(max(0,available)/sol_price*1e9))
            if units<=0: return {'status':'inventory_changed_after_reverse_probe'}
        if side=='buy': profit_target,loss_limit=self.rules.exit_limits(units/1e9*sol_price)
        unsigned,context=adapter.build(side,units,snapshot=market)
        simulated=adapter.simulate(unsigned,context,reserve=int(self.rules.native_fee_reserve_sol*1e9))
        if side=='sell' and reason=='profit target':
            net_minimum=(context['min_output']-simulated['fee_lamports']-simulated['rent_lamports']+simulated.get('recoverable_rent_lamports',0))/1e9*sol_price-position['cost_usd']
            if net_minimum<profit_target:
                return {'status':'holding','reason':'net minimum proceeds have not reached profit target','estimated_net_pnl_usd':net_minimum}
        if side=='buy':
            setup=(simulated['rent_lamports']-simulated.get('recoverable_rent_lamports',0))/1e9*sol_price
            round_trip=(2*simulated['fee_lamports']/1e9*sol_price+units/1e9*sol_price*2*(market['fee_bps']/10000+self.rules.slippage_per_side))
            if round_trip+setup>=loss_limit: return {'status':'estimated_costs_exceed_loss_budget'}
        from meme_learning import decision_context
        context_record=decision_context(snapshot,self.rules,assessment if side=='buy' else None)
        intent=dict(id=str(uuid.uuid4()),side=side,mint=snapshot['mint'],pool=snapshot['pool'],kind=snapshot['market_kind'],
            take_profit_usd=profit_target,max_loss_usd=loss_limit,entry_context=context_record,onchain_risk=risk_report,entry_profile=profile_name,input_units=units,minimum=context['min_output'],sol_price_usd=sol_price,bar=bar,reason=reason,
            base_account=str(market['base_user']),quote_account=str(market['quote_user']),simulation=simulated,
            message_hash=context['message_hash'],last_valid_block_height=context['last_valid_block_height'])
        if not self.execute:
            self.store.save('unsigned_simulation',intent)
            return {'status':'unsigned_simulation_passed','intent':intent,'transactions_submitted':0}
        # Reserve before signing. Journal signed bytes BEFORE any send. An
        # ambiguous send remains pending across restart and is never replayed.
        from solders.transaction import VersionedTransaction
        from solders.message import to_bytes_versioned
        if str(self.signer.pubkey())!=self.user: raise ValueError('Signing wallet mismatch')
        if hashlib.sha256(to_bytes_versioned(unsigned.message)).hexdigest()!=context['message_hash']:
            raise ValueError('Message changed after simulation')
        if time.time()-market['read_at']>20: raise ValueError('Quote expired before signing')
        intent['pre_inventory']={key:market[key] for key in ('native_lamports','base_balance','quote_balance')}
        intent['token_program']=str(market['token_program'])
        s['pending']=intent;self.store.save('reserved',intent)
        signed=VersionedTransaction(unsigned.message,[self.signer])
        self.orders.submit(intent['id'],str(signed.signatures[0]),bytes(signed))
        confirmed=self.recover()
        if risk_report is not None: confirmed['risk']=risk_report
        return confirmed
