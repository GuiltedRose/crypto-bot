"""Bounded WS notification path plus existing finalized recovery.

Notifications are durable hints, never swap amounts or completeness evidence.
No per-notification HTTP requests: finalized recovery coalesces all hints.
"""
import asyncio
import hashlib
import json
import time
from urllib.parse import urlparse
import websockets
from websockets.exceptions import WebSocketException, InvalidStatus
from provider_limits import BUDGET, ProviderBackoff
from data_policy import SourceAccessBlocked
from onchain_decode import Decoder


def validate_ws_endpoint(endpoint):
    parsed=urlparse(endpoint.get('ws_url',''))
    loopback=False
    if endpoint.get('allow_loopback_ws') is True and parsed.hostname:
        import ipaddress
        try:loopback=parsed.scheme=='ws' and ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:pass
    if (parsed.scheme!='wss' and not loopback) or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise SourceAccessBlocked('Explicit WSS or opted-in literal loopback WS endpoint required')
    return endpoint['ws_url']


class SubscriptionSession:
    """Connection-scoped JSON-RPC acknowledgment and notification validation."""
    def __init__(self, mentions):
        from solders.pubkey import Pubkey
        if not 1 <= len(mentions) <= 2 or len(set(mentions)) != len(mentions):
            raise ValueError('One or two distinct subscription mentions required')
        for mention in mentions: Pubkey.from_string(mention)
        self.requests = {number: mention for number, mention in enumerate(mentions, 1)}
        self.active = {}

    def subscriptions(self):
        return [{'jsonrpc':'2.0','id':number,'method':'logsSubscribe',
                 'params':[{'mentions':[mention]},{'commitment':'confirmed'}]}
                for number,mention in self.requests.items()]

    def receive(self, message):
        if not isinstance(message,dict) or message.get('jsonrpc') != '2.0':
            raise ValueError('Invalid JSON-RPC envelope')
        if 'error' in message:
            if type(message.get('id')) is not int or message['id'] not in self.requests:
                raise ValueError('Unmatched subscription error')
            raise SourceAccessBlocked('Subscription rejected; authorization review required')
        if 'id' in message:
            number,subscription = message['id'],message.get('result')
            if type(number) is not int or number not in self.requests or type(subscription) is not int or subscription<0:
                raise ValueError('Invalid subscription acknowledgment')
            if number in self.active and self.active[number] != subscription:
                raise ValueError('Conflicting subscription acknowledgment')
            if any(n != number and value == subscription for n,value in self.active.items()):
                raise ValueError('Subscription ID reused for another request')
            self.active[number] = subscription
            return False
        if message.get('method') != 'logsNotification':
            raise ValueError('Unexpected subscription message')
        params = message.get('params')
        if not isinstance(params,dict) or type(params.get('subscription')) is not int or params['subscription'] not in self.active.values():
            raise ValueError('Notification without matching acknowledgment')
        try:
            result=params['result'];value=result['value'];slot=result['context']['slot']
            from solders.signature import Signature
            Signature.from_string(value['signature'])
            if type(slot) is not int or slot<0 or 'err' not in value:
                raise ValueError('Invalid notification slot/status')
            if len(json.dumps(value['err'])) > 4096:
                raise ValueError('Execution error exceeds observation budget')
            if value['err'] is not None and not isinstance(value['err'],(str,dict)):
                raise ValueError('Invalid execution error')
            logs=value.get('logs')
            if logs is not None and (not isinstance(logs,list) or not all(isinstance(x,str) for x in logs)):
                raise ValueError('Invalid notification logs')
        except (KeyError,TypeError):
            raise ValueError('Incomplete notification') from None
        return True


class ObservationCapacityExceeded(RuntimeError):
    pass


