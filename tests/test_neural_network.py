import copy
import json
import math
import unittest
from neural_network import initialize,forward,train,validate
from meme_learning import new_weighted_model,weighted_prediction


class NeuralNetworkTests(unittest.TestCase):
    def test_backprop_matches_numerical_cross_entropy_gradient(self):
        parameters=initialize(3);values=[.2,-.4,.7];rate=1e-4
        updated,_=train(parameters,values,1.,rate,1.7,0.)
        def loss(p): return -1.7*math.log(forward(p,values)[0])
        for key,index in [('hidden_weights',(2,1)),('hidden_bias',(2,)),
                          ('output_weights',(2,)),('output_bias',())]:
            plus=copy.deepcopy(parameters);minus=copy.deepcopy(parameters)
            def change(p,amount):
                if len(index)==2:p[key][index[0]][index[1]]+=amount
                elif index:p[key][index[0]]+=amount
                else:p[key]+=amount
            def get(p):
                if len(index)==2:return p[key][index[0]][index[1]]
                return p[key][index[0]] if index else p[key]
            change(plus,1e-6);change(minus,-1e-6)
            numeric=(loss(plus)-loss(minus))/2e-6
            actual=(get(parameters)-get(updated))/rate
            self.assertAlmostEqual(actual,numeric,places=6)

    def test_hidden_layer_can_learn_nonlinear_xor(self):
        parameters=initialize(2)
        samples=[([-1.,-1.],0),([-1.,1.],1),([1.,-1.],1),([1.,1.],0)]
        for _ in range(800):
            for values,target in samples:
                parameters,_=train(parameters,values,target,.1,1.,0.)
        for values,target in samples:
            self.assertEqual(forward(parameters,values)[0]>.5,bool(target))

    def test_json_roundtrip_and_rejection_of_old_or_corrupt_weights(self):
        model=new_weighted_model();context={'flow_metrics':{'buy_sell_ratio':2}}
        self.assertEqual(weighted_prediction(model,context),weighted_prediction(json.loads(json.dumps(model)),context))
        with self.assertRaises(ValueError):weighted_prediction({'version':'online-logistic-v1'},context)
        corrupt=copy.deepcopy(model['weights']);corrupt['hidden_weights'][0][0]=float('nan')
        with self.assertRaises(ValueError):validate(corrupt,len(model['feature_names']))
        self.assertEqual(new_weighted_model(),model)
