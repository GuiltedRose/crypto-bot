import copy
import sqlite3
import tempfile
import unittest
from pathlib import Path
from meme_learning import chart_features,model_features,new_weighted_model,train_shared_model
from neural_exits import record,train,decision
from test_paper_parallel import observation
from test_volume_candles import rules


class ChartLearningTests(unittest.TestCase):
    def test_duplicate_negative_class_cannot_dilute_positive_class_mass(self):
        context={'observed_at':1200,'flow_metrics':{'buy_sell_ratio':2}}
        win={'entry_context':context,'exit_at':1,'exit_signature':'win','net_pnl_usd':1}
        loss=dict(win,exit_at=2,exit_signature='loss',net_pnl_usd=-1)
        small=train_shared_model(live=[win,loss])
        large=train_shared_model(live=[win]+[dict(loss,exit_signature=str(i)) for i in range(100)])
        for a,b in zip(small['weights']['output_weights'],large['weights']['output_weights']):
            self.assertAlmostEqual(a,b,places=12)
        self.assertEqual(large['class_balance'],{'positive_class_mass':2,'negative_class_mass':1})

    def test_chart_features_reject_future_or_gapped_bars(self):
        row=observation('coin')
        self.assertTrue(any(chart_features(row)))
        row['candle_history']['candles'][-1]['timestamp']=1200
        self.assertEqual(chart_features(row),(0.,)*4)
        row=observation('coin');row['candle_history']['candles'][0]['timestamp']-=60
        self.assertEqual(chart_features(row),(0.,)*4)

    def test_exit_labels_require_real_later_quotes_and_do_not_cross_trades(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'quotes.sqlite';db=sqlite3.connect(path)
            for now,net in ((1200,0),(1260,.2),(1320,-.1)):
                record(db,'trade',observation('coin',now),rules(),net,2)
                record(db,'trade',observation('coin',now),rules(),net,2)
            record(db,'different',observation('coin',1380),rules(),100,2)
            db.close()
            model=train([path])
            self.assertEqual((model['samples'],model['wins'],model['losses']),(2,1,1))

    def test_early_exit_warms_up_and_ignores_stale_or_incompatible_models(self):
        model=new_weighted_model();row=observation('coin')
        self.assertIsNone(decision(model,row,rules(),.1,2))
        model.update(task='one_minute_net_continuation',samples=30,wins=15,losses=15,trades=5)
        model['weights']['output_weights']=[0.]*8;model['weights']['output_bias']=-8
        self.assertEqual(decision(model,row,rules(),.1,2)['reason'],'neural early profit exit')
        self.assertEqual(decision(model,row,rules(),-.1,2)['reason'],'neural early loss exit')
        stale=copy.deepcopy(row);stale['observed_at']+=300
        self.assertIsNone(decision(model,stale,rules(),.1,2))
        model['version']='old'
        self.assertIsNone(decision(model,row,rules(),.1,2))