class StreamHints:
    def __init__(self, store, source, capacity=4096, observation_limit=100000):
        store.policy(source)
        if not 1 <= capacity <= 100000:
            raise ValueError('Invalid bounded stream capacity')
        if type(observation_limit) is not int or not 1<=observation_limit<=1000000:
            raise ValueError('Invalid observation retention budget')
        self.observation_limit=observation_limit
        self.store,self.source,self.capacity = store,source,capacity
        with store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS stream_hints(signature TEXT PRIMARY KEY,slot INTEGER,received REAL)')
            db.execute('CREATE TABLE IF NOT EXISTS stream_state(name TEXT PRIMARY KEY,value TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS stream_counts(name TEXT PRIMARY KEY,value INTEGER NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS stream_observations(source TEXT,signature TEXT,slot INTEGER,failed INTEGER,received REAL,payload TEXT,PRIMARY KEY(source,signature,slot))')
            db.execute('CREATE INDEX IF NOT EXISTS stream_observation_slots ON stream_observations(slot)')

    def state(self, name, value):
        with self.store.connect() as db:
            db.execute('INSERT OR REPLACE INTO stream_state VALUES(?,?)',(name,json.dumps(value)))

    def count(self, name):
        with self.store.connect() as db:
            db.execute('INSERT INTO stream_counts VALUES(?,1) ON CONFLICT(name) DO UPDATE SET value=value+1',(name,))

    def accept(self, message, now=None):
        if message.get('method') != 'logsNotification':
            if 'error' in message:
                raise SourceAccessBlocked('WebSocket subscription rejected; review provider authorization')
            return False
        result=message['params']['result'];value=result['value'];slot=result['context']['slot']
        if type(slot) is not int or slot<0 or not isinstance(value['signature'],str) or 'err' not in value:
            raise ValueError('Invalid stream notification')
        received=time.time() if now is None else now
        failed=value['err'] is not None
        # Preserve normalized observations, not unbounded log bodies. The full
        # executed transaction remains the finalized pipeline's responsibility.
        payload=json.dumps({'commitment':'confirmed','err':value['err'],
                            'notification_sha256':hashlib.sha256(json.dumps(message,sort_keys=True).encode()).hexdigest()})
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("INSERT INTO stream_counts VALUES('received',1) ON CONFLICT(name) DO UPDATE SET value=value+1")
            old=db.execute('SELECT failed,payload FROM stream_observations WHERE source=? AND signature=? AND slot=?',
                           (self.source,value['signature'],slot)).fetchone()
            if old:
                db.execute("INSERT INTO stream_counts VALUES('duplicates',1) ON CONFLICT(name) DO UPDATE SET value=value+1")
                if old[0] != int(failed) or json.loads(old[1])['err'] != value['err']:
                    db.execute("INSERT OR REPLACE INTO stream_state VALUES('gap','\"conflicting execution observation; recover from finalized ledger\"')")
                return False
            if db.execute('SELECT count(*) FROM stream_observations').fetchone()[0]>=self.observation_limit:
                # Raise after persisting the gap below via the separate state path.
                raise ObservationCapacityExceeded('Observation retention budget exhausted; preserve/archive state before resuming')
            pending=db.execute('SELECT count(*) FROM stream_hints').fetchone()[0]
            if not failed and pending>=self.capacity:
                db.execute("INSERT INTO stream_counts VALUES('overflow',1) ON CONFLICT(name) DO UPDATE SET value=value+1")
                db.execute("INSERT OR REPLACE INTO stream_state VALUES('gap','\"overflow; finalized recovery required\"')")
                return False
            db.execute('INSERT INTO stream_observations VALUES(?,?,?,?,?,?)',
                       (self.source,value['signature'],slot,int(failed),received,payload))
            if failed:
                db.execute("INSERT INTO stream_counts VALUES('failed_notifications',1) ON CONFLICT(name) DO UPDATE SET value=value+1")
                return False
            old_hint=db.execute('SELECT slot FROM stream_hints WHERE signature=?',(value['signature'],)).fetchone()
            if old_hint:
                db.execute("INSERT OR REPLACE INTO stream_state VALUES('gap','\"signature observed on another slot/fork\"')")
                db.execute('UPDATE stream_hints SET slot=max(slot,?) WHERE signature=?',(slot,value['signature']))
            else:
                db.execute('INSERT INTO stream_hints VALUES(?,?,?)',(value['signature'],slot,received))
        return True

    def report(self):
        return self.read_status(self.store)

    @staticmethod
    def read_status(store):
        with store.connect() as db:
            counts=dict(db.execute('SELECT name,value FROM stream_counts'))
            pending=db.execute('SELECT count(*),min(received) FROM stream_hints').fetchone()
            finalized=db.execute('SELECT count(*) FROM transactions').fetchone()[0]
            swaps=db.execute("SELECT count(*) FROM events e JOIN blocks b ON e.slot=b.slot WHERE e.kind='swap' AND b.issues='[]'").fetchone()[0]
            matched=db.execute('SELECT avg(o.received-b.time) FROM stream_observations o JOIN blocks b ON o.slot=b.slot WHERE o.received>=b.time AND o.received-b.time<=86400').fetchone()[0]
            states={row[0]:json.loads(row[1]) for row in db.execute('SELECT name,value FROM stream_state')}
        return {'notifications':counts,'pending_hints':pending[0],
                'oldest_pending_age_seconds':max(0,time.time()-pending[1]) if pending[1] is not None else None,
                'finalized_relevant_transactions':finalized,'verified_swap_events':swaps,
                'blocktime_to_receipt_seconds_proxy':matched,'states':states,
                'independent_coverage_proven':False}

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


