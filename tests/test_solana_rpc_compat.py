"""UNEXECUTED offline compatibility tests. Native I/O and policy gates mocked."""
import asyncio
import json
import io
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from data_policy import SourceAccessBlocked
from solana_rpc_compat import make_rpc, RustIndexRpc, stream_connector, check_boundary
from onchain_ingest import IndexRpc

ENDPOINT={'url':'https://example.invalid','source_id':'fixture','backend':'rust','approved_namespace_inode':1,
          'ws_url':'wss://example.invalid','policy':{'stream_verified':True}}


class CompatTests(unittest.TestCase):
    def rpc(self):
        budget=MagicMock()
        rpc=RustIndexRpc(ENDPOINT,budget)
        rpc._transport=MagicMock()
        rpc._transport.request=AsyncMock(return_value=(200,'',json.dumps({'jsonrpc':'2.0','id':1,'result':99})))
        rpc._transport.request_sync.side_effect=lambda method,params:asyncio.run(rpc._transport.request(method,params))
        return rpc

    def test_read_only_session_status_and_expiry_fail_closed(self):
        with patch('pathlib.Path') as path, patch('os.stat') as stat, patch('time.time',return_value=100):
            path.return_value.is_symlink.return_value=False
            path.return_value.stat.return_value=SimpleNamespace(st_uid=0,st_mode=0o100644,st_mtime=100)
            stat.return_value=SimpleNamespace(st_ino=7)
            endpoint={**ENDPOINT,'approved_namespace_inode':7,'policy':{'storage_verified':True,'evidence':'fixture'}}
            for expires,allowed,expected in [(110,True,True),(99,True,False),(110,False,False)]:
                path.return_value.open.return_value=io.BytesIO(json.dumps({'namespace_inode':7,'expires':expires,'optional_allowed':allowed}).encode())
                if expected:self.assertEqual(check_boundary(endpoint),7)
                else:
                    with self.assertRaises(SourceAccessBlocked):check_boundary(endpoint)
            for stamp in (0,101):
                path.return_value.stat.return_value.st_mtime=stamp
                with self.assertRaises(SourceAccessBlocked):check_boundary(endpoint)

    def test_default_and_auto_stay_python(self):
        for mode in ('python','auto'):
            self.assertIs(type(make_rpc({**ENDPOINT,'backend':mode})),IndexRpc)
        self.assertIsInstance(make_rpc(ENDPOINT),RustIndexRpc)

    def test_repeated_sync_calls_and_explicit_shutdown(self):
        rpc=self.rpc()
        with patch('solana_rpc_compat.check_boundary'):
            for _ in range(8):self.assertEqual(rpc.call('getSlot',[]),99)
        self.assertEqual(rpc._transport.request_sync.call_count,8)
        rpc.close();rpc._transport.close.assert_called_once()
        with self.assertRaises(RuntimeError):rpc.call('getSlot',[])

    def test_http_contract_and_no_signing(self):
        rpc=self.rpc()
        with patch('solana_rpc_compat.check_boundary'):
            self.assertEqual(rpc.call('getSlot',[]),99)
            rpc._transport.request.assert_awaited_once_with('getSlot','[]')
            with self.assertRaises(SourceAccessBlocked):rpc.call('sendTransaction',[])
        self.assertEqual(rpc.requests,1)

    def test_malformed_evidence_never_falls_back(self):
        rpc=self.rpc()
        for response in ('bad','{"jsonrpc":"2.0","id":true,"result":1}', '{"jsonrpc":"2.0","id":1,"result":1,"error":{}}'):
            rpc._transport.request.return_value=(200,'',response)
            with patch('solana_rpc_compat.check_boundary'),self.assertRaises(SourceAccessBlocked):rpc.call('getSlot',[])

    def test_provider_failure_vs_access_denial_and_retry_after(self):
        rpc=self.rpc()
        for status,error in [(503,ConnectionError),(403,SourceAccessBlocked),(429,ValueError)]:
            rpc._transport.request.return_value=(status,'60','')
            with patch('solana_rpc_compat.check_boundary'),self.assertRaises(error):rpc.call('getSlot',[])
            rpc.budget.failure.assert_called_with(rpc.url,status,{'Retry-After':'60'})

    def test_boundary_denial_precedes_native_io(self):
        rpc=self.rpc()
        with patch('solana_rpc_compat.check_boundary',side_effect=SourceAccessBlocked('quota')),self.assertRaises(SourceAccessBlocked):rpc.call('getSlot',[])
        rpc._transport.request.assert_not_called()

    def test_explicit_close_does_not_mark_provider_unavailable(self):
        rpc=self.rpc()
        async def interrupted(*args):
            rpc.close()
            raise ConnectionError('closed socket')
        rpc._transport.request.side_effect=interrupted
        async def scenario():
            with patch('solana_rpc_compat.check_boundary'),self.assertRaises(RuntimeError):
                await rpc.call_async('getSlot',[])
        asyncio.run(scenario())
        rpc.budget.failure.assert_not_called()

    def test_cancellation_propagates_without_retry(self):
        rpc=self.rpc();rpc._transport.request.side_effect=asyncio.CancelledError
        async def scenario():
            with patch('solana_rpc_compat.check_boundary'),self.assertRaises(asyncio.CancelledError):await rpc.call_async('getSlot',[])
        asyncio.run(scenario()); self.assertEqual(rpc._transport.request.await_count,1)

    def test_ws_context_closes_and_preserves_send_recv(self):
        socket=MagicMock(send=AsyncMock(),recv=AsyncMock(return_value='packet'),close=AsyncMock())
        native=MagicMock(connect_ws=AsyncMock(return_value=socket))
        async def scenario():
            with patch('solana_rpc_compat.check_boundary'),patch('solana_rpc_compat.native_module',return_value=native):
                connector=stream_connector(ENDPOINT,None)
                async with connector('wss://example.invalid') as ws:
                    await ws.send('request');self.assertEqual(await ws.recv(),'packet')
            socket.close.assert_awaited_once()
        asyncio.run(scenario())
