"""UNEXECUTED event parity and fail-closed compact projection tests."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import test_onchain_indexer as fixtures
from onchain_decode import Decoder, read_struct
from solana_rpc_compat import extract_event_batch
try:
    from _solana_binary import NativeBorshSchema
except ImportError:
    NativeBorshSchema=None


class CompactTests(unittest.TestCase):
    def test_projection_preserves_columns_and_never_grants_market_eligibility(self):
        tx=fixtures.transaction()
        expected,errors=Decoder().transaction(tx,1,0,1000)
        batch=extract_event_batch([tx],slot=1,block_time=1000,source_id='synthetic')
        actual=[{batch['columns'][i]:row[i] for i in present} for row,present in zip(batch['rows'],batch['presence'])]
        self.assertEqual(actual,expected if not errors else [])
        self.assertFalse(batch['market_eligible'])
        with self.assertRaises(ValueError):extract_event_batch([tx]*65,slot=1,block_time=1000,source_id='synthetic')

    def test_parity_mismatch_quarantines_entire_transaction(self):
        with patch.object(Decoder,'event_fields',side_effect=ValueError('Native event field parity mismatch')):
            batch=extract_event_batch([fixtures.transaction()],slot=1,block_time=1000,source_id='synthetic')
        self.assertEqual(batch['rows'],[])
        self.assertTrue(batch['issues'])


@unittest.skipUnless(NativeBorshSchema is not None,'Build rpc-engine first')
class NativeEventTests(unittest.TestCase):
    def test_synthetic_both_programs_and_recorded_layout_quarantine(self):
        inputs=[fixtures.transaction(venue=venue,buy=buy) for venue in ('pump','pump_amm') for buy in (True,False)]
        inputs += [json.loads(Path('tests/fixtures/onchain/'+name+'.json').read_text()) for name in ('curve','pumpswap')]
        for tx in inputs:
            expected=Decoder().transaction(tx,1,0,1000)
            for backend in ('rust','compare'):
                with self.subTest(backend=backend):self.assertEqual(Decoder(backend).transaction(tx,1,0,1000),expected)

    def test_bad_bounds_boolean_and_schema(self):
        schema=NativeBorshSchema('[{"name":"flag","type":"bool"}]','{}')
        for raw,offset in [(b'',0),(b'\2',0),(b'\0',2),(b'\0'*65537,0)]:
            with self.assertRaises(ValueError):schema.decode(raw,offset)
        with self.assertRaises(ValueError):NativeBorshSchema('[]','[]')

    def test_large_integer_is_lossless(self):
        schema=NativeBorshSchema('[{"name":"amount","type":"u128"}]','{}')
        text,end=schema.decode(bytes([255])*16,0)
        self.assertEqual(int(json.loads(text)['amount']['$integer']),2**128-1)
        self.assertEqual(end,16)
