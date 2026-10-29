"""Read-only decision/outcome research for bot orders, separately from manual data.

Reports empirical patterns; does not rewrite funded rules, reset losses, hide
open trades, or turn a few profitable outcomes into a validated model.
"""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import sqlite3


def decision_context(snapshot,rules,scorecard=None):
    fields=('mint','pool','observed_at','price_usd','market_kind','buy_volume_usd','sell_volume_usd','liquidity_usd',
            'buy_volume_window_seconds','buy_volume_coverage_pct')
    context={key:snapshot.get(key) for key in fields}
    history=snapshot.get('candle_history',{})
    context['completed_candles']=[{key:row.get(key) for key in ('timestamp','open','high','low','close','volume_usd')}
        for row in history.get('candles',[])[-12:] if isinstance(row,dict)]
    context['scorecard']=scorecard
    context['limits']={key:getattr(rules,key) for key in ('max_position_usd','take_profit_usd','max_loss_usd','slippage_per_side','drawdown_halt')}
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
