import tempfile
import unittest
from pathlib import Path
from meme_learning import (adverse_live_cohorts,decision_context,new_weighted_model,
    outcome_feedback,report,train_followed_wallet_model,train_shared_model,paper_outcomes,update_weighted_model,weighted_prediction)
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
    def test_live_entry_features_use_followed_buy_receipts_only(self):
        row=snapshot()
        row['research_context']={'wallet_signals':[{'observations':[
            {'wallet':'followed','wallet_origin':'pinned','quote_delta_amount':-.2},
            {'wallet':'leaderboard','wallet_origin':'leaderboard','quote_delta_amount':-.8}]}]}
        model=new_weighted_model()
        model['wallet_stats']={'followed':{'closed_trades':4,'wins':3,'win_rate':.75},
            'leaderboard':{'closed_trades':9,'wins':9,'win_rate':1.}}
        context=decision_context(row,DipRules(),weighted_model=model)
        self.assertEqual(context['followed_wallet_features']['activity_count'],1)
        self.assertEqual(context['followed_wallet_features']['buy_size_quote'],.2)
        self.assertEqual(context['followed_wallet_features']['prior_win_rate'],.75)
        self.assertNotIn('research_context',context)
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
            store.save('transaction_failed',{'id':'failure','network_cost_usd':.01})
            store.state.pop('learning_model_live')
            store.save('legacy_state_without_learner',{})
            store.close()
            migrated=MemeLiveStore(root,'user',DipRules())
            self.assertEqual(migrated.state['learning_model_live']['samples'],1)
            migrated.close()
            data=report(Path(root)/'meme_live.sqlite')
            self.assertEqual(data['closed_bot_trades'],1)
            self.assertEqual(data['closed_trades'][0]['entry_context'],ctx)
            self.assertEqual(data['closed_trades'][0]['label_kind'],'confirmed_bot_receipt_pnl')
            self.assertAlmostEqual(data['live_realized_pnl_including_failed_orders_usd'],.99)
            self.assertFalse(data['automatic_live_changes'])
            feedback={(row['feature'],row['band'],row['review_status']) for row in data['outcome_feedback']}
            self.assertIn(('buy_sell_ratio','at_least_1.1','insufficient_sample'),feedback)
            self.assertTrue(data['feedback_policy']['live_and_paper_separated'])
            self.assertEqual(data['weighted_learning']['followed_wallet_model']['training_samples'],0)
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

    def test_weighted_learning_updates_bounded_features_and_skips_duplicate_or_missing_labels(self):
        context={'flow_metrics':{'buy_sell_ratio':1.5,'hourly_turnover_to_liquidity':2,
            'max_completed_candle_range_fraction':.08},
            'signal':{'metrics':{'runup_gain_fraction':.1,'pullback_fraction':.1,
                'reclaim_to_pullback_volume_ratio':1.2}}}
        model=new_weighted_model()
        model,win=update_weighted_model(model,context,.2,'exit-1')
        self.assertTrue(win['updated']);self.assertEqual(win['class_weight'],1)
        self.assertGreater(weighted_prediction(model,context)['score'],.5)
        duplicate,skipped=update_weighted_model(model,context,-.2,'exit-1')
        self.assertFalse(skipped['updated']);self.assertEqual(duplicate['samples'],1)
        missing,skipped=update_weighted_model(model,{},-.2,'exit-2')
        self.assertFalse(skipped['updated']);self.assertEqual(missing['samples'],1)
        model,loss=update_weighted_model(model,context,-.2,'exit-2')
        self.assertTrue(loss['updated']);self.assertEqual(model['losses'],1)
        from neural_network import validate
        validate(model['weights'],len(model['feature_names']))

    def test_only_followed_wallet_outcomes_train_the_live_model(self):
        context={'followed_wallet_features':{'buy_size_quote':.2,'prior_win_rate':.6}}
        outcomes=[{'wallet':'followed-a','mint':'coin','entry_context':context,
            'exit_at':2,'exit_signature':'sale-a','net_pnl_quote':.1},
            {'wallet':'followed-b','mint':'coin','entry_context':context,
            'exit_at':3,'exit_signature':'sale-b','net_pnl_quote':-.05}]
        model,_=train_followed_wallet_model(outcomes)
        self.assertEqual((model['samples'],model['wins'],model['losses']),(2,1,1))
        self.assertIn('followed_wallet_buy_size',model['feature_names'])
        self.assertIn('followed_wallet_prior_win_rate',model['feature_names'])

    def test_confirmed_live_negative_cohort_blocks_only_after_minimum_sample(self):
        with tempfile.TemporaryDirectory() as root:
            store=MemeLiveStore(root,'user',DipRules())
            context={'observed_at':1000,'price_usd':.1,'buy_volume_usd':900,
                     'sell_volume_usd':1000,'flow_metrics':{'buy_sell_ratio':.9,
                     'hourly_turnover_to_liquidity':2,'max_completed_candle_range_fraction':.08}}
            for index in range(10):
                mint='coin'+str(index)
                store.save('reserved',{'id':'buy'+str(index),'side':'buy','mint':mint,'entry_context':context})
                store.save('confirmed_buy',{'mint':mint,'signature':'entry'+str(index)})
                store.save('reserved',{'id':'sell'+str(index),'side':'sell','mint':mint})
                store.save('confirmed_sell',{'mint':mint,'signature':'exit'+str(index),'pnl_usd':-.1})
            candidate=dict(context,mint='newcoin',pool='newpool')
            self.assertEqual(len(adverse_live_cohorts(Path(root)/'meme_live.sqlite',candidate)),3)
            self.assertEqual(adverse_live_cohorts(Path(root)/'meme_live.sqlite',candidate)[0]['trades'],10)
            self.assertTrue(all(row['source']=='bot_live' for row in report(Path(root)/'meme_live.sqlite')['outcome_feedback']))
            store.close()


