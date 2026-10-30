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


def decision_context(snapshot,rules,scorecard=None,signal=None,risk=None,entry_profile=None):
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
    if isinstance(risk,dict):
        context['onchain_risk']={key:risk.get(key) for key in
            ('passed','status','reasons','coverage','largest_sampled_owner_pct','top10_sampled_owners_pct',
             'busiest_buyer_share','busiest_slot_buy_share')}
    else:
        context['onchain_risk']=None
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


def paper_outcomes(path):
    db=sqlite3.connect('file:'+str(Path(path).resolve())+'?mode=ro',uri=True)
    result=[];unclosed=[];unmatched=0
    try:
        for run, in db.execute('SELECT id FROM paper_runs ORDER BY created').fetchall():
            opened=None
            for at,event_raw,snapshot_raw in db.execute('SELECT observed,event,snapshot FROM paper_events WHERE run_id=? ORDER BY sequence',(run,)):
                event=json.loads(event_raw);snapshot=json.loads(snapshot_raw)
                if event['action']=='paper_buy':
                    if opened: unmatched+=1
                    opened={'source':'bot_paper','run_id':run,'mint':event['mint'],'entry_at':at,
                            'entry_profile':event.get('entry_profile'),'entry_context':snapshot,'status':'open'}
                elif event['action']=='paper_sell':
                    if not opened or opened['mint']!=event['mint']: unmatched+=1;continue
                    result.append({**opened,'exit_at':at,'net_pnl_usd':event['pnl_usd'],'exit_reason':event.get('reason'),
                                   'status':'closed','label_kind':'modeled_paper_pnl'})
                    opened=None
            if opened: unclosed.append(opened)
    finally: db.close()
    return result,unclosed,[],unmatched


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


def report(live=None,paper=None):
    closed=[];opened=[];failures=[];unmatched=0
    for source,path in [(live_outcomes,live),(paper_outcomes,paper)]:
        if path and Path(path).exists():
            c,o,f,u=source(path);closed+=c;opened+=o;failures+=f;unmatched+=u
    groups=defaultdict(list)
    for trade in closed: groups[(trade['source'],trade.get('entry_profile') or 'unclassified')].append(trade['net_pnl_usd'])
    summaries=[]
    for (source,profile),values in groups.items():
        summaries.append({'source':source,'profile':profile,'trades':len(values),'net_pnl_usd':sum(values),
            'mean_net_pnl_usd':sum(values)/len(values),'wins':sum(v>0 for v in values),'losses':sum(v<0 for v in values),
            'worst_trade_usd':min(values)})
    return {'closed_bot_trades':len(closed),'open_or_unfinished_bot_trades':len(opened),'failed_live_orders':len(failures),
            'failed_live_network_cost_usd':sum(f.get('network_cost_usd',0) for f in failures),
            'live_realized_pnl_including_failed_orders_usd':sum(t['net_pnl_usd'] for t in closed if t['source']=='bot_live')-sum(f.get('network_cost_usd',0) for f in failures),
            'failed_orders':failures,'unmatched_receipts':unmatched,
            'by_source_and_profile':summaries,'closed_trades':closed,'open_trades':opened,
            'outcome_feedback':outcome_feedback(closed),
            'feedback_policy':{'minimum_review_samples':10,'live_and_paper_separated':True,
                'automatic_entry_or_risk_changes':False},
            'automatic_live_changes':False,'validated_model':False,
            'limitations':'Descriptive decision/outcome data; paper and live labels are separate. No fresh holdout/prospective model validation yet. Manual receipts need independent cost/rent reconstruction before profit labels.'}


def export_report(live,paper,output):
    output=Path(output);data=report(live,paper)
    output.parent.mkdir(parents=True,exist_ok=True)
    temp=output.with_suffix('.tmp');temp.write_text(json.dumps(data,indent=2,allow_nan=False)+'\n');temp.replace(output)
    return data


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live-db',type=Path,default=Path('state/meme_live/meme_live.sqlite'))
    parser.add_argument('--paper-db',type=Path,default=Path('state/paper_volume/paper.sqlite'))
    parser.add_argument('--output',type=Path,default=Path('state/research/bot_outcomes.json'))
    args=parser.parse_args();data=export_report(args.live_db,args.paper_db,args.output)
    print(json.dumps({k:v for k,v in data.items() if k not in ('closed_trades','open_trades','failed_orders')},indent=2))


if __name__=='__main__': main()
