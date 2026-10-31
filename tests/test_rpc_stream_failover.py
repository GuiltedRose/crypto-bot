"""UNEXECUTED explicitly authorized source switching; no real sockets."""
import asyncio
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from data_policy import SourceAccessBlocked
from onchain_store import EventStore
from onchain_stream import run_stream, StreamHints, listen
import test_onchain_indexer as fixtures


class StreamFailoverTests(unittest.TestCase):
    def setUp(self):
        self.helper=fixtures.IndexerTests();self.helper.setUp();self.addCleanup(self.helper.doCleanups)
        self.store=self.helper.store
        policy={**fixtures.POLICY,'stream_verified':True}
        self.sources=[]
        for name in ('first','second'):
            self.store.register_source(name,policy)
            self.sources.append({'source_id':name,'policy':policy,'url':'https://'+name+'.invalid','ws_url':'wss://'+name+'.invalid'})
        self.endpoint={**self.sources[0],'stream_sources':self.sources}
        self.ingester=SimpleNamespace(store=self.store,endpoints=[MagicMock(source=name) for name in ('first','second')])

    def test_only_connection_failure_switches_explicit_sources(self):
        reached=threading.Event();calls=[]
        async def listener(endpoint,*args,**kwargs):
            calls.append(endpoint['source_id'])
            if len(calls)==1:raise ConnectionError('bounded reconnect exhausted')
            reached.set();await asyncio.Future()
        def step(*args):
            if not reached.wait(2):raise AssertionError('Backup never selected')
            return {'progress':{'lag_slots':0}}
        self.ingester.step=step
        with patch('onchain_stream.listen',side_effect=listener):
            asyncio.run(asyncio.wait_for(run_stream(self.ingester,self.endpoint,1,SimpleNamespace(sync=lambda:{}),max_cycles=1),5))
        self.assertEqual(calls,['first','second'])
        self.assertEqual(StreamHints(self.store,'first').report()['notifications']['source_switches'],1)

    def test_terminal_evidence_error_is_not_hidden_by_last_batch_or_retried(self):
        reached=threading.Event();calls=[]
        async def listener(endpoint,*args,**kwargs):
            calls.append(endpoint['source_id']);reached.set()
            raise SourceAccessBlocked('Malformed evidence')
        def step(*args):
            if not reached.wait(2):raise AssertionError('Listener never started')
            return {'progress':{'lag_slots':0}}
        self.ingester.step=step
        with patch('onchain_stream.listen',side_effect=listener):
            with self.assertRaises(SourceAccessBlocked):
                asyncio.run(asyncio.wait_for(run_stream(self.ingester,self.endpoint,1,SimpleNamespace(sync=lambda:{}),max_cycles=1),5))
        self.assertEqual(calls,['first'])

    def test_mismatched_backup_budget_is_rejected_before_streaming(self):
        self.sources[1]['network_budget']={'ledger':'different-budget'}
        self.ingester.step=MagicMock()
        with patch('onchain_stream.listen') as listener:
            with self.assertRaises(SourceAccessBlocked):
                asyncio.run(run_stream(self.ingester,self.endpoint,1,SimpleNamespace(sync=lambda:{}),max_cycles=1))
            listener.assert_not_called()
        self.ingester.step.assert_not_called()

    def test_malformed_ack_is_terminal_without_reconnect(self):
        hints=StreamHints(self.store,'first')
        entered=[]
        class Socket:
            async def __aenter__(self):entered.append(True);return self
            async def __aexit__(self,*_):pass
            async def send(self,_):pass
            async def recv(self):return 'invalid-json'
        with patch('onchain_stream.BUDGET.reserve'):
            with self.assertRaises(SourceAccessBlocked):
                asyncio.run(asyncio.wait_for(listen(self.sources[0],hints,asyncio.Event(),connector=lambda *a,**k:Socket()),2))
        self.assertEqual(entered,[True])
        self.assertEqual(hints.report()['notifications']['malformed'],1)
