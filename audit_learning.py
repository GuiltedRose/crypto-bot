"""Read-only replay of recorded evaluation scores; no training, HTTP, or promotions."""
import argparse
import json
from collections import Counter
from pathlib import Path
from evaluation_state import load
from exit_evaluation import promotion_failures,compare,live_eligibility,digest
from meme_learning import paper_outcomes
from neural_exits import load_groups,samples_for


def audit(directory,live_db=None):
    directory=Path(directory)
    state,_=load(directory/'exit_evaluation.json')
    state=state or {}
    trades,opened,_,unmatched=paper_outcomes(directory/'paper.sqlite')
    groups=load_groups([directory/'paper.sqlite',live_db])
    decisions=[];causes=Counter()
    for index,event in enumerate(state.get('history',[])):
        if event['status'] not in ('rejected','promoted'):continue
        metrics=event['metrics'];failures=promotion_failures(metrics)
        causes.update(failures)
        snapshot=event.get('snapshot') or {}
        replay=None
        if snapshot.get('validation_rows'):
            keys=[tuple(json.loads(key)) for key in snapshot['validation_ids']]
            closed={tuple(json.loads(key)):value['baseline'] for key,value in snapshot['validation_inputs'].items()}
            replay=compare(None,None,{},closed,keys,snapshot['validation_rows'])
            assert all(replay[key]==metrics[key] for key in ('positions','accuracy','brier','net_improvement_usd'))
        decisions.append({'event':index,'id':event.get('candidate_id','legacy'),'status':event['status'],
            'positions':metrics['positions'],'changed_exits':metrics['changed_positions'],
            'net_usd':metrics['neural_net_usd'],'delta_usd':metrics['net_improvement_usd'],
            'accuracy':metrics.get('accuracy'),'brier':metrics.get('brier'),
            'failed_requirements':failures,'saved_score_replay_verified':replay is not None,
            'training_samples':snapshot.get('model',{}).get('samples'),
            'weights_digest':digest(snapshot['model']['weights'])[:12] if snapshot.get('model') else None})
    return {'paper_closed':len(trades),'paper_wins':sum(row['net_pnl_usd']>0 for row in trades),
        'paper_losses':sum(row['net_pnl_usd']<0 for row in trades),
        'valid_paper_closed':sum(row.get('training_eligible',True) for row in trades),
        'paper_runs':len({row['run_id'] for row in trades}),'open':len(opened),'unmatched':unmatched,
        'quote_positions':len(groups),'quote_pairs':len(samples_for(groups)),
        'decisions':decisions,'rejection_causes':dict(causes),'live_eligibility':live_eligibility(state),
        'mode':'read_only_saved_score_replay_no_new_training_or_inference'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir',type=Path,default=Path('state/paper_volume'))
    parser.add_argument('--live-db',type=Path,default=Path('state/meme_live/meme_live.sqlite'))
    args=parser.parse_args()
    print(json.dumps(audit(args.state_dir,args.live_db),indent=2))
