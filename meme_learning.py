"""Read-only decision/outcome research for bot orders, separately from manual data.

Reports empirical patterns; does not rewrite funded rules, reset losses, hide
open trades, or turn a few profitable outcomes into a validated model.
"""
import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import sqlite3

MODEL_FEATURES=('buy_sell_balance','turnover_vs_liquidity','candle_range',
                'impulse_strength','pullback_depth','reclaim_volume',
                'followed_wallet_buy_size','followed_wallet_prior_win_rate',
                'followed_wallet_activity')
MODEL_LEARNING_RATE=.15
MODEL_L2=.01
POSITIVE_OUTCOME_MULTIPLIER=2.


def _finite(value):
    return type(value) in (int,float) and math.isfinite(value)


def _safe_ratio(numerator,denominator):
    if not _finite(numerator) or not _finite(denominator) or denominator<=0:
        return None
    value=numerator/denominator
    return value if _finite(value) else None


def _flow_metrics(snapshot):
    buy=snapshot.get('buy_volume_usd')
    sell=snapshot.get('sell_volume_usd')
    liquidity=snapshot.get('liquidity_usd')
    history=snapshot.get('candle_history',{})
    candles=history.get('candles',[]) if isinstance(history,dict) else []
    ranges=[]
    if isinstance(candles,list):
        for candle in candles[-12:]:
            if not isinstance(candle,dict): continue
            opening,high,low=(candle.get(key) for key in ('open','high','low'))
            if all(_finite(value) for value in (opening,high,low)) and opening>0 and high>=low:
                value=(high-low)/opening
                if _finite(value): ranges.append(value)
    return {
        'buy_sell_ratio':_safe_ratio(buy,sell),
        'hourly_turnover_to_liquidity':_safe_ratio(buy+sell,liquidity)
            if _finite(buy) and _finite(sell) else None,
        'max_completed_candle_range_fraction':max(ranges) if ranges else None,
    }


def model_features(context):
    flow=context.get('flow_metrics')
    if not isinstance(flow,dict): flow=_flow_metrics(context)
    signal=context.get('signal')
    signal=signal.get('metrics',{}) if isinstance(signal,dict) else {}
    signal=signal if isinstance(signal,dict) else {}

    def bounded(value,scale,*,logarithmic=False):
        if not _finite(value) or value<0: return 0.
        value=math.log1p(value) if logarithmic else value
        return max(-1.,min(1.,value/scale*2-1))

    ratio=flow.get('buy_sell_ratio')
    balance=0. if not _finite(ratio) else max(-1.,min(1.,(ratio-1)/(ratio+1)))
    turnover=bounded(flow.get('hourly_turnover_to_liquidity'),math.log1p(20),logarithmic=True)
    candle=bounded(flow.get('max_completed_candle_range_fraction'),.5)
    impulse=bounded(signal.get('runup_gain_fraction'),.15)
    pullback=bounded(signal.get('pullback_fraction'),.5)
    reclaim=bounded(signal.get('reclaim_to_pullback_volume_ratio'),2.)
    wallet=context.get('followed_wallet_features')
    wallet=wallet if isinstance(wallet,dict) else {}
    buy_size=bounded(wallet.get('buy_size_quote'),math.log1p(5),logarithmic=True)
    win_rate=wallet.get('prior_win_rate')
    prior_rate=0. if not _finite(win_rate) else max(-1.,min(1.,2*win_rate-1))
    activity=bounded(wallet.get('activity_count'),3.)
    return dict(zip(MODEL_FEATURES,(balance,turnover,candle,impulse,pullback,reclaim,
                                    buy_size,prior_rate,activity)))


