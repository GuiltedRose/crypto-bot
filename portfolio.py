"""One durable portfolio coordinator across all reviewed spot assets."""
import hashlib
import base64
import json
import math
import time
import uuid
from pathlib import Path
from solders.pubkey import Pubkey
from bot import SOL_MINT,Universe
from market import USDC_MINT
from strategy import Candle,Position,entry_budget,DONCHIAN_ID
from strategies import decide
from runtime import Coordinator
from scan_markets import load_assets,parse_candles
from meme_policy import MatureMemePolicy
from pumpswap import TOKEN
from tokens import ata,token_account,mint_decimals,program_for,TOKEN_2022
from jupiter import Jupiter
from execution import Execution,raw_units


def market_data(asset,now):
    from history import load_history
    if asset['mint']==SOL_MINT:
        bars=load_history('data/verified_pool_hourly.csv',now)
        try:
            from market import pool_candles
            from pumpswap import DEFAULT_POOL
            events=Path('data/live_pool.jsonl')
            if events.exists():
                recorded=pool_candles((json.loads(line) for line in events.read_text().splitlines() if line.strip()),DEFAULT_POOL,now)
                by_hour={bar.timestamp:bar for bar in bars}
                by_hour.update({bar.timestamp:bar for bar in recorded})
                merged=sorted(by_hour.values(),key=lambda bar:bar.timestamp)
                from strategy import validate
                validate(merged,now)
                bars=merged
        except (OSError,ValueError,KeyError,TypeError):
            pass
        return bars,True
    folder=Path('data/markets')/asset['mint']
    provenance=json.loads((folder/'provenance.json').read_text())
    raw=(folder/'source.json').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=provenance['sha256'] or not 0<=now-provenance['retrieved_at']<=3600:
        raise ValueError('Stale or altered market history')
    pool=provenance['pool']
    if pool['mint']!=asset['mint']:
        raise ValueError('Market mint mismatch')
    bars=parse_candles(json.loads(raw),asset['mint'],now)
    approved=True
    if asset['classification']=='mature_meme_candidate' or asset.get('screening_required'):
        risk_path=folder/'risk_source.json'
        # Fetch time is recorded separately so refreshing candles cannot renew
        # an old risk report. Existing caches without this field fail closed.
        checked=provenance.get('risk_retrieved_at',0)
        if not 0<=now-checked<3600:
            approved=False
        else:
            policy=MatureMemePolicy() if asset['classification']=='mature_meme_candidate' else MatureMemePolicy(token_observed_days=90,min_liquidity_usd=100000,min_daily_volume_usd=10000)
            approved=policy.assess(asset,pool,json.loads(risk_path.read_text()),now)['passed']
    return bars,approved


