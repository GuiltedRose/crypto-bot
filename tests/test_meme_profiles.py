from dataclasses import replace,asdict
import unittest
from meme_dip import DipRules,PaperDipTrader
from meme_profiles import assess_profiles,profiles,ALLOWED
from test_paper_compare import observation
from test_paper_accounting import funded_snapshot
from test_meme_live import FakeAdapter
from meme_live import MemeLiveStore,MemeLiveEngine
import tempfile


class ProjectProfileTests(unittest.TestCase):
    def setUp(self):
        self.rules=DipRules(entry_style='trend_pullback',entry_profile_mode='adaptive',evidence_mode='market_only',
            trend_min_bars=3,max_position_usd=1.78,take_profit_usd=1,max_loss_usd=.2)
    def test_narrowest_profile_is_chosen_for_each_coin_from_current_data(self):
        row=funded_snapshot();row.update(liquidity_usd=10000,sell_volume_usd=1000)
        self.assertEqual(assess_profiles(row,1200,self.rules)['selected_profile'],'current')
        row=observation()
        self.assertEqual(assess_profiles(row,1200,self.rules)['selected_profile'],'moderate')
        row['candle_history']['candles'][0]['low']=.57;row['buy_volume_usd']=1500
        self.assertEqual(assess_profiles(row,1200,self.rules)['selected_profile'],'aggressive')
    def test_all_failed_profiles_do_not_force_a_selection(self):
        row=observation();row['buy_volume_usd']=500
        result=assess_profiles(row,1200,self.rules)
        self.assertIsNone(result['selected_profile']);self.assertEqual(result['action'],'reject')
        self.assertFalse(result['learned_model'])
    def test_selected_profiles_preserve_dollar_risk_and_slippage(self):
        base=asdict(self.rules)
        for profile in profiles(self.rules).values():
            changed={key for key,value in asdict(profile).items() if value!=base[key]}
            self.assertTrue(changed<=ALLOWED|{'entry_profile_mode'})
            self.assertEqual(profile.entry_profile_mode,'fixed')
    def test_adaptive_paper_uses_selected_profile_without_mutating_global_rules(self):
        trader=PaperDipTrader(0,self.rules,.065,100)
        event=trader.on_snapshot(observation())
        self.assertEqual(event['action'],'paper_buy',event)
        self.assertEqual(event['entry_profile'],'moderate')
        self.assertEqual(trader.rules,self.rules)
        self.assertLessEqual(event['budget_usd'],1.78)
    def test_live_and_paper_choose_same_profile_with_live_execution_guards_still_required(self):
        with tempfile.TemporaryDirectory() as root:
            store=MemeLiveStore(root,'user',self.rules)
            try:
                engine=MemeLiveEngine({},'user',store,self.rules,adapter_factory=FakeAdapter)
                row=observation();row.pop('execution');row['market_kind']='bonding_curve'
                result=engine.prepare(row,100,6.5,1200)
                self.assertEqual(result['status'],'unsigned_simulation_passed',result)
                self.assertEqual(result['intent']['entry_profile'],'moderate')
                self.assertIsNone(store.state['position'])
                self.assertEqual(store.db.execute("SELECT COUNT(*) FROM events WHERE kind='entry_profile_selection'").fetchone()[0],1)
            finally: store.close()
    def test_two_candles_cannot_be_made_eligible_by_aggressive_selection(self):
        row=observation();row['candle_history']['candles']=row['candle_history']['candles'][-2:]
        result=assess_profiles(row,1200,self.rules)
        self.assertIsNone(result['selected_profile'])
