import unittest
from dataclasses import replace
from meme_dip import DipRules, PaperDipTrader
from test_paper_accounting import funded_snapshot

class PercentageLimitsTests(unittest.TestCase):
    def rules(self):
        return DipRules(entry_style='trend_pullback',evidence_mode='market_only',
                        position_fraction=.274,take_profit_fraction=.562,max_loss_fraction=.112)

    def test_scaling_and_legacy_fallback(self):
        r=self.rules()
        self.assertAlmostEqual(r.position_cap(6.5),1.781)
        self.assertAlmostEqual(r.position_cap(13),3.562)
        tp,sl=r.exit_limits(1.781)
        self.assertAlmostEqual(tp,1.000922)
        self.assertAlmostEqual(sl,.199472)
        r=DipRules(max_position_usd=1.78,take_profit_usd=1,max_loss_usd=.2)
        self.assertEqual(r.position_cap(100),1.78)
        self.assertEqual(r.exit_limits(10),(1,.2))

    def test_paper_freezes_net_exits_at_entry(self):
        trader=PaperDipTrader(0,self.rules(),.065,100)
        row=funded_snapshot();row['liquidity_usd']=10000
        bought=trader.on_snapshot(row)
        self.assertEqual(bought['action'],'paper_buy',bought)
        position=trader.position
        self.assertAlmostEqual(position['take_profit_usd'],position['cash_spent']*.562)
        self.assertAlmostEqual(position['max_loss_usd'],position['cash_spent']*.112)
        target=position['target']
        trader.rules=replace(trader.rules,take_profit_fraction=.9,max_loss_fraction=.01)
        self.assertEqual(trader.on_snapshot(funded_snapshot(1210,target*.999))['action'],'hold')
        sold=trader.on_snapshot(funded_snapshot(1220,target*1.001))
        self.assertEqual(sold['action'],'paper_sell',sold)
        self.assertGreaterEqual(sold['pnl_usd'],position['take_profit_usd'])

    def test_invalid_fractions(self):
        for values in ({'position_fraction':1.1},{'max_loss_fraction':1},{'take_profit_fraction':-.1}):
            with self.assertRaises(ValueError): DipRules(**values)