async def listen(endpoint, hints, wake, max_failures=6, connector=None):
    url=validate_ws_endpoint(endpoint);policy=hints.store.policy(hints.source)
    if policy.get('stream_verified') is not True:
        raise SourceAccessBlocked('Explicit authorized WSS endpoint and stream policy required')
    mentions=endpoint.get('stream_mentions') or list(Decoder().specs)
    if not 1<=len(mentions)<=2:raise SourceAccessBlocked('At most two explicitly authorized subscriptions')
    from solders.pubkey import Pubkey
    for program in mentions:Pubkey.from_string(program)
    session=SubscriptionSession(mentions)
    failures=0
    while failures<max_failures:
        try:
            await asyncio.to_thread(BUDGET.reserve,url,2)
            session=SubscriptionSession(mentions)
            hints.state('subscriptions',{'expected':len(mentions),'acknowledged':0})
            async with (connector or websockets.connect)(url,max_queue=32,max_size=2_000_000,
                                          open_timeout=15,ping_interval=20,ping_timeout=20) as ws:
                for request in session.subscriptions():
                    await ws.send(json.dumps(request))
                hints.state('connection','connected; awaiting acknowledgments')
                while True:
                    # Bound acknowledgment wait; normal idle subscriptions may
                    # remain quiet after all acknowledgments have been validated.
                    raw=await asyncio.wait_for(ws.recv(),15 if len(session.active)<len(mentions) else None)
                    try:
                        message=json.loads(raw)
                        notification=session.receive(message)
                    except (ValueError,KeyError,TypeError,RecursionError):
                        hints.count('malformed')
                        raise
                    hints.state('subscriptions',{'expected':len(mentions),'acknowledged':len(session.active)})
                    if len(session.active)==len(mentions):hints.state('connection','subscribed; observations provisional')
                    if notification and hints.accept(message):wake.set()
        except ObservationCapacityExceeded:
            hints.state('gap','observation retention budget exhausted; stopped without pruning')
            raise
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
        except (OSError,ValueError,KeyError,TypeError,RecursionError,WebSocketException):
            pass
        failures+=1
        hints.count('disconnects')
        hints.state('gap',{'reason':'stream disconnected','attempt':failures,'at':time.time()})
        wake.set()
        await asyncio.sleep(min(60,2**failures))
    raise ConnectionError('Bounded stream reconnect attempts exhausted')


async def durable_work(function, *args):
    task=asyncio.create_task(asyncio.to_thread(function,*args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


async def run_stream(ingester, endpoint, start, candles, batch=8, interval=5, connector=None, max_cycles=None):
    hints=StreamHints(ingester.store,endpoint['source_id'],
                      capacity=endpoint.get('pending_capacity',4096),
                      observation_limit=endpoint.get('observation_limit',100000))
    def check_network_budget():
        configured=endpoint.get('network_budget')
        if configured:
            import sqlite3
            from solana_network_budget import Budget
            try:
                if endpoint.get('backend')=='rust':
                    from solana_rpc_compat import check_boundary
                    check_boundary(endpoint)
                    return
                usage=Budget(configured['ledger']).report(time.time())
                if usage['optional_collection_suspended']:
                    raise ValueError('Normal billing-cycle limit reached')
            except (ValueError,OSError,KeyError,sqlite3.Error) as error:
                hints.state('gap',{'reason':'network budget suspension','at':time.time(),
                                   'checkpoint_preserved':True})
                raise SourceAccessBlocked('Network budget unavailable or exhausted; explicit gap recorded') from None
    check_network_budget()
    wake=asyncio.Event()
    listener=asyncio.create_task(listen(endpoint,hints,wake,connector=connector))
    failures=0
    cycles=0
    try:
        while True:
            check_network_budget()
            if listener.done():
                listener.result()
            try:
                if endpoint.get('retention'):
                    from observer_retention import accounting
                    retention=endpoint['retention']
                    usage=await durable_work(accounting,ingester.store,retention['archive_directory'],retention)
                    if usage['pause_nonessential_writes']:
                        raise SourceAccessBlocked('Observer storage budget reached; ingestion paused, checkpoint preserved')
                result=await durable_work(ingester.step,start,batch)
                start=None if ingester.store.next_slot() is not None else start
                failures=0;hints.recover();result['stream']=hints.report()
                result['candles']=await durable_work(candles.sync)
                print(json.dumps(result),flush=True)
                cycles+=1
                if max_cycles is not None and cycles>=max_cycles:return
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
        hints.state('connection','stopped; finalized checkpoint preserved')
        listener.cancel()
        await asyncio.gather(listener,return_exceptions=True)
