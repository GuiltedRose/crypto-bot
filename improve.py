"""Autonomous bounded strategy research using recorded history and actual fills.

Never imports wallet configuration, sends orders, edits code or clears halts.
"""
import argparse
import fcntl
import json
import os
import sqlite3
import time
from pathlib import Path
from adaptive import AdaptationStore,PROFILES
from evaluate import Costs
from portfolio import market_data
from scan_markets import load_assets


def read_runtime(path='state/runtime.sqlite'):
    db=sqlite3.connect('file:'+str(Path(path).resolve())+'?mode=ro',uri=True)
    try:return json.loads(db.execute('SELECT data FROM runtime WHERE id=1').fetchone()[0])
    finally:db.close()


def cycle(store,now):
    state=read_runtime()
    store.deploy_or_rollback(state,now)
    status_path=Path('state/last_status.json')
    status=json.loads(status_path.read_text()) if status_path.exists() else {}
    equity=status.get('equity_usd_estimate',4)
    notional=max(.000001,min(equity*.01,max(0,equity-1)))
    report={'timestamp':now,'objective':'improve after-cost profit with fixed live risk limits',
        'profiles':list(PROFILES),'markets':[],'live_risk_limits_changed':False}
    assets=load_assets()
    for asset in assets:
        if not asset.get('approved_for_live'):continue
        mint=asset['mint']
        try:
            candles,_=market_data(asset,now)
            archived=store.archive(mint,candles,now)
            # Charge at least the conservative startup assumption. Actual fee
            # observations can raise the cost model, never lower it automatically.
            fees=[f['fee_usd'] for f in state.get('fills',[]) if f.get('mint')==mint]
            fixed=max(.005,max(fees,default=0))
            result=store.research(mint,archived,Costs(notional,.003,.005,fixed))
            report['markets'].append({'symbol':asset['symbol'],'mint':mint,'archived_bars':len(archived),**result})
        except (OSError,ValueError,KeyError,TypeError,sqlite3.Error) as error:
            report['markets'].append({'symbol':asset['symbol'],'mint':mint,'accepted':False,
                                      'reason':'history/evaluation unavailable','error_type':type(error).__name__})
    from research_history import backfill_one
    report['historical_backfill']=backfill_one(store,assets,now)
    # Re-read state before activation; new settings only affect future entries.
    store.deploy_or_rollback(read_runtime(),int(time.time()))
    from arbitrage import status as arbitrage_status
    from sniping import status as snipe_status
    report['event_strategies']={'arbitrage':arbitrage_status(),'sniping':snipe_status(equity)}
    report['active']=[{'mint':m,'profile':p,'activated':at} for m,p,at in store.db.execute('SELECT mint,profile,activated FROM active')]
    report['recent_events']=[{'at':at,'mint':m,'action':a,'detail':d} for at,m,a,d in store.db.execute('SELECT at,mint,action,detail FROM events ORDER BY id DESC LIMIT 20')]
    target=Path('state/improvement_status.json')
    temporary=target.with_suffix('.tmp')
    temporary.write_text(json.dumps(report,indent=2,allow_nan=False))
    os.replace(temporary,target)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--loop',action='store_true')
    args=parser.parse_args()
    os.umask(0o077)
    Path('state').mkdir(exist_ok=True)
    lock=open('state/improvement.lock','w')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    store=AdaptationStore()
    last_hour=None
    while True:
        now=int(time.time())
        if now//3600!=last_hour:
            report=cycle(store,now)
            last_hour=now//3600
            print(json.dumps({'status':'improvement review complete','markets':len(report['markets']),
                'accepted':sum(bool(m['accepted']) for m in report['markets']),'active':report['active']}),flush=True)
        if not args.loop:break
        time.sleep(30)


if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:pass
    except Exception as error:
        print('Improvement worker stopped ('+type(error).__name__+'); live risk limits unchanged.',flush=True)
        raise SystemExit(1)