class SharedLearningTests(unittest.TestCase):
    def test_paper_reset_removes_archived_labels_from_live_weights(self):
        import sqlite3,json
        with tempfile.TemporaryDirectory() as root:
            paper=Path(root)/'paper.sqlite'
            db=sqlite3.connect(paper)
            db.executescript('CREATE TABLE paper_runs(id TEXT,created REAL,archived REAL); CREATE TABLE paper_events(sequence INTEGER,run_id TEXT,observed REAL,event TEXT,snapshot TEXT);')
            db.execute("INSERT INTO paper_runs VALUES('run',0,NULL)")
            context={'flow_metrics':{'buy_sell_ratio':2},'observed_at':1}
            for seq,event in enumerate(({'action':'paper_buy','mint':'coin'},
                    {'action':'paper_sell','mint':'coin','pnl_usd':-.2}),1):
                db.execute('INSERT INTO paper_events VALUES(?,?,?,?,?)',(seq,'run',seq,json.dumps(event),json.dumps(context)))
            db.commit()
            store=MemeLiveStore(Path(root)/'live','user',DipRules(),learning_paper_db=paper)
            self.assertEqual(store.state['learning_model_live']['source_samples'],{'bot_paper':1})
            # Returning principal is not a winning trade or another sample.
            db.execute('INSERT INTO paper_events VALUES(3,?,?,?,?)',('run',3,json.dumps({'action':'paper_rent_reclaimed','pnl_usd':100}),'{}'))
            db.commit();store.refresh_learning_model()
            self.assertEqual(store.state['learning_model_live']['samples'],1)
            db.execute("UPDATE paper_runs SET archived=4 WHERE id='run'")
            db.execute("INSERT INTO paper_runs VALUES('fresh',4,NULL)")
            db.commit();store.refresh_learning_model()
            self.assertEqual(store.state['learning_model_live']['samples'],0)
            self.assertFalse(paper_outcomes(paper)[0][0]['training_eligible'])
            self.assertEqual(report(paper=paper)['outcome_feedback'],[])
            store.close();db.close()

    def test_shared_sources_deduplicate_and_exclude_ineligible_paper(self):
        context={'flow_metrics':{'buy_sell_ratio':2}}
        live={'entry_context':context,'exit_at':1,'exit_signature':'same','net_pnl_usd':.2}
        paper=dict(live,run_id='r',exit_at=2)
        external={'entry_context':context,'exit_at':3,'exit_signature':'wallet-exit','wallet':'saved','net_pnl_quote':-.1}
        model=train_shared_model([external],[live,live],[paper,paper,dict(paper,exit_signature='void',training_eligible=False)])
        self.assertEqual(model['source_samples'],{'bot_live':1,'bot_paper':1,'followed_wallet':1})
        self.assertEqual((model['samples'],model['wins'],model['losses']),(3,2,1))
