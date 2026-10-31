"""UNEXECUTED observer tests; all fixtures and databases are isolated."""
import asyncio
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from solders.signature import Signature
from data_policy import SourceAccessBlocked
from onchain_stream import SubscriptionSession, StreamHints, ObservationCapacityExceeded, listen, validate_ws_endpoint, durable_work
from onchain_store import EventStore
from solana_observer import PUMP_PROGRAM, PUMPSWAP_PROGRAM, prepare, status, observe
import test_onchain_indexer as fixtures

SIG=str(Signature.default())


def notification(slot=20, signature=SIG, error=None, subscription=101):
    return {'jsonrpc':'2.0','method':'logsNotification','params':{
        'subscription':subscription,'result':{'context':{'slot':slot},
        'value':{'signature':signature,'err':error,'logs':[]}}}}


def session():
    value=SubscriptionSession([PUMP_PROGRAM,PUMPSWAP_PROGRAM])
    value.receive({'jsonrpc':'2.0','id':1,'result':101})
    value.receive({'jsonrpc':'2.0','id':2,'result':102})
    return value


class ProtocolTests(unittest.TestCase):
    def test_transport_requires_tls_or_explicit_literal_loopback(self):
        self.assertEqual(validate_ws_endpoint({'ws_url':'wss://example.invalid'}),'wss://example.invalid')
        self.assertEqual(validate_ws_endpoint({'ws_url':'ws://127.0.0.1:8900','allow_loopback_ws':True}),
                         'ws://127.0.0.1:8900')
        for endpoint in [{'ws_url':'ws://127.0.0.1:8900'},
                         {'ws_url':'ws://example.invalid','allow_loopback_ws':True},
                         {'ws_url':'wss://user:password@example.invalid'}]:
            with self.subTest(endpoint=endpoint),self.assertRaises(SourceAccessBlocked):
                validate_ws_endpoint(endpoint)

    def test_acknowledgments_match_requests_and_real_signature_shape(self):
        value=SubscriptionSession([PUMP_PROGRAM,PUMPSWAP_PROGRAM])
        self.assertEqual([r['id'] for r in value.subscriptions()],[1,2])
        with self.assertRaises(ValueError):value.receive(notification())
        self.assertFalse(value.receive({'jsonrpc':'2.0','id':1,'result':101}))
        self.assertTrue(value.receive(notification()))
        with self.assertRaises(ValueError):value.receive({'jsonrpc':'2.0','id':2,'result':101})
        with self.assertRaises(ValueError):value.receive({'jsonrpc':'2.0','id':1,'result':999})

    def test_malformed_and_unmatched_messages_reject(self):
        value=session()
        bad=[]
        item=notification();item['params']['result']['context']['slot']=True;bad.append(item)
        item=notification();item['params']['result']['value']['signature']='bad';bad.append(item)
        item=notification();del item['params']['result']['value']['err'];bad.append(item)
        bad.extend([[],{},notification(subscription=999),{'jsonrpc':'1.0','id':1,'result':101}])
        for item in bad:
            with self.subTest(item=item),self.assertRaises(ValueError):value.receive(item)
        with self.assertRaises(SourceAccessBlocked):value.receive({'jsonrpc':'2.0','id':1,'error':{'code':403}})


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.helper=fixtures.IndexerTests();self.helper.setUp();self.addCleanup(self.helper.doCleanups)
        self.store=self.helper.store;self.helper.populated()

    def test_success_failure_dedup_and_restart_do_not_create_swaps(self):
        hints=StreamHints(self.store,'fixture')
        before=self.store.metrics()['events']
        self.assertTrue(hints.accept(notification(),now=100))
        self.assertFalse(hints.accept(notification(),now=101))
        self.assertFalse(hints.accept(notification(slot=21,error={'InstructionError':[0,'failed']}),now=102))
        reopened=StreamHints(EventStore(self.store.path),'fixture')
        report=reopened.report()
        self.assertEqual(report['notifications']['received'],3)
        self.assertEqual(report['notifications']['duplicates'],1)
        self.assertEqual(report['notifications']['failed_notifications'],1)
        self.assertEqual(report['pending_hints'],1)
        self.assertEqual(self.store.metrics()['events'],before)
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM stream_observations').fetchone()[0],2)

    def test_overflow_recovery_and_retention_are_explicit(self):
        hints=StreamHints(self.store,'fixture',capacity=1)
        hints.accept(notification(slot=4),now=100)
        self.assertFalse(hints.accept(notification(slot=20,signature=str(Signature.from_bytes(bytes([1])*64))),now=101))
        self.assertEqual(hints.report()['notifications']['overflow'],1)
        hints.recover()  # Existing finalized next_slot is 6.
        self.assertEqual(hints.report()['pending_hints'],0)
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM stream_observations').fetchone()[0],1)
        self.assertIn('gap',hints.report()['states'])
        limited=StreamHints(self.store,'fixture',observation_limit=1)
        with self.assertRaises(ObservationCapacityExceeded):limited.accept(notification(slot=22),now=102)

    def test_status_is_read_only_and_preserves_counts(self):
        StreamHints(self.store,'fixture').accept(notification(),now=100)
        before=self.store.metrics()
        report=status(self.store.path)
        self.assertEqual(report['stream']['pending_hints'],1)
        self.assertFalse(report['live_execution_enabled'])
        self.assertEqual(self.store.metrics(),before)

    def test_cancel_waits_for_inflight_durable_work(self):
        async def run():
            entered=asyncio.Event()
            release=asyncio.Event()
            completed=[]
            async def worker(function,*args):
                entered.set()
                await release.wait()
                completed.append(True)
            with patch('onchain_stream.asyncio.to_thread',side_effect=worker):
                task=asyncio.create_task(durable_work(lambda:None))
                await entered.wait()
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done())
                release.set()
                with self.assertRaises(asyncio.CancelledError):await task
            self.assertEqual(completed,[True])
        asyncio.run(run())

    def test_config_requires_rights_and_isolated_database(self):
        endpoint={'enabled':True,'url':'https://example.invalid','ws_url':'wss://example.invalid',
                  'source_id':'fixture','policy':dict(fixtures.POLICY)}
        config={'endpoints':[endpoint],'markets':[{'mint':fixtures.MINT,'pool':fixtures.POOL}]}
        path=Path(self.helper.temp.name)/'new.sqlite'
        with self.assertRaises(ValueError):prepare(config,path)
        self.assertFalse(path.exists())
        endpoint['policy']['stream_verified']=True
        ingester,_,_=prepare(config,path)
        self.assertIsNone(ingester.store.next_slot())
        with self.assertRaises(ValueError):prepare(config,path)  # no implicit overwrite/resume

    def test_original_observe_entry_delegates_without_connecting(self):
        async def run():
            ingester=type('Ingester',(),{'store':self.store})()
            with patch('solana_observer.prepare',return_value=(ingester,{'source_id':'fixture'},object())), patch('solana_observer.run_stream',new_callable=AsyncMock) as coordinator:
                await observe({},self.store.path,resume=True,max_cycles=1)
                self.assertTrue(coordinator.await_args.kwargs['connector'])
                self.assertEqual(coordinator.await_args.kwargs['max_cycles'],1)
        asyncio.run(run())

    def test_reconnect_resubscribes_and_records_gap(self):
        self.store.register_source('authorized-stream',{**fixtures.POLICY,'stream_verified':True})
        hints=StreamHints(self.store,'authorized-stream')
        class Socket:
            async def __aenter__(self):
                self.queue=iter([{'jsonrpc':'2.0','id':1,'result':101},
                                 {'jsonrpc':'2.0','id':2,'result':102},notification()])
                return self
            async def __aexit__(self,*args):pass
            async def send(self,raw):pass
            async def recv(self):
                item=next(self.queue,None)
                if item is None:raise OSError('injected disconnect')
                return json.dumps(item)
        async def run():
            with patch('onchain_stream.asyncio.to_thread',new_callable=AsyncMock), patch('onchain_stream.asyncio.sleep',new_callable=AsyncMock):
                with self.assertRaises(ConnectionError):
                    await listen({'ws_url':'wss://example.invalid'},hints,asyncio.Event(),max_failures=2,connector=lambda *a,**k:Socket())
        asyncio.run(run())
        self.assertEqual(hints.report()['notifications']['duplicates'],1)
        self.assertIn('gap',hints.report()['states'])