def _followed_entry_features(snapshot,model):
    research=snapshot.get('research_context')
    signals=research.get('wallet_signals',[]) if isinstance(research,dict) else []
    stats=model.get('wallet_stats',{}) if isinstance(model,dict) else {}
    observations={}
    for group in signals if isinstance(signals,list) else []:
        if not isinstance(group,dict): continue
        for row in group.get('observations',[]):
            if not isinstance(row,dict) or row.get('wallet_origin')!='pinned':
                continue
            wallet=row.get('wallet')
            size=row.get('quote_delta_amount')
            if not isinstance(wallet,str) or not _finite(size) or size>=0:
                continue
            observations[wallet]=-size
    win_rates=[]
    for wallet in observations:
        record=stats.get(wallet)
        rate=record.get('win_rate') if isinstance(record,dict) else None
        if _finite(rate) and 0<=rate<=1: win_rates.append(rate)
    return {'buy_size_quote':sum(observations.values())/len(observations) if observations else None,
        'prior_win_rate':sum(win_rates)/len(win_rates) if win_rates else None,
        'activity_count':len(observations)}


def new_weighted_model():
    from neural_network import VERSION,initialize
    return {'version':VERSION,'samples':0,'wins':0,'losses':0,
            'positive_outcome_multiplier':POSITIVE_OUTCOME_MULTIPLIER,
            'feature_names':list(MODEL_FEATURES),'weights':initialize(len(MODEL_FEATURES)),
            'last_trade_signature':None,'wallet_stats':{}}


def weighted_prediction(model,context):
    from neural_network import VERSION,forward
    if model.get('version')!=VERSION or model.get('feature_names')!=list(MODEL_FEATURES):
        raise ValueError('Unsupported neural model version or feature schema; rebuild required')
    features=model_features(context)
    probability,_=forward(model['weights'],list(features.values()))
    return {'version':VERSION,'training_samples':model.get('samples',0),
            'positive_outcomes':model.get('wins',0),'score':probability,'features':features,
            'interpretation':'uncalibrated_shadow_score_not_entry_authorization'}


def update_weighted_model(model,context,net_pnl_usd,trade_signature=None):
    from neural_network import VERSION,train
    if not _finite(net_pnl_usd): raise ValueError('Finite confirmed trade P&L required for learning')
    if not isinstance(context,dict) or not context:
        return dict(model or new_weighted_model()),{'updated':False,'reason':'recorded entry context required'}
    model=dict(model or new_weighted_model())
    if model.get('version')!=VERSION or model.get('feature_names')!=list(MODEL_FEATURES):
        raise ValueError('Unsupported neural model version or feature schema; rebuild required')
    if trade_signature and model.get('last_trade_signature')==trade_signature:
        return model,{'updated':False,'reason':'trade already learned'}
    features=model_features(context)
    target=1. if net_pnl_usd>0 else 0.
    wins=model.get('wins',0);losses=model.get('losses',0)
    if type(wins) is not int or type(losses) is not int or wins<0 or losses<0:
        raise ValueError('Invalid persisted shadow model outcome counts')
    prior_samples=wins+losses
    class_count=wins if target else losses
    sample_weight=1. if prior_samples==0 else min(3.,prior_samples/(2*max(1,class_count)))
    sample_weight*=POSITIVE_OUTCOME_MULTIPLIER if target else 1.
    weights,probability=train(model['weights'],list(features.values()),target,
        MODEL_LEARNING_RATE,sample_weight,MODEL_L2)
    updated={**model,'samples':prior_samples+1,'wins':wins+int(target),
        'losses':losses+int(not target),'weights':weights,
        'last_trade_signature':trade_signature or model.get('last_trade_signature')}
    return updated,{'updated':True,'target_net_positive':bool(target),'class_weight':sample_weight,
        'probability_before_update':probability,'prediction_after_update':weighted_prediction(updated,context)['score'],
        'training_samples':updated['samples']}


