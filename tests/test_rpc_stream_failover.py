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

    def test_backup_notifications_recover_report_restart_and_switch_back(self):
        import json
        from test_observer_migration_stress import notification
        self.helper.populated()
        initial_checkpoint=self.store.next_slot()
        initial_events=self.store.metrics()['events']
        reached=threading.Event()
        async def listener(endpoint,hints,*args,**kwargs):
            if endpoint['source_id']=='first':raise ConnectionError('primary offline')
            hints.accept(notification(0),now=100) # slot 4, already finalized
            hints.accept(notification(3),now=100) # slot 7, still pending
            hints.accept(notification(3),now=101) # exact duplicate
            reached.set();await asyncio.Future()
        def step(*args):
            if not reached.wait(2):raise AssertionError('Backup notification missing')
            return {'progress':{'lag_slots':0}}
        self.ingester.step=step
        with patch('onchain_stream.listen',side_effect=listener),patch('builtins.print') as output:
            asyncio.run(asyncio.wait_for(run_stream(self.ingester,self.endpoint,None,SimpleNamespace(sync=lambda:{}),max_cycles=1),5))
        reports=[json.loads(call.args[0]) for call in output.call_args_list]
        stream=next(row['stream'] for row in reports if 'stream' in row)
        self.assertEqual(stream['active_source_id'],'second')
        self.assertEqual(stream['pending_hints'],1)
        self.assertEqual(stream['notifications']['duplicates'],1)
        reopened=EventStore(self.store.path)
        self.assertEqual(StreamHints(reopened,'first').report()['active_source_id'],'second')
        with reopened.connect() as db:
            self.assertEqual([tuple(row) for row in db.execute('SELECT source,slot FROM stream_observations ORDER BY slot')],[('second',4),('second',7)])
        previous_gap=stream['states']['gap']
        reached.clear()
        async def primary(endpoint,hints,*args,**kwargs):
            hints.accept(notification(3),now=102)
            reached.set();await asyncio.Future()
        with patch('onchain_stream.listen',side_effect=primary):
            asyncio.run(asyncio.wait_for(run_stream(self.ingester,self.sources[0],None,SimpleNamespace(sync=lambda:{}),max_cycles=1),5))
        self.assertEqual(StreamHints(reopened,'second').report()['active_source_id'],'first')
        self.assertEqual(StreamHints(reopened,'first').report()['pending_hints'],1)
        self.assertEqual(StreamHints(reopened,'first').report()['states']['gap'],previous_gap)
        self.assertEqual(self.store.next_slot(),initial_checkpoint)
        self.assertEqual(self.store.metrics()['events'],initial_events)
