import copy
import tempfile
import unittest
from dataclasses import replace
from test_volume_candles import row,rules
from test_meme_live import FakeAdapter
from meme_dip import PaperDipTrader
from meme_live import MemeLiveEngine,MemeLiveStore
from volume_candles import assess_volume_candles
from meme_profiles import assess_profiles


def policy():
    return replace(rules(),momentum_reclaim_enabled=True,trend_pullback_max_fraction=.5)


def setup():
    r=row();bars=r['candle_history']['candles']
    for b,prices,vol in zip(bars,[(1,1.25,.98,1.2),(1.2,1.21,1,1.08),(1.08,1.24,1.02,1.22)],[100,200,300]):
        b.update(zip(('open','high','low','close'),prices));b['volume_usd']=vol
    r['price_usd']=1.22
    return r


class ReclaimTests(unittest.TestCase):
    def test_confirmed_setup_buys_but_downtrend_does_not(self):
        self.assertEqual(assess_volume_candles(setup(),1200,policy())['action'],'buy')
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
