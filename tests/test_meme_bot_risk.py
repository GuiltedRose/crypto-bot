import tempfile
import unittest
from meme_dip import DipRules
from meme_live import MemeLiveStore
from meme_bot_risk import mark_bot_risk


class BotRiskTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=MemeLiveStore(self.temp.name,'user',DipRules())
    def tearDown(self): self.store.close();self.temp.cleanup()
    def test_manual_wallet_activity_and_unallocated_sol_changes_do_not_halt(self):
        for value in [6.5,7.2,5.3,1.,9.]: mark_bot_risk(self.store,value,.05)
        self.assertFalse(self.store.state['halted'])
        self.assertEqual(self.store.state['strategy_risk']['drawdown_usd'],0)
        self.assertEqual(self.store.state['strategy_risk']['capital_usd'],6.5)
    def test_bot_realized_loss_halts_independently_of_deposits(self):
        mark_bot_risk(self.store,6.5,.05)
        self.store.state['realized_pnl_usd']=-.4
        mark_bot_risk(self.store,100,.05)
        self.assertTrue(self.store.state['halted'])
        self.assertAlmostEqual(self.store.state['strategy_risk']['limit_usd'],.325)
    def test_bot_open_position_drawdown_includes_its_net_liquidation_pnl(self):
        mark_bot_risk(self.store,6.5,.05)
        self.store.state['position']={'mint':'bot','units':100}
        mark_bot_risk(self.store,50,.05,.2)
        mark_bot_risk(self.store,100,.05,-.2)
        self.assertTrue(self.store.state['halted'])
        self.assertAlmostEqual(self.store.state['strategy_risk']['drawdown_usd'],.4)
    def test_proven_wallet_only_legacy_halt_is_migrated_on_next_user_run(self):
        self.store.state.update(halted=True,peak_equity=7.2)
        self.store.save('portfolio_mark',{'equity_usd':6.5,'halted':False})
        self.store.save('portfolio_mark',{'equity_usd':5.3,'halted':True})
        mark_bot_risk(self.store,6.5,.05)
        self.assertFalse(self.store.state['halted'])
        self.assertEqual(self.store.state['peak_equity'],7.2) # Historical record is kept.
    def test_legacy_halt_with_actual_bot_activity_is_preserved(self):
        self.store.state.update(halted=True,realized_pnl_usd=-.2)
        mark_bot_risk(self.store,6.5,.05)
        self.assertTrue(self.store.state['halted'])
        self.assertTrue(self.store.state['strategy_risk']['legacy_halt_preserved'])
    def test_existing_bot_halt_is_not_reset_on_restart(self):
        mark_bot_risk(self.store,6.5,.05)
        self.store.state['realized_pnl_usd']=-.4;mark_bot_risk(self.store,6.1,.05)
        self.store.close();self.store=MemeLiveStore(self.temp.name,'user',DipRules())
        mark_bot_risk(self.store,100,.05)
        self.assertTrue(self.store.state['halted'])

    def test_fifteen_percent_limit_allows_point_four_but_halts_above_point_nine_seven_five(self):
        mark_bot_risk(self.store,6.5,.15)
        self.store.state['realized_pnl_usd']=-.4
        mark_bot_risk(self.store,6.1,.15)
        self.assertFalse(self.store.state['halted'])
        self.assertAlmostEqual(self.store.state['strategy_risk']['limit_usd'],.975)
        self.store.state['realized_pnl_usd']=-.98
        mark_bot_risk(self.store,100,.15)
        self.assertTrue(self.store.state['halted'])
