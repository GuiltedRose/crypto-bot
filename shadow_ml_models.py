"""UNTESTED bounded CPU shadow models. Never used to authorize orders.

Linear soft-margin SVM and actual LSTM with full truncated-window BPTT. No external
ML runtime, pretrained data, networking or mutation of existing neural weights.
"""
import math
import random

MAX_ROWS=1024
MAX_INPUTS=128
MAX_STEPS=8
HIDDEN=4


def sigmoid(value):return 1/(1+math.exp(-max(-30.,min(30.,value))))


def vector(values):
    if not isinstance(values,list) or not 1<=len(values)<=MAX_INPUTS or any(type(v) not in (int,float) or not math.isfinite(v) or abs(v)>1 for v in values):
        raise ValueError('Expected bounded finite normalized features')
    return values


def rows(samples,sequential=False):
    if not 1<=len(samples)<=MAX_ROWS:raise ValueError('Sample budget exceeded')
    size=None
    for x,y in samples:
        if type(y) is not int or y not in (0,1):raise ValueError('Verified binary target required')
        if sequential and not 1<=len(x)<=MAX_STEPS:raise ValueError('Sequence budget exceeded')
        for point in x if sequential else [x]:
            vector(point)
            if size is None:size=len(point)
            if len(point)!=size:raise ValueError('Feature schema mismatch')
    return size


def fit_svm(samples):
    size=rows(samples);weights=[0.]*size;bias=0.
    for _ in range(5):
        for x,label in samples:
            target=1 if label else -1
            margin=target*(sum(w*v for w,v in zip(weights,x))+bias)
            weights=[max(-8.,min(8.,w-.01*(.01*w-(target*v if margin<1 else 0)))) for w,v in zip(weights,x)]
            if margin<1:bias=max(-8.,min(8.,bias+.01*target))
    return {'family':'svm','weights':weights,'bias':bias,'calibration':None}


def margin(model,x):
    vector(x)
    if len(x)!=len(model['weights']):raise ValueError('Feature mismatch')
    return sum(w*v for w,v in zip(model['weights'],x))+model['bias']


def calibrate_svm(model,samples):
    rows(samples);a,b=1.,0.
    for _ in range(20):
        for x,label in samples:
            value=margin(model,x);error=sigmoid(a*value+b)-label
            a=max(0.,min(8.,a-.01*error*value));b=max(-8.,min(8.,b-.01*error))
    return {**model,'calibration':[a,b]}


def initialize_lstm(size):
    rng=random.Random(42)
    return {'family':'lstm','size':size,'weights':[[[rng.uniform(-.1,.1) for _ in range(size+HIDDEN)] for _ in range(HIDDEN)] for _ in range(4)],
            'bias':[[1. if gate==1 else 0. for _ in range(HIDDEN)] for gate in range(4)],
            'output':[rng.uniform(-.1,.1) for _ in range(HIDDEN)],'output_bias':0.}


def forward_lstm(model,sequence):
    if not 1<=len(sequence)<=MAX_STEPS:raise ValueError('Sequence budget exceeded')
    hidden=[0.]*HIDDEN;cell=[0.]*HIDDEN;cache=[]
    for x in sequence:
        vector(x)
        if len(x)!=model['size']:raise ValueError('Feature mismatch')
        joined=x+hidden;previous=cell
        gates=[]
        for gate in range(4):
            logits=[sum(w*v for w,v in zip(weights,joined))+bias for weights,bias in zip(model['weights'][gate],model['bias'][gate])]
            gates.append([math.tanh(v) if gate==3 else sigmoid(v) for v in logits])
        incoming,forget,output,proposal=gates
        cell=[f*c+i*g for f,c,i,g in zip(forget,previous,incoming,proposal)]
        hidden=[o*math.tanh(c) for o,c in zip(output,cell)]
        cache.append((joined,previous,gates,cell,hidden))
    probability=sigmoid(sum(w*h for w,h in zip(model['output'],hidden))+model['output_bias'])
    return probability,cache


def fit_lstm(samples):
    size=rows(samples,True);model=initialize_lstm(size)
    for _ in range(3):
        for sequence,label in samples:
            probability,cache=forward_lstm(model,sequence)
            error=probability-label
            dw=[[[0.]*(size+HIDDEN) for _ in range(HIDDEN)] for _ in range(4)]
            db=[[0.]*HIDDEN for _ in range(4)]
            dh=[error*w for w in model['output']];dc=[0.]*HIDDEN
            for joined,previous,gates,cell,hidden in reversed(cache):
                incoming,forget,output,proposal=gates
                total=[c+h*o*(1-math.tanh(v)**2) for c,h,o,v in zip(dc,dh,output,cell)]
                derivatives=[
                    [v*g*i*(1-i) for v,g,i in zip(total,proposal,incoming)],
                    [v*c*f*(1-f) for v,c,f in zip(total,previous,forget)],
                    [h*math.tanh(c)*o*(1-o) for h,c,o in zip(dh,cell,output)],
                    [v*i*(1-g*g) for v,i,g in zip(total,incoming,proposal)]]
                djoined=[0.]*(size+HIDDEN)
                for gate in range(4):
                    for unit in range(HIDDEN):
                        gradient=derivatives[gate][unit];db[gate][unit]+=gradient
                        for j,value in enumerate(joined):
                            dw[gate][unit][j]+=gradient*value
                            djoined[j]+=gradient*model['weights'][gate][unit][j]
                dh=djoined[size:];dc=[v*f for v,f in zip(total,forget)]
            def step(value,gradient):return max(-8.,min(8.,value-.005*max(-1.,min(1.,gradient))))
            for gate in range(4):
                for unit in range(HIDDEN):
                    model['weights'][gate][unit]=[step(w,g) for w,g in zip(model['weights'][gate][unit],dw[gate][unit])]
                    model['bias'][gate][unit]=step(model['bias'][gate][unit],db[gate][unit])
            model['output']=[step(w,error*h) for w,h in zip(model['output'],cache[-1][-1])]
            model['output_bias']=step(model['output_bias'],error)
    return model


def predict(model,x):
    if model['family']=='svm':
        if model['calibration'] is None:raise ValueError('SVM needs independent calibration data')
        a,b=model['calibration'];return sigmoid(a*margin(model,x)+b)
    if model['family']=='lstm':return forward_lstm(model,x)[0]
    raise ValueError('Unknown model family')
