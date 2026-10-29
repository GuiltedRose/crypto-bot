import copy
import unittest
from dataclasses import replace
from meme_dip import DipRules,PaperDipTrader
from trend_pullback import assess_trend_pullback
import test_meme_dip as fixtures


def snapshot(prices=(.50,.52,.54,.56,.58,.60,.62,.64,.63,.61),now=1200):
    fixture=fixtures.DipTests();fixture.setUp()
    row=copy.deepcopy(fixture.snapshot)
    end=int(now)//60*60
    row.update(observed_at=now,price_usd=prices[-1],liquidity_usd=1000)
    row['candle_history'].update(retrieved_at=now,candles=[
        {'timestamp':end-(len(prices)-i)*60,'open':value*1.004,'high':value*1.005,
         'low':value*.995,'close':value,'volume_usd':100} for i,value in enumerate(prices)])
    row['research_context']={'callouts':[{'id':'post','mint':'Mint','author_wallet':'author',
                               'published_at':now-60,'observed_at':now,'views':100,'watched_author':False}],
                             'wallet_signals':[]}
    return row


class TrendPullbackTests(unittest.TestCase):
    def setUp(self):
        self.rules=DipRules(entry_style='trend_pullback',evidence_mode='market_only')

    def test_established_rise_popularity_and_first_decline_qualify_without_ath_or_rebound(self):
        row=snapshot()
        row.pop('ath_price_usd')
        result=assess_trend_pullback(row,1200,self.rules)
        self.assertEqual(result['action'],'buy',result)
        trader=PaperDipTrader(6.5,self.rules)
        result=trader.on_snapshot(row)
        self.assertEqual(result['action'],'paper_buy',result)
        self.assertEqual(result['pullback_measure'],'recent_price_peak')
        self.assertFalse(result['safety_qualified'])
        self.assertLessEqual(result['budget_usd'],.065)

    def test_pure_downtrend_or_no_social_interest_does_not_qualify(self):
        for prices in ((.64,.62,.60,.58,.56,.54,.52,.50,.48,.46),):
            self.assertNotEqual(assess_trend_pullback(snapshot(prices),1200,self.rules)['action'],'buy')
        row=snapshot();row['research_context']={}
        self.assertEqual(assess_trend_pullback(row,1200,self.rules)['action'],'reject')

    def test_no_chasing_peak_or_catching_deep_extended_dump(self):
        for prices in ((.50,.52,.54,.56,.58,.60,.62,.64,.65,.66),
                       (.50,.52,.54,.56,.58,.60,.62,.64,.60,.50),
                       (.50,.52,.54,.56,.58,.64,.63,.62,.61,.60)):
            self.assertNotEqual(assess_trend_pullback(snapshot(prices),1200,self.rules)['action'],'buy')

    def test_stale_duplicate_or_foreign_callouts_do_not_inflate_interest(self):
        row=snapshot()
        row['research_context']['callouts'][0]['published_at']=0
        self.assertEqual(assess_trend_pullback(row,1200,self.rules)['action'],'reject')
        row=snapshot();callout=row['research_context']['callouts'][0];callout['views']=60
        row['research_context']['callouts'].append(copy.deepcopy(callout))
        self.assertEqual(assess_trend_pullback(row,1200,self.rules)['action'],'reject')
        row=snapshot();row['research_context']['callouts'][0]['mint']='Other'
        self.assertEqual(assess_trend_pullback(row,1200,self.rules)['action'],'reject')

    def test_costs_remain_a_real_blocker(self):
        row=snapshot();row['fixed_cost_per_side_usd']=1
        self.assertEqual(PaperDipTrader(6.5,self.rules).on_snapshot(row)['reason'],'paper allocation cannot cover modeled costs')

    def test_time_exit_and_same_candle_reentry_guard(self):
        trader=PaperDipTrader(6.5,replace(self.rules,max_holding_seconds=30))
        row=snapshot()
        self.assertEqual(trader.on_snapshot(row)['action'],'paper_buy')
        row['observed_at']=1231
        result=trader.on_snapshot(row)
        self.assertEqual(result['action'],'paper_sell')
        self.assertEqual(result['reason'],'time limit')
        row['observed_at']=1232
        self.assertEqual(trader.on_snapshot(row)['reason'],'already entered on this completed candle')


if __name__=='__main__':unittest.main()

class ShortLaunchTests(unittest.TestCase):
    def setUp(self):
        self.rules=DipRules(entry_style='trend_pullback',evidence_mode='market_only',trend_min_bars=3)
    def test_three_completed_candles_can_show_runup_then_controlled_decline(self):
        row=snapshot((.50,.54,.52))
        result=assess_trend_pullback(row,1200,self.rules)
        self.assertEqual(result['action'],'buy',result)
        self.assertEqual(result['metrics']['completed_bars'],3)
        self.assertEqual(result['metrics']['runup_bars'],2)
        self.assertEqual(result['metrics']['evidence_window'],'short_launch')
    def test_two_bars_and_three_bar_downtrend_do_not_establish_runup_and_pullback(self):
        result=assess_trend_pullback(snapshot((.55,.53)),1200,self.rules)
        self.assertIn('only 2 completed 1m candles available; need 3',result['reasons'])
        self.assertNotEqual(assess_trend_pullback(snapshot((.55,.53,.50)),1200,self.rules)['action'],'buy')
    def test_short_mode_retains_social_candle_and_dip_checks(self):
        for row in [snapshot((.50,.55,.40)),snapshot((.50,.55,.56))]:
            self.assertNotEqual(assess_trend_pullback(row,1200,self.rules)['action'],'buy')
        row=snapshot((.50,.54,.52));row['research_context']={}
        self.assertEqual(assess_trend_pullback(row,1200,self.rules)['action'],'reject')
    def test_longer_history_is_used_instead_of_truncated_to_three_bars(self):
        result=assess_trend_pullback(snapshot(),1200,self.rules)
        self.assertEqual(result['metrics']['runup_bars'],6)
        self.assertEqual(result['metrics']['completed_bars'],10)
