"""Pump meme strategy: unsigned simulation by default, isolated live state.

Real execution requires --execute and a locally configured wallet. Never
inherits or promotes paper cash, positions, proof flags or simulated fills.
"""
import argparse
import json
import math
import os
from pathlib import Path
import time
from live import Rpc, LiveOrders
from meme_dip import DipRules, SOL_MINT, USDC_MINT
from meme_execution import TOKEN, TOKEN2022, WSOL
from solders.pubkey import Pubkey
from meme_live import MemeLiveStore, MemeLiveEngine, positive
from pump_market import PumpMarketSources
from paper_sources import UnsupportedMarket
from pump_narratives import ROOT, valid_mint
from wallet_signals import SharedWalletFeed, ensure_worker, load_watchlist, rank_candidate_mints


class SimulationRpc:
    """Unsigned mode has no RPC ability to submit any transaction."""
    ALLOWED={'getGenesisHash','getMultipleAccounts','getLatestBlockhash','getFeeForMessage',
             'simulateTransaction','getMinimumBalanceForRentExemption','getBalance','getTokenAccountsByOwner','getTokenLargestAccounts','getSignaturesForAddress','getTransaction'}
    def __init__(self,rpc): self.rpc=rpc
    def call(self,method,params):
        if method not in self.ALLOWED: raise ValueError('RPC method forbidden in unsigned mode')
        return self.rpc.call(method,params)


def valuation(sources):
    now=time.time();cached=getattr(sources,'_recent_sol_valuation',None)
    if cached and 0<=now-cached['retrieved_at']<=15 and 0<=now-cached['source_at']<=120:
        return cached['price']
    row=sources.fetch(ROOT+'/sol-price');now=time.time()
    if row.get('stale') is not False or not 0<=now-float(row['asOfTimestamp'])/1000<=120:
        raise ValueError('Fresh SOL/USD valuation unavailable')
    price=positive(float(row['solPrice']))
    sources._recent_sol_valuation={'retrieved_at':now,'source_at':float(row['asOfTimestamp'])/1000,'price':price}
    return price


def wallet_equity(rpc,user,price,position,sources,rent_accounts=()):
    """Value native SOL + all known SPL/Token-2022 holdings without fake zeros.

    Unpriceable holdings are exposed as incomplete; promotion/demotion waits
    for a complete valuation when the $1k tier could be affected.
    """
    native=rpc.call('getBalance',[user,{'commitment':'confirmed'}])['value']/1e9
    holdings={};unknown=[]
    for program in (TOKEN,TOKEN2022):
        response=rpc.call('getTokenAccountsByOwner',[user,{'programId':str(program)},{'encoding':'jsonParsed','commitment':'confirmed'}])
        for row in response['value']:
            try:
                info=row['account']['data']['parsed']['info']
                if info['owner']!=user: raise ValueError('Wallet inventory owner mismatch')
                mint=info['mint'];units=int(info['tokenAmount']['amount']);decimals=info['tokenAmount']['decimals']
                if units<0 or type(decimals) is not int or not 0<=decimals<=18: raise ValueError('Invalid token quantity')
                if not units: continue
                if mint in holdings and holdings[mint][1]!=decimals: raise ValueError('Inconsistent token decimals')
                holdings[mint]=(holdings.get(mint,(0,decimals))[0]+units,decimals)
            except (KeyError,TypeError): unknown.append(row.get('pubkey','unparsed_token_account'))
    equity=native*price;now=time.time()
    cache=getattr(sources,'_bankroll_price_cache',{});sources._bankroll_price_cache=cache
    for mint,(units,decimals) in holdings.items():
        quantity=units/(10**decimals)
        if mint==SOL_MINT: equity+=quantity*price;continue
        if mint==USDC_MINT: equity+=quantity;continue
        cached=cache.get(mint)
        if cached and 0<=now-cached['at']<=60:
            value=cached['price']
        else:
            try:
                if not valid_mint(mint): raise ValueError('Invalid holding mint')
                row=sources.fetch(ROOT+'/coins-v3/'+mint+'?includeLiveStreamInfo=false')
                if row['mint']!=mint or row.get('chain_id') not in (None,'solana','solana:mainnet','solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp'): raise ValueError('Holding-price identity mismatch')
                value=float(row['price_usd'])
                if not math.isfinite(value) or value<0: raise ValueError('Holding price unavailable')
            except Exception: value=None
            cache[mint]={'at':time.time(),'price':value}
        if value is None: unknown.append(mint)
        else: equity+=quantity*value
    if rent_accounts:
        import base64
        accounts=rpc.call('getMultipleAccounts',[list(rent_accounts),{'encoding':'base64','commitment':'confirmed'}])['value']
        for address,account in zip(rent_accounts,accounts):
            if account is None: continue
            raw=base64.b64decode(account['data'][0],validate=True)
            if len(raw)<165 or raw[32:64]!=bytes(Pubkey.from_string(user)): raise ValueError('Tracked rent account identity changed')
            backing=int.from_bytes(raw[64:72],'little') if raw[:32]==bytes(WSOL) else 0
            equity+=max(0,account['lamports']-backing)/1e9*price
    sources._bankroll_valuation={'complete':not unknown,'known_value_usd':equity,'unpriced_mints':unknown,'observed_at':time.time()}
    return equity