class PortfolioCoordinator(Coordinator):
    def initialize_inventory_baseline(self,balances,decimals,now):
        """Freeze inherited wallet holdings once; never absorb later mismatches."""
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            state=self.read()
            if 'inventory_baseline_units' in state:
                return state['inventory_baseline_units']
            if state['pending']:
                raise ValueError('Reconcile pending order before initializing portfolio')
            baseline={}
            for mint,actual in balances.items():
                expected=sum(raw_units(p['quantity'],decimals[mint]) for p in state['positions'] if p.get('mint',SOL_MINT)==mint)
                if actual<expected:
                    raise ValueError('Wallet inventory below recorded positions')
                baseline[mint]=actual-expected
            state['inventory_baseline_units']=baseline
            state['inventory_baseline_created_at']=now
            self._write(state)
            return baseline

    def plan_portfolio(self,markets,now,equity,universe):
        if not math.isfinite(equity) or equity<=0:
            raise ValueError('Invalid portfolio equity')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            state=self.read()
            if state['pending']:
                return None,'reconcile pending intent before trading'
            records=state['positions']
            for mint,market in markets.items():
                expected=sum(p['quantity'] for p in records if p.get('mint',SOL_MINT)==mint)
                if not math.isclose(expected,market['quantity'],rel_tol=1e-6,abs_tol=1/10**market['decimals']):
                    return None,'wallet inventory mismatch; reconcile '+mint
            if any(p.get('mint',SOL_MINT) not in markets for p in records):
                return None,'open position quote unavailable; entries blocked'
            peak=max(state['peak_equity'] or equity,equity)
            state['peak_equity']=peak
            state['halted']=state['halted'] or equity<=peak*.9
            intent=None
            reason='no eligible breakout'
            exits=[]
            for record in records:
                mint=record.get('mint',SOL_MINT)
                market=markets[mint]
                levels=dict(record['levels'])
                levels.setdefault('strategy_id',record.get('strategy_id'))
                signal=decide(market['candles'],now,market['price'],Position(**levels))
                if signal.action=='sell':
                    exits.append((record,signal,mint,market))
            if exits:
                record,signal,mint,market=min(exits,key=lambda pair:0 if 'stop' in pair[1].reason else 1)
                intent=dict(side='sell',mint=mint,decimals=market['decimals'],quantity=record['quantity'],
                            position_id=record['position_id'],strategy_id=record.get('strategy_id','legacy-pullback-v1'))
                reason=signal.reason
            elif state['review_halted']:
                reason='manual strategy review required'
            elif state['halted']:
                reason='drawdown halt'
            else:
                capacity=self.promotion_status(DONCHIAN_ID,state)['max_open_positions']
                if len(records)>=2:
                    reason='open position limit reached'
                else:
                    exposure=sum(p['quantity']*markets[p.get('mint',SOL_MINT)]['price'] for p in records)
                    active=[p.get('mint',SOL_MINT) for p in records]
                    processed=state.setdefault('last_entry_bars',{})
                    for mint,market in markets.items():
                        if not market['approved']:
                            continue
                        signal=decide(market['candles'],now,market['price'],rules=market.get('rules'))
                        if signal.action!='buy':
                            continue
                        capacity=self.promotion_status(signal.strategy_id,state)['max_open_positions']
                        if len(records)>=capacity:
                            reason='open position limit reached'
                            continue
                        bar=market['candles'][-1].timestamp
                        if processed.get(mint)==bar:
                            continue
                        history=state.get('asset_history',{}).get(mint,{}).get(signal.strategy_id,[])
                        existing=sum(p['quantity'] for p in records if p.get('mint',SOL_MINT)==mint)*market['price']
                        budget,reason=entry_budget(mint,equity,history,signal,market['price'],market['cost'],
                            open_exposure_usd=exposure,existing_token_usd=existing,active_mints=active,
                            universe=universe,max_open_positions=capacity,open_position_count=len(records))
                        if budget:
                            intent=dict(side='buy',mint=mint,decimals=market['decimals'],budget_usd=budget,
                                stop_distance=signal.stop_distance,target_distance=signal.target_distance,
                                take_profit=signal.take_profit,strategy_id=signal.strategy_id)
                            processed[mint]=bar
                            reason=signal.reason
                            break
            if intent:
                intent.update(order_id=str(uuid.uuid4()),created_at=now)
                state['pending']=intent
            self._write(state)
            return intent,reason


