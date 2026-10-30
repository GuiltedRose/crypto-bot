import tempfile
import unittest
from pathlib import Path
from meme_learning import decision_context,outcome_feedback,report
from meme_live import MemeLiveStore
from meme_dip import DipRules
from test_trend_pullback import snapshot


class BotLearningTests(unittest.TestCase):
    def test_context_keeps_inputs_and_risk_settings_without_unrelated_secrets(self):
        row=snapshot();row['private_key']='SECRET';row['candle_history']['candles'][0]['cookie']='SECRET'
        row['sell_volume_usd']=2000
        risk={'passed':True,'status':'bounded_checks_passed','wallet_secret':'SECRET'}
        context=decision_context(row,DipRules(max_position_usd=1.78,take_profit_usd=1,max_loss_usd=.2),
            signal={'action':'buy','reason':'test','metrics':{'pullback_fraction':.1}},risk=risk,entry_profile='current')
        self.assertNotIn('SECRET',str(context));self.assertTrue(context['recorded_before_order'])
        self.assertEqual(context['limits']['max_position_usd'],1.78)
        self.assertEqual(context['entry_profile'],'current')
        self.assertEqual(context['flow_metrics']['buy_sell_ratio'],2.5)
        self.assertEqual(context['signal']['action'],'buy')
        self.assertNotIn('wallet_secret',context['onchain_risk'])
    def test_own_confirmed_orders_form_decision_outcome_pairs_and_failures_remain_costs(self):
        with tempfile.TemporaryDirectory() as root:
            store=MemeLiveStore(root,'user',DipRules())
            ctx={'observed_at':1000,'price_usd':.1,
                 'flow_metrics':{'buy_sell_ratio':1.2,'hourly_turnover_to_liquidity':2,
                                 'max_completed_candle_range_fraction':.08},
                 'onchain_risk':{'status':'bounded_checks_passed'}}
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
            feedback={(row['feature'],row['band'],row['review_status']) for row in data['outcome_feedback']}
            self.assertIn(('buy_sell_ratio','at_least_1.1','insufficient_sample'),feedback)
            self.assertTrue(data['feedback_policy']['live_and_paper_separated'])
    def test_open_position_and_unmatched_sale_are_not_fabricated_wins(self):
        with tempfile.TemporaryDirectory() as root:
            store=MemeLiveStore(root,'user',DipRules())
            store.save('confirmed_sell',{'mint':'old','signature':'unknown','pnl_usd':10})
            store.save('confirmed_buy',{'mint':'open','signature':'entry'});store.close()
            data=report(Path(root)/'meme_live.sqlite')
            self.assertEqual(data['closed_bot_trades'],0)
            self.assertEqual(data['unmatched_receipts'],1)
            self.assertEqual(data['open_or_unfinished_bot_trades'],1)

    def test_negative_cohort_feedback_requires_minimum_support(self):
        context={'flow_metrics':{'buy_sell_ratio':.9,'hourly_turnover_to_liquidity':12,
                                 'max_completed_candle_range_fraction':.3}}
        trades=[{'source':'bot_live','entry_context':context,'net_pnl_usd':-.1} for _ in range(10)]
        feedback=outcome_feedback(trades)
        ratio=next(row for row in feedback if row['feature']=='buy_sell_ratio')
        self.assertEqual(ratio['review_status'],'review_negative_expectancy')
        self.assertEqual(ratio['trades'],10)
        self.assertTrue(all(row['source']=='bot_live' for row in feedback))
