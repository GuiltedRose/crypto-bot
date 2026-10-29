import copy
import tempfile
import unittest
from pathlib import Path
from dataclasses import replace
from meme_dip import DipRules,PaperDipTrader,SOL_MINT
from paper_runtime import PaperSession
from test_trend_pullback import snapshot


def funded_snapshot(now=1200,price=.61):
    row=snapshot(now=now)
    row['price_usd']=price
    row['quote_mint']=SOL_MINT
    row['execution']={'mint':'Mint','pool':'Pool','observed_at':now,'sol_price_usd':100,
                      'network_fee_sol':.000005,'entry_rent_sol':.00148844,'exit_rent_sol':0,
                      'buy_route_available':True,'sell_route_available':True,'quote_mint':SOL_MINT,
                      'model_only':True,'rent_is_refundable':True,'fee_per_side':.0125}
    return row


class PaperAccountingTests(unittest.TestCase):
    def rules(self,**overrides):
        return DipRules(entry_style='trend_pullback',evidence_mode='market_only',**overrides)

    def test_rent_moves_to_locked_capital_not_a_permanent_fee(self):
        trader=PaperDipTrader(0,self.rules(),.065,100)
        event=trader.on_snapshot(funded_snapshot())
        self.assertEqual(event['action'],'paper_buy',event)
        self.assertAlmostEqual(sum(trader.account_rent.values()),.00148844)
        self.assertAlmostEqual(trader.position['entry_native_cost_usd'],.0005)
        expected=trader.native_sol*100+trader.position['quantity']*.61+.148844
        self.assertAlmostEqual(trader.equity(trader.position['quantity']*.61),expected)
        before=trader.native_sol
        self.assertEqual(trader.reclaim_rent()['returned_sol'],0)  # account is in use
        self.assertEqual(trader.native_sol,before)

    def test_reclaim_returns_principal_without_pnl_and_reentry_does_not_pay_rent_twice(self):
        trader=PaperDipTrader(0,self.rules(max_holding_seconds=30),.065,100)
        self.assertEqual(trader.on_snapshot(funded_snapshot())['action'],'paper_buy')
        row=funded_snapshot(1231)
        self.assertEqual(trader.on_snapshot(row)['action'],'paper_sell')
        equity_before=trader.equity()
        liquid_before=trader.native_sol
        reclaim=trader.reclaim_rent()
        self.assertEqual(reclaim['pnl_usd'],0)
        self.assertAlmostEqual(reclaim['returned_sol'],.00148844)
        self.assertAlmostEqual(trader.native_sol-liquid_before,.00148844)
        self.assertAlmostEqual(trader.equity(),equity_before)
        self.assertEqual(trader.reclaim_rent()['returned_sol'],0)

    def test_existing_simulated_account_avoids_second_rent(self):
        trader=PaperDipTrader(0,self.rules(max_holding_seconds=30),.065,100)
        trader.on_snapshot(funded_snapshot())
        trader.on_snapshot(funded_snapshot(1231))
        row=funded_snapshot(1320)
        before=trader.native_sol
        bought=trader.on_snapshot(row)
        self.assertEqual(bought['action'],'paper_buy',bought)
        self.assertAlmostEqual(before-trader.native_sol,bought['budget_usd']/100+.000005)
        self.assertAlmostEqual(sum(trader.account_rent.values()),.00148844)

    def test_restart_and_reclaim_journal_preserve_equity_without_creating_profit(self):
        with tempfile.TemporaryDirectory() as root:
            session=PaperSession(root,0,self.rules(max_holding_seconds=30),.065,100)
            session.process(funded_snapshot())
            session.process(funded_snapshot(1231))
            before=session.report()
            session.close()
            session=PaperSession(root)
            self.assertAlmostEqual(session.report()['locked_rent_sol'],.00148844)
            event=session.reclaim_rent()
            self.assertAlmostEqual(event['returned_sol'],.00148844)
            after=session.report()
            self.assertAlmostEqual(after['marked_equity_usd'],before['marked_equity_usd'])
            self.assertAlmostEqual(after['realized_pnl_usd'],before['realized_pnl_usd'])
            session.close()

    def test_one_dollar_profit_target_uses_net_proceeds_not_gross_move(self):
        trader=PaperDipTrader(0,self.rules(max_position_usd=1.78,take_profit_usd=1,max_loss_usd=.2),.065,100)
        row=funded_snapshot();row['liquidity_usd']=10000
        entry=trader.on_snapshot(row)
        self.assertEqual(entry['action'],'paper_buy',entry)
        self.assertAlmostEqual(entry['budget_usd'],1.78)
        target=trader.position['target']
        row=funded_snapshot(1210,target*.999)
        self.assertEqual(trader.on_snapshot(row)['action'],'hold')
        row=funded_snapshot(1220,target*1.001)
        sold=trader.on_snapshot(row)
        self.assertEqual(sold['action'],'paper_sell')
        self.assertEqual(sold['reason'],'target')
        self.assertGreaterEqual(sold['pnl_usd'],1)

    def test_twenty_cent_loss_trigger_includes_fees_and_allows_gap_loss(self):
        trader=PaperDipTrader(0,self.rules(max_position_usd=1.78,take_profit_usd=1,max_loss_usd=.2),.065,100)
        row=funded_snapshot();row['liquidity_usd']=10000
        trader.on_snapshot(row)
        stop=trader.position['stop']
        self.assertEqual(trader.on_snapshot(funded_snapshot(1210,stop*1.001))['action'],'hold')
        sold=trader.on_snapshot(funded_snapshot(1220,stop*.99))
        self.assertEqual(sold['action'],'paper_sell')
        self.assertEqual(sold['reason'],'stop')
        self.assertLessEqual(sold['pnl_usd'],-.2)

    def test_report_survives_non_object_snapshot(self):
        with tempfile.TemporaryDirectory() as root:
            session=PaperSession(root,10)
            session.process(None)
            self.assertEqual(session.report()['event_counts'],{'invalid_snapshot':1})
            session.close()


if __name__=='__main__':unittest.main()
