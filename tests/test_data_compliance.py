import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from data_policy import SourceAccessBlocked,require_learning_rights
from provider_limits import ProviderBudget,ProviderBackoff
from paper_sources import PublicSources,SourceCooldown
from pump_narratives import fetch_json
from exit_evaluation import live_eligibility,promotion_failures
from meme_learning import new_weighted_model


class ComplianceTests(unittest.TestCase):
    def test_pump_hosts_blocked_before_transport_including_standalone_scraper(self):
        with patch('paper_sources.build_opener') as transport:
            for host in ('frontend-api-v3.pump.fun','swap-api.pump.fun','advanced-indexer.pump.fun','api.pump.fun'):
                with self.assertRaises(SourceAccessBlocked):PublicSources().fetch('https://'+host+'/test')
            transport.assert_not_called()
        with patch('pump_narratives.build_opener') as transport:
            with self.assertRaises(SourceAccessBlocked):fetch_json('https://frontend-api-v3.pump.fun/coins')
            transport.assert_not_called()
        with self.assertRaises(SourceAccessBlocked):require_learning_rights({'data_source_urls':['https://frontend-api-v3.pump.fun/candles']})
        with self.assertRaises(SourceAccessBlocked):require_learning_rights({'data_source_urls':['https://api.geckoterminal.com/ohlcv']})

    def test_shared_budget_retry_after_dates_and_denials_are_respected(self):
        with tempfile.TemporaryDirectory() as root:
            clock=[1000.]
            def sleep(seconds):clock[0]+=seconds
            first=ProviderBudget(Path(root)/'budget.sqlite',clock=lambda:clock[0],sleep=sleep)
            second=ProviderBudget(first.path,clock=lambda:clock[0],sleep=sleep)
            url='https://api.geckoterminal.com/test'
            first.reserve(url);second.reserve(url)
            self.assertEqual(clock[0],1006.)
            second.failure(url,429,{'Retry-After':'1000'})
            with self.assertRaises(ProviderBackoff):first.reserve(url)
            clock[0]+=1001;first.reserve(url)
            first.failure(url,503,{'Retry-After':'Thu, 01 Jan 1970 00:40:00 GMT'})
            with self.assertRaises(ProviderBackoff):second.reserve(url)
            clock[0]=2500;first.reserve(url)
            first.failure(url,403)
            with self.assertRaises(SourceAccessBlocked):second.reserve(url)

    def test_allowed_gets_are_cached_and_not_reissued(self):
        with tempfile.TemporaryDirectory() as root:
            budget=ProviderBudget(Path(root)/'budget.sqlite',clock=lambda:1000)
            with patch('paper_sources.BUDGET',budget),patch('paper_sources.build_opener') as transport:
                transport.return_value.open.return_value.__enter__.return_value.read.return_value=b'{"value":1}'
                source=PublicSources(interval=0)
                self.assertEqual(source.fetch('https://api.geckoterminal.com/test'),{'value':1})
                self.assertEqual(source.fetch('https://api.geckoterminal.com/test'),{'value':1})
                self.assertEqual(transport.return_value.open.call_count,1)

    def test_ten_consecutive_promotions_gate_ignores_funding_and_restarts(self):
        state={'version':new_weighted_model()['version'],'champion':{'id':'champion'},'history':[]}
        for _ in range(9):state['history'].append({'status':'promoted'})
        state['history']+= [{'status':'paper_balance_reset'},{'status':'state_recovered'}]
        self.assertFalse(live_eligibility(state)['live_eligible'])
        state['history'].append({'status':'promoted'})
        self.assertTrue(live_eligibility(state)['live_eligible'])
        state['history'].append({'status':'rejected'})
        self.assertEqual(live_eligibility(state)['consecutive_promotions'],0)
        self.assertFalse(live_eligibility(state)['live_eligible'])

    def test_rejection_reasons_match_unchanged_requirements(self):
        reasons=promotion_failures({'positions':10,'accuracy':.5,'reference_accuracy':.5,
            'brier':.27,'reference_brier':.5,'changed_positions':0,'neural_net_usd':-1,
            'improvement_lower_usd':0,'neural_drawdown_usd':1,'reference_drawdown_usd':1})
        self.assertEqual(reasons,['nonpositive_neural_net','fewer_than_three_changed_exits','no_positive_improvement_lower_bound'])

    def test_wallet_rpc_honors_429_without_retrying_transport(self):
        from wallet_watch import PublicRpc
        with tempfile.TemporaryDirectory() as root:
            budget=ProviderBudget(Path(root)/'budget.sqlite',clock=lambda:1000)
            error=HTTPError('https://api.mainnet-beta.solana.com',429,'limited',{'Retry-After':'120'},None)
            with patch('provider_limits.BUDGET',budget),patch('wallet_watch.build_opener') as transport:
                transport.return_value.open.side_effect=error
                rpc=PublicRpc(interval=0)
                with self.assertRaises(HTTPError):rpc.call('getGenesisHash',[])
                with self.assertRaises(ProviderBackoff):rpc.call('getGenesisHash',[])
                self.assertEqual(transport.return_value.open.call_count,1)
            error.close()

    def test_main_rpc_uses_the_same_cooldown_without_automatic_retry(self):
        from live import Rpc
        with tempfile.TemporaryDirectory() as root:
            budget=ProviderBudget(Path(root)/'budget.sqlite',clock=lambda:1000)
            error=HTTPError('https://api.mainnet-beta.solana.com',429,'limited',{'Retry-After':'120'},None)
            with patch('provider_limits.BUDGET',budget),patch('live.urllib.request.build_opener') as transport:
                transport.return_value.open.side_effect=error
                rpc=Rpc('https://api.mainnet-beta.solana.com')
                with self.assertRaises(HTTPError):rpc.call('getGenesisHash',[])
                with self.assertRaises(ProviderBackoff):rpc.call('getGenesisHash',[])
                self.assertEqual(transport.return_value.open.call_count,1)
            error.close()

    def test_rights_block_preserves_challenger_without_manufacturing_failure(self):
        from exit_evaluation import update
        from evaluation_state import load
        from test_exit_evaluation import dataset,model
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'evaluation.json';groups,closed=dataset(12)
            with patch('exit_evaluation.load_groups',return_value=groups),patch('exit_evaluation.baselines',return_value=closed),patch('exit_evaluation.train_groups',return_value=model()):
                update([],path);before=update([],path)
                saved,_=load(path);frozen=json.dumps(saved['candidate'],sort_keys=True)
                groups[next(iter(groups))][0][1]['data_source_urls']=['https://frontend-api-v3.pump.fun/candles']
                after=update([],path);state,_=load(path)
                self.assertEqual(after['evaluation']['status'],'paused')
                self.assertEqual(json.dumps(state['candidate'],sort_keys=True),frozen)
                self.assertEqual(before['evaluation']['oos'],after['evaluation']['oos'])
                self.assertFalse(any(event['status']=='rejected' for event in state['history']))
