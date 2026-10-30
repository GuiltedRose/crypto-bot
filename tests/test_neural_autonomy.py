import copy
import unittest
from meme_learning import new_weighted_model
from neural_autonomy import advance,entry_decision,qualification


def trained():
    model=new_weighted_model();model.update(samples=30,training_ids=['original'],source_samples={'bot_paper':30})
    return model


def observations(candidate):
    rows=[]
    for day in range(1,16):
        for n in range(3):
            at=day*86400+n*60
            rows.append({'source':'bot_live','entry_at':at,'exit_at':at+30,
                'exit_signature':f'{day}:{n}','net_pnl_usd':1. if n<2 else -.4,
                'entry_context':{'recorded_before_order':True,'neural_selection':{
                    'candidate_id':candidate['id'],'score':.8 if n<2 else .2}}})
    return rows


class AutonomyTests(unittest.TestCase):
    def test_waits_then_freezes_and_uses_future_confirmed_predictions_only(self):
        self.assertEqual(advance(None,new_weighted_model(),[],{},0)['status'],'waiting_for_training')
        model=trained();state=advance(None,model,[],{},0)
        frozen=copy.deepcopy(state['candidate']['model'])
        model['weights']['output_bias']=2
        state=advance(state,model,[],{},1)
        self.assertEqual(state['candidate']['model'],frozen)
        rows=observations(state['candidate'])
        for row in rows: row['source']='bot_paper'
        self.assertEqual(qualification(state['candidate'],rows)['confirmed_validation_trades'],0)
        for row in rows:row['source']='bot_live';row['entry_context']['neural_selection']['candidate_id']='other'
        self.assertEqual(qualification(state['candidate'],rows)['confirmed_validation_trades'],0)

    def test_qualification_promotes_only_when_flat_and_allowed(self):
        state=advance(None,trained(),[],{},0);rows=observations(state['candidate'])
        waiting=advance(state,trained(),rows,{},16*86400,can_activate=False)
        self.assertEqual(waiting['status'],'collecting')
        self.assertEqual(waiting['qualification']['status'],'qualified')
        active=advance(waiting,trained(),rows,{},16*86400)
        self.assertEqual(active['status'],'active')
        # Repeated receipts cannot inflate sample counts.
        self.assertEqual(qualification(state['candidate'],rows+rows)['confirmed_validation_trades'],45)

    def test_resets_and_policy_changes_invalidate_frozen_model(self):
        state=advance(None,trained(),[],{},0);original=state['candidate']['id']
        model=trained();model['training_ids']=[];model['samples']=0
        result=advance(state,model,[],{},1)
        self.assertEqual(result['status'],'waiting_for_training');self.assertNotIn('candidate',result)
        result=advance(state,trained(),[],{'different_policy':True},2)
        self.assertNotEqual(original,result['candidate']['id'])

    def test_live_filter_is_inert_until_qualified_and_rolls_back(self):
        state=advance(None,trained(),[],{},0)
        state['candidate']['model']['weights']['output_weights']=[0.]*8
        state['candidate']['model']['weights']['output_bias']=-8.
        self.assertTrue(entry_decision(state,{})['allow'])
        state.update(status='active',activated_at=1)
        self.assertFalse(entry_decision(state,{})['allow'])
        rows=observations(state['candidate'])[:2]
        for row in rows:row['net_pnl_usd']=-1.
        result=advance(state,trained(),rows,{},2*86400)
        self.assertEqual(result['status'],'collecting')
        self.assertEqual(result['reason'],'two losses or nonpositive last five closes')
        self.assertTrue(entry_decision(result,{})['allow'])

    def test_negative_results_fail_and_old_in_sample_labels_cannot_qualify(self):
        state=advance(None,trained(),[],{},0);rows=observations(state['candidate'])
        for row in rows:row['net_pnl_usd']=-1.
        self.assertEqual(qualification(state['candidate'],rows)['status'],'rejected')
        for row in rows:row['entry_at']=0
        self.assertEqual(qualification(state['candidate'],rows)['confirmed_validation_trades'],0)


    def test_failure_fees_cannot_disappear_from_qualification(self):
        state=advance(None,trained(),[],{},0);rows=observations(state['candidate'])
        result=qualification(state['candidate'],rows,[{'observed_at':86400,'network_cost_usd':100.}])
        self.assertEqual(result['status'],'rejected')
        self.assertEqual(result['failed_order_cost_usd'],100.)

    def test_engine_applies_only_active_veto_and_keeps_existing_entry_checks(self):
        import tempfile
        from meme_live import MemeLiveStore,MemeLiveEngine
        from test_momentum_reclaim import policy,setup
        from test_meme_live import FakeAdapter
        with tempfile.TemporaryDirectory() as root:
            store=MemeLiveStore(root,'user',policy())
            try:
                state=advance(None,trained(),[],store.state['binding'],0)
                state['candidate']['model']['weights']['output_weights']=[0.]*8
                state['candidate']['model']['weights']['output_bias']=-8.
                store.state['neural_autonomy']=state
                row=setup();row.pop('execution');row['market_kind']='bonding_curve'
                engine=MemeLiveEngine({},'user',store,policy(),adapter_factory=FakeAdapter)
                # A shadow score cannot block; the mandatory risk check still does.
                self.assertEqual(engine.prepare(row,100,6.5,1200)['status'],'onchain_risk_unavailable')
                state['status']='active'
                self.assertEqual(engine.prepare(row,100,6.5,1200)['status'],'neural_entry_veto')
            finally:store.close()


    def test_frozen_candidate_survives_restart_and_open_position_skips_training(self):
        import tempfile
        from unittest.mock import patch
        from meme_live import MemeLiveStore
        from meme_dip import DipRules
        with tempfile.TemporaryDirectory() as root:
            with patch('meme_learning.train_shared_model',return_value=trained()):
                store=MemeLiveStore(root,'user',DipRules())
                original=copy.deepcopy(store.state['neural_autonomy'])
                store.close()
                store=MemeLiveStore(root,'user',DipRules())
                self.assertEqual(store.state['neural_autonomy']['candidate'],original['candidate'])
                store.state['position']={'mint':'open'}
                with patch('meme_learning.train_shared_model',side_effect=AssertionError('must not train while holding')):
                    store.refresh_learning_model()
                store.state['position']=None;store.state['pending']={'id':'uncertain'}
                with patch('meme_learning.train_shared_model',side_effect=AssertionError('must reconcile first')):
                    store.refresh_learning_model()
                store.close()


    def test_positive_weighting_requires_compensated_threshold_and_new_candidate(self):
        import math
        state=advance(None,trained(),[],{},0)
        state['candidate']['model']['weights']['output_weights']=[0.]*8
        state['candidate']['model']['weights']['output_bias']=math.log(.6/.4)
        state['status']='active'
        self.assertFalse(entry_decision(state,{})['allow'])
        old_id=state['candidate']['id']
        state['candidate']['model']['version']='online-mlp-v1'
        changed=advance(state,trained(),[],{},1)
        self.assertNotEqual(changed['candidate']['id'],old_id)
        self.assertEqual(changed['status'],'collecting')


    def test_online_live_mode_vetoes_without_claiming_qualification(self):
        import tempfile
        from meme_live import MemeLiveStore,MemeLiveEngine
        from test_momentum_reclaim import policy,setup
        from test_meme_live import FakeAdapter
        with tempfile.TemporaryDirectory() as root:
            store=MemeLiveStore(root,'user',policy())
            try:
                model=trained();model['weights']['output_weights']=[0.]*8
                model['weights']['output_bias']=-8.
                store.state['learning_model_live']=model
                engine=MemeLiveEngine({},'user',store,policy(),adapter_factory=FakeAdapter)
                engine.neural_mode='learning'
                row=setup();row.pop('execution');row['market_kind']='bonding_curve'
                decision=engine.prepare(row,100,6.5,1200)
                self.assertEqual(decision['status'],'neural_entry_veto')
                self.assertFalse(decision['neural_selection']['qualified'])
                model['weights']['output_bias']=8.
                # Positive score never bypasses mandatory execution/risk screening.
                self.assertEqual(engine.prepare(row,100,6.5,1200)['status'],'onchain_risk_unavailable')
                engine.neural_mode='off'
                model['weights']['output_bias']=-8.
                self.assertEqual(engine.prepare(row,100,6.5,1200)['status'],'onchain_risk_unavailable')
            finally:store.close()
