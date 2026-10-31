import copy
import sqlite3
import tempfile
import unittest
from pathlib import Path
from meme_dip import DipRules
from meme_watchlist import CandidateWatchlist,token_analysis
from meme_console import format_status
from solders.pubkey import Pubkey


class WatchlistTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'watch.sqlite';self.db=sqlite3.connect(self.path)
        self.now=1000;self.watch=CandidateWatchlist(self.db,clock=lambda:self.now)
        self.mint=str(Pubkey.new_unique());self.pool=str(Pubkey.new_unique());self.rules=DipRules(max_position_usd=1.78)
        self.row={'mint':self.mint,'pool':self.pool,'symbol':'COIN','observed_at':1000,'buy_volume_usd':6000,
                  'buy_volume_window_seconds':3600,'liquidity_usd':10000,
                  'research_context':{'callouts':[{'id':'post','mint':self.mint,'author_wallet':'author','published_at':990,'observed_at':1000,'views':200}]}}
        self.result={'status':'watch','signal':{'reason':'waiting for an early pullback'},'scorecard':{'score':.8}}
    def tearDown(self): self.db.close();self.temp.cleanup()
    def test_candidate_persists_and_is_due_only_after_revisit_delay(self):
        self.assertTrue(self.watch.observe(self.row,self.result,self.rules)['retained'])
        self.assertEqual(self.watch.due(),[])
        self.db.close();self.db=sqlite3.connect(self.path);self.watch=CandidateWatchlist(self.db,clock=lambda:self.now)
        self.now=1060;self.assertEqual(self.watch.due(),[self.mint])
    def test_repeated_observations_do_not_extend_expiry_forever(self):
        first=self.watch.observe(self.row,self.result,self.rules)
        self.now=1100;self.row['observed_at']=1100
        second=self.watch.observe(self.row,self.result,self.rules)
        self.assertEqual(first['expires_at'],second['expires_at'])
        self.now=2800;self.assertEqual(self.watch.due(),[]);self.assertEqual(self.watch.count(),0)
    def test_confirmed_buy_or_flagged_risk_removes_watched_candidate(self):
        for result in [{'status':'confirmed_buy'},{'status':'onchain_risk_rejected','risk':{'status':'flagged'}}]:
            self.watch.observe(self.row,self.result,self.rules)
            self.assertFalse(self.watch.observe(self.row,result,self.rules)['retained'])
            self.assertEqual(self.watch.count(),0)
    def test_promising_temporary_rejection_can_be_revisited_without_entry_authorization(self):
        result={'status':'entry_rejected','reasons':['extreme candle range']}
        self.assertTrue(self.watch.observe(self.row,result,self.rules)['retained'])
        self.now=1060;self.assertEqual(self.watch.due(),[self.mint])
        self.watch.failed(self.mint);self.assertEqual(self.watch.due(),[])
    def test_capacity_is_bounded(self):
        self.watch.capacity=2
        for _ in range(4):
            self.row['mint']=str(Pubkey.new_unique());self.watch.observe(self.row,self.result,self.rules)
        self.assertEqual(self.watch.count(),2)
    def test_analysis_does_not_invent_increasing_volume_or_passed_risk(self):
        report=token_analysis(self.row,self.result,self.rules,1000)
        self.assertNotIn('Increasing',report['buy_volume'])
        self.assertEqual(report['onchain_risk'],'Not checked yet')
        self.assertEqual(report['fresh_callouts'],1);self.assertEqual(report['fresh_authors'],1)
        text=format_status(dict(report,timestamp=1000,mode='live'))
        self.assertIn('TOKEN ANALYSIS: COIN',text);self.assertIn('WATCH',text)
    def test_provider_symbol_cannot_inject_terminal_control_sequences(self):
        self.row['symbol']='COIN\nFAKE\x1b[2J'
        report=token_analysis(self.row,self.result,self.rules,1000)
        text=format_status(dict(report,timestamp=1000,mode='live'))
        self.assertNotIn('\x1b',text);self.assertNotIn('COIN\nFAKE',text)

    def test_retained_coin_is_revisited_even_after_it_leaves_incoming_feeds(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        from run_meme_live import tick
        self.watch.observe(self.row,self.result,self.rules)
        observed=[];test=self
        class Sources:
            def observation(self,mint):
                observed.append(mint);return dict(test.row,mint=mint,observed_at=1060)
            def discover(self): return []
        class Feed:
            def poll(self,wallets): return {}
            def read_context(self,wallets): return {'status':'bounded_sample','callouts':[],'signals':[]}
        state={'position':None,'pending':None,'halted':False,'realized_pnl_usd':0}
        engine=SimpleNamespace(store=SimpleNamespace(db=self.db,state=state,save=lambda *args:None,refresh_learning_model=Mock()),
                               rules=self.rules,execute=False,rpc=None,user="user",mark=lambda equity:None,
                               loss_streak_cooldown=lambda now:None,
                               prepare=lambda *args:copy.deepcopy(self.result))
        incoming=[str(Pubkey.new_unique()) for _ in range(15)]
        with patch('run_meme_live.valuation',return_value=100),patch('run_meme_live.wallet_equity',return_value=6.5),patch('run_meme_live.rank_candidate_mints',return_value=incoming),patch('meme_watchlist.time.time',return_value=1060):
            result=tick(engine,Sources(),Feed(),['wallet'],[],False)
        engine.store.refresh_learning_model.assert_called_once_with()
        self.assertEqual(observed[0],self.mint)
        self.assertEqual(len(set(observed)),10)
        self.assertGreater(result['watched_candidates'],0)

    def test_minimal_policy_drops_expired_volume_opportunity(self):
        from dataclasses import replace
        rules=replace(self.rules,entry_style='volume_candles')
        self.assertTrue(self.watch.observe(self.row,self.result,rules)['retained'])
        self.row['buy_volume_usd']=4999
        self.assertFalse(self.watch.observe(self.row,{'status':'entry_rejected'},rules)['retained'])
        self.assertEqual(self.watch.count(),0)