def decision_context(snapshot,rules,scorecard=None,signal=None,risk=None,entry_profile=None,weighted_model=None):
    fields=('mint','pool','observed_at','price_usd','market_kind','buy_volume_usd','sell_volume_usd','liquidity_usd',
            'buy_volume_window_seconds','buy_volume_coverage_pct')
    context={key:snapshot.get(key) for key in fields}
    history=snapshot.get('candle_history',{})
    context['completed_candles']=[{key:row.get(key) for key in ('timestamp','open','high','low','close','volume_usd')}
        for row in history.get('candles',[])[-12:] if isinstance(row,dict)]
    context['scorecard']=scorecard
    context['flow_metrics']=_flow_metrics(snapshot)
    context['entry_profile']=entry_profile
    context['signal']={key:signal.get(key) for key in ('action','reason','metrics')} if isinstance(signal,dict) else None
    context['followed_wallet_features']=_followed_entry_features(snapshot,weighted_model)
    if isinstance(risk,dict):
        context['onchain_risk']={key:risk.get(key) for key in
            ('passed','status','reasons','coverage','largest_sampled_owner_pct','top10_sampled_owners_pct',
             'busiest_buyer_share','busiest_slot_buy_share')}
    else:
        context['onchain_risk']=None
    context['shadow_prediction']=weighted_prediction(weighted_model,context) if weighted_model else None
    context['limits']={key:getattr(rules,key) for key in (
        'position_fraction','take_profit_fraction','max_loss_fraction','max_position_usd',
        'take_profit_usd','max_loss_usd','slippage_per_side','drawdown_halt','min_buy_sell_ratio',
        'loss_streak_limit','loss_streak_cooldown_seconds')}
    context['recorded_before_order']=True
    return context


def live_outcomes(path):
    db=sqlite3.connect('file:'+str(Path(path).resolve())+'?mode=ro',uri=True)
    try: events=db.execute('SELECT at,kind,body FROM events ORDER BY id').fetchall()
    finally: db.close()
    result=[];opened=None;reservations={};by_id={};unmatched=0;failures=[]
    for at,kind,raw in events:
        row=json.loads(raw)
        if kind=='reserved':
            reservations[(row['side'],row['mint'])]=row;by_id[row['id']]=row
        elif kind=='transaction_failed':
            intent=by_id.get(row.get('id'),{})
            failures.append({'observed_at':at,'source':'bot_live','mint':intent.get('mint'),'entry_profile':intent.get('entry_profile'),**row})
        elif kind=='confirmed_buy':
            if opened: unmatched+=1
            intent=reservations.get(('buy',row['mint']),{})
            opened={'source':'bot_live','mint':row['mint'],'entry_at':at,'entry_signature':row['signature'],
                    'entry_profile':intent.get('entry_profile'),'entry_context':intent.get('entry_context'),
                    'entry_fee_lamports':row.get('fee_lamports'),'status':'open'}
        elif kind=='confirmed_sell':
            if not opened or opened['mint']!=row['mint']: unmatched+=1;continue
            result.append({**opened,'exit_at':at,'exit_signature':row['signature'],'net_pnl_usd':row['pnl_usd'],
                'exit_fee_lamports':row.get('fee_lamports'),'exit_reason':reservations.get(('sell',row['mint']),{}).get('reason'),
                'status':'closed','label_kind':'confirmed_bot_receipt_pnl'})
            opened=None
    return result,([opened] if opened else []),failures,unmatched


def _paper_correction(event):
    return (event.get('action') in {'paper_refund','paper_trade_refund','paper_trade_refunded',
            'paper_void','paper_trade_voided','paper_reimbursement','paper_reimbursed'}
        or any(event.get(key) is True for key in ('refunded','voided','reimbursed')))