def tick(engine,sources,feed,wallets,mints,discover=True,progress=None):
    if progress: progress({'status':'checking_wallet_and_market'})
    if engine.store.state['pending']: return engine.recover()
    engine.store.refresh_learning_model()
    price=valuation(sources)
    position=engine.store.state['position']
    # Resolve a held mint even if its old bonding curve has migrated. The
    # adapter validates actual mint, pool PDA and native SOL quote on chain.
    if position:
        row=sources.observation(position['mint'])
        if row['market_kind']!=position['kind'] or row['pool']!=position['pool']:
            from meme_execution import MemeAdapter
            candidate=MemeAdapter(engine.rpc,engine.user,row['mint'],row['pool'],row['market_kind'])
            candidate.snapshot()
            position.update(pool=row['pool'],kind=row['market_kind'])
            engine.store.save('verified_migration',{'mint':row['mint'],'pool':row['pool'],'kind':row['market_kind']})
        equity=wallet_equity(engine.rpc,engine.user,price,None,sources,engine.store.state.get('rent_accounts',[]))
        engine.bankroll_valuation=getattr(sources,'_bankroll_valuation',{'complete':True})
        return engine.prepare(row,price,equity)
    equity=wallet_equity(engine.rpc,engine.user,price,None,sources,engine.store.state.get('rent_accounts',[]))
    engine.bankroll_valuation=getattr(sources,'_bankroll_valuation',{'complete':True})
    engine.mark(equity)
    if engine.store.state['halted']: return {'status':'drawdown_halted','risk':engine.store.state.get('strategy_risk',{})}
    context=feed.poll(wallets)
    ranked=rank_candidate_mints(context)
    errors={};skipped={};discovered=[]
    if discover:
        try: discovered=[row['mint'] for row in sources.discover()]
        except Exception as error: errors['discovery']=type(error).__name__
    from meme_watchlist import CandidateWatchlist,token_analysis
    watchlist=CandidateWatchlist(engine.store.db)
    due=watchlist.due(10)
    pinned=list(dict.fromkeys(due+mints+ranked[:2]))[:10]
    remaining=[mint for mint in dict.fromkeys(ranked+discovered) if mint not in pinned]
    cursor=engine.store.state.get('scan_cursor',0)
    count=min(10-len(pinned),len(remaining))
    rotated=[remaining[(cursor+i)%len(remaining)] for i in range(count)] if remaining else []
    candidates=pinned+rotated
    engine.store.state['scan_cursor']=(cursor+count)%len(remaining) if remaining else 0
    engine.store.save('scan_candidates',{'mints':candidates})
    decisions=[]
    for index,mint in enumerate(candidates,1):
        if progress: progress({'status':'scanning_candidate','mint':mint,'candidate':index,'total_candidates':len(candidates)})
        try:
            row=sources.observation(mint)
            fresh=feed.read_context(wallets)
            row['research_context']={'source_status':fresh.get('status','unknown'),'hot_tokens':[r for r in fresh.get('hot_tokens',[]) if r['mint']==mint],
                'hot_token_source':fresh.get('hot_token_source',{}),'callouts':[r for r in fresh.get('callouts',[]) if r['mint']==mint],
                'wallet_signals':[r for r in fresh.get('signals',[]) if r['mint']==mint]}
            # Fetch a fresh valuation after potentially slow market requests.
            current_price=valuation(sources)
            current_equity=wallet_equity(engine.rpc,engine.user,current_price,None,sources,engine.store.state.get('rent_accounts',[]))
            engine.bankroll_valuation=getattr(sources,'_bankroll_valuation',{'complete':True})
            result=engine.prepare(row,current_price,current_equity)
            if getattr(engine,'last_profile_selection',None): result['profile_selection']=engine.last_profile_selection
            result['watchlist']=watchlist.observe(row,result,engine.rules)
            decisions.append({'mint':mint,**result})
            if progress: progress(token_analysis(row,result,engine.rules,time.time()))
            if result['status'] in ('confirmed_buy','unsigned_simulation_passed') or engine.store.state['pending']:
                break
        except UnsupportedMarket:
            watchlist.remove(mint)
            skipped[mint]='unsupported market or quote asset'
            if progress: progress({'status':'unsupported_market','mint':mint,'reason':skipped[mint]})
        except Exception as error:
            watchlist.failed(mint)
            errors[mint]=type(error).__name__
            if progress: progress({'status':'candidate_source_error','mint':mint,'error_type':type(error).__name__})
    return {'status':'scan_complete','decisions':decisions,'source_errors':errors,'skipped_markets':skipped,
            'realized_pnl_usd':engine.store.state['realized_pnl_usd'],'execution_enabled':engine.execute,'watched_candidates':watchlist.count(),'strategy_risk':engine.store.state.get('strategy_risk',{}),
            'neural_autonomy':{key:value for key,value in engine.store.state.get('neural_autonomy',{}).items() if key!='candidate'}}


