import tempfile
import unittest
from dataclasses import replace
from drawdown_schedule import drawdown_state
from meme_dip import DipRules,PaperDipTrader
from meme_live import MemeLiveStore
from meme_bot_risk import mark_bot_risk
from paper_runtime import encode_state,decode_state


class PromotionScheduleTests(unittest.TestCase):
    def test_boundary_promotes_at_exactly_one_thousand_and_demotes_strictly_below(self):
        for pnl,tier,fraction in [(993.49,'base',.15),(993.5,'promoted',.30),(993.51,'promoted',.30)]:
            r=drawdown_state(6.5,pnl,pnl,.15,1000,.30)
            self.assertEqual(r['tier'],tier);self.assertEqual(r['active_drawdown_fraction'],fraction)
        self.assertAlmostEqual(drawdown_state(6.5,993.5,993.5,.15,1000,.30)['limit_usd'],300)
        self.assertAlmostEqual(drawdown_state(6.5,993.49,993.5,.15,1000,.30)['limit_usd'],150)
    def test_promotion_is_reversible_and_uses_current_not_peak_bankroll(self):
        a=drawdown_state(1000,100,100,.15,1000,.30)
        b=drawdown_state(1000,-10,100,.15,1000,.30)
        c=drawdown_state(1000,0,100,.15,1000,.30)
        self.assertEqual([a['tier'],b['tier'],c['tier']],['promoted','base','promoted'])
        self.assertAlmostEqual(b['limit_usd'],165)
    def test_live_and_paper_share_schedule_and_manual_wallet_changes_are_ignored(self):
        rules=DipRules(entry_style='volume_candles',drawdown_halt=.15,drawdown_promotion_bankroll_usd=1000,promoted_drawdown_halt=.30)
        paper=PaperDipTrader(1000,rules)
        with tempfile.TemporaryDirectory() as root:
            store=MemeLiveStore(root,'user',rules)
            try:
                for pnl,wallet in [(0,1000),(100,5000),(-10,10),(0,9999)]:
                    store.state['realized_pnl_usd']=pnl
                    live=mark_bot_risk(store,wallet,.15,promotion_at=1000,promoted_fraction=.30)
                    paper.strategy_realized_pnl_usd=pnl;paper.mark_risk(wallet)
                    self.assertEqual(live['active_drawdown_fraction'],paper.strategy_risk['active_drawdown_fraction'])
                    self.assertAlmostEqual(live['limit_usd'],paper.strategy_risk['limit_usd'])
                    self.assertAlmostEqual(live['bot_bankroll_usd'],paper.strategy_risk['bot_bankroll_usd'])
            finally: store.close()
    def test_checkpoint_keeps_tier_and_pnl_without_clearing_actual_halt(self):
        rules=DipRules(entry_style='volume_candles',drawdown_halt=.15,drawdown_promotion_bankroll_usd=1000)
        paper=PaperDipTrader(1000,rules);paper.strategy_realized_pnl_usd=100;paper.mark_risk(1100)
        restored=decode_state(encode_state(paper))
        self.assertEqual(restored.strategy_risk['tier'],'promoted')
        restored.strategy_realized_pnl_usd=-200;restored.mark_risk(800)
        self.assertTrue(restored.halted)
        restored.strategy_realized_pnl_usd=200;restored.mark_risk(1200)
        self.assertTrue(restored.halted)
    def test_invalid_promoted_fraction_cannot_override_validation(self):
        with self.assertRaises(ValueError): DipRules(drawdown_halt=.15,drawdown_promotion_bankroll_usd=1000,promoted_drawdown_halt=.1)
        with self.assertRaises(ValueError): DipRules(promoted_drawdown_halt=1.)

class TotalDemotionScheduleTests(unittest.TestCase):
    def test_total_bankroll_sizes_limit_and_withdrawal_demotes_without_new_bot_loss(self):
        promoted=drawdown_state(6.5,993.5,993.5,.15,1000,.30,total_bankroll=1000)
        self.assertEqual(promoted['tier'],'promoted');self.assertEqual(promoted['limit_usd'],300)
        withdrawn=drawdown_state(6.5,993.5,993.5,.15,1000,.30,total_bankroll=800,previous_tier='promoted')
        self.assertEqual(withdrawn['tier'],'base');self.assertEqual(withdrawn['limit_usd'],120)
        self.assertEqual(withdrawn['drawdown_usd'],0)
        repeat=drawdown_state(6.5,993.5,993.5,.15,1000,.30,total_bankroll=800,previous_tier='base')
        self.assertEqual(repeat['tier'],'base')
    def test_promoted_tier_demotes_only_when_total_falls_strictly_below_threshold(self):
        for total,tier in [(1000,'promoted'),(999.99,'base')]:
            r=drawdown_state(1000,-10,0,.15,1000,.30,total_bankroll=total,previous_tier='promoted')
            self.assertEqual(r['tier'],tier)
    def test_deposit_alone_cannot_create_initial_bot_promotion(self):
        r=drawdown_state(6.5,0,0,.15,1000,.30,total_bankroll=2000,previous_tier='base')
        self.assertEqual(r['tier'],'base')
    def test_incomplete_valuation_does_not_invent_promotion_or_demotion(self):
        for previous,expected in [('base','base'),('promoted','promoted')]:
            r=drawdown_state(1000,0,0,.15,1000,.30,total_bankroll=800,previous_tier=previous,total_complete=False)
            self.assertEqual(r['tier'],expected)
