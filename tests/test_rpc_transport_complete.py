"""UNEXECUTED actual Rust sockets; explicit monolith loopback opt-in only."""
import asyncio
import os
import sys
import unittest
from unittest.mock import MagicMock, patch
from data_policy import SourceAccessBlocked
from onchain_ingest import IndexRpc, READ_METHODS
from solana_rpc_compat import RustIndexRpc
from rpc_loopback_fixture import LocalRpcServer, RESULTS
try:
    from _solana_binary import NativeHttp
except ImportError:NativeHttp=None

ENABLED=os.environ.get('SOLANA_NATIVE_LOOPBACK_STRESS')=='1' and sys.platform=='linux' and NativeHttp is not None and hasattr(NativeHttp,'request_sync')


@unittest.skipUnless(ENABLED,'Explicit Linux loopback opt-in and current native extension required')
class CompleteTransportTests(unittest.TestCase):
    def setup_rpc(self,url):
        return RustIndexRpc({'url':url,'source_id':'loopback-fixture','allow_loopback_http':True},MagicMock())

    def test_all_read_methods_match_python_repeated_sync_reuses_connection(self):
        with LocalRpcServer() as server,patch('solana_rpc_compat.check_boundary',return_value=os.stat('/proc/self/ns/net').st_ino):
            with self.setup_rpc(server.url) as native:
                for method in sorted(READ_METHODS):self.assertEqual(native.call(method,[]),RESULTS[method])
                for _ in range(32):self.assertEqual(native.call('getSlot',[]),7)
                self.assertEqual(server.connections,1)
            with self.assertRaises(RuntimeError):native.call('getSlot',[])
            python=IndexRpc({'url':server.url,'source_id':'loopback-fixture','allow_loopback_http':True},MagicMock())
            for method in sorted(READ_METHODS):self.assertEqual(python.call(method,[]),RESULTS[method])

    def test_chunked_denied_redirect_oversize_and_no_malformed_backend_fallback(self):
        with LocalRpcServer() as server,patch('solana_rpc_compat.check_boundary',return_value=os.stat('/proc/self/ns/net').st_ino),self.setup_rpc(server.url) as native:
            server.script=[{'chunked':True}]
            self.assertEqual(native.call('getSlot',[]),7)
            for fault,error in [({'status':403},SourceAccessBlocked),({'status':429},ValueError),
                                ({'status':503},ConnectionError),({'status':302,'redirect':True},ValueError),
                                ({'body':'not-json'},SourceAccessBlocked),({'body':'{"jsonrpc":"2.0","id":2,"result":1}'},SourceAccessBlocked),
                                ({'declared_length':64000001},SourceAccessBlocked),({'encoding':'gzip'},SourceAccessBlocked)]:
                with self.subTest(fault=fault):
                    server.script=[fault]
                    before=len(server.requests)
                    with self.assertRaises(error):native.call('getSlot',[])
                    self.assertEqual(len(server.requests),before+1)
            before=len(server.requests)
            with self.assertRaises(SourceAccessBlocked):native.call('sendTransaction',[])
            self.assertEqual(len(server.requests),before)

    def test_async_sync_interleaving_runtime_and_disconnect_recovery(self):
        with LocalRpcServer() as server,patch('solana_rpc_compat.check_boundary',return_value=os.stat('/proc/self/ns/net').st_ino),self.setup_rpc(server.url) as native:
            self.assertEqual(native.call('getSlot',[]),7)
            self.assertEqual(asyncio.run(native.call_async('getSlot',[])),7)
            self.assertEqual(native.call('getSlot',[]),7)
            self.assertEqual(server.connections,1)
            server.script=[{'disconnect':True}]
            with self.assertRaises(ConnectionError):native.call('getSlot',[])
            self.assertEqual(native.call('getSlot',[]),7) # explicit retry only
            async def loop():
                with self.assertRaises(RuntimeError):native.call('getSlot',[])
                self.assertEqual(await native.call_async('getSlot',[]),7)
            asyncio.run(loop())
