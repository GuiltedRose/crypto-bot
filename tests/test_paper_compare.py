import copy
from dataclasses import asdict,replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from meme_dip import DipRules
from meme_profiles import profiles,ALLOWED
from paper_compare import Comparison
from test_paper_accounting import funded_snapshot


def observation(now=1200,price=1.07):
    row=funded_snapshot(now,price)
    row.update(buy_volume_usd=3000,sell_volume_usd=1000,liquidity_usd=10000)
    end=now//60*60
    row['candle_history']['candles']=[
        {'timestamp':end-180,'open':.8,'high':1.02,'low':.70,'close':1.,'volume_usd':100},
        {'timestamp':end-120,'open':1.,'high':1.11,'low':.98,'close':1.10,'volume_usd':100},
        {'timestamp':end-60,'open':1.10,'high':1.11,'low':1.05,'close':1.07,'volume_usd':100}]
    return row


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.directory=Path(self.temp.name)/'trial'
        self.base=DipRules(entry_style='trend_pullback',evidence_mode='market_only',trend_min_bars=3,
                           max_position_usd=1.78,take_profit_usd=1,max_loss_usd=.2)
        self.variants=profiles(self.base)
    def tearDown(self): self.temp.cleanup()
    def new(self): return Comparison(self.directory,self.variants,0,.065,100)
    def test_only_three_entry_tolerances_differ_not_costs_or_dollar_risk(self):
        base=asdict(self.base)
        for rule in self.variants.values():
            self.assertTrue({key for key,value in asdict(rule).items() if value!=base[key]}<=ALLOWED)
        bad=Path(self.temp.name)/'bad.json';bad.write_text(json.dumps({n:{'slippage_per_side':.05} for n in self.variants}))
        with self.assertRaises(ValueError): profiles(self.base,bad)
    def test_identical_stream_different_entries_restart_and_idempotency(self):
        comparison=self.new()
        try:
            row=observation();events=comparison.process(row)
            self.assertEqual(events['current']['action'],'reject',events)
            self.assertEqual(events['moderate']['action'],'paper_buy',events)
            self.assertEqual(events['aggressive']['action'],'paper_buy',events)
            report=comparison.publish()
            self.assertEqual(report['shared_observations'],1)
            self.assertTrue(all(r['observations']==1 for r in report['profiles'].values()))
            self.assertLess(report['profiles']['moderate']['estimated_liquidation_equity_usd'],report['profiles']['moderate']['marked_equity_usd'])
            self.assertIsNone(comparison.process(row))
        finally: comparison.close()
        comparison=Comparison(self.directory)
        try:
            events=comparison.process(observation(1210,.94))
            self.assertEqual(events['moderate']['action'],'paper_sell',events)
            report=comparison.publish()
            self.assertEqual(report['profiles']['moderate']['closed_trades'],1)
            self.assertLess(report['profiles']['moderate']['realized_pnl_usd'],0)
            self.assertEqual(report['profiles']['moderate']['volatility_cohorts']['30-45%']['closed_trades'],1)
            self.assertGreater(report['profiles']['moderate']['modeled_slippage_usd'],0)
            self.assertFalse(report['live_qualified'])
        finally: comparison.close()
    def test_partial_fanout_recovers_without_duplicate_fills(self):
        comparison=self.new()
        with patch.object(comparison.sessions['moderate'],'process',side_effect=RuntimeError('interruption')):
            with self.assertRaises(RuntimeError): comparison.process(observation())
        comparison.close()
        comparison=Comparison(self.directory)
        try:
            self.assertEqual(comparison.db.execute('SELECT COUNT(*) FROM observations WHERE completed=1').fetchone()[0],1)
            for name in ['moderate','aggressive']:
                self.assertEqual(comparison.sessions[name].report()['event_counts']['paper_buy'],1)
            self.assertEqual(comparison.db.execute('SELECT COUNT(*) FROM samples').fetchone()[0],3)
        finally: comparison.close()
    def test_changed_profiles_cannot_relabel_existing_performance(self):
        comparison=self.new();comparison.close()
        altered=dict(self.variants,moderate=replace(self.variants['moderate'],min_buy_volume_usd=2000))
        with self.assertRaises(ValueError): Comparison(self.directory,altered)
    def test_existing_live_or_paper_state_cannot_be_used_as_comparison_root(self):
        self.directory.mkdir();(self.directory/'meme_live.sqlite').touch()
        with self.assertRaises(ValueError): self.new()
    def test_malformed_observation_is_journaled_equally_not_partially_filled(self):
        comparison=self.new()
        try:
            events=comparison.process(None)
            self.assertTrue(all(e['action']=='invalid_snapshot' for e in events.values()))
            self.assertEqual(comparison.publish()['shared_observations'],1)
        finally: comparison.close()

    def test_status_reads_saved_report_while_comparison_writer_is_active(self):
        import io
        from contextlib import redirect_stdout
        from run_paper_compare import main
        comparison=self.new()
        try:
            comparison.publish()
            stream=io.StringIO()
            with patch('sys.argv',['run_paper_compare.py','--state-dir',str(self.directory),'--status']),redirect_stdout(stream):
                main()
            self.assertIn('PAIRED PAPER COMPARISON',stream.getvalue())
        finally: comparison.close()
