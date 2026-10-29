"""Capital-gated, keyless discovery of confirmed PumpSwap pool creations.

Discovery never authorizes a trade or edits assets.json. A new pool is not
proof that its token is new, liquid, safe, or profitable.
"""
import argparse
import asyncio
import base64
import fcntl
import json
import math
import os
import sqlite3
import time
from pathlib import Path
from solders.pubkey import Pubkey
from pumpswap import IDL,TYPES,PROGRAM,decode_struct,decode_account,pda
from bot import SOL_MINT
from market import USDC_MINT
from sniping import CAPITAL_THRESHOLD_USD
from live import Rpc

RPC_URL='https://api.mainnet-beta.solana.com'
WSS_URL='wss://api.mainnet-beta.solana.com'
MAX_AGE=300


def funded_gate(now,status_path='state/last_status.json'):
    try:
        status=json.loads(Path(status_path).read_text())
        equity=status['equity_usd_estimate']
        return (math.isfinite(equity) and equity>=CAPITAL_THRESHOLD_USD and
                0<=now-status['timestamp']<=60 and not status.get('promotion',{}).get('review_halted',False))
    except (OSError,ValueError,KeyError,TypeError):return False


def decode_creations(logs):
    stack=[];events=[]
    discriminator=bytes(next(x['discriminator'] for x in IDL['events'] if x['name']=='CreatePoolEvent'))
    for line in logs:
        if line.startswith('Program ') and ' invoke [' in line:stack.append(line.split()[1])
        elif line.startswith('Program ') and (' success' in line or ' failed:' in line):
            if stack and stack[-1]==line.split()[1]:stack.pop()
        elif line.startswith('Program data: ') and stack and stack[-1]==str(PROGRAM):
            raw=base64.b64decode(line[14:],validate=True)
            if raw[:8]==discriminator:
                event,_=decode_struct(raw,TYPES['CreatePoolEvent']['fields'],8)
                events.append(event)
    return events


class DiscoveryStore:
    def __init__(self,path='state/meme_discovery.sqlite'):
        Path(path).parent.mkdir(exist_ok=True,parents=True)
        self.db=sqlite3.connect(path)
        self.db.execute('CREATE TABLE IF NOT EXISTS candidates(pool TEXT PRIMARY KEY,mint TEXT,seen INTEGER,signature TEXT,data TEXT)')
        self.db.commit()

    def ingest(self,event,signature,account,now,capital_unlocked):
        if not capital_unlocked:return False,'capital gate locked'
        if not signature or not 0<=now-event['timestamp']<=MAX_AGE:return False,'stale/future launch'
        base,quote=event['base_mint'],event['quote_mint']
        if quote not in (SOL_MINT,USDC_MINT) or base in (SOL_MINT,USDC_MINT):return False,'not a meme-candidate quoted pool'
        pool=decode_account(account,'Pool',PROGRAM)
        if (pool['base_mint'],pool['quote_mint'],pool['creator'],pool['index'])!=(base,quote,event['creator'],event['index']):
            raise ValueError('Creation event and on-chain pool disagree')
        derived=pda([b'pool',event['index'].to_bytes(2,'little'),bytes(Pubkey.from_string(event['creator'])),
                     bytes(Pubkey.from_string(base)),bytes(Pubkey.from_string(quote))])
        if str(derived)!=event['pool']:raise ValueError('Discovery pool PDA mismatch')
        candidate={**event,'source':'confirmed PumpSwap CreatePoolEvent and RPC pool/PDA validation',
                   'approval':'awaiting token risk, mint-age, liquidity and execution screening',
                   'live_execution_enabled':False}
        with self.db:
            self.db.execute('DELETE FROM candidates WHERE seen<?',(now-MAX_AGE,))
            self.db.execute('INSERT OR IGNORE INTO candidates VALUES(?,?,?,?,?)',(event['pool'],base,now,signature,json.dumps(candidate)))
            self.db.execute('DELETE FROM candidates WHERE pool IN (SELECT pool FROM candidates ORDER BY seen DESC LIMIT -1 OFFSET 200)')
        return True,'candidate queued; no trading approval'


async def watch(store):
    from websockets.asyncio.client import connect
    if not funded_gate(time.time()):return
    rpc=Rpc(RPC_URL)
    async with connect(WSS_URL,max_size=2**20,max_queue=32,ping_interval=20) as websocket:
        await websocket.send(json.dumps({'jsonrpc':'2.0','id':1,'method':'logsSubscribe',
            'params':[{'mentions':[str(PROGRAM)]},{'commitment':'confirmed'}]}))
        while funded_gate(time.time()):
            try:message=json.loads(await asyncio.wait_for(websocket.recv(),timeout=5))
            except asyncio.TimeoutError:continue
            value=message.get('params',{}).get('result',{}).get('value',{})
            if not value or value.get('err') is not None:continue
            for event in decode_creations(value.get('logs',[])):
                now=int(time.time())
                if not funded_gate(now):return
                if not 0<=now-event['timestamp']<=MAX_AGE:continue
                account=rpc.call('getMultipleAccounts',[[event['pool']],{'encoding':'base64','commitment':'confirmed'}])['value'][0]
                store.ingest(event,value['signature'],account,now,True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--loop',action='store_true')
    args=parser.parse_args()
    os.umask(0o077);Path('state').mkdir(exist_ok=True)
    lock=open('state/discovery.lock','w');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    store=DiscoveryStore()
    while True:
        unlocked=funded_gate(time.time())
        report={'timestamp':int(time.time()),'capital_unlocked':unlocked,'automatic_discovery':True,
                'live_execution_enabled':False,'queued_candidates':store.db.execute('SELECT COUNT(*) FROM candidates').fetchone()[0],
                'status':'watching confirmed pool creations' if unlocked else 'locked: fresh verified equity of $5,000 required'}
        Path('state/discovery_status.json').write_text(json.dumps(report))
        if unlocked:
            try:asyncio.run(watch(store))
            except Exception:print('Discovery connection unavailable; no candidate approved or traded.',flush=True)
        if not args.loop:
            print(json.dumps(report),flush=True);break
        time.sleep(30)


if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:pass
