"""Live runner with five-win promotion from one to two position slots.

Requires an actively maintained, complete SOL/USDC pool JSONL recording.
"""
import argparse
import fcntl
import json
import os
import subprocess
import sys
import time
import urllib.error
from pathlib import Path
from config import LiveConfig
from wallet import load_wallet
from live import Rpc, LiveOrders
from pumpswap import PumpSwap,SnapshotChanged
from execution import Execution
from runtime import Coordinator
from market import pool_candles
from bot import SOL_MINT
from history import load_history
from strategy import WARMUP_BARS,validate,DONCHIAN_ID


def review_stop(state):
    return state.get('review_halted',False) and not state.get('positions') and not state.get('position') and not state.get('pending')


def cycle_result(engine,events_path,ticker=None):
    """Retry only known data/network conditions; uncertainty stays journaled."""
    try:
        return (ticker or tick)(engine,events_path),False
    except urllib.error.HTTPError as error:
        transient=error.code in (408,429,500,502,503,504)
        error.close()
        return {'status':'temporary RPC issue; retrying without replacement orders' if transient else 'HTTP request rejected; inspect before resuming',
                'error_type':'HTTPError','http_status':error.code},not transient
    except (SnapshotChanged,urllib.error.URLError,TimeoutError,ConnectionError) as error:
        return {'status':'temporary snapshot/RPC issue; retrying without replacement orders',
                'error_type':type(error).__name__},False
    except Exception as error:
        known={'Swap simulation failed; no transaction signed':'simulation_rejected',
               'Quote expired':'quote_expired','Stale pool snapshot':'snapshot_expired',
               'Quote expired during simulation':'quote_expired',
               'Pool PDA mismatch':'pool_identity_mismatch',
               'Network fee exceeds limit':'fee_limit',
               'Insufficient prepared token balance; inventory setup needed':'token_balance_insufficient'}
        return {'status':'cycle stopped; inspect/reconcile before resuming',
                'error_type':type(error).__name__,
                'error_code':known.get(str(error),'unclassified_validation_error')},True


def tick(engine,events_path,history_path='data/verified_pool_hourly.csv'):
    if review_stop(engine.coordinator.read()):
        return {'status':'stopped after two consecutive net losses; manual review required','stop_runner':True}
    adapter=engine.adapter
    s=adapter.snapshot()
    recovery=engine.recover(s['price'])
    if review_stop(engine.coordinator.read()):
        return {'status':'stopped after two consecutive net losses; manual review required','stop_runner':True}
    if recovery!='idle':
        return {'status':recovery}
    state=engine.coordinator.read()
    sol_index=0 if str(s['base'])==SOL_MINT else 1
    wrapped=s['user_balances'][sol_index]/1e9
    usdc=s['user_balances'][1-sol_index]/1e6
    native=s['native_lamports']/1e9
    equity=usdc+(wrapped+native)*s['price']
    now=int(time.time())
    candles=[]
    if Path(history_path).exists():
        try:
            candles=load_history(history_path,now,str(adapter.pool))
        except (ValueError,KeyError,TypeError,OSError):
            if not state['position']:
                return {'status':'verified history provenance failed; entries blocked'}
    seed_end=candles[-1].timestamp if candles else -1
    if Path(events_path).exists():
        try:
            with open(events_path) as source:
                recorded=pool_candles((json.loads(line) for line in source if line.strip()),str(adapter.pool),now)
            candles=candles+[c for c in recorded if c.timestamp>seed_end]
            validate(candles,now)
        except (ValueError,KeyError,TypeError):
            if not candles and not state['position']:
                return {'status':'invalid/incomplete pool history; entries blocked'}
            # Provider-verified complete candles remain usable independently
            # of the collector. Never bridge a missing hour with a flat candle.
            candles=[c for c in candles if c.timestamp<=seed_end]
    # Native SOL is already market exposure. Do not treat an inherited wallet
    # balance as stable reserves, or automatically liquidate it to start trading.
    if not state['position'] and native>.00502:
        return {'status':'inventory setup required: native SOL exceeds fee reserve',
                'native_sol':native,'usdc':usdc,'wrapped_sol':wrapped,'equity_usd_estimate':equity}
    if not state['position'] and usdc<=1:
        return {'status':'funded USDC trading balance and $1 reserve required'}
    if not state['position'] and len(candles)<WARMUP_BARS:
        return {'status':f'waiting for {WARMUP_BARS} complete contiguous pool candles','candles':len(candles)}
    # Current reserve mark is used only for sizing. Swap itself has hard output
    # bounds and is simulated again against freshly fetched pool state.
    cost=2*.000005*s['price'] + equity*.01*2*(s['fee_bps']/10000+.005)
    intent,reason=engine.coordinator.plan(candles,now,s['price'],equity,cost,wrapped)
    if intent:
        result=engine.submit_reserved(intent)
        if review_stop(engine.coordinator.read()):
            return {'status':'stopped after two consecutive net losses; manual review required','stop_runner':True}
        return {'status':result,'reason':reason,'side':intent['side'],
                'promotion':engine.coordinator.promotion_status()}
    return {'status':'waiting','reason':reason,'strategy':DONCHIAN_ID,'candles':len(candles),
            'promotion':engine.coordinator.promotion_status()}


