"""Record confirmed PumpSwap events via Solana RPC WebSocket, with no wallet signing.

Start collecting now or prepend verified complete history for the same pool.
A reconnect resets candle coverage; never silently trade across missed data.
"""
import argparse
import asyncio
import fcntl
import base64
import json
import os
from pathlib import Path
import time
from urllib.parse import urlsplit,urlunsplit
from config import LiveConfig
from pumpswap import PROGRAM, IDL, TYPES, decode_struct, DEFAULT_POOL


def decode_logs(logs,signature,pool):
    stack=[]
    events=[]
    for line in logs:
        if line.startswith('Program ') and ' invoke [' in line:
            stack.append(line.split()[1])
        elif line.startswith('Program ') and (' success' in line or ' failed:' in line):
            if stack and stack[-1]==line.split()[1]:
                stack.pop()
        elif line.startswith('Program data: ') and stack and stack[-1]==str(PROGRAM):
            raw=base64.b64decode(line[len('Program data: '):],validate=True)
            for name in ('BuyEvent','SellEvent'):
                definition=next(e for e in IDL['events'] if e['name']==name)
                if raw[:8]!=bytes(definition['discriminator']):
                    continue
                fields=TYPES[name]['fields']
                pool_index=next(i for i,f in enumerate(fields) if f['name']=='pool')
                data,_=decode_struct(raw,fields[:pool_index+1],8)
                if data['pool']!=pool:
                    continue
                buying=name=='BuyEvent'
                events.append({'poolId':pool,'action':'buy' if buying else 'sell',
                    'signature':signature,'timestamp':data['timestamp']*1000,
                    'tokenAmount':data['base_amount_out' if buying else 'base_amount_in']/1e6,
                    'quoteAmount':data['quote_amount_in' if buying else 'quote_amount_out']/1e9,
                    'mint':'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v',
                    'quoteMint':'So11111111111111111111111111111111111111112',
                    'liquidityUsdEstimate':2*data['pool_base_token_reserves']/1e6})
    return events


async def collect(path):
    from websockets.asyncio.client import connect
    from live import Rpc
    from pumpswap import PumpSwap
    from wallet import load_wallet
    config=LiveConfig.from_env()
    os.umask(0o077)
    Path('state').mkdir(exist_ok=True)
    lock=open('state/collector.lock','w')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    # Validate selected pair orientation before applying fixed event decimals.
    snapshot=PumpSwap(Rpc(config.rpc_url),load_wallet(config)).snapshot()
    if str(snapshot['base'])!='EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v':
        raise ValueError('Collector currently requires USDC-base/SOL-quote pool')
    split=urlsplit(config.rpc_url)
    endpoint=os.environ.get('SOLANA_WS_URL') or urlunsplit(('wss',split.netloc,split.path,split.query,''))
    if not endpoint.startswith('wss://'):
        raise ValueError('WebSocket RPC must use WSS')
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    while True:
        try:
            async with connect(endpoint,max_size=8*1024*1024,ping_interval=20,ping_timeout=20) as ws:
                await ws.send(json.dumps({'jsonrpc':'2.0','id':1,'method':'logsSubscribe',
                    'params':[{'mentions':[DEFAULT_POOL]},{'commitment':'confirmed'}]}))
                ack=json.loads(await ws.recv())
                if 'error' in ack or 'result' not in ack:
                    raise ValueError('RPC refused log subscription')
                with open(path,'a',buffering=1) as target:
                    target.write(json.dumps({'kind':'coverage_reset','timestamp':int(time.time()*1000)})+'\n')
                    print('Confirmed pool feed connected; candle coverage starts at next full hour.',flush=True)
                    async for message in ws:
                        payload=json.loads(message)
                        if payload.get('method')!='logsNotification':
                            continue
                        value=payload['params']['result']['value']
                        if value['err'] is not None:
                            continue
                        for event in decode_logs(value['logs'],value['signature'],DEFAULT_POOL):
                            target.write(json.dumps(event)+'\n')
        except asyncio.CancelledError:
            raise
        except Exception as error:
            print('Feed interrupted ('+type(error).__name__+'); reconnecting with coverage reset.',flush=True)
            await asyncio.sleep(5)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',default='data/live_pool.jsonl')
    args=parser.parse_args()
    try:
        asyncio.run(collect(args.output))
    except KeyboardInterrupt:
        pass
    except Exception as error:
        print('Collector stopped ('+type(error).__name__+'); credentials suppressed.')
        raise SystemExit(1)
