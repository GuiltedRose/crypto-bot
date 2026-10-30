"""Small CPU network adapted from ai-tool/dense.py and activation.py.

Dense -> tanh -> dense -> sigmoid, binary cross entropy. Pure Python keeps
trading free of the source project's EEG, TensorFlow and NumPy dependencies.
Only JSON parameters cross the persistence boundary; no pretrained artifacts.
"""
import math
import random

VERSION='online-mlp-v2'
HIDDEN=8


def initialize(inputs):
    rng=random.Random(42)
    scale=math.sqrt(2/(inputs+HIDDEN))
    return {'hidden_weights':[[rng.gauss(0,scale) for _ in range(inputs)] for _ in range(HIDDEN)],
            'hidden_bias':[0.]*HIDDEN,
            'output_weights':[rng.gauss(0,math.sqrt(2/(HIDDEN+1))) for _ in range(HIDDEN)],
            'output_bias':0.}


def validate(parameters,inputs):
    def vector(value,length):
        return (isinstance(value,list) and len(value)==length
                and all(type(x) in (float,int) and math.isfinite(x) and abs(x)<=8 for x in value))
    matrix=parameters.get('hidden_weights')
    if (not isinstance(matrix,list) or len(matrix)!=HIDDEN
        or not all(vector(row,inputs) for row in matrix)
        or not vector(parameters.get('hidden_bias'),HIDDEN)
        or not vector(parameters.get('output_weights'),HIDDEN)
        or not vector([parameters.get('output_bias')],1)):
        raise ValueError('Invalid neural network parameters')


def forward(parameters,values):
    validate(parameters,len(values))
    hidden=[math.tanh(sum(w*x for w,x in zip(row,values))+bias)
            for row,bias in zip(parameters['hidden_weights'],parameters['hidden_bias'])]
    logit=parameters['output_bias']+sum(w*h for w,h in zip(parameters['output_weights'],hidden))
    probability=1/(1+math.exp(-max(-30.,min(30.,logit))))
    return probability,hidden


def train(parameters,values,target,rate,sample_weight,l2):
    probability,hidden=forward(parameters,values)
    error=sample_weight*(probability-target)
    # Backpropagate with the OLD output weights before applying any updates.
    hidden_gradient=[error*w*(1-h*h) for w,h in zip(parameters['output_weights'],hidden)]
    def step(value,gradient): return max(-8.,min(8.,value-rate*gradient))
    result={
        'hidden_weights':[[step(w,g*x+l2*w) for w,x in zip(row,values)]
                          for row,g in zip(parameters['hidden_weights'],hidden_gradient)],
        'hidden_bias':[step(b,g) for b,g in zip(parameters['hidden_bias'],hidden_gradient)],
        'output_weights':[step(w,error*h+l2*w) for w,h in zip(parameters['output_weights'],hidden)],
        'output_bias':step(parameters['output_bias'],error)}
    return result,probability
