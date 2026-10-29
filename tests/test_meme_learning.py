import tempfile
import unittest
from pathlib import Path
from meme_learning import decision_context,report
from meme_live import MemeLiveStore
from meme_dip import DipRules
from test_trend_pullback import snapshot


class BotLearningTests(unittest.TestCase):
    def test_context_keeps_inputs_and_risk_settings_without_unrelated_secrets(self):
        row=snapshot();row['private_key']='SECRET';row['candle_history']['candles'][0]['cookie']='SECRET'
        context=decision_context(row,DipRules(max_position_usd=1.78,take_profit_usd=1,max_loss_usd=.2))
        self.assertNotIn('SECRET',str(context));self.assertTrue(context['recorded_before_order'])
        self.assertEqual(context['limits']['max_position_usd'],1.78)
    def test_own_confirmed_orders_form_decision_outcome_pairs_and_failures_remain_costs(self):
        with tempfile.TemporaryDirectory() as root:
            store=MemeLiveStore(root,'user',DipRules())
            ctx={'observed_at':1000,'price_usd':.1}
            store.save('reserved',{'id':'buy','side':'buy','mint':'coin','entry_profile':'aggressive','entry_context':ctx})
            store.save('confirmed_buy',{'mint':'coin','signature':'entry','fee_lamports':5000})
            store.save('reserved',{'id':'sell','side':'sell','mint':'coin','entry_profile':'aggressive','reason':'target'})
            store.save('confirmed_sell',{'mint':'coin','signature':'exit','pnl_usd':1.,'fee_lamports':5000})
            store.save('transaction_failed',{'id':'failure','network_cost_usd':.01});store.close()
            data=report(Path(root)/'meme_live.sqlite')
            self.assertEqual(data['closed_bot_trades'],1)
            self.assertEqual(data['closed_trades'][0]['entry_context'],ctx)
            self.assertEqual(data['closed_trades'][0]['label_kind'],'confirmed_bot_receipt_pnl')
            self.assertAlmostEqual(data['live_realized_pnl_including_failed_orders_usd'],.99)
            self.assertFalse(data['automatic_live_changes'])
    def test_open_position_and_unmatched_sale_are_not_fabricated_wins(self):
        with tempfile.TemporaryDirectory() as root:
            store=MemeLiveStore(root,'user',DipRules())
            store.save('confirmed_sell',{'mint':'old','signature':'unknown','pnl_usd':10})
            store.save('confirmed_buy',{'mint':'open','signature':'entry'});store.close()
            data=report(Path(root)/'meme_live.sqlite')
            self.assertEqual(data['closed_bot_trades'],0)
            self.assertEqual(data['unmatched_receipts'],1)
            self.assertEqual(data['open_or_unfinished_bot_trades'],1)
