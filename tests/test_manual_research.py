import copy
import tempfile
import unittest
from manual_research import ManualResearch,verify_profile
from test_wallet_watch import transaction,WALLET,MINT


class ManualResearchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.identity={'wallet':WALLET,'username':'LunaMoth69','profile_verified':True}
        self.archive=ManualResearch(self.temp.name,self.identity,900)
    def tearDown(self): self.archive.close();self.temp.cleanup()
    def test_public_profile_wallet_match_is_required(self):
        good=lambda url:{'address':WALLET,'username':'LunaMoth69'}
        self.assertTrue(verify_profile(WALLET,'LunaMoth69',good)['profile_verified'])
        with self.assertRaises(ValueError): verify_profile(WALLET,'wrong',good)
    def test_historical_trades_keep_original_time_and_never_become_fresh_signals(self):
        tx=transaction(timestamp=1000)
        self.assertTrue(self.archive.ingest(tx,'sig'))
        report=self.archive.report()
        self.assertEqual(report['known_pump_trades_nonbot'],1)
        self.assertEqual(report['buys'],1)
        self.assertFalse(report['profit_labels_verified'])
        import json
        event=json.loads(self.archive.db.execute('SELECT event FROM transactions').fetchone()[0])
        self.assertEqual(event['observed_at'],1000)
        self.assertTrue(event['historical']);self.assertFalse(event['automatic_trade_approval'])
    def test_known_bot_orders_are_kept_for_research_but_separate_from_manual_and_idempotent(self):
        tx=transaction(timestamp=1000)
        self.archive.ingest(tx,'sig',{'sig'});self.archive.ingest(tx,'sig',{'sig'})
        report=self.archive.report()
        self.assertEqual(report['transactions_archived'],1)
        self.assertEqual(report['known_bot_orders_archived'],1)
        self.assertEqual(report['known_pump_trades_nonbot'],0)
        self.assertEqual(report['research_trades_total'],1)
        self.assertEqual(report['known_pump_trades_bot'],1)
    def test_missing_receipt_and_wrong_signature_cannot_create_research_trades(self):
        self.assertFalse(self.archive.ingest(None,'sig'))
        with self.assertRaises(ValueError): self.archive.ingest(transaction(),'wrong')
        self.assertEqual(self.archive.report()['known_pump_trades_nonbot'],0)
    def test_ambiguous_and_outside_window_transactions_are_not_labeled_profitable(self):
        tx=transaction();tx['transaction']['message']['instructions']=[]
        self.archive.ingest(tx,'sig')
        self.assertEqual(self.archive.report()['unclassified_user_transactions'],1)
        old=transaction(timestamp=800,signature='old');self.archive.ingest(old,'old')
        self.assertEqual(self.archive.report()['known_pump_trades_nonbot'],0)
    def test_collection_is_bounded_and_unavailable_receipts_stay_pending(self):
        calls=[]
        class RPC:
            def call(self,method,params):
                calls.append(method)
                if method=='getSignaturesForAddress': return [{'signature':'sig','blockTime':1000}]
                if method=='getTransaction': return None
                raise AssertionError(method)
        report=self.archive.collect(RPC(),budget=1)
        self.assertEqual(calls.count('getTransaction'),1)
        self.assertEqual(report['pending_receipts'],1)
        self.assertFalse(report['complete_archive_independently_verified'])
