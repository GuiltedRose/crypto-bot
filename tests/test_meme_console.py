import unittest
from dataclasses import replace
from meme_console import format_status
from meme_dip import DipRules
from trend_pullback import assess_trend_pullback
from candle_quality import CandleQualityPolicy
from test_trend_pullback import snapshot


class ConsoleDiagnosticsTests(unittest.TestCase):
    def test_rejection_explains_configured_limits_without_json_noise(self):
        line=format_status({'timestamp':0,'mode':'live','status':'entry_rejected','mint':'A'*32,
                            'reasons':['extreme candle range','recent buy volume below threshold']},
                           DipRules(max_candle_range_fraction=.25,min_buy_volume_usd=7000))
        self.assertIn('SKIP',line);self.assertIn('25%',line);self.assertIn('$7,000',line)
        self.assertNotIn('"timestamp"',line)
    def test_scan_summary_separates_unsupported_markets_and_data_errors(self):
        line=format_status({'timestamp':0,'mode':'live','status':'scan_complete','realized_pnl_usd':0,
             'decisions':[{'status':'entry_rejected'},{'status':'watch'}],'source_errors':{},'skipped_markets':{'mint':'unsupported'}})
        for label,value in [('Candidates evaluated',2),('Entries rejected',1),('Waiting',1),('Unsupported skipped',1),('Source errors',0)]:
            self.assertTrue(any(row.startswith(label+':') and row.split()[-1]==str(value) for row in line.splitlines()),line)
    def test_short_history_has_exact_count_not_generic_invalid_error(self):
        row=snapshot();row['candle_history']['candles']=row['candle_history']['candles'][-4:]
        result=assess_trend_pullback(row,1200,DipRules())
        self.assertIn('only 4 completed 1m candles available; need 8',result['reasons'])
    def test_missing_provider_history_and_gapped_history_are_distinct(self):
        row=snapshot();row.pop('candle_history');row['source_errors']={'candles':{'error_type':'HTTPError'}}
        self.assertEqual(assess_trend_pullback(row,1200,DipRules())['reasons'],['one-minute candle data unavailable (HTTPError)'])
        row=snapshot();row['candle_history']['candles'].pop(-3)
        reasons=CandleQualityPolicy(require_uptrend=False,min_bars=8).assess(row,1200)['reasons']
        self.assertIn('missing one or more consecutive candle intervals',reasons)
    def test_raw_provider_values_do_not_leak_through_unknown_exception_messages(self):
        row=snapshot();row['candle_history']['candles'][-1]['open']='untrusted source data'
        result=CandleQualityPolicy(require_uptrend=False).assess(row,1200)
        self.assertNotIn('untrusted source data',str(result));self.assertFalse(result['passed'])

    def test_multiple_rejection_reasons_count_coins_once_and_categories_overlap(self):
        record={'timestamp':0,'mode':'live','status':'scan_complete','execution_enabled':True,
                'decisions':[{'status':'entry_rejected','reasons':['extreme candle range','excessive wick-dominated candles','recent buy volume below threshold']},
                             {'status':'entry_rejected','reasons':['only 4 completed 1m candles available; need 8']} ]}
        text=format_status(record)
        for label,value in [('Candidates evaluated','2'),('Entries rejected','2'),('Extreme volatility','1'),('Low buying volume','1'),('Insufficient history','1'),('Live execution','ENABLED')]:
            self.assertTrue(any(row.startswith(label+':') and row.split()[-1]==value for row in text.splitlines()),text)
        self.assertIn('may overlap',text)
    def test_simulation_acceptance_is_not_reported_as_confirmed_buy(self):
        text=format_status({'timestamp':0,'mode':'unsigned_simulation','status':'scan_complete',
                            'decisions':[{'status':'unsigned_simulation_passed'}],'execution_enabled':False})
        self.assertTrue(any(row.startswith('Confirmed buys:') and row.split()[-1]=='0' for row in text.splitlines()))
        self.assertIn('DISABLED',text);self.assertIn('no trades submitted',text)

class StartupDiagnosticsTests(unittest.TestCase):
    def test_rule_mismatch_explains_flat_adoption_without_exposing_values(self):
        from run_meme_live import startup_error
        message=startup_error(ValueError('Live state wallet/rules differ; use --adopt-rules only while flat with no unresolved orders'))
        self.assertIn('--adopt-rules',message);self.assertIn('preserved',message)
    def test_unknown_errors_never_echo_credentials_or_rpc_urls(self):
        from run_meme_live import startup_error
        message=startup_error(ValueError('https://private.example/?api-key=SECRET'))
        self.assertNotIn('SECRET',message);self.assertNotIn('private.example',message)
    def test_duplicate_runner_is_actionable(self):
        from run_meme_live import startup_error
        self.assertIn('Stop the old process',startup_error(BlockingIOError()))


class ChallengerPersistenceDisplayTests(unittest.TestCase):
    def test_live_summary_shows_saved_challenger_progress(self):
        text=format_status({'timestamp':0,'mode':'live','status':'holding','exit_evaluation':{
            'challenger_id':'abc123','current_challenger_positions':2,
            'challengers_completed':2,'total_evaluated_positions':22}})
        self.assertIn('CHALLENGER abc123 | 2/10 positions',text)
        self.assertIn('completed 2 · cumulative 22 evaluated',text)
