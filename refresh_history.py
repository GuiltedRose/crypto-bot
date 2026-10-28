"""Refresh verified exact-pool history. Read-only network access; no signing."""
import argparse
import fcntl
import json
from pathlib import Path
import time
from config import LiveConfig
from live import Rpc
from wallet import load_wallet
from pumpswap import PumpSwap
from history import publish


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--loop',action='store_true')
    args=parser.parse_args()
    Path('state').mkdir(exist_ok=True)
    lock=open('state/history-refresh.lock','w')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    config=LiveConfig.from_env()
    adapter=PumpSwap(Rpc(config.rpc_url),load_wallet(config))
    while True:
        try:
            result=publish(adapter)
            print(json.dumps({'status':'verified history installed','candles':result['candles'],
                  'chain_samples':len(result['chain_samples']),'last_timestamp':result['last_timestamp']}),flush=True)
        except Exception as error:
            print(json.dumps({'status':'history refresh failed; previous verified file retained',
                              'error_type':type(error).__name__}),flush=True)
            if not args.loop:
                raise SystemExit(1)
        if not args.loop:
            break
        # Retry frequently just after a candle closes, so a provider's short
        # publication delay cannot miss the strategy's five-minute entry window.
        phase=time.time()%3600
        time.sleep(60 if phase<360 else min(300,3600-phase+10))
