import copy
import tempfile
import sqlite3
import unittest
from pathlib import Path
import json
import test_meme_dip as fixtures
from test_candle_quality import rising_history
from meme_dip import DipRules, PaperDipTrader
from paper_runtime import PaperSession
from run_paper import feed_once, markets_once, candidate_queue


class PaperRuntimeTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.DipTests()
        fixture.setUp()
        self.snapshot = fixture.snapshot
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def snapshot_at(self, timestamp, price):
        return dict(copy.deepcopy(self.snapshot), observed_at=timestamp, price_usd=price,candle_history=rising_history(timestamp,price))

    def test_restart_preserves_setup_fill_and_idempotency(self):
        session = PaperSession(self.directory, 10)
        first = self.snapshot_at(1010, .6)
        self.assertEqual(session.process(first)['action'], 'watch')
        session.close()
        session = PaperSession(self.directory)
        self.assertIsNone(session.process(first))
        self.assertEqual(session.process(self.snapshot_at(1020, .63))['action'], 'paper_buy')
        cash = session.trader.cash
        session.close()
        session = PaperSession(self.directory)
        self.assertEqual(session.trader.cash, cash)
        self.assertIsNotNone(session.trader.position)
        self.assertEqual(session.process(self.snapshot_at(1030, .9))['action'], 'paper_sell')
        self.assertEqual(session.report()['event_counts']['paper_buy'], 1)
        session.close()

    def test_paper_training_and_auto_refill_preserve_outcomes(self):
        session=PaperSession(self.directory,10)
        try:
            session.process(self.snapshot_at(1010,.6))
            session.process(self.snapshot_at(1020,.63))
            self.assertIsNotNone(session.trader.position)
            session.trader.halted=True
            self.assertIsNone(session.auto_reset_if_exhausted())
            session.process(self.snapshot_at(1030,.9))
            before=session.refresh_learning()
            self.assertEqual(before['samples'],1)
            previous=session.run_id
            event=session.auto_reset_if_exhausted()
            self.assertEqual(event['archived_run'],previous)
            self.assertEqual(event['pnl_usd'],0)
            self.assertEqual(session.trader.cash,8.5)
            self.assertFalse(session.trader.halted)
            after=session.refresh_learning()
            self.assertEqual(before,after)
            self.assertTrue((self.directory/'learning.json').exists())
            self.assertIsNone(session.auto_reset_if_exhausted())
        finally:session.close()

    def test_auto_refill_only_when_unspendable_or_halted(self):
        session=PaperSession(self.directory,10)
        try:
            session.process(self.snapshot_at(1010,.6))
            session.process(self.snapshot_at(1020,.63))
            session.process(self.snapshot_at(1030,.9))
            self.assertIsNone(session.auto_reset_if_exhausted())
            session.trader.cash=.5
            event=session.auto_reset_if_exhausted()
            self.assertEqual(event['reason'],'spendable paper balance exhausted')
            self.assertEqual(session.trader.cash,8.5)
        finally:session.close()

    def test_paper_cooldown_shortens_existing_wait_and_survives_restart(self):
        session=PaperSession(self.directory,10)
        session.trader.consecutive_losses=2
        session.trader.loss_cooldown_until=4600  # Loss at 1000, original pause 3600.
        session.configure_loss_cooldown(60)
        self.assertEqual(session.trader.loss_cooldown_until,1060)
        self.assertEqual(session.trader.rules.loss_streak_cooldown_seconds,3600)
        self.assertEqual(session.process(self.snapshot_at(1050,.6))['reason'],'paper consecutive-loss cooldown')
        session.close()
        session=PaperSession(self.directory)
        try:
            self.assertEqual(session.trader.paper_loss_cooldown_seconds,60)
            self.assertEqual(session.trader.consecutive_losses,2)
            result=session.process(self.snapshot_at(1060,.6))
            self.assertNotEqual(result.get('reason'),'paper consecutive-loss cooldown')
        finally:session.close()

    def test_reset_archives_positions_and_all_events(self):
        session = PaperSession(self.directory, 10, DipRules(rebound_fraction=0))
        session.process(self.snapshot_at(1010, .6))
        previous = session.run_id
        session.reset(20)
        self.assertNotEqual(session.run_id, previous)
        self.assertEqual(session.trader.cash, 20)
        self.assertIsNone(session.trader.position)
        self.assertEqual(session.report()['event_counts'], {})
        self.assertEqual(session.report()['archived_runs'], 1)
        self.assertEqual(session.db.execute('SELECT COUNT(*) FROM paper_events WHERE run_id=?', (previous,)).fetchone()[0], 1)
        session.close()

    def test_invalid_reset_does_not_archive_active_run(self):
        session = PaperSession(self.directory, 10)
        previous = session.run_id
        with self.assertRaises(ValueError):
            session.reset(-1)
        self.assertEqual(session.run_id, previous)
        self.assertEqual(session.report()['archived_runs'], 0)
        session.close()

    def test_single_writer_and_changed_configuration(self):
        session = PaperSession(self.directory, 10)
        with self.assertRaises(ValueError):
            PaperSession(self.directory)
        session.close()
        with self.assertRaises(ValueError):
            PaperSession(self.directory, 20)
        session = PaperSession(self.directory)
        session.close()

    def test_invalid_input_does_not_change_checkpoint(self):
        session = PaperSession(self.directory, 10)
        before = session.trader.cash
        self.assertEqual(session.process({'mint': 'Mint'})['action'], 'invalid_snapshot')
        self.assertEqual(session.trader.cash, before)
        self.assertEqual(session.trader.last_timestamp, -1)
        session.close()

    def test_failed_event_write_does_not_apply_a_fill_or_checkpoint(self):
        session = PaperSession(self.directory, 10, DipRules(rebound_fraction=0))
        session.db.execute("CREATE TRIGGER block_write BEFORE INSERT ON paper_events BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            session.process(self.snapshot_at(1010, .6))
        self.assertEqual(session.trader.cash, 10)
        self.assertIsNone(session.trader.position)
        session.db.execute('DROP TRIGGER block_write')
        session.close()
        session = PaperSession(self.directory)
        self.assertEqual(session.trader.cash, 10)
        self.assertEqual(session.report()['event_counts'], {})
        self.assertEqual(session.process(self.snapshot_at(1010, .6))['action'], 'paper_buy')
        session.close()

    def test_append_feed_waits_for_complete_lines_and_skips_existing_records(self):
        path = self.directory/'feed.jsonl'
        first = json.dumps(self.snapshot_at(1010, .6))
        second = json.dumps(self.snapshot_at(1020, .63))
        path.write_text(first+'\n'+second[:50])
        session = PaperSession(self.directory, 10)
        self.assertEqual(feed_once(session, path)['status'], 'waiting_for_complete_record')
        path.write_text(first+'\n'+second+'\n')
        self.assertEqual(feed_once(session, path)['processed'], 1)
        self.assertIsNotNone(session.trader.position)
        self.assertEqual(feed_once(session, path)['processed'], 0)
        session.close()

    def test_outage_keeps_open_position_and_quotes_it_before_new_candidates(self):
        session = PaperSession(self.directory, 10, DipRules(rebound_fraction=0))
        session.process(self.snapshot_at(1010, .6))
        class Unavailable:
            calls = []
            def observation(self, mint, **kwargs):
                self.calls.append(mint)
                raise ConnectionError('unavailable')
        sources = Unavailable()
        status = markets_once(session, sources, ['Other'])
        self.assertEqual(sources.calls[0], 'Mint')
        self.assertTrue(status['open_position_quote_unavailable'])
        self.assertIsNotNone(session.trader.position)
        session.close()

    def test_wallet_candidates_receive_the_same_market_and_risk_screen(self):
        session = PaperSession(self.directory, 10)
        fixture = self.snapshot_at(1010, .6)
        fixture['evidence'] = {}  # Watching a trader cannot fill in missing proof.
        class Sources:
            def observation(self, mint, **kwargs): return fixture
        class Watcher:
            def poll(self, wallets):
                return {'candidate_mints': ['Mint'], 'errors': {}, 'signals': []}
        report = markets_once(session, Sources(), [], watcher=Watcher(), wallets=['wallet'])
        self.assertEqual(report['candidate_mints'], ['Mint'])
        self.assertEqual(session.report()['event_counts'], {'reject': 1})
        self.assertIsNone(session.trader.position)
        session.close()

    def test_native_costs_reserves_and_failed_exit(self):
        snapshot = self.snapshot_at(1010, .6)
        snapshot['execution'] = {'mint': 'Mint', 'pool': 'Pool', 'observed_at': 1010,
                                 'sol_price_usd': 100, 'network_fee_sol': .000005,
                                 'entry_rent_sol': .00001, 'exit_rent_sol': 0,
                                 'buy_route_available': True, 'sell_route_available': True}
        trader = PaperDipTrader(10, DipRules(rebound_fraction=0), .006, 100)
        self.assertEqual(trader.on_snapshot(snapshot)['action'], 'paper_buy')
        self.assertAlmostEqual(trader.native_sol, .005985)
        blocked = copy.deepcopy(snapshot)
        blocked.update(observed_at=1020, price_usd=.1)
        blocked['execution']['sell_route_available'] = False
        self.assertEqual(trader.on_snapshot(blocked)['action'], 'reject')
        self.assertIsNotNone(trader.position)
        blocked['observed_at'] = 1030
        blocked['execution']['sell_route_available'] = True
        self.assertEqual(trader.on_snapshot(blocked)['action'], 'paper_sell')
        self.assertAlmostEqual(trader.native_sol, .00598)
        trader = PaperDipTrader(10, DipRules(rebound_fraction=0), .005, 100)
        self.assertEqual(trader.on_snapshot(snapshot)['reason'], 'paper native fee reserve')
        self.assertEqual(trader.cash, 10)

    def test_reclaimed_sol_can_fund_trades_without_usdc(self):
        from meme_dip import SOL_MINT
        trader=PaperDipTrader(0,DipRules(rebound_fraction=0),.065,100)
        self.assertEqual(trader.cash+trader.native_sol*100,6.5)
        snapshot=self.snapshot_at(1010,.6)
        snapshot['quote_mint']=SOL_MINT
        snapshot['execution']={'mint':'Mint','pool':'Pool','observed_at':1010,'sol_price_usd':100,
                               'network_fee_sol':.000005,'entry_rent_sol':0,'exit_rent_sol':0,
                               'buy_route_available':True,'sell_route_available':True,'quote_mint':SOL_MINT}
        bought=trader.on_snapshot(snapshot)
        self.assertEqual(bought['action'],'paper_buy')
        self.assertEqual(trader.cash,0)
        self.assertAlmostEqual(trader.native_sol,.065-.000005-bought['budget_usd']/100)
        self.assertGreater(trader.native_sol,.005)
        snapshot.update(observed_at=1020,price_usd=.9)
        sold=trader.on_snapshot(snapshot)
        self.assertEqual(sold['action'],'paper_sell')
        self.assertEqual(trader.cash,0)
        self.assertGreater(trader.native_sol,.065)
        self.assertGreater(sold['pnl_usd'],0)

    def test_sol_quote_mismatch_cannot_spend_the_wrong_balance(self):
        from meme_dip import SOL_MINT,USDC_MINT
        snapshot=self.snapshot_at(1010,.6)
        snapshot['quote_mint']=SOL_MINT
        snapshot['execution']={'mint':'Mint','pool':'Pool','observed_at':1010,'sol_price_usd':100,
                               'network_fee_sol':.000005,'entry_rent_sol':0,'exit_rent_sol':0,
                               'buy_route_available':True,'sell_route_available':True,'quote_mint':USDC_MINT}
        trader=PaperDipTrader(0,DipRules(rebound_fraction=0),.065,100)
        self.assertEqual(trader.on_snapshot(snapshot)['action'],'reject')
        self.assertEqual(trader.native_sol,.065)

    def test_exploratory_ledger_reports_strict_blockers_separately(self):
        session=PaperSession(self.directory,6.5,DipRules(evidence_mode='market_only',rebound_fraction=0))
        snapshot=self.snapshot_at(1010,.6)
        snapshot.pop('token_safety_passed')
        snapshot.pop('safety_checked_at')
        snapshot['evidence']={}
        event=session.process(snapshot)
        self.assertEqual(event['action'],'paper_buy')
        self.assertFalse(event['safety_qualified'])
        self.assertTrue(event['strict_entry_blockers'])
        self.assertEqual(session.report()['safety_evidence_mode'],'market_only')
        self.assertFalse(session.report()['live_qualified'])
        session.close()

    def test_ranked_head_plus_rotating_candidates_preserves_priority_and_coverage(self):
        session=PaperSession(self.directory,10)
        ranked=['m'+str(i) for i in range(12)]
        first=candidate_queue(session,None,[],ranked,[])
        second=candidate_queue(session,None,[],ranked,[])
        self.assertEqual(first[:2],ranked[:2])
        self.assertEqual(second[:2],ranked[:2])
        self.assertNotEqual(first[2:],second[2:])
        session.close()
        session=PaperSession(self.directory)
        third=candidate_queue(session,None,[],ranked,[])
        self.assertEqual(set(first+second+third),set(ranked))
        session.close()

    def test_native_model_requires_execution_proof_and_keeps_usdc_reserve(self):
        trader = PaperDipTrader(1, DipRules(rebound_fraction=0), .006, 100)
        self.assertEqual(trader.on_snapshot(self.snapshot_at(1010, .6))['action'], 'reject')
        self.assertIsNone(trader.position)
        self.assertEqual(trader.cash, 1)


if __name__ == '__main__':
    unittest.main()