def paper_outcomes(path):
    db=sqlite3.connect('file:'+str(Path(path).resolve())+'?mode=ro',uri=True)
    result=[];unclosed=[];unmatched=0
    try:
        for run,archived in db.execute('SELECT id,archived FROM paper_runs ORDER BY created').fetchall():
            opened={}
            rows=db.execute('SELECT sequence,observed,event,snapshot FROM paper_events WHERE run_id=? ORDER BY sequence',(run,)).fetchall()
            # Corrections may arrive after a close. Never learn an earlier label
            # from a run whose paper fills were reimbursed/voided. Rent reclaim
            # is principal accounting, not a trade refund or a profit label.
            corrected=any(_paper_correction(json.loads(row[2])) for row in rows)
            for sequence,at,event_raw,snapshot_raw in rows:
                event=json.loads(event_raw);snapshot=json.loads(snapshot_raw)
                if event['action']=='paper_buy':
                    if event['mint'] in opened: unmatched+=1
                    context=dict(event.get('entry_context') or snapshot)
                    context['flow_metrics']=_flow_metrics(snapshot)
                    signal_metrics=event.get('signal_metrics')
                    if not isinstance(signal_metrics,dict):
                        signal_metrics={'pullback_fraction':event.get('pullback_fraction')}
                    context['signal']={'metrics':signal_metrics}
                    opened[event['mint']]={'source':'bot_paper','run_id':run,'mint':event['mint'],'entry_at':at,
                            'entry_profile':event.get('entry_profile'),'entry_context':context,
                            'entry_data_kind':event.get('data_kind','supplied_observation'),
                            'training_eligible':not corrected and event.get('data_kind')!='synthetic' and snapshot.get('synthetic') is not True,
                            'status':'open'}
                elif event['action']=='paper_sell':
                    if event['mint'] not in opened: unmatched+=1;continue
                    entry=opened.pop(event['mint'])
                    result.append({**entry,'exit_at':at,'exit_signature':f'paper:{run}:{sequence}',
                                   'net_pnl_usd':event['pnl_usd'],'exit_reason':event.get('reason'),
                                   'status':'closed','label_kind':'modeled_paper_pnl',
                                   'training_eligible':entry['training_eligible'] and event.get('data_kind')!='synthetic' and snapshot.get('synthetic') is not True})

            unclosed.extend(opened.values())
    finally: db.close()
    return result,unclosed,[],unmatched


def train_followed_wallet_model(outcomes):
    model=new_weighted_model()
    scored=[]
    wallet_stats={}
    ordered=sorted(outcomes,key=lambda row:(row['exit_at'],row['wallet'],row['exit_signature']))
    for outcome in ordered:
        context=outcome['entry_context']
        before=weighted_prediction(model,context)['score']
        model,_=update_weighted_model(model,context,outcome['net_pnl_quote'],outcome['exit_signature'])
        scored.append((before,outcome['net_pnl_quote']>0))
        stats=wallet_stats.setdefault(outcome['wallet'],{'closed_trades':0,'wins':0})
        stats['closed_trades']+=1
        stats['wins']+=int(outcome['net_pnl_quote']>0)
        model['wallet_stats']=dict(model['wallet_stats'],**{
            outcome['wallet']:{**stats,'win_rate':stats['wins']/stats['closed_trades']}})
    count=len(scored)
    return model,{'entry_score_evaluation':{'trades':count,
        'accuracy':sum((score>=.5)==won for score,won in scored)/count if count else None,
        'brier_score':sum((score-int(won))**2 for score,won in scored)/count if count else None}}


def train_shared_model(external=(),live=(),paper=()):
    """Rebuild from eligible labels so corrections remove previously learned data.

    Paper labels stay modeled; they never count as confirmed live performance.
    Sources are namespaced and deduplicated before chronological updates.
    """
    model=new_weighted_model();seen=set();counts=defaultdict(int)
    rows=[]
    for source,outcomes,pnl_key in (('followed_wallet',external,'net_pnl_quote'),
            ('bot_live',live,'net_pnl_usd'),('bot_paper',paper,'net_pnl_usd')):
        for row in outcomes:
            if (row.get('training_eligible',True) is not True or _paper_correction(row)
                or row.get('entry_data_kind')=='synthetic'
                or not isinstance(row.get('entry_context'),dict) or not row['entry_context']
                or not _finite(row.get(pnl_key))): continue
            identity=(source,row.get('run_id'),row.get('wallet'),row.get('exit_signature'))
            if not identity[-1] or identity in seen: continue
            seen.add(identity)
            rows.append((row['exit_at'],repr(identity),source,row,pnl_key))
    wallet_stats={}
    for _,identity,source,row,pnl_key in sorted(rows):
        model,_=update_weighted_model(model,row['entry_context'],row[pnl_key],identity)
        counts[source]+=1
        if source=='followed_wallet':
            stats=wallet_stats.setdefault(row['wallet'],{'closed_trades':0,'wins':0})
            stats['closed_trades']+=1;stats['wins']+=int(row[pnl_key]>0)
            model['wallet_stats'][row['wallet']]={**stats,'win_rate':stats['wins']/stats['closed_trades']}
    model['source_samples']=dict(counts)
    model['training_ids']=[identity for _,identity,_,_,_ in sorted(rows)]
    return model


