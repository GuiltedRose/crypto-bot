import copy
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import patch
from test_volume_candles import row,rules
from test_meme_live import FakeAdapter
from meme_dip import PaperDipTrader
from meme_live import MemeLiveEngine,MemeLiveStore
from volume_candles import assess_volume_candles
from meme_profiles import assess_profiles


def policy():
    return replace(rules(),momentum_reclaim_enabled=True,trend_pullback_max_fraction=.5,
                   min_buy_sell_ratio=1.0)


def setup():
    r=row();bars=r['candle_history']['candles']
    r.update(buy_volume_usd=6000,sell_volume_usd=4000)
    for b,prices,vol in zip(bars,[(1,1.25,.98,1.2),(1.2,1.21,1,1.08),(1.08,1.24,1.02,1.22)],[100,200,300]):
        b.update(zip(('open','high','low','close'),prices));b['volume_usd']=vol
    r['price_usd']=1.22
    return r


class ReclaimTests(unittest.TestCase):
    def test_confirmed_setup_buys_but_downtrend_does_not(self):
        r=setup();r['buy_volume_usd']=6000;r['sell_volume_usd']=6000
        self.assertEqual(assess_volume_candles(r,1200,policy())['action'],'buy')
        self.assertNotEqual(assess_volume_candles(row(),1200,policy())['action'],'buy')
    def test_no_rebound_breakdown_low_volume_and_chasing_wait(self):
        for field,value in [('close',1.15),('low',.99),('volume_usd',100)]:
            r=setup();r['candle_history']['candles'][-1][field]=value
            self.assertEqual(assess_volume_candles(r,1200,policy())['action'],'watch')
        r=setup();r['price_usd']=1.4
        self.assertEqual(assess_volume_candles(r,1200,policy())['action'],'watch')
    def test_missing_candle_and_low_volume_cannot_buy(self):
        r=setup();r['candle_history']['candles'][0]['timestamp']-=60
        self.assertEqual(assess_volume_candles(r,1200,policy())['action'],'watch')
        r=setup();r['buy_volume_usd']=4999
        self.assertEqual(assess_volume_candles(r,1200,policy())['action'],'reject')
    def test_sell_dominance_and_missing_sell_volume_block_reclaim(self):
        for sell in (6100,6500):
            r=setup();r['sell_volume_usd']=sell
            result=assess_volume_candles(r,1200,policy())
            self.assertEqual(result['action'],'watch',result)
            self.assertIn('hourly buy pressure',result['reason'])
        self.assertEqual(assess_volume_candles(setup(),1200,policy())['action'],'buy')
        r=setup();r.pop('sell_volume_usd')
        self.assertEqual(assess_volume_candles(r,1200,policy())['action'],'watch')
    def test_partial_reclaim_volume_can_confirm_but_weak_volume_cannot(self):
        r=setup();r['candle_history']['candles'][-1]['volume_usd']=150
        self.assertEqual(assess_volume_candles(r,1200,policy())['action'],'buy')
        r['candle_history']['candles'][-1]['volume_usd']=149
        self.assertEqual(assess_volume_candles(r,1200,policy())['action'],'watch')
    def test_adaptive_and_paper_share_setup(self):
        p=replace(policy(),entry_profile_mode='adaptive')
        self.assertEqual(assess_profiles(setup(),1200,p)['action'],'buy')
        trader=PaperDipTrader(0,p,.065,100)
        bought=trader.on_snapshot(setup())
        self.assertEqual(bought['action'],'paper_buy',bought)
        self.assertEqual(trader.position['take_profit_usd'],1)
        self.assertEqual(trader.position['max_loss_usd'],.2)
    def test_live_requires_risk_check_and_blocks_flagged_coin(self):
        class Risk:
            def assess(self,*args):return {'passed':False,'status':'flagged','reasons':['concentrated owners']}
        with tempfile.TemporaryDirectory() as root:
            store=MemeLiveStore(root,'user',policy())
            try:
                r=setup();r.pop('execution');r['market_kind']='bonding_curve'
                engine=MemeLiveEngine({},'user',store,policy(),adapter_factory=FakeAdapter)
                self.assertEqual(engine.prepare(r,100,6.5,1200)['status'],'onchain_risk_unavailable')
                engine.risk_assessor=Risk()
                self.assertEqual(engine.prepare(r,100,6.5,1200)['status'],'onchain_risk_rejected')
                engine.risk_assessor=type('GoodRisk',(),{'assess':lambda *args:{'passed':True}})()
                self.assertEqual(engine.prepare(r,100,6.5,1200)['status'],'unsigned_simulation_passed')
            finally:store.close()

    def test_confirmed_negative_context_feedback_vetoes_matching_live_entry(self):
        with tempfile.TemporaryDirectory() as root:
            configured=policy()
            store=MemeLiveStore(root,'user',configured)
            try:
                engine=MemeLiveEngine({},'user',store,configured,adapter_factory=FakeAdapter)
                with patch('meme_learning.adverse_live_cohorts',return_value=[
                    {'feature':'buy_sell_ratio','band':'1.0_to_1.1','trades':10,'mean_net_pnl_usd':-.1}]):
                    candidate=setup();candidate.pop('execution')
                    result=engine.prepare(candidate,100,6.5,1200)
                self.assertEqual(result['status'],'historical_loss_cohort_block')
                self.assertEqual(len(result['feedback']),1)
            finally:
                store.close()

    def test_setup_survives_more_than_three_candles(self):
        r=setup();bars=r['candle_history']['candles']
        bars[-1].update(close=1.15,high=1.18,volume_usd=100)
        bars.append(dict(timestamp=bars[-1]['timestamp']+60,open=1.15,high=1.24,low=1.04,close=1.22,volume_usd=300,final=True))
        r['observed_at']=1260;r['candle_history']['retrieved_at']=1260
        self.assertEqual(assess_volume_candles(r,1260,policy())['action'],'buy')

    def test_percentage_sizing_uses_shared_budget(self):
        p=replace(policy(),position_fraction=.274,max_loss_fraction=.112,take_profit_fraction=.562)
        r=setup();trader=PaperDipTrader(0,p,.065,100)
        bought=trader.on_snapshot(r)
        self.assertEqual(bought['action'],'paper_buy',bought)
        self.assertAlmostEqual(bought['budget_usd'],p.entry_budget(6.5,5))
