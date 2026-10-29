import copy
from dataclasses import replace
import tempfile
import unittest
from meme_dip import DipRules
from meme_scorecard import scorecard,FAMILIES
from meme_live import MemeLiveStore
from meme_results import results
from test_trend_pullback import snapshot


class ScorecardTests(unittest.TestCase):
    def setUp(self):
        self.rules=DipRules(entry_style='trend_pullback',evidence_mode='market_only',max_position_usd=1.78)
        self.row=snapshot();self.row.update(sell_volume_usd=3000,liquidity_usd=10000)
        self.row['research_context']['source_status']='bounded_sample'
    def test_equal_family_weight_and_no_entry_authorization(self):
        result=scorecard(self.row,1200,self.rules)
        self.assertEqual(set(result['groups']),set(FAMILIES))
        self.assertTrue(all(group['weight']==.25 for group in result['groups'].values()))
        self.assertAlmostEqual(result['score'],sum(group['score'] for group in result['groups'].values())/4)
        self.assertFalse(result['automatic_entry_authorization'])
    def test_missing_data_does_not_redistribute_weight_or_invent_full_score(self):
        self.row.pop('sell_volume_usd');result=scorecard(self.row,1200,self.rules)
        self.assertEqual(result['coverage_fraction'],.75);self.assertIsNone(result['score'])
        self.assertIsNone(result['groups']['buying_demand']['score'])
        self.assertLessEqual(result['known_contribution'],.75)
    def test_price_group_does_not_count_social_interest_twice(self):
        a=scorecard(self.row,1200,self.rules)
        self.row['research_context']={'source_status':'bounded_sample'}
        b=scorecard(self.row,1200,self.rules)
        self.assertEqual(a['groups']['price_structure']['score'],b['groups']['price_structure']['score'])
        self.assertEqual(b['groups']['community_interest']['score'],0)
    def test_unavailable_social_source_remains_unknown_not_zero(self):
        self.row['research_context']={'source_status':'stale_wallet_source','callouts':[]}
        self.assertIsNone(scorecard(self.row,1200,self.rules)['groups']['community_interest']['score'])
    def test_stale_observation_cannot_be_fully_scored(self):
        result=scorecard(self.row,1400,self.rules)
        self.assertEqual(result['coverage_fraction'],0);self.assertIsNone(result['score'])
    def test_future_candle_is_not_observed_price_evidence(self):
        self.row['candle_history']['candles'][-1]['timestamp']=1200
        self.assertIsNone(scorecard(self.row,1200,self.rules)['groups']['price_structure']['score'])


class ResultsTests(unittest.TestCase):
    def test_results_include_losses_breakeven_failed_fees_and_open_position_flag(self):
        with tempfile.TemporaryDirectory() as root:
            store=MemeLiveStore(root,'user',DipRules())
            for pnl in [1,-.2,0]: store.save('confirmed_sell',{'pnl_usd':pnl})
            store.save('transaction_failed',{'network_cost_usd':.01})
            store.state.update(realized_pnl_usd=.79,position={'mint':'held'})
            store.save('test',{});store.close()
            report=results(root+'/meme_live.sqlite')
            self.assertEqual(report['closed_trades'],3)
            self.assertEqual(report['win_rate'],1/3)
            self.assertAlmostEqual(report['average_net_trade_usd'],.8/3)
            self.assertEqual(report['realized_pnl_usd'],.79)
            self.assertEqual(report['failed_transaction_cost_usd'],.01)
            self.assertTrue(report['position_open']);self.assertFalse(report['positive_edge_established'])
    def test_no_trades_does_not_invent_win_rate_or_expectancy(self):
        with tempfile.TemporaryDirectory() as root:
            store=MemeLiveStore(root,'user',DipRules());store.close()
            report=results(root+'/meme_live.sqlite')
            self.assertIsNone(report['win_rate']);self.assertIsNone(report['average_net_trade_usd'])
