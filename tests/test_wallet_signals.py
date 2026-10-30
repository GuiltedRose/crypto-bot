import copy
import fcntl
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from wallet_signals import digest, read_signals, prioritize_assets, live_context, collect_once, ensure_worker
from wallet_watch import SOL

WALLET = '11111111111111111111111111111111'
MINT = 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA'


class SharedWalletTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.report = {'schema': 1, 'watchlist_digest': digest([WALLET]), 'published_at': 1100,
                       'status': 'bounded_sample', 'errors': {},
                       'wallet_checks': {WALLET: {'checked_at': 1100, 'status': 'bounded_sample'}},
                       'signals': [{'mint': MINT, 'watched_buyers': 999, 'observations': [
                           {'wallet': WALLET, 'mint': MINT, 'observed_at': 1000, 'action': 'buy', 'signature': 'sig'}]}]}

    def tearDown(self):
        self.temp.cleanup()

    def publish(self):
        (self.directory/'status.json').write_text(json.dumps(self.report))

    def test_paper_and_live_read_same_signals_without_network_or_approvals(self):
        self.publish()
        paper = read_signals([WALLET], self.directory, 1150)
        live = read_signals([WALLET], self.directory, 1150)
        self.assertEqual(paper, live)
        self.assertEqual(live['candidate_mints'], [MINT])
        self.assertEqual(live['signals'][0]['watched_buyers'], 1)  # Don't trust cached aggregate counts.
        self.assertFalse(live['automatic_trade_approval'])
        self.assertFalse(live['independent_human_verified'])

    def test_stale_report_old_receipt_or_watchlist_change_cannot_influence_entries(self):
        self.publish()
        self.assertEqual(read_signals([WALLET], self.directory, 1300)['candidate_mints'], [])
        self.assertEqual(read_signals([SOL], self.directory, 1150)['candidate_mints'], [])
        self.report['published_at'] = 1400
        self.report['wallet_checks'][WALLET]['checked_at'] = 1400
        self.publish()
        self.assertEqual(read_signals([WALLET], self.directory, 1400)['candidate_mints'], [])

    def test_unavailable_wallet_and_foreign_mint_binding_rejected(self):
        self.report['wallet_checks'][WALLET]['status'] = 'unavailable'
        self.publish()
        self.assertEqual(read_signals([WALLET], self.directory, 1150)['candidate_mints'], [])
        self.report['wallet_checks'][WALLET]['status'] = 'bounded_sample'
        self.report['signals'][0]['observations'][0]['mint'] = SOL
        self.publish()
        self.assertEqual(read_signals([WALLET], self.directory, 1150)['candidate_mints'], [])

    def test_live_priority_cannot_add_assets_or_change_approvals(self):
        assets = [{'mint': SOL, 'approved_for_live': True}, {'mint': MINT, 'approved_for_live': False}]
        before = copy.deepcopy(assets)
        report = {'signals': [{'mint': MINT, 'watched_buyers': 1}], 'candidate_mints': [MINT, WALLET]}
        prioritized = prioritize_assets(assets, report)
        self.assertEqual(prioritized[0]['mint'], MINT)
        self.assertFalse(prioritized[0]['approved_for_live'])
        self.assertEqual(assets, before)
        context = live_context(report, assets)
        self.assertEqual(context['unreviewed_candidates'], [WALLET])
        self.assertFalse(context['automatic_trade_approval'])
        self.assertEqual(len(prioritized), len(assets))

    def test_collector_failure_atomically_replaces_old_buy_signals(self):
        self.publish()
        class Failed:
            def poll(self, wallets, now=None): raise ConnectionError()
        collect_once(Failed(), [WALLET], self.directory, 1150)
        self.assertEqual(read_signals([WALLET], self.directory, 1150)['candidate_mints'], [])
        self.assertFalse((self.directory/'status.tmp').exists())

    def test_unapproved_cached_callouts_are_excluded_from_both_modes(self):
        from test_pump_callouts import payload
        from pump_callouts import parse_hot_callouts
        rows, _ = parse_hot_callouts(payload(), 1100, [WALLET])
        self.report['signals'] = []
        self.report['callouts'] = rows
        self.publish()
        report = read_signals([WALLET], self.directory, 1150)
        self.assertEqual(report['candidate_mints'], [])
        self.assertEqual(report['signals'], [])
        self.assertEqual(len(live_context(report, [])['callouts']), 0)
        assets = [{'mint': SOL, 'approved_for_live': True}, {'mint': MINT, 'approved_for_live': False}]
        prioritized = prioritize_assets(assets, report)
        self.assertEqual(prioritized[0]['mint'], SOL)
        self.assertTrue(prioritized[0]['approved_for_live'])

    def test_candidate_ranking_uses_popularity_instead_of_lexical_mint_order(self):
        from wallet_signals import rank_candidate_mints
        report={'candidate_mints':[MINT,SOL], 'signals':[], 'callouts':[
            {'mint':MINT,'author_wallet':WALLET,'views':10},
            {'mint':SOL,'author_wallet':WALLET,'views':1000}]}
        self.assertEqual(rank_candidate_mints(report)[0],SOL)
        self.assertEqual(report['candidate_mints'],[MINT,SOL])

    def test_worker_single_writer_and_no_credential_inheritance(self):
        watchlist = self.directory/'watchlist.json'
        watchlist.write_text(json.dumps({'wallets': [WALLET]}))
        with (self.directory/'collector.lock').open('a') as lock, patch('wallet_signals.subprocess.Popen') as spawn:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertFalse(ensure_worker(watchlist, self.directory))
            spawn.assert_not_called()
        with patch.dict(os.environ, {'SOLANA_PRIVATE_KEY': 'must-not-inherit'}), patch('wallet_signals.subprocess.Popen') as spawn:
            self.assertTrue(ensure_worker(watchlist, self.directory))
            environment = spawn.call_args.kwargs['env']
            self.assertNotIn('SOLANA_PRIVATE_KEY', environment)
            self.assertEqual(set(environment), {'PATH', 'LANG', 'PYTHONUNBUFFERED'})


if __name__ == '__main__':
    unittest.main()
