"""Public user-profile research collector; cannot sign or send transactions."""
import argparse
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import time
from zoneinfo import ZoneInfo
from manual_research import ManualResearch,verify_profile,bot_signatures
from paper_sources import PublicSources
from wallet_watch import PublicRpc,MAINNET


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile',default='LunaMoth69')
    parser.add_argument('--wallet',help='Public wallet address; otherwise use the existing public wallet observation')
    parser.add_argument('--state-dir',type=Path,default=Path('state/manual_research'))
    parser.add_argument('--since',default='2026-02-01')
    parser.add_argument('--rpc',default='https://api.mainnet-beta.solana.com')
    parser.add_argument('--orders-db',type=Path,default=Path('state/meme_live/orders.sqlite'))
    parser.add_argument('--paper-db',type=Path,default=Path('state/paper_volume/paper.sqlite'))
    parser.add_argument('--loop',action='store_true')
    args=parser.parse_args();os.umask(0o077)
    wallet=args.wallet or json.loads(Path('state/wallet_observation.json').read_text())['wallet']
    since=datetime.strptime(args.since,'%Y-%m-%d').replace(tzinfo=ZoneInfo('America/New_York')).timestamp()
    args.state_dir.mkdir(parents=True,exist_ok=True)
    with (args.state_dir/'collector.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        from meme_learning import export_report
        def update_bot_research():
            return export_report(args.orders_db.with_name('meme_live.sqlite'),args.paper_db,Path('state/research/bot_outcomes.json'))
        rpc=PublicRpc(args.rpc)
        while True:
            update_bot_research()
            try:
                identity=verify_profile(wallet,args.profile,PublicSources().fetch)
                if rpc.call('getGenesisHash',[])!=MAINNET: raise ValueError('Research requires mainnet RPC')
                break
            except Exception as error:
                if not args.loop: raise
                print('Research startup waiting for public profile/mainnet validation ('+type(error).__name__+').',flush=True)
                time.sleep(60)
        archive=ManualResearch(args.state_dir,identity,since)
        try:
            print('Verified public profile. Collecting historical research only; no trading.',flush=True)
            while True:
                update_bot_research()
                try: report=archive.collect(rpc,bot_signatures(args.orders_db))
                except Exception as error:
                    report={**archive.report(),'collection_error_type':type(error).__name__}
                    tmp=args.state_dir/'summary.tmp';tmp.write_text(json.dumps(report,indent=2));tmp.replace(args.state_dir/'summary.json')
                print(json.dumps({k:report.get(k) for k in ['transactions_archived','known_pump_trades_nonbot','pending_receipts','known_bot_orders_archived','collection_error_type']}),flush=True)
                if not args.loop: break
                time.sleep(60)
        finally: archive.close()


if __name__=='__main__':
    try: main()
    except KeyboardInterrupt: pass
    except Exception as error:
        print('Public research stopped ('+type(error).__name__+'); no secrets or transactions involved.')
        raise SystemExit(1)
