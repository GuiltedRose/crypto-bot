"""UNTESTED bounded, offline unified ML worker. No order/RPC capabilities."""
import argparse
import json
from pathlib import Path
import time
from unified_observations import ObservationStore
from unified_learning import build_candidate, load_candidate, forecast, evaluate_candidate


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',type=Path,required=True)
    parser.add_argument('--initialize',action='store_true')
    parser.add_argument('--input',type=Path,help='Reviewed normalized JSONL, maximum 1024 records per pass')
    parser.add_argument('--status',action='store_true')
    parser.add_argument('--train',choices=['svm','lstm','rag','all'])
    parser.add_argument('--candidate',help='Frozen candidate ID for inference/evaluation')
    parser.add_argument('--forecast-input',type=Path,help='JSON containing observations and position_id')
    parser.add_argument('--evaluation-input',type=Path,help='Verified confirmed outcomes/failures JSON')
    args=parser.parse_args()
    store=ObservationStore(args.db,create=args.initialize)
    imported=0
    if args.input:
        with args.input.open('rb') as stream:
            for index in range(1025):
                line=stream.readline(65538)
                if not line:break
                if index>=1024 or len(line)>65537:raise ValueError('Input record/byte budget exceeded; use a new reviewed batch')
                store.add(json.loads(line));imported+=1
    reports={}
    for family in ('svm','lstm','rag') if args.train=='all' else [args.train] if args.train else []:
        candidate=build_candidate(store,family)
        reports[family]={key:candidate[key] for key in ('id','status','reason','historical_evaluation') if key in candidate}
    if args.status:
        with store.connect(readonly=True) as db:
            reports['status']={'observations':db.execute('SELECT count(*) FROM observations').fetchone()[0],
                               'revocations':db.execute('SELECT count(*) FROM revocations').fetchone()[0],
                               'models':[row[0] for row in db.execute('SELECT id FROM models ORDER BY id LIMIT 100')]}
    output={'imported':imported,'models':reports,'order_authorization':False}
    for mode,path in [('forecast',args.forecast_input),('evaluation',args.evaluation_input)]:
        if path:
            if not args.candidate:parser.error('--candidate required')
            with path.open('rb') as stream:raw=stream.read(1000001)
            if len(raw)>1000000:raise ValueError('Decision/evaluation input exceeds byte budget')
            value=json.loads(raw)
            if mode=='forecast':output[mode]=forecast(store,args.candidate,value['observations'],value['position_id'])
            else:
                if len(value.get('outcomes',[]))>1024 or len(value.get('failures',[]))>1024:raise ValueError('Evaluation count budget exceeded')
                output[mode]=evaluate_candidate(store,args.candidate,value['outcomes'],value.get('failures',[]))
    print(json.dumps(output,allow_nan=False))


if __name__=='__main__':main()
