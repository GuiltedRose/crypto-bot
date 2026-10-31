"""UNEXECUTED deterministic offline stress. Scripted I/O only, no sockets.

Real native HTTP/WS socket tests are separately opt-in; mocks do not validate Rust.
"""
import asyncio
import json
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from solders.signature import Signature
from data_policy import SourceAccessBlocked
from onchain_store import EventStore
from onchain_stream import StreamHints, listen
from solana_rpc_compat import RustIndexRpc
import test_onchain_indexer as fixtures


def notification(index):
    return {'jsonrpc':'2.0','method':'logsNotification','params':{'subscription':101,'result':{
        'context':{'slot':index+4},'value':{'signature':str(Signature.from_bytes(index.to_bytes(64,'little'))),
                                         'err':None,'logs':[]}}}}


class MigrationStressTests(unittest.TestCase):
    def setUp(self):
        self.helper=fixtures.IndexerTests();self.helper.setUp();self.addCleanup(self.helper.doCleanups)
        self.store=self.helper.store;self.helper.populated()
        self.store.register_source('stream',{**fixtures.POLICY,'stream_verified':True})

    def test_burst_pressure_dedup_recovery_and_restart(self):
        hints=StreamHints(self.store,'stream',capacity=8,observation_limit=32)
        checkpoint=self.store.next_slot(); events=self.store.metrics()['events']
        for index in range(256):hints.accept(notification(index),now=100)
        for index in range(8):self.assertFalse(hints.accept(notification(index),now=101))
        report=hints.report()
        self.assertEqual(report['pending_hints'],8)
        self.assertEqual(report['notifications']['overflow'],248)
        self.assertEqual(report['notifications']['duplicates'],8)
        reopened=StreamHints(EventStore(self.store.path),'stream',capacity=8,observation_limit=32)
        self.assertEqual(reopened.report()['notifications'],report['notifications'])
        reopened.recover() # Existing synthetic finalized prefix covers slots 4 and 5.
        self.assertEqual(reopened.report()['pending_hints'],6)
        self.assertEqual(self.store.next_slot(),checkpoint)
        self.assertEqual(self.store.metrics()['events'],events)
        self.assertIn('gap',reopened.report()['states'])

    def test_delayed_bursts_disconnects_and_bounded_reconnections(self):
        hints=StreamHints(self.store,'stream',capacity=8)
        sessions=[]
        class Socket:
            async def __aenter__(self):
                sessions.append(self)
                self.closed=False;self.messages=iter([{'jsonrpc':'2.0','id':1,'result':101},
                    {'jsonrpc':'2.0','id':2,'result':102}]+[notification(i) for i in range(16)])
                return self
            async def __aexit__(self,*_):self.closed=True
            async def send(self,_):pass
            async def recv(self):
                await asyncio.sleep(0) # deterministic scheduling delay, no real-time wait
                value=next(self.messages,None)
                if value is None:raise ConnectionError('scripted disconnect')
                return json.dumps(value)
        async def scenario():
            original_sleep=asyncio.sleep
            async def yield_only(_): await original_sleep(0)
            with patch('onchain_stream.BUDGET.reserve'),patch('onchain_stream.asyncio.sleep',side_effect=yield_only):
                with self.assertRaises(ConnectionError):
                    await asyncio.wait_for(listen({'ws_url':'wss://fixture.invalid'},hints,asyncio.Event(),max_failures=3,connector=lambda *a,**k:Socket()),5)
        asyncio.run(scenario())
        self.assertEqual(len(sessions),3)
        self.assertTrue(all(session.closed for session in sessions))
        self.assertEqual(hints.report()['pending_hints'],8)
        self.assertEqual(hints.report()['notifications']['disconnects'],3)

    def test_cancellation_closes_scripted_socket_without_advancing_checkpoint(self):
        hints=StreamHints(self.store,'stream');closed=[];entered=asyncio.Event()
        class Socket:
            async def __aenter__(self):return self
            async def __aexit__(self,*_):closed.append(True)
            async def send(self,_):pass
            async def recv(self):entered.set();await asyncio.Future()
        async def scenario():
            with patch('onchain_stream.BUDGET.reserve'):
                task=asyncio.create_task(listen({'ws_url':'wss://fixture.invalid'},hints,asyncio.Event(),connector=lambda *a,**k:Socket()))
                await asyncio.wait_for(entered.wait(),2);task.cancel()
                with self.assertRaises(asyncio.CancelledError):await asyncio.wait_for(task,2)
        before=self.store.next_slot();asyncio.run(scenario())
        self.assertEqual(closed,[True]);self.assertEqual(self.store.next_slot(),before)

    def test_http_provider_error_burst_and_terminal_malformed_evidence(self):
        rpc=RustIndexRpc({'url':'https://fixture.invalid','source_id':'stream'},MagicMock())
        rpc._transport=MagicMock(request=AsyncMock())
        async def scenario():
            with patch('solana_rpc_compat.check_boundary'):
                for index in range(64):
                    status=503 if index%2 else 429
                    rpc._transport.request.return_value=(status,'60','')
                    with self.assertRaises(ConnectionError if status==503 else ValueError):await rpc.call_async('getSlot',[])
                rpc._transport.request.return_value=(200,'','malformed')
                with self.assertRaises(SourceAccessBlocked):await rpc.call_async('getSlot',[])
        asyncio.run(asyncio.wait_for(scenario(),5))
        self.assertEqual(rpc._transport.request.await_count,65)
