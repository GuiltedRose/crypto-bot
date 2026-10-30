import tempfile
import unittest
from paper_bankroll import reset_target,confirmed_milestone
from paper_runtime import PaperSession


class ProgressiveBankrollTests(unittest.TestCase):
    def test_reset_formula_and_discrete_milestones(self):
        for reference,expected in ((5,6),(7,6),(10,8.5),(100,85),(1000,850),(5000,4250)):
            self.assertEqual(reset_target(reference),expected)
        self.assertEqual(confirmed_milestone(24.99),10)
        self.assertEqual(confirmed_milestone(25),25)

    def test_milestone_survives_losses_restart_and_reset_without_counting_refill(self):
        with tempfile.TemporaryDirectory() as root:
            session=PaperSession(root,10)
            session.trader.strategy_realized_pnl_usd=90
            self.assertEqual(session.record_bankroll_milestone(),100)
            session.trader.strategy_realized_pnl_usd=-5
            session.close();session=PaperSession(root)
            try:
                session.trader.halted=True
                event=session.auto_reset_if_exhausted()
                self.assertEqual(event['reference_milestone_usd'],100)
                self.assertEqual(session.trader.cash,85)
                self.assertEqual(event['pnl_usd'],0)
                self.assertEqual(session.trader.strategy_realized_pnl_usd,0)
                self.assertEqual(session.record_bankroll_milestone(),100)
                self.assertEqual(session.report()['rolling_performance']['lifetime']['trades'],0)
            finally:session.close()

    def test_native_reset_uses_last_recorded_price_and_preserves_open_positions(self):
        with tempfile.TemporaryDirectory() as root:
            session=PaperSession(root,0,native_sol=.1,sol_price_usd=100)
            try:
                session.trader.position={'mint':'held'}
                session.trader.halted=True
                self.assertIsNone(session.auto_reset_if_exhausted())
                self.assertEqual(session.record_bankroll_milestone(),10)
                session.trader.position=None
                session.trader.sol_price_usd=200
                event=session.auto_reset_if_exhausted()
                self.assertEqual(event['reset_bankroll_usd'],8.5)
                self.assertEqual(session.trader.cash,0)
                self.assertAlmostEqual(session.trader.native_sol*200,8.5)
            finally:session.close()