def _feedback_features(context):
    stored=context.get('flow_metrics')
    metrics=stored if isinstance(stored,dict) else _flow_metrics(context)
    ratio=metrics.get('buy_sell_ratio')
    turnover=metrics.get('hourly_turnover_to_liquidity')
    candle_range=metrics.get('max_completed_candle_range_fraction')
    risk=context.get('onchain_risk')
    risk_status=risk.get('status') if isinstance(risk,dict) else None
    return {
        'buy_sell_ratio':('unknown' if not _finite(ratio) else
            'below_1.0' if ratio<1 else '1.0_to_1.1' if ratio<1.1 else 'at_least_1.1'),
        'hourly_turnover_to_liquidity':('unknown' if not _finite(turnover) else
            'below_1x' if turnover<1 else '1x_to_10x' if turnover<10 else 'at_least_10x'),
        'max_candle_range':('unknown' if not _finite(candle_range) else
            'below_10pct' if candle_range<.1 else '10_to_25pct' if candle_range<.25 else 'at_least_25pct'),
        'onchain_risk_status':risk_status or 'unknown',
    }


def outcome_feedback(closed,minimum_review_samples=10):
    groups=defaultdict(list)
    for trade in closed:
        context=trade.get('entry_context')
        if trade.get('training_eligible',True) is not True or _paper_correction(trade): continue
        if not isinstance(context,dict): continue
        for feature,band in _feedback_features(context).items():
            groups[(trade['source'],feature,band)].append(trade['net_pnl_usd'])
    result=[]
    for (source,feature,band),values in sorted(groups.items()):
        mean=sum(values)/len(values)
        result.append({'source':source,'feature':feature,'band':band,'trades':len(values),
            'net_pnl_usd':sum(values),'mean_net_pnl_usd':mean,'wins':sum(value>0 for value in values),
            'losses':sum(value<0 for value in values),
            'review_status':'insufficient_sample' if len(values)<minimum_review_samples else
                'review_negative_expectancy' if mean<0 else 'descriptive_only'})
    return result


def adverse_live_cohorts(database,context,minimum_samples=10):
    """Block only matching live-entry cohorts with enough confirmed negative outcomes."""
    closed,_,_,_=live_outcomes(database)
    live=[trade for trade in closed if trade.get('source')=='bot_live']
    current=_feedback_features(context)
    return [row for row in outcome_feedback(live,minimum_samples)
        if row['review_status']=='review_negative_expectancy'
        and row['band']!='unknown'
        and current.get(row['feature'])==row['band']]


