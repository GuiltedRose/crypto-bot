import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from exit_evaluation import split_positions,compare,update
from meme_learning import new_weighted_model


def model():
    result=new_weighted_model()
    result.update(samples=40,wins=20,losses=20,trades=8,task='one_minute_net_continuation')
    result['weights']['output_weights']=[0.]*8
    result['weights']['output_bias']=-8.
    return result


def dataset(count,start=0,offset=0,losing_rule=True):
    groups={};closed={}
    for i in range(count):
        key=('paper',str(offset+i));at=start+i*300+1
        groups[key]=[(at,{},1.),(at+60,{},.5),(at+120,{},.1)]
        closed[key]={'entry_at':at-1,'exit_at':at+180,'net_pnl_usd':-.5 if losing_rule else 2.}
    return groups,closed


class ExitEvaluationTests(unittest.TestCase):
    def test_chronological_split_purges_overlapping_positions(self):
        groups,closed=dataset(10)
        closed[('paper','6')]['exit_at']=4000
        training,heldout,cutoff=split_positions(groups,closed)
        self.assertFalse(set(training)&set(heldout))
        self.assertEqual(cutoff,4000)
        self.assertTrue(all(closed[k]['entry_at']>cutoff for k in heldout))

    def test_metrics_are_position_weighted_and_compare_actual_rule_outcomes(self):
        groups,closed=dataset(10)
        metrics=compare(model(),None,groups,closed,list(groups))
        self.assertEqual(metrics['positions'],10)
        self.assertEqual(metrics['accuracy'],1.)
        self.assertEqual(metrics['reference_accuracy'],0.)
        self.assertEqual(metrics['neural_vs_rule_usd'],15.)
        self.assertGreater(metrics['improvement_lower_usd'],0)
        self.assertLess(metrics['brier'],metrics['reference_brier'])

    def test_frozen_challenger_promotes_then_rolls_back_on_fresh_worse_positions(self):
        groups,closed=dataset(40)
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'evaluation.json'
            with patch('exit_evaluation.load_groups',return_value=groups),patch('exit_evaluation.baselines',return_value=closed),patch('exit_evaluation.train_groups',return_value=model()):
                first=update([],path)
                self.assertFalse(first['evaluation']['approved'])
                second=update([],path)
                self.assertTrue(second['evaluation']['approved'])
                self.assertEqual(second['evaluation']['oos']['positions'],10)
                # New positions, entirely later than the consumed holdout.
                more,outcomes=dataset(7,start=13000,offset=40,losing_rule=False)
                groups.update(more);closed.update(outcomes)
                third=update([],path)
                self.assertFalse(third['evaluation']['approved'])
                self.assertEqual(third['evaluation']['rollbacks'],1)

    def test_holdout_cannot_retrain_candidate_and_unchanged_data_is_cached(self):
        groups,closed=dataset(12)
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'evaluation.json'
            with patch('exit_evaluation.load_groups',return_value=groups),patch('exit_evaluation.baselines',return_value=closed),patch('exit_evaluation.train_groups',return_value=model()) as fit:
                update([],path)
                count=fit.call_count
                for _ in range(3):update([],path)
                self.assertEqual(fit.call_count,count)
                trained_keys=set(fit.call_args.args[0])
                self.assertTrue(all(closed[k]['entry_at']>max(closed[t]['exit_at'] for t in trained_keys)
                                    for k in closed if k not in trained_keys))


    def test_correction_invalidates_frozen_weights_without_deleting_other_samples(self):
        groups,closed=dataset(12)
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'evaluation.json'
            with patch('exit_evaluation.load_groups',return_value=groups),patch('exit_evaluation.baselines',return_value=closed),patch('exit_evaluation.train_groups',return_value=model()):
                update([],path)
                first_key=next(iter(groups))
                groups[first_key][0]=(groups[first_key][0][0],{},99.)
                result=update([],path)
                self.assertFalse(result['evaluation']['approved'])
                self.assertEqual(result['evaluation']['corpus_positions'],12)