class PortfolioExecution:
    def __init__(self,coordinator,orders,sol_adapter):
        self.coordinator,self.orders,self.adapter=coordinator,orders,sol_adapter
        self.adapters={SOL_MINT:sol_adapter}

    def token_adapter(self,mint):
        if mint not in self.adapters:
            self.adapters[mint]=Jupiter(self.adapter.rpc,self.adapter.wallet,mint)
        return self.adapters[mint]

    def recover(self,sol_price):
        pending=self.coordinator.read()['pending']
        if pending and pending.get('kind')=='atomic_arbitrage':
            from atomic_execution import AtomicExecution
            return AtomicExecution(self.coordinator,self.orders,self.adapter).recover()
        adapter=self.token_adapter(pending.get('mint',SOL_MINT)) if pending else self.adapter
        return Execution(self.coordinator,self.orders,adapter).recover(sol_price)

    def submit_reserved(self,intent,sol_price):
        if intent['side']=='buy':
            from venue_policy import entry_allowed
            if not entry_allowed('pumpswap' if intent['mint']==SOL_MINT else 'jupiter'):
                self.coordinator.record_failed(intent['order_id'])
                raise ValueError('Venue eligibility blocks new entry')
        adapter=self.token_adapter(intent['mint'])
        if intent['mint']==SOL_MINT:
            return Execution(self.coordinator,self.orders,adapter).submit_reserved(intent)
        try:
            units=raw_units(intent['budget_usd'],6) if intent['side']=='buy' else raw_units(intent['quantity'],intent['decimals'])
            unsigned,context=adapter.build(intent['side'],units)
            round_trip_cost=0
            exit_rent=0
            if intent['side']=='buy':
                reverse=adapter.fetch(adapter.user,adapter.mint,USDC_MINT,context['expected_output'])
                from jupiter import validate_build
                reverse_setups=validate_build(reverse,adapter.user,adapter.mint,USDC_MINT,context['expected_output'])[0]
                missing=[ix for ix in reverse_setups if str(ix.accounts[1].pubkey) not in context['setup_addresses']]
                records=adapter.accounts([ix.accounts[1].pubkey for ix in missing]) if missing else []
                for ix,record in zip(missing,records):
                    if record is None:
                        length=165 if ix.accounts[5].pubkey==TOKEN else 170
                        exit_rent+=adapter.rpc.call('getMinimumBalanceForRentExemption',[length])
                round_trip_cost=max(0,intent['budget_usd']-int(reverse['outAmount'])/1e6)
            fee=adapter.simulate(unsigned,context)
            if intent['side']=='buy':
                quantity=context['expected_output']/10**intent['decimals']
                # Include entry rent, both network fees, round-trip swap costs
                # and worst-case slippage. Never round up to cover account rent.
                cost=(context['rent']+exit_rent+fee*2)/1e9*sol_price+round_trip_cost+intent['budget_usd']*.01
                if context['native']<context['rent']+exit_rent+fee*2+20000:
                    raise ValueError('Insufficient native fee reserve')
                if quantity*intent['target_distance']<1.5*cost:
                    raise ValueError('Trade target cannot cover fresh execution costs')
                if abs(intent['budget_usd']/quantity/intent['signal_price']-1)>.005:
                    raise ValueError('Execution price moved beyond signal tolerance')
            elif context['expected_output']/1e6 < intent['quantity']*intent['signal_price']*.995:
                raise ValueError('Execution price moved beyond signal tolerance')
            signed=adapter.sign(unsigned,context)
        except Exception:
            self.coordinator.record_failed(intent['order_id'])
            raise
        self.orders.submit(intent['order_id'],str(signed.signatures[0]),bytes(signed))
        return self.recover(sol_price)

    def maybe_arbitrage(self,equity,now):
        from atomic_execution import AtomicExecution
        from orca import HELPER
        from venue_policy import entry_allowed
        if not HELPER.exists():return {'status':'blocked','reason':'Orca helper must be built locally'}
        if not entry_allowed('orca') or not entry_allowed('pumpswap'):
            return {'status':'blocked','reason':'venue eligibility'}
        state=self.coordinator.read()
        if state['positions'] or state['pending'] or state['halted'] or state['review_halted'] or state.get('arbitrage_halted'):
            return {'status':'blocked','reason':'portfolio slot/order/halt'}
        if now-getattr(self,'last_atomic_scan',0)<60:return {'status':'waiting for next quote scan'}
        self.last_atomic_scan=now
        engine=AtomicExecution(self.coordinator,self.orders,self.adapter)
        try:
            unsigned,context=engine.prepare(equity)
            return {'status':engine.submit(unsigned,context,equity,now)}
        except ValueError as error:
            expected={'Worst-case atomic return cannot cover costs and profit','Arbitrage quotes stale or future-dated',
                      'Arbitrage exceeds startup allocation','Atomic transaction exceeds supported packet/signers',
                      'Orca public quote unavailable','Atomic simulation rejected; neither leg signed','Atomic quote expired',
                      'Atomic USDC funds insufficient','Atomic fee reserve insufficient','Atomic Kelly budget unavailable'}
            if str(error) not in expected:raise
            return {'status':'no executable atomic opportunity','reason':str(error)}


