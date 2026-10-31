"""UNEXECUTED completeness regressions, isolated SQLite and mocked transport."""
import unittest
from unittest.mock import MagicMock
from rpc_contract import response_value, remote_error, RpcEvidenceError, RpcHistoryUnavailable, http_error
from onchain_ingest import Ingester
from solana_rpc_compat import close_rpc_clients
import test_onchain_indexer as fixtures


class ResultContractTests(unittest.TestCase):
    def test_slot_and_result_validation(self):
        for method in ('getSlot','getFirstAvailableBlock','minimumLedgerSlot'):
            self.assertEqual(response_value(method,0,'fixture'),0)
            for value in (True,-1,None,'1',1.5):
                with self.subTest(method=method,value=value),self.assertRaises(RpcEvidenceError):response_value(method,value,'fixture')
        for value in (None,[True],[1,1],[2,1],[-1]):
            with self.assertRaises(RpcEvidenceError):response_value('getBlocks',value,'fixture')
        with self.assertRaises(RpcHistoryUnavailable):response_value('getBlock',None,'fixture')
        self.assertIsNone(response_value('getTransaction',None,'fixture'))

    def test_all_clients_close_even_when_one_cleanup_fails(self):
        first=MagicMock();second=MagicMock()
        second.close.side_effect=RuntimeError('cleanup failure')
        with self.assertRaises(RuntimeError):close_rpc_clients([first,second])
        first.close.assert_called_once();second.close.assert_called_once()

    def test_http_errors_preserve_classification_and_source(self):
        for status,kind in [(403,ValueError),(429,ValueError),(503,ConnectionError)]:
            error=http_error(status,'fixture')
            self.assertIsInstance(error,kind)
            self.assertEqual(error.http_status,status)
            self.assertEqual(error.source_id,'fixture')

    def test_history_and_invalid_request_errors_are_distinct(self):
        for code in (-32001,-32004,-32009,-32011,-32014):
            error=remote_error(code,'fixture');self.assertIsInstance(error,RpcHistoryUnavailable)
            self.assertEqual(error.rpc_code,code)
        self.assertIsInstance(remote_error(-32015,'fixture'),RpcEvidenceError)
        self.assertEqual(remote_error(429,'fixture').category,'throttled')


class HistoryGuardTests(unittest.TestCase):
    def setUp(self):
        self.helper=fixtures.IndexerTests();self.helper.setUp();self.addCleanup(self.helper.doCleanups)
        self.store=self.helper.store

    def rpc(self,floors):
        rpc=MagicMock(source='fixture',supports_history_probe=True,requests=0,wire_bytes=0,decoded_bytes=0)
        values=iter(floors)
        def call(method,params):
            if method=='getSlot':return 10
            if method=='getFirstAvailableBlock':return next(values)
            if method=='getBlocks':return []
            raise AssertionError('Unexpected RPC method')
        rpc.call.side_effect=call
        return rpc

    def test_pruned_provider_never_certifies_missing_history_as_skips(self):
        rpc=self.rpc([5]);engine=Ingester(self.store,[rpc],decoder=self.helper.decoder)
        with self.assertRaises(RpcHistoryUnavailable):engine.step(1,1)
        self.assertIsNone(self.store.next_slot())
        self.assertEqual(self.store.metrics()['blocks'],0)
        self.assertEqual(rpc.call.call_args_list[-1].args[0],'getFirstAvailableBlock')

    def test_retention_advancing_during_request_preserves_checkpoint(self):
        rpc=self.rpc([0,5]);engine=Ingester(self.store,[rpc],decoder=self.helper.decoder)
        engine.deployments=MagicMock(return_value={})
        with self.assertRaises(RpcHistoryUnavailable):engine.step(1,1)
        self.assertIsNone(self.store.next_slot())

    def test_explicit_configured_backup_can_supply_complete_skipped_range(self):
        first=self.rpc([5]);second=self.rpc([0,0])
        engine=Ingester(self.store,[first,second],decoder=self.helper.decoder)
        engine.deployments=MagicMock(return_value={})
        engine.step(1,1)
        self.assertEqual(self.store.next_slot(),2)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM ranges').fetchone()[0],1)

    def test_invalid_floor_never_tries_backup(self):
        first=self.rpc([True]);second=self.rpc([0,0])
        engine=Ingester(self.store,[first,second],decoder=self.helper.decoder)
        with self.assertRaises(RpcEvidenceError):engine.step(1,1)
        second.call.assert_not_called()
