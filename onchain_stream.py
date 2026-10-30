"""Bounded WS notification path plus existing finalized recovery.

Notifications are durable hints, never swap amounts or completeness evidence.
No per-notification HTTP requests: finalized recovery coalesces all hints.
"""
import asyncio
import json
import time
from urllib.parse import urlparse
import websockets
from websockets.exceptions import WebSocketException, InvalidStatus
from provider_limits import BUDGET, ProviderBackoff
from data_policy import SourceAccessBlocked
from onchain_decode import Decoder


class StreamHints:
    def __init__(self, store, source, capacity=4096):
        store.policy(source)
        if not 1 <= capacity <= 100000:
            raise ValueError('Invalid bounded stream capacity')
        self.store,self.source,self.capacity = store,source,capacity
        with store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS stream_hints(signature TEXT PRIMARY KEY,slot INTEGER,received REAL)')
            db.execute('CREATE TABLE IF NOT EXISTS stream_state(name TEXT PRIMARY KEY,value TEXT)')

    def state(self, name, value):
        with self.store.connect() as db:
            db.execute('INSERT OR REPLACE INTO stream_state VALUES(?,?)',(name,json.dumps(value)))

    def accept(self, message, now=None):
        if message.get('method') != 'logsNotification':
            if 'error' in message:
                raise SourceAccessBlocked('WebSocket subscription rejected; review provider authorization')
            return False
        result=message['params']['result'];value=result['value'];slot=result['context']['slot']
        if type(slot) is not int or slot<0 or not isinstance(value['signature'],str):
            raise ValueError('Invalid stream notification')
        if value.get('err') is not None:
            return False
        with self.store.connect() as db:
            if db.execute('SELECT 1 FROM stream_hints WHERE signature=?',(value['signature'],)).fetchone():
                return False
            if db.execute('SELECT count(*) FROM stream_hints').fetchone()[0]>=self.capacity:
                db.execute("INSERT OR REPLACE INTO stream_state VALUES('gap','\"overflow; finalized recovery required\"')")
                return False
            db.execute('INSERT INTO stream_hints VALUES(?,?,?)',(value['signature'],slot,time.time() if now is None else now))
        return True

    def recover(self):
        cursor=self.store.next_slot()
        if cursor is not None:
            with self.store.connect() as db:
                db.execute('DELETE FROM stream_hints WHERE slot<?',(cursor,))
        with self.store.connect() as db:
            return {'pending_hints':db.execute('SELECT count(*) FROM stream_hints').fetchone()[0],
                    'gaps':dict(db.execute('SELECT name,value FROM stream_state'))}
        # Never erase disconnect/overflow evidence: the finalized coverage ledger
        # independently determines whether recovery actually completed.


async def listen(endpoint, hints, wake, max_failures=6):
    url=endpoint.get('ws_url','');policy=hints.store.policy(hints.source)
    if urlparse(url).scheme!='wss' or policy.get('stream_verified') is not True:
        raise SourceAccessBlocked('Explicit authorized WSS endpoint and stream policy required')
    mentions=endpoint.get('stream_mentions') or list(Decoder().specs)
    if not 1<=len(mentions)<=2:raise SourceAccessBlocked('At most two explicitly authorized subscriptions')
    from solders.pubkey import Pubkey
    for program in mentions:Pubkey.from_string(program)
    failures=0
    while failures<max_failures:
        try:
            await asyncio.to_thread(BUDGET.reserve,url,2)
            async with websockets.connect(url,max_queue=32,max_size=2_000_000,
                                          open_timeout=15,ping_interval=20,ping_timeout=20) as ws:
                for number,program in enumerate(mentions,1):
                    await ws.send(json.dumps({'jsonrpc':'2.0','id':number,'method':'logsSubscribe',
                                  'params':[{'mentions':[program]},{'commitment':'confirmed'}]}))
                hints.state('connection','connected; notifications provisional')
                async for raw in ws:
                    if hints.accept(json.loads(raw)):
                        wake.set()
        except SourceAccessBlocked:
            hints.state('gap','stream authorization denied')
            raise
        except ProviderBackoff as error:
            await asyncio.sleep(error.seconds)
            continue
        except InvalidStatus as error:
            status=error.response.status_code
            BUDGET.failure(url,status,error.response.headers)
            if status in (401,403):raise SourceAccessBlocked('Stream access denied; manual review required') from None
        except (OSError,ValueError,KeyError,TypeError,WebSocketException):
            pass
        failures+=1
        hints.state('gap',{'reason':'stream disconnected','attempt':failures,'at':time.time()})
        wake.set()
        await asyncio.sleep(min(60,2**failures))
    raise ConnectionError('Bounded stream reconnect attempts exhausted')


async def run_stream(ingester, endpoint, start, candles, batch=8, interval=5):
    hints=StreamHints(ingester.store,endpoint['source_id'])
    wake=asyncio.Event()
    listener=asyncio.create_task(listen(endpoint,hints,wake))
    failures=0
    try:
        while True:
            if listener.done():
                listener.result()
            try:
                result=await asyncio.to_thread(ingester.step,start,batch)
                start=None if ingester.store.next_slot() is not None else start
                failures=0;result['stream']=hints.recover()
                result['candles']=await asyncio.to_thread(candles.sync)
                print(json.dumps(result),flush=True)
                if result['progress']['lag_slots']:
                    continue
                wake.clear()
                try:await asyncio.wait_for(wake.wait(),interval)
                except asyncio.TimeoutError:pass
            except SourceAccessBlocked:
                raise
            except (ValueError,ConnectionError) as error:
                failures+=1
                if ingester.store.next_slot() is not None:start=None
                if failures>=6:raise
                print(json.dumps({'status':'stream_recovery_paused','reason':type(error).__name__}),flush=True)
                await asyncio.sleep(min(60,2**failures))
    finally:
        listener.cancel()
        await asyncio.gather(listener,return_exceptions=True)
