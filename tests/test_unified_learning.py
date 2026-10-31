"""UNEXECUTED isolated model/data-policy regression software."""
import copy
from pathlib import Path
import tempfile
import unittest
from shadow_ml_models import fit_svm, calibrate_svm, predict, fit_lstm, initialize_lstm
from unified_observations import ObservationStore, eligible, tensor, digest
from unified_learning import grouped_split, metrics, build_candidate, load_candidate


def record(index=0,**overrides):
    row=dict(identity=str(index),position_id=str(index),group_id=str(index),source='bot_paper',mint='fixture',
             feature_at=index*100+1,known_at=index*100+1,label_known_at=index*100+10,
             features=[.5,None],feature_schema='fixture-v1',net_pnl_usd=1 if index%2 else -1,net_of_costs_verified=True,
             training_eligible=True,rights={'storage_verified':True,'ml_verified':True,'evaluation_verified':True,'evidence':'synthetic tests'},
             provenance={'verified':True,'evidence':'synthetic unit fixture'})
    return dict(row,**overrides)


class UnifiedTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=ObservationStore(Path(self.temp.name)/'new.sqlite',create=True)

    def test_append_dedup_conflict_revocation_and_rights(self):
        row=record();identifier=self.store.add(row)
        self.assertEqual(self.store.add(row),identifier)
        self.assertEqual(len(self.store.observations()),1)
        with self.assertRaises(ValueError):self.store.add(dict(row,net_pnl_usd=4))
        self.assertFalse(eligible(dict(row,rights={**row['rights'],'ml_verified':False})))
        self.assertFalse(eligible(dict(row,known_at=20)))
        self.assertEqual(tensor(row),[.0625,1.,0.,0.])
        self.store.revoke(row['source'],row['identity'],'corrected upstream evidence')
        self.assertEqual(self.store.by_ids([identifier]),[])
        with self.store.connect(readonly=True) as db:self.assertEqual(db.execute('SELECT count(*) FROM observations').fetchone()[0],1)

    def test_retrieval_excludes_future_and_correlated_outcomes(self):
        for i in range(6):self.store.add(record(i,group_id='correlated' if i<3 else str(i)))
        result=self.store.retrieve(tensor(record()),asof=450,exclude_group='4')
        self.assertEqual(len(result['neighbors']),2)
        self.assertEqual(len({row['group_id'] for row in result['neighbors']}),2)
        self.assertFalse(result['market_eligible'])
        frozen=self.store.retrieve(tensor(record()),asof=900,exclude_group='none',allow_ids={digest(['bot_paper','0'])})
        self.assertEqual([row['identity'] for row in frozen['neighbors']],['0'])

    def test_grouped_chronological_splits_and_position_aggregation(self):
        train,val,test=grouped_split([record(i) for i in range(100)])
        self.assertGreater(min(r['feature_at'] for r in val),max(r['label_known_at'] for r in train))
        self.assertGreater(min(r['feature_at'] for r in test),max(r['label_known_at'] for r in val))
        result=metrics([(.9,1,record(1)),(.8,1,record(1))])
        self.assertEqual(result['positions'],1);self.assertEqual(result['estimated_selected_net_usd'],1)
        self.assertEqual(build_candidate(self.store,'svm')['status'],'blocked')

    def test_svm_and_lstm_are_bounded_and_do_not_modify_existing_models(self):
        svm=calibrate_svm(fit_svm([([-.5],0),([.5],1)]),[([-.5],0),([.5],1)])
        self.assertGreater(predict(svm,[.5]),predict(svm,[-.5]))
        original=initialize_lstm(1);snapshot=copy.deepcopy(original)
        learned=fit_lstm([([[.1],[.2]],1),([[-.1],[-.2]],0)])
        self.assertNotEqual(learned['weights'],original['weights'])
        self.assertEqual(original,snapshot)
        self.assertTrue(0<=predict(learned,[[.1],[.2]])<=1)
        with self.assertRaises(ValueError):predict(learned,[[.1]]*9)

    def test_frozen_svm_restart_reuses_identical_training_snapshot(self):
        for i in range(100):
            self.store.add(record(i,features=[1. if i%2 else -1.,None],risk_label=i%2,
                                  risk_label_verified=True,risk_label_at=i*100+2))
        candidate=build_candidate(self.store,'svm',now=20000)
        self.assertEqual(candidate['status'],'shadow')
        again=build_candidate(ObservationStore(self.store.path),'svm',now=30000)
        self.assertEqual(candidate['id'],again['id'])
        self.assertEqual(load_candidate(self.store,candidate['id']),candidate)
        self.assertFalse(candidate['live_eligible'])