def report(live=None,paper=None,followed_wallet_db=None,followed_wallets=()):
    closed=[];opened=[];failures=[];unmatched=0
    for source,path in [(live_outcomes,live),(paper_outcomes,paper)]:
        if path and Path(path).exists():
            c,o,f,u=source(path);closed+=c;opened+=o;failures+=f;unmatched+=u
    groups=defaultdict(list)
    for trade in closed:
        if trade.get('training_eligible',True) is not True: continue
        groups[(trade['source'],trade.get('entry_profile') or 'unclassified')].append(trade['net_pnl_usd'])
    summaries=[]
    for (source,profile),values in groups.items():
        summaries.append({'source':source,'profile':profile,'trades':len(values),'net_pnl_usd':sum(values),
            'mean_net_pnl_usd':sum(values)/len(values),'wins':sum(v>0 for v in values),'losses':sum(v<0 for v in values),
            'worst_trade_usd':min(values)})
    learned_models={}
    external=[]
    if followed_wallet_db and Path(followed_wallet_db).exists():
        from wallet_watch import followed_wallet_outcomes
        external=followed_wallet_outcomes(followed_wallet_db,followed_wallets)
        model,metrics=train_followed_wallet_model(external)
        learned_models['followed_wallet_model']={'version':model['version'],
            'training_samples':model['samples'],'positive_outcomes':model['wins'],
            'negative_outcomes':model['losses'],'feature_weights':model['weights'],
            'followed_wallets':sorted(set(row['wallet'] for row in external)),
            'training_source':'configured_followed_wallets_only',
            'pnl_basis':'receipt_reconstructed_quote_currency_flat_to_flat',
            **metrics,'status':'insufficient_data_for_interpretation' if model['samples']<10 else 'shadow_only',
            'trains_live_model':True,'automatically_changes_entries':False}
    else:
        learned_models['followed_wallet_model']={'version':new_weighted_model()['version'],
            'training_samples':0,'positive_outcomes':0,'negative_outcomes':0,
            'feature_weights':new_weighted_model()['weights'],
            'followed_wallets':[],'training_source':'configured_followed_wallets_only',
            'pnl_basis':'receipt_reconstructed_quote_currency_flat_to_flat',
            'status':'no_followed_wallet_receipt_data','trains_live_model':True,
            'automatically_changes_entries':False}
    shared=train_shared_model(external,[t for t in closed if t['source']=='bot_live'],[t for t in closed if t['source']=='bot_paper'])
    learned_models['shared_model']=shared
    return {'closed_bot_trades':len(closed),'open_or_unfinished_bot_trades':len(opened),'failed_live_orders':len(failures),
            'failed_live_network_cost_usd':sum(f.get('network_cost_usd',0) for f in failures),
            'live_realized_pnl_including_failed_orders_usd':sum(t['net_pnl_usd'] for t in closed if t['source']=='bot_live')-sum(f.get('network_cost_usd',0) for f in failures),
            'failed_orders':failures,'unmatched_receipts':unmatched,
            'by_source_and_profile':summaries,'closed_trades':closed,'open_trades':opened,
            'outcome_feedback':outcome_feedback(closed),
            'weighted_learning':learned_models,
            'feedback_policy':{'minimum_review_samples':10,'live_and_paper_separated':True,
                'source_specific_outcome_cohorts':True,'bot_and_paper_outcomes_train_live_model':True,'refunded_paper_trades_train_model':False,
                'negative_live_cohorts_block_new_entries':True,
                'neural_selection_requires_prospective_qualification':True,
                'automatic_risk_or_position_size_changes':False},
            'automatic_live_changes':False,'validated_model':False,
            'limitations':'Eligible closed bot and paper outcomes train shared shadow weights; paper remains modeled, not confirmed performance. Followed-wallet labels require a complete forward-observed flat-to-flat receipt cycle. No prospective validation yet.'}


def export_report(live,paper,output,followed_wallet_db=None,followed_wallets=()):
    output=Path(output);data=report(live,paper,followed_wallet_db,followed_wallets)
    output.parent.mkdir(parents=True,exist_ok=True)
    temp=output.with_suffix('.tmp');temp.write_text(json.dumps(data,indent=2,allow_nan=False)+'\n');temp.replace(output)
    return data


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live-db',type=Path,default=Path('state/meme_live/meme_live.sqlite'))
    parser.add_argument('--paper-db',type=Path,default=Path('state/paper_volume/paper.sqlite'))
    parser.add_argument('--followed-wallet-db',type=Path,default=Path('state/wallet_watch/wallet_watch.sqlite'))
    parser.add_argument('--watchlist',type=Path,default=Path('paper_watchlist.json'))
    parser.add_argument('--output',type=Path,default=Path('state/research/bot_outcomes.json'))
    args=parser.parse_args()
    from wallet_signals import load_watchlist
    data=export_report(args.live_db,args.paper_db,args.output,args.followed_wallet_db,
        load_watchlist(args.watchlist))
    print(json.dumps({k:v for k,v in data.items() if k not in ('closed_trades','open_trades','failed_orders')},indent=2))


if __name__=='__main__': main()
