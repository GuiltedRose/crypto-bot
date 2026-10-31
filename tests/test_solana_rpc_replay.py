"""UNEXECUTED identical offline RPC response replay across Python/Rust."""
import base64
import json
import unittest
from test_solana_binary import fixture
from solana_rpc_replay import replay_binary_response
try:
    from _solana_binary import replay_binary_response as native_replay
except ImportError:
    native_replay=None


def packet(raw,err=None):
    return {'transaction':[base64.b64encode(raw).decode(),'base64'],'meta':{'err':err}}


def replay():
    return json.dumps({'jsonrpc':'2.0','id':1,'result':{'transactions':[
        packet(fixture()),packet(fixture(0),{'InstructionError':[0,'failed']}),
        packet(fixture()[:-1]),{'transaction':{'message':{}}},
        {'transaction':[base64.b64encode(fixture()).decode(),'base64']}]}})


class ReplayTests(unittest.TestCase):
    def test_order_quarantine_and_no_execution_verification(self):
        rows=replay_binary_response(replay())
        self.assertEqual([row[0] for row in rows],list(range(5)))
        self.assertEqual([row[1] for row in rows],['parsed_unverified','parsed_unverified','quarantined','unsupported_json','quarantined'])
        self.assertEqual(rows[1][2],False)

    @unittest.skipUnless(native_replay is not None,'Build rpc-engine first')
    def test_native_reference_equality_and_invalid_envelopes(self):
        self.assertEqual(native_replay(replay()),replay_binary_response(replay()))
        for text in ('bad','{}','{"jsonrpc":"2.0","id":true,"result":{}}'):
            for function in (native_replay,replay_binary_response):
                with self.assertRaises(ValueError):function(text)