def tick_portfolio(engine):
    from run_live import review_stop
    state=engine.coordinator.read()
    if review_stop(state):
        return {'status':'stopped after two consecutive net losses; manual review required','stop_runner':True}
    snapshot=engine.adapter.snapshot()
    sol_price=snapshot['price']
    recovery=engine.recover(sol_price)
    if recovery!='idle':
        return {'status':recovery}
    now=int(time.time())
    assets=[a for a in load_assets() if a.get('approved_for_live',False)]
    rpc,user=engine.adapter.rpc,engine.adapter.user
    # Read all ATA balances together. Standard SPL identity/state checks are
    # identical to the direct adapter; no cross-token quantity aggregation.
    addresses=[ata(user,Pubkey.from_string(a['mint'])) for a in assets]
    all_records=engine.adapter.accounts(addresses+[Pubkey.from_string(a['mint']) for a in assets])[0]
    records=all_records[:len(assets)]
    account_exists={a['mint']:r is not None for a,r in zip(assets,records)}
    if not hasattr(engine,'rent_rates') or now-engine.rent_rates[0]>=3600:
        engine.rent_rates=(now,{str(TOKEN):rpc.call('getMinimumBalanceForRentExemption',[165]),
                               str(TOKEN_2022):rpc.call('getMinimumBalanceForRentExemption',[170])})
    mint_records={a['mint']:r for a,r in zip(assets,all_records[len(assets):])}
    balances={a['mint']:token_account(r,Pubkey.from_string(a['mint']),user) for a,r in zip(assets,records)}
    decimals_by_mint={a['mint']:mint_decimals(mint_records[a['mint']],a['mint']) for a in assets}
    baseline=engine.coordinator.initialize_inventory_baseline(balances,decimals_by_mint,now)
    managed_balances={mint:amount-baseline.get(mint,0) for mint,amount in balances.items()}
    if any(amount<0 for amount in managed_balances.values()):
        return {'status':'wallet inventory below preserved baseline; reconcile before trading'}
    sol_index=0 if str(snapshot['base'])==SOL_MINT else 1
    usdc=snapshot['user_balances'][1-sol_index]/1e6
    equity=usdc+snapshot['native_lamports']/1e9*sol_price
    markets={}
    blocked={}
    established=set()
    memes=set()
    approved_memes=set()
    for asset in assets:
        mint=asset['mint']
        if asset['classification']=='mature_meme_candidate':
            memes.add(mint)
        try:
            if mint==SOL_MINT:
                decimals=9
            else:
                decimals=mint_decimals(mint_records[mint],mint)
            quantity=managed_balances[mint]/10**decimals
            requires_screen=asset['classification']=='mature_meme_candidate' or asset.get('screening_required')
            authorities_clear=True
            if requires_screen:
                raw=base64.b64decode(mint_records[mint]['data'][0],validate=True)
                authorities_clear=not any(raw[:4]) and not any(raw[46:50])
            try:
                bars,approved=market_data(asset,now)
            except (ValueError,KeyError,TypeError,OSError):
                bars,approved=[],False
            from venue_policy import entry_allowed
            venue='pumpswap' if mint==SOL_MINT else 'jupiter'
            venue_ok=entry_allowed(venue)
            approved=approved and authorities_clear and venue_ok
            # Price exits from executable exact-input sell quotes whenever
            # holding a position, independent of candle/risk screening freshness.
            if quantity and mint!=SOL_MINT:
                document=engine.token_adapter(mint).fetch(user,mint,USDC_MINT,managed_balances[mint])
                from jupiter import validate_build
                validate_build(document,user,mint,USDC_MINT,managed_balances[mint],max_price_impact=None)
                price=int(document['outAmount'])/1e6/quantity
            elif mint==SOL_MINT:
                price=sol_price
            elif bars:
                price=bars[-1].close
            else:
                blocked[asset['symbol']]='market history unavailable'
                continue
            equity+=quantity*price
            # Missing ATA rent is accounted again against fresh execution.
            rent=0 if account_exists[mint] else engine.rent_rates[1][str(program_for(mint))]/1e9*sol_price
            from adaptive import active_profile
            profile,rules=active_profile(mint)
            markets[mint]=dict(profile=profile,rules=rules,price=price,quantity=quantity,decimals=decimals,candles=bars,
                               approved=approved,cost=2*.000005*sol_price+rent)
            if not venue_ok:
                blocked[asset['symbol']]='venue eligibility blocks new entries: '+venue
            if approved:
                (approved_memes if mint in memes else established).add(mint)
            elif venue_ok:
                blocked[asset['symbol']]='history or risk screening unavailable/stale/failed'
        except Exception:
            if managed_balances[mint] or any(p.get('mint',SOL_MINT)==mint for p in engine.coordinator.read()['positions']):
                raise
            blocked[asset['symbol']]='token metadata/route unavailable'
    for market in markets.values():
        market['cost']+=equity*.01*.015
    universe=Universe(frozenset(established),frozenset(memes),frozenset(approved_memes),now+30)
    if usdc<=1 and not engine.coordinator.read()['positions']:
        return {'status':'funded USDC balance and $1 reserve required','blocked':blocked}
    intent,reason=engine.coordinator.plan_portfolio(markets,now,equity,universe)
    if intent:
        # Persist the reference quote for both entry and exit price bounds.
        intent['signal_price']=markets[intent['mint']]['price']
        with engine.coordinator.db:
            state=engine.coordinator.read()
            state['pending']=intent
            engine.coordinator._write(state)
        try:
            result=engine.submit_reserved(intent,sol_price)
        except ValueError as error:
            expected={'Jupiter price impact exceeds entry limit','Trade target cannot cover fresh execution costs','Execution price moved beyond signal tolerance',
                      'Insufficient native fee reserve','Swap too small','Swap simulation failed; no transaction signed',
                      'Quote expired','Quote expired during simulation','Unsupported Jupiter instruction layout','Venue eligibility blocks new entry'}
            if str(error) not in expected or engine.coordinator.read()['pending']:
                raise
            return {'status':'entry preparation rejected; no trade submitted','reason':str(error),'mint':intent['mint']}
        return {'status':result,'mint':intent['mint'],'side':intent['side'],'reason':reason}
    atomic_status=engine.maybe_arbitrage(equity,now)
    from sniping import status as sniping_status
    return {'arbitrage':atomic_status,'sniping':sniping_status(equity),'status':'waiting','reason':reason,'markets_loaded':len(markets),'blocked':blocked,
            'adaptive_profiles':{mint:market['profile'] for mint,market in markets.items()},
            'equity_usd_estimate':equity,'preserved_wallet_inventory':{m:n for m,n in baseline.items() if n},'promotion':engine.coordinator.promotion_status()}
