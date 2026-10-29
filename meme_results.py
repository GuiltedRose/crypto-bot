"""Read-only confirmed meme results; no training or live policy promotion."""
import argparse
import json
from pathlib import Path
import sqlite3


def results(path):
    db=sqlite3.connect('file:'+str(Path(path).resolve())+'?mode=ro',uri=True)
    try:
        rows=db.execute("SELECT kind,body FROM events WHERE kind IN ('confirmed_sell','transaction_failed','onchain_entry_risk','entry_scorecard') ORDER BY id").fetchall()
        state=json.loads(db.execute('SELECT body FROM state WHERE id=1').fetchone()[0])
    finally: db.close()
    closed=[];failed_cost=0.;scorecards=0;risks=0
    for kind,raw in rows:
        row=json.loads(raw)
        if kind=='confirmed_sell': closed.append(row['pnl_usd'])
        elif kind=='transaction_failed': failed_cost+=row['network_cost_usd']
        elif kind=='entry_scorecard': scorecards+=1
        elif kind=='onchain_entry_risk': risks+=1
    wins=[pnl for pnl in closed if pnl>0];losses=[pnl for pnl in closed if pnl<0]
    return {'closed_trades':len(closed),'wins':len(wins),'losses':len(losses),'breakeven':sum(pnl==0 for pnl in closed),
            'win_rate':len(wins)/len(closed) if closed else None,
            'average_net_trade_usd':sum(closed)/len(closed) if closed else None,
            'average_win_usd':sum(wins)/len(wins) if wins else None,
            'average_loss_usd':sum(losses)/len(losses) if losses else None,
            'failed_transaction_cost_usd':failed_cost,'realized_pnl_usd':state['realized_pnl_usd'],
            'position_open':state['position'] is not None,'unresolved_order':state['pending'] is not None,
            'shadow_scorecards':scorecards,'onchain_risk_checks':risks,
            'positive_edge_established':False,
            'note':'Descriptive confirmed outcomes only; open inventory is not included in realized P&L. No holdout validation or auto-promotion.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir',type=Path,default=Path('state/meme_live'))
    args=parser.parse_args()
    report=results(args.state_dir/'meme_live.sqlite')
    manual_path=Path('state/manual_research/summary.json')
    if manual_path.exists():
        try:
            manual=json.loads(manual_path.read_text())
            report['manual_research']={k:manual.get(k) for k in ('username','transactions_archived','known_pump_trades_nonbot','known_bot_orders_archived','known_pump_trades_bot','research_trades_total','pending_receipts','requested_period_traversed','profit_labels_verified')}
        except (OSError,ValueError,TypeError): report['manual_research']={'status':'unavailable'}
    print(json.dumps(report,indent=2))


if __name__=='__main__': main()
