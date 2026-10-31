"""UNEXECUTED startup regressions; temporary stores and mocked configured RPC."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from data_policy import SourceAccessBlocked
from onchain_store import EventStore
from solana_observer import observe


class StartSlotTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'observer.sqlite'
        self.store=None
        self.rpc=MagicMock();self.rpc.call.return_value=123
        self.stream=AsyncMock()
        def prepare(*args):
            self.store=EventStore(self.path)
            return SimpleNamespace(store=self.store,endpoints=[self.rpc]),{'backend':'rust'},object()
        self.prepare=patch('solana_observer.prepare',side_effect=prepare)
        self.prepare.start();self.addCleanup(self.prepare.stop)
        self.runner=patch('solana_observer.run_stream',self.stream)
        self.runner.start();self.addCleanup(self.runner.stop)

    def run_observer(self,**kwargs):
        asyncio.run(observe({},self.path,max_cycles=1,**kwargs))

    def test_new_database_discovers_and_durably_initializes_once(self):
        self.assertFalse(self.path.exists())
        self.run_observer()
        self.rpc.validate.assert_called_once_with()
        self.rpc.call.assert_called_once_with('getSlot',[{'commitment':'finalized'}])
        self.assertEqual(self.store.next_slot(),123)
        self.assertEqual(self.stream.await_args.args[2],123)
        self.run_observer() # no --resume needed; checkpoint wins
        self.assertEqual(self.rpc.call.call_count,1)
        self.assertIsNone(self.stream.await_args.args[2])

    def test_existing_checkpoint_wins_over_explicit_override(self):
        self.store=EventStore(self.path)
        with self.store.connect() as db:db.execute("INSERT INTO checkpoints VALUES('next_slot',77)")
        self.run_observer(start_slot=999)
        self.assertEqual(self.store.next_slot(),77)
        self.rpc.call.assert_not_called()
        self.assertIsNone(self.stream.await_args.args[2])

    def test_explicit_override_avoids_tip_lookup(self):
        self.run_observer(start_slot=0)
        self.assertEqual(self.store.next_slot(),0)
        self.rpc.call.assert_not_called()
        self.assertEqual(self.stream.await_args.args[2],0)

    def test_invalid_responses_never_initialize_or_start_stream(self):
        for response in (None,True,-1,'123',1.5):
            with self.subTest(response=response):
                self.rpc.call.return_value=response
                with self.assertRaises(ValueError):self.run_observer()
                self.assertIsNone(self.store.next_slot())
        self.stream.assert_not_awaited()

    def test_unavailable_authorization_and_budget_errors_never_guess(self):
        for error in (ConnectionError('unavailable'),SourceAccessBlocked('unauthorized'),SourceAccessBlocked('budget exhausted')):
            with self.subTest(error=error):
                self.rpc.call.side_effect=error
                with self.assertRaises(type(error)):self.run_observer()
                self.assertIsNone(self.store.next_slot())
        self.stream.assert_not_awaited()

    def test_restart_after_stream_failure_does_not_rediscover_or_skip(self):
        self.stream.side_effect=ConnectionError('interrupted after initialization')
        with self.assertRaises(ConnectionError):self.run_observer()
        self.assertEqual(self.store.next_slot(),123)
        self.stream.side_effect=None;self.rpc.call.return_value=999
        self.run_observer()
        self.assertEqual(self.store.next_slot(),123)
        self.assertEqual(self.rpc.call.call_count,1)

    def test_checkpoint_created_during_discovery_wins(self):
        def discover(*args):
            with self.store.connect() as db:db.execute("INSERT INTO checkpoints VALUES('next_slot',77)")
            return 123
        self.rpc.call.side_effect=discover
        self.run_observer()
        self.assertEqual(self.store.next_slot(),77)
        self.assertIsNone(self.stream.await_args.args[2])
