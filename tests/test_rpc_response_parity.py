"""UNEXECUTED body-contract parity; both HTTP paths isolated from networking."""
import unittest
from unittest.mock import MagicMock, patch
from onchain_ingest import IndexRpc
from solana_rpc_compat import RustIndexRpc


class ResponseParityTests(unittest.TestCase):
    def test_both_backends_share_strict_envelope_and_error_contract(self):
        endpoint={'url':'https://offline.invalid','source_id':'fixture'}
        python=IndexRpc(endpoint,MagicMock());rust=RustIndexRpc(endpoint,MagicMock())
        bodies=['{"jsonrpc":"2.0","id":1,"result":7}',
                '{"jsonrpc":"2.0","id":true,"result":7}',
                '{"jsonrpc":"1.0","id":1,"result":7}',
                '{"jsonrpc":"2.0","id":2,"result":7}',
                '{"jsonrpc":"2.0","id":1,"id":2,"result":7}',
                '{"jsonrpc":"2.0","id":1,"result":NaN}',
                '{"jsonrpc":"2.0","id":1,"result":true}',
                '{"jsonrpc":"2.0","id":1,"result":7,"error":{"code":429}}',
                '{"jsonrpc":"2.0","id":1,"error":{"code":429}}',
                '{"jsonrpc":"2.0","id":1,"error":{"code":-32001}}',
                'not-json','{}']
        def outcome(function):
            try:return ('value',function())
            except Exception as error:return ('error',type(error),getattr(error,'rpc_code',None),getattr(error,'category',None))
        for text in bodies:
            with self.subTest(text=text),patch('onchain_ingest.build_opener') as opener:
                response=opener.return_value.open.return_value.__enter__.return_value
                response.read.return_value=text.encode();response.headers={}
                self.assertEqual(outcome(lambda:python.call('getSlot',[])),
                                 outcome(lambda:rust._finish(200,'',text,method='getSlot')))
