import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from trade_metrics import rolling_performance,format_rolling
from paper_console import format_report
from meme_console import format_status


class RollingPerformanceTests(unittest.TestCase):
    def test_latest_twenty_excludes_open_invalid_and_duplicate_outcomes(self):
        trades=[{'entry_at':i,'exit_at':i+1,'exit_signature':str(i),
                 'net_pnl_usd':1 if i%2==0 else -2,'run_id':str(i//5)} for i in range(25)]
        trades+=[dict(trades[-1]),dict(trades[-1],exit_at=1000,exit_signature='invalid',
                                    net_pnl_usd=999,training_eligible=False)]
        with tempfile.NamedTemporaryFile() as path,patch('trade_metrics.paper_outcomes',return_value=(trades,[{'net_pnl_usd':999}],[],0)):
            metrics=rolling_performance(path.name,'bot_paper')
        self.assertEqual((metrics['trades'],metrics['wins'],metrics['losses']),(20,10,10))
        self.assertEqual(metrics['net_pnl_usd'],-10)
        self.assertEqual(metrics['win_rate'],.5)

    def test_live_includes_failed_fees_without_counting_them_as_closed_trades(self):
        trades=[{'entry_at':100,'exit_at':200,'exit_signature':'a','net_pnl_usd':.3},
                {'entry_at':210,'exit_at':300,'exit_signature':'b','net_pnl_usd':-.1}]
        failures=[{'observed_at':50,'network_cost_usd':9}, {'observed_at':150,'network_cost_usd':.05}]
        with tempfile.NamedTemporaryFile() as path,patch('trade_metrics.live_outcomes',return_value=(trades,[],failures,0)):
            metrics=rolling_performance(path.name,'bot_live')
        self.assertEqual(metrics['trades'],2)
        self.assertAlmostEqual(metrics['net_pnl_usd'],.15)
        self.assertEqual(metrics['failed_order_cost_usd'],.05)

    def test_partial_empty_windows_and_both_consoles(self):
        with tempfile.TemporaryDirectory() as root:
            metrics=rolling_performance(Path(root)/'missing.sqlite','bot_paper')
        self.assertIsNone(metrics['win_rate'])
        self.assertIn('0/20 closed',format_rolling(metrics))
        paper=format_report({'marked_equity_usd':6,'rolling_performance':metrics})
        live=format_status({'timestamp':0,'mode':'live','status':'holding','rolling_performance':metrics})
        for text in (paper,live):
            self.assertIn('LAST 20',text)
            self.assertIn('win n/a',text)


    def test_real_paper_ledger_keeps_closed_losses_across_reset_but_ignores_refill(self):
        import sqlite3,json
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'paper.sqlite';db=sqlite3.connect(path)
            db.executescript('CREATE TABLE paper_runs(id TEXT,archived REAL,created REAL); CREATE TABLE paper_events(sequence INTEGER,run_id TEXT,observed REAL,event TEXT,snapshot TEXT);')
            db.executemany('INSERT INTO paper_runs VALUES(?,?,?)',[('old',3,0),('new',None,3)])
            events=[('old',{'action':'paper_buy','mint':'a'}),('old',{'action':'paper_sell','mint':'a','pnl_usd':-.2}),
                    ('new',{'action':'paper_balance_reset','pnl_usd':1000}),
                    ('new',{'action':'paper_buy','mint':'b'}),('new',{'action':'paper_sell','mint':'b','pnl_usd':.5})]
            for i,(run,event) in enumerate(events):
                db.execute('INSERT INTO paper_events VALUES(?,?,?,?,?)',(i,run,i,json.dumps(event),'{}'))
            db.commit();db.close()
            metrics=rolling_performance(path,'bot_paper')
        self.assertEqual(metrics['trades'],2)
        self.assertAlmostEqual(metrics['net_pnl_usd'],.3)
