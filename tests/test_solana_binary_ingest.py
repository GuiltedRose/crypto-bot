"""UNEXECUTED synthetic receipt tests; no network or operational databases."""
import base64
import unittest
from unittest.mock import patch
from test_solana_binary import fixture
from solana_binary_ingest import ingest_binary, ingest_rpc_response
import solana_binary

POLICY={'storage_verified':True,'binary_ingestion_verified':True,'evidence':'synthetic test only'}


def context(**overrides):
    return dict(dict(source_id='fixture-v1',policy=POLICY,slot=12,tx_index=2,commitment='finalized',
                     metadata={'err':None,'preBalances':[0,0],'postBalances':[0,0]},backend='python'),**overrides)


class IngestTests(unittest.TestCase):
    def test_receipt_preserves_order_metadata_and_unverified_status(self):
        result=ingest_binary(fixture(),**context())
        self.assertEqual(result['status'],'parsed')
        self.assertEqual((result['slot'],result['tx_index']),(12,2))
        self.assertTrue(result['source_reports_success'])
        self.assertFalse(result['execution_verified'])
        self.assertFalse(result['consensus_verified'])
        self.assertFalse(result['market_eligible'])

    def test_rpc_accepts_original_base64_not_json_reserialization(self):
        args=context(); meta=args.pop('metadata'); args.pop('slot')
        response={'result':{'slot':12,'meta':meta,'transaction':[base64.b64encode(fixture()).decode(),'base64']}}
        self.assertEqual(ingest_rpc_response(response,**args)['raw'],fixture())
        response['result']['transaction']={'message':{}}
        with self.assertRaises(ValueError):ingest_rpc_response(response,**args)

    def test_incomplete_or_invalid_evidence_is_quarantined(self):
        for update in ({'policy':{}},{'source_id':''},{'slot':True},{'tx_index':-1},
                       {'commitment':'processed'},{'metadata':{}},{'metadata':None}):
            with self.subTest(update=update):
                self.assertEqual(ingest_binary(fixture(),**context(**update))['status'],'quarantined')
        result=ingest_binary(fixture(),**context(metadata={'err':{'InstructionError':[0,'failed']},'preBalances':[0,0],'postBalances':[0,0]}))
        self.assertEqual(result['status'],'parsed')
        self.assertFalse(result['source_reports_success'])

    def test_v0_lookups_remain_quarantined_without_matching_addresses(self):
        self.assertEqual(ingest_binary(fixture(0,loaded=True),**context())['status'],'quarantined')
        metadata={'err':None,'loadedAddresses':{'writable':['11111111111111111111111111111111'],'readonly':[]},'preBalances':[0,0,0],'postBalances':[0,0,0]}
        self.assertEqual(ingest_binary(fixture(0,loaded=True),**context(metadata=metadata))['status'],'parsed')

    def test_missing_native_falls_back_but_native_errors_quarantine(self):
        with patch('solana_binary._native',None):
            self.assertEqual(ingest_binary(fixture(),**context(backend='auto'))['status'],'parsed')
        with patch('solana_binary._native',side_effect=ValueError('malformed native input')):
            result=ingest_binary(fixture(),**context(backend='auto'))
            self.assertEqual(result['status'],'quarantined')
            self.assertEqual(result['reason'],'malformed native input')

    @unittest.skipUnless(solana_binary._native is not None,'Native extension required')
    def test_native_and_reference_envelopes_match(self):
        for raw in (fixture(),fixture(0),fixture()[:-1]):
            self.assertEqual(ingest_binary(raw,**context(backend='rust')),
                             ingest_binary(raw,**context(backend='python')))