def cycle_rest(state,interval):
    from meme_profiles import response_rest
    active=state.get('position') or state.get('pending')
    if active: return min(interval,response_rest(active.get('entry_profile'),holding=True))
    recent=state.get('last_entry_regime')
    if recent and 0<=time.time()-recent['observed_at']<=120: return min(interval,response_rest(recent['profile']))
    return interval


def startup_error(error):
    """Only fixed, known diagnostics may reach the console; never echo URLs/keys."""
    if isinstance(error,BlockingIOError):
        return 'Another live runner holds the wallet lock. Stop the old process before restarting.'
    messages={
        'Another meme runner holds this state':'Another meme runner is active. Stop that process before restarting.',
        'Live state wallet/rules differ; use --adopt-rules only while flat with no unresolved orders':
            'Rule adoption blocked: saved wallet differs, a position is open, or an order is unresolved. Reconcile pending orders using the saved rules before using --adopt-rules while flat. State was preserved.',
        'Execution state mode mismatch':'This state directory belongs to another execution mode. Use separate unsigned and live state directories.',
        'Unbound unresolved orders require manual reconciliation':'An unresolved submitted order is missing its strategy binding. Reconcile it before trading; keep all state files.',
        '.env must be accessible only to its owner (chmod 600 .env)':'.env permissions are too broad. Run chmod 600 .env.',
        'Cannot load wallet: check secret format and keypair file permissions locally':'Cannot load the local wallet. Check the secret format or keypair file permissions without sharing the secret.',
        'Set exactly one of SOLANA_KEYPAIR_PATH or SOLANA_PRIVATE_KEY locally':'Configure exactly one local signing source: SOLANA_KEYPAIR_PATH or SOLANA_PRIVATE_KEY.',
        'Set SOLANA_RPC_URL to an HTTPS RPC endpoint':'SOLANA_RPC_URL must be configured as an HTTPS endpoint.',
        'Risk RPC must be mainnet':'The selected risk RPC is not Solana mainnet.',
    }
    known=messages.get(str(error))
    if known: return known
    return 'Meme runner stopped ('+type(error).__name__+'); credentials suppressed. Check configuration and saved state; do not delete order journals.'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wallet',help='Public wallet address for unsigned simulation; no private key needed')
    parser.add_argument('--rpc',default='https://api.mainnet-beta.solana.com')
    parser.add_argument('--risk-rpc',help='Optional HTTPS read-only RPC for indexed holder/trade queries; never receives signed transactions')
    parser.add_argument('--reconcile-only',action='store_true',help='Check the original pending transaction once; never scan, sign or submit a new order')
    parser.add_argument('--adopt-rules',action='store_true',help='Explicitly adopt changed rules only when flat with no unresolved orders; preserves P&L and drawdown halt')
    parser.add_argument('--neural-mode',choices=['learning','qualified','off'],default='learning',help='Experimental online entry veto (default); qualified requires prospective evidence; off uses rule entries')
    parser.add_argument('--profile',choices=['current','moderate','aggressive','adaptive'],help='Shared entry-tolerance profile; dollar limits and slippage are unchanged')
    parser.add_argument('--rules',type=Path,default=Path('meme_live_rules.json'))
    parser.add_argument('--state-dir',type=Path)
    parser.add_argument('--learning-paper-db',type=Path,default=Path('state/paper_volume/paper.sqlite'),help='Closed eligible paper outcomes for shared shadow weights; refunds and synthetic fills excluded')
    parser.add_argument('--watchlist',type=Path,default=Path('paper_watchlist.json'))
    parser.add_argument('--wallet-source-dir',type=Path,default=Path('state/wallet_watch'))
    parser.add_argument('--mint',action='append',default=[])
    parser.add_argument('--evidence-dir',type=Path)
    parser.add_argument('--no-discover',action='store_true')
    parser.add_argument('--loop',action='store_true')
    parser.add_argument('--json',action='store_true',help='Emit JSON console records instead of readable text; status.json remains JSON either way')
    parser.add_argument('--interval',type=float,default=30,help='Rest between flat-wallet scans (default 30s); open/pending positions rest at most 15s between checks')
    parser.add_argument('--execute',action='store_true',help='USER CONTROLLED: sign and submit real-money transactions')
    args=parser.parse_args()
    if not 10<=args.interval<=3600: parser.error('Interval must be 10 through 3600 seconds')
    if len(args.mint)>5 or any(not valid_mint(mint) for mint in args.mint): parser.error('At most five valid mints')
    from meme_profiles import selected
    base_rules=DipRules(**json.loads(args.rules.read_text()))
    rules=selected(base_rules,args.profile)
    wallets=load_watchlist(args.watchlist)
    if rules.entry_style not in ('trend_pullback','volume_candles') or min(rules.position_cap(1),*rules.exit_limits(1))<=0:
        parser.error('Live meme profile requires a supported entry policy and explicit dollar limits')
    os.umask(0o077)
    signer=orders=None
    if args.execute:
        from config import LiveConfig
        from wallet import load_wallet
        config=LiveConfig.from_env();signer=load_wallet(config);user=str(signer.pubkey());rpc=Rpc(config.rpc_url)
        if args.wallet and args.wallet!=user: parser.error('Public wallet differs from signing wallet')
    else:
        if not args.wallet or not valid_mint(args.wallet): parser.error('Unsigned mode requires a public --wallet address')
        user=args.wallet;rpc=SimulationRpc(Rpc(args.rpc))
    directory=args.state_dir or Path('state/meme_live' if args.execute else 'state/meme_unsigned')
    if directory.resolve() in (Path('state').resolve(),Path('state/paper_memes').resolve()):
        parser.error('Use an isolated meme execution directory')
    store=MemeLiveStore(directory,user,rules,adopt_rules=args.adopt_rules,
        learning_wallet_db=args.wallet_source_dir/'wallet_watch.sqlite',
        followed_wallets=wallets,learning_paper_db=args.learning_paper_db)
    try:
        # Bind mode as well as wallet/rules: never silently reuse unsigned state
        # (marks, simulated observations) as a funded live experiment.
        mode_path=directory/'mode.json';mode={'execute':args.execute}
        if mode_path.exists() and json.loads(mode_path.read_text())!=mode: raise ValueError('Execution state mode mismatch')
        mode_path.write_text(json.dumps(mode))
        if args.execute:
            # Shared lock prevents legacy and meme live runners spending the
            # same wallet concurrently; unsigned mode never acquires this lock.
            import fcntl
            global_lock=Path('state/runner.lock').open('a')
            fcntl.flock(global_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            orders=LiveOrders(rpc,str(directory/'orders.sqlite'))
        risk_assessor=None
        if rules.entry_style!='volume_candles' or rules.momentum_reclaim_enabled:
            from meme_onchain_risk import OnchainRisk
            risk_rpc=SimulationRpc(Rpc(args.risk_rpc)) if args.risk_rpc else SimulationRpc(rpc)
            if args.risk_rpc:
                from wallet import MAINNET_GENESIS
                if risk_rpc.call('getGenesisHash',[])!=MAINNET_GENESIS: raise ValueError('Risk RPC must be mainnet')
            risk_assessor=OnchainRisk(risk_rpc)
        engine=MemeLiveEngine(rpc,user,store,rules,orders,signer,risk_assessor=risk_assessor,fixed_profile=args.profile if args.profile!='adaptive' else None)
        engine.neural_mode=args.neural_mode
        def publish(result):
            record={'timestamp':time.time(),'mode':'live' if args.execute else 'unsigned_simulation',**result}
            tmp=directory/'status.tmp';tmp.write_text(json.dumps(record,allow_nan=False));tmp.replace(directory/'status.json')
            from meme_console import format_status
            print(json.dumps(record) if args.json else format_status(record,rules),flush=True)
        publish({'status':'started','strategy_profile':args.profile or 'configured','execution_enabled':args.execute,'position_open':store.state['position'] is not None,
                 'pending_order':store.state['pending'] is not None})
        if args.reconcile_only:
            publish(engine.recover())
            return
        try: ensure_worker(args.watchlist,args.wallet_source_dir,args.rpc)
        except (OSError,ValueError): pass
        feed=SharedWalletFeed(args.wallet_source_dir)
        sources=PumpMarketSources(args.evidence_dir,include_legacy_metrics=rules.entry_style!='volume_candles')
        while True:
            try: result=tick(engine,sources,feed,wallets,args.mint,not args.no_discover,progress=publish)
            except Exception as error:
                result={'status':'cycle_error_no_replacement_orders','error_type':type(error).__name__}
            delay=cycle_rest(store.state,args.interval)
            if args.loop: result['next_check_seconds']=delay
            publish(result)
            if not args.loop: return 0
            time.sleep(delay)
    finally:
        if orders: orders.db.close()
        store.close()


if __name__=='__main__':
    try: raise SystemExit(main())
    except KeyboardInterrupt: print('Stopped. Open positions are preserved; no automatic liquidation.')
    except Exception as error:
        print(startup_error(error))
        raise SystemExit(1)
