"""Recent closed-trade performance, separate from lifetime learning labels."""
from pathlib import Path
from meme_learning import live_outcomes,paper_outcomes,_finite


def rolling_performance(path,source,limit=20):
    if source not in ('bot_paper','bot_live') or type(limit) is not int or limit<1:
        raise ValueError('Valid trade source and positive window required')
    trades=[];failures=[]
    if Path(path).exists():
        trades,_,failures,_=(paper_outcomes if source=='bot_paper' else live_outcomes)(path)
    eligible=[];seen=set()
    for row in sorted(trades,key=lambda row:(row['exit_at'],row['exit_signature']),reverse=True):
        if (row.get('training_eligible',True) is not True or not _finite(row.get('net_pnl_usd'))
            or row['exit_signature'] in seen):continue
        seen.add(row['exit_signature']);eligible.append(row)
    all_trades=eligible
    eligible=all_trades[:limit]
    lifetime_fees=sum(row['network_cost_usd'] for row in failures
        if _finite(row.get('network_cost_usd')) and row['network_cost_usd']>=0)
    lifetime_pnl=sum(row['net_pnl_usd'] for row in all_trades)
    lifetime_wins=sum(row['net_pnl_usd']>0 for row in all_trades)
    lifetime={'trades':len(all_trades),'wins':lifetime_wins,
        'losses':sum(row['net_pnl_usd']<0 for row in all_trades),
        'breakeven':sum(row['net_pnl_usd']==0 for row in all_trades),
        'win_rate':lifetime_wins/len(all_trades) if all_trades else None,
        'closed_trade_net_usd':lifetime_pnl,'failed_order_cost_usd':lifetime_fees,
        'net_pnl_usd':lifetime_pnl-lifetime_fees}
    count=len(eligible);pnl=sum(row['net_pnl_usd'] for row in eligible)
    start=min((row['entry_at'] for row in eligible),default=None)
    failed_cost=sum(row['network_cost_usd'] for row in failures if start is not None
        and row['observed_at']>=start and _finite(row.get('network_cost_usd')) and row['network_cost_usd']>=0)
    wins=sum(row['net_pnl_usd']>0 for row in eligible)
    return {'lifetime':lifetime,'source':source,'window':limit,'trades':count,'wins':wins,
            'losses':sum(row['net_pnl_usd']<0 for row in eligible),
            'breakeven':sum(row['net_pnl_usd']==0 for row in eligible),
            'win_rate':wins/count if count else None,'closed_trade_net_usd':pnl,
            'failed_order_cost_usd':failed_cost,'net_pnl_usd':pnl-failed_cost,
            'since':start,'through':max((row['exit_at'] for row in eligible),default=None),
            'scope':'latest valid closed trades across runs/models; failure fees since earliest included entry'}


def _format_window(metrics):
    rate=f"{metrics['win_rate']:.1%}" if metrics['win_rate'] is not None else 'n/a'
    return (f"LAST {metrics['window']} | {metrics['trades']}/{metrics['window']} closed"
            f" | net ${metrics['net_pnl_usd']:+.2f} | win {rate}"
            f" ({metrics['wins']}W/{metrics['losses']}L/{metrics['breakeven']} flat)")


def format_rolling(metrics):
    recent=_format_window(metrics)
    total=metrics.get('lifetime')
    if total is None:return recent
    rate=f"{total['win_rate']:.1%}" if total['win_rate'] is not None else 'n/a'
    lifetime=(f"LIFETIME | {total['trades']} closed | net ${total['net_pnl_usd']:+.2f} | win {rate}"
              f" ({total['wins']}W/{total['losses']}L/{total['breakeven']} flat)")
    return lifetime+'\n'+recent
