import unittest
from paper_console import format_report


class PaperConsoleTests(unittest.TestCase):
    def test_summary_trades_and_blockers_exclude_raw_wallet_data(self):
        report={'marked_equity_usd':6.34,'realized_pnl_usd':-.2,'positions':[{}],'max_positions':4,
                'neural_training':{'samples':71,'wins':8,'losses':63},
                'source_status':{'status':'ok','wallet_watch':{'raw':'NOISY WALLET DUMP'}}}
        events=[{'action':'paper_buy','mint':'coin','budget_usd':1.78},
                {'action':'paper_sell','mint':'other','pnl_usd':.25,'reason':'target'},
                {'action':'reject','reasons':['low volume']}]
        text=format_report(report,events)
        for part in ('BUY coin','SELL other','$+0.25','open 1/4','71 samples','low volume'):
            self.assertIn(part,text)
        self.assertNotIn('NOISY',text)
    def test_cooldown_and_source_problems_remain_visible(self):
        report={'marked_equity_usd':5,'published_at':100,'loss_cooldown_until':160,
                'drawdown_halted':True,'source_status':{'sources':{'rpc':{}},'learning_error':'ValueError'}}
        text=format_report(report)
        for part in ('HALTED','cooldown 60s','1 source errors','training unavailable'):
            self.assertIn(part,text)

    def test_training_breakdown_and_exit_progress_are_readable(self):
        report={'marked_equity_usd':6,'neural_training':{'samples':71,'wins':8,'losses':63,
            'source_samples':{'bot_paper':68,'bot_live':3},
            'exit_model':{'samples':12,'wins':4,'losses':8,'trades':2}}}
        text=format_report(report)
        for part in ('NN | 71 samples','8 positive / 63 negative','paper 68 · live 3 · wallets 0',
                     'EXIT NN | 12/30 quote pairs','2/5 positions','collecting data'):
            self.assertIn(part,text)
        report['source_status']={'learning_error':'ValueError'}
        self.assertIn('NN cached',format_report(report))

    def test_position_grouped_evaluation_and_rollback_are_visible(self):
        report={'marked_equity_usd':6,'neural_training':{'samples':100,'wins':30,'losses':70,
            'exit_model':{'samples':50,'wins':20,'losses':30,'trades':8,
                'evaluation':{'status':'champion','approved':True,'rollbacks':1,
                    'oos':{'positions':10,'accuracy':.8,'brier':.12,'neural_vs_rule_usd':.5}}}}}
        text=format_report(report)
        for value in ('EXIT OOS challenger','10 positions','accuracy 80.0%','Brier 0.120',
                      'net vs rules $+0.50','rollbacks 1','quote replay'):
            self.assertIn(value,text)

    def test_progressive_reset_prints_target_and_confirmed_reference(self):
        text=format_report({'marked_equity_usd':85},[{'action':'paper_balance_reset',
            'reset_bankroll_usd':85,'reference_milestone_usd':100}])
        self.assertIn('virtual bankroll $85.00',text)
        self.assertIn('milestone $100.00',text)
        self.assertIn('not P&L',text)
