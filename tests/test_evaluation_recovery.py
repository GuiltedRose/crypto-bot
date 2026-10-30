import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from evaluation_state import load,save
from exit_evaluation import update
from test_exit_evaluation import dataset,model


class EvaluationRecoveryTests(unittest.TestCase):
    def test_real_paper_and_live_process_restart_keeps_exact_challenger_and_scores(self):
        script='''
import json,sys
from pathlib import Path
import exit_evaluation as evaluation
from meme_learning import new_weighted_model
payload=json.loads(Path(sys.argv[1]).read_text())
groups={tuple(key):rows for key,rows in payload['groups']}
closed={tuple(key):row for key,row in payload['closed']}
evaluation.load_groups=lambda paths:groups
evaluation.baselines=lambda paths,groups:closed
def fit(groups):
    if sys.argv[3]=='restart':raise AssertionError('Restart refitted a frozen challenger')
    model=new_weighted_model()
    model.update(samples=40,wins=20,losses=20,trades=8,task='one_minute_net_continuation')
    model['weights']['output_weights']=[0.]*8
    model['weights']['output_bias']=-8.
    return model
evaluation.train_groups=fit
result=evaluation.update([],sys.argv[2])
if sys.argv[3]=='initial':result=evaluation.update([],sys.argv[2])
print(json.dumps(result['evaluation']))
'''
        for mode in ('paper','live'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as root:
                groups,closed=dataset(12)
                data=Path(root)/'data.json'
                data.write_text(json.dumps({'groups':list(groups.items()),'closed':list(closed.items())}))
                path=Path(root)/mode/'exit_evaluation.json'
                def run(stage):
                    return json.loads(subprocess.check_output([sys.executable,'-c',script,str(data),str(path),stage],text=True))
                before=run('initial');saved=json.loads(path.read_text())
                after=run('restart');reloaded=json.loads(path.read_text())
                self.assertEqual(before,after)
                self.assertEqual(saved['candidate'],reloaded['candidate'])
                self.assertEqual(saved['consumed_positions'],reloaded['consumed_positions'])
                self.assertEqual(saved['history'],reloaded['history'])
                self.assertEqual(after['current_challenger_positions'],4)
                self.assertEqual(before['oos']['accuracy'],after['oos']['accuracy'])

    def test_interrupted_export_recovers_latest_committed_state_and_partial_temp(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'exit_evaluation.json'
            old={'version':'v','history':[],'consumed_until':0,'candidate':{'id':'same','weights':[1]},'progress':4}
            save(path,old)
            newer=copy.deepcopy(old);newer['progress']=7
            with patch('evaluation_state.os.replace',side_effect=OSError('interrupted export')):
                with self.assertRaises(OSError):save(path,newer)
            path.with_suffix('.tmp').write_text('{partial')
            recovered,reason=load(path)
            self.assertEqual(recovered['progress'],7)
            self.assertEqual(recovered['candidate'],old['candidate'])
            self.assertIsNotNone(reason)
            save(path,recovered)
            path.write_text('{corrupt primary')
            recovered,reason=load(path)
            self.assertEqual(recovered['progress'],7)
            self.assertIsNotNone(reason)

    def test_unrecoverable_state_never_silently_initializes(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'exit_evaluation.json';path.write_text('{broken')
            with self.assertRaisesRegex(ValueError,'recovery blocked'):load(path)
            self.assertEqual(path.read_text(),'{broken')

    def test_late_training_quotes_do_not_invalidate_or_refit_frozen_challenger(self):
        groups,closed=dataset(12)
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'evaluation.json'
            with patch('exit_evaluation.load_groups',return_value=groups),patch('exit_evaluation.baselines',return_value=closed),patch('exit_evaluation.train_groups',return_value=model()) as fit:
                update([],path);before=update([],path)
                frozen=copy.deepcopy(json.loads(path.read_text())['candidate'])
                first=next(iter(groups));groups[first].insert(1,(groups[first][0][0]+30,{},.8))
                fit.side_effect=AssertionError('Late addition must not refit')
                after=update([],path)
                self.assertEqual(before['evaluation']['oos'],after['evaluation']['oos'])
                self.assertEqual(frozen,json.loads(path.read_text())['candidate'])

    def test_missing_history_pauses_without_rejection_then_resumes_unchanged(self):
        groups,closed=dataset(12)
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'evaluation.json'
            with patch('exit_evaluation.load_groups',return_value=groups),patch('exit_evaluation.baselines',return_value=closed),patch('exit_evaluation.train_groups',return_value=model()):
                update([],path);before=update([],path)
                frozen=copy.deepcopy(json.loads(path.read_text())['candidate'])
                key=next(iter(groups));missing=groups.pop(key)
                paused=update([],path)
                saved=json.loads(path.read_text())
                self.assertEqual(paused['evaluation']['status'],'paused')
                self.assertEqual(saved['candidate'],frozen)
                self.assertEqual(saved['evaluation'],before['evaluation']['oos'])
                self.assertFalse(any(row['status']=='rejected' for row in saved['history']))
                groups[key]=missing
                resumed=update([],path)
                self.assertEqual(resumed['evaluation']['oos'],before['evaluation']['oos'])
                self.assertEqual(frozen,json.loads(path.read_text())['candidate'])


    def test_feature_version_change_pauses_without_failing_or_replacing_model(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'evaluation.json'
            groups,closed=dataset(12)
            with patch('exit_evaluation.load_groups',return_value=groups),patch('exit_evaluation.baselines',return_value=closed),patch('exit_evaluation.train_groups',return_value=model()):
                update([],path);original=update([],path)
                state,_=load(path)
                state['version']='previous-feature-version'
                state['consumed_positions']=['already-consumed']
                state['history'].append({'status':'rolled_back','reason':'earlier event'})
                frozen=copy.deepcopy(state['candidate']);history=copy.deepcopy(state['history'])
                save(path,state)
                result=update([],path);recovered,_=load(path)
                self.assertEqual(result['evaluation']['status'],'paused')
                self.assertEqual(result['evaluation']['oos'],original['evaluation']['oos'])
                self.assertEqual(recovered['candidate'],frozen)
                self.assertEqual(recovered['consumed_positions'],['already-consumed'])
                self.assertEqual(recovered['history'][:len(history)],history)
                self.assertNotIn('rejected',[row['status'] for row in recovered['history']])


    def test_legacy_percentage_is_not_overwritten_if_reconstruction_differs(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'evaluation.json';groups,closed=dataset(12)
            with patch('exit_evaluation.load_groups',return_value=groups),patch('exit_evaluation.baselines',return_value=closed),patch('exit_evaluation.train_groups',return_value=model()):
                update([],path);update([],path)
                state,_=load(path)
                for key in ('validation_ids','validation_rows','validation_inputs'):state['candidate'].pop(key)
                state['evaluation']['accuracy']=.56
                state['result']['evaluation']['oos']['accuracy']=.56
                frozen=copy.deepcopy(state['candidate']);save(path,state)
                result=update([],path);after,_=load(path)
                self.assertEqual(result['evaluation']['oos']['accuracy'],.56)
                self.assertEqual(result['evaluation']['status'],'paused')
                self.assertEqual(after['candidate'],frozen)
                self.assertFalse(any(row['status']=='rejected' for row in after['history']))