def start_improvement_worker(state_dir):
    """Start at most one researcher with a clean, credential-free environment."""
    probe=(state_dir/'improvement.lock').open('a')
    try:
        fcntl.flock(probe,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        probe.close()
        return
    probe.close()
    # The runner has loaded .env; do not pass its wallet or RPC credentials to
    # the research subprocess. It only consumes local market/trade records.
    environment={'PATH':os.environ.get('PATH','/usr/bin:/bin'),'LANG':'C.UTF-8','PYTHONUNBUFFERED':'1'}
    with (state_dir/'improvement.log').open('a') as log:
        subprocess.Popen([sys.executable,str(Path(__file__).with_name('improve.py')),'--loop'],
                         stdout=log,stderr=subprocess.STDOUT,env=environment,start_new_session=True)


def start_discovery_worker(state_dir):
    probe=(state_dir/'discovery.lock').open('a')
    try:
        fcntl.flock(probe,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        probe.close()
        return
    probe.close()
    environment={'PATH':os.environ.get('PATH','/usr/bin:/bin'),'LANG':'C.UTF-8','PYTHONUNBUFFERED':'1'}
    with (state_dir/'discovery.log').open('a') as log:
        subprocess.Popen([sys.executable,str(Path(__file__).with_name('discover_memes.py')),'--loop'],
                         stdout=log,stderr=subprocess.STDOUT,env=environment,start_new_session=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--events',required=True,help='Complete, current pool event JSONL recording')
    parser.add_argument('--once',action='store_true')
    parser.add_argument('--watchlist',type=Path,default=Path('paper_watchlist.json'))
    parser.add_argument('--wallet-source-dir',type=Path,default=Path('state/wallet_watch'))
    parser.add_argument('--sol-only',action='store_true',help='Use legacy direct SOL pool only')
    args=parser.parse_args()
    os.umask(0o077)
    state_dir=Path('state')
    state_dir.mkdir(exist_ok=True)
    lock=(state_dir/'runner.lock').open('w')
    try:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        raise ValueError('Another live runner is active') from None
    config=LiveConfig.from_env()
    adapter=PumpSwap(Rpc(config.rpc_url),load_wallet(config))
    binding=state_dir/'binding.json'
    expected={'wallet':str(adapter.user),'pool':str(adapter.pool)}
    if binding.exists() and json.loads(binding.read_text())!=expected:
        raise ValueError('State belongs to a different wallet/pool')
    binding.write_text(json.dumps(expected))
    from portfolio import PortfolioCoordinator,PortfolioExecution,tick_portfolio
    coordinator=(Coordinator if args.sol_only else PortfolioCoordinator)(str(state_dir/'runtime.sqlite'))
    orders=LiveOrders(adapter.rpc,str(state_dir/'orders.sqlite'))
    engine=(Execution if args.sol_only else PortfolioExecution)(coordinator,orders,adapter)
    if not args.sol_only:
        start_improvement_worker(state_dir)
        start_discovery_worker(state_dir)
    from wallet_signals import ensure_worker, load_watchlist, read_signals, live_context, unavailable
    try:
        ensure_worker(args.watchlist,args.wallet_source_dir)
    except (OSError,ValueError,KeyError,TypeError):
        pass  # Research availability must not stop reconciliation or exits.
    while True:
        try:
            watched=read_signals(load_watchlist(args.watchlist),args.wallet_source_dir)
        except (OSError,ValueError,KeyError,TypeError):
            watched=unavailable('wallet_source_unavailable')
        engine.wallet_signals=watched
        if args.sol_only:
            result,fatal=cycle_result(engine,args.events)
        else:
            result,fatal=cycle_result(engine,args.events,lambda *_:tick_portfolio(engine))
        from scan_markets import load_assets
        try:
            research_registry=load_assets()
        except (OSError,ValueError,KeyError,TypeError):
            research_registry=[]
        result['wallet_watch']=live_context(watched,research_registry)
        # Credential-free persistent status survives terminal/session loss.
        record={'timestamp':int(time.time()),**result}
        (state_dir/'last_status.json').write_text(json.dumps(record))
        with (state_dir/'events.jsonl').open('a') as log:
            log.write(json.dumps(record)+'\n')
        print(json.dumps(result),flush=True)
        if fatal:
            return 1
        if result.get('stop_runner'):
            return 0
        if args.once:
            return 0
        time.sleep(15)


if __name__=='__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print('Runner stopped. Open positions are NOT automatically sold.')
    except Exception as error:
        print('Live runner stopped ('+type(error).__name__+'). Credentials suppressed.')
        raise SystemExit(1)
