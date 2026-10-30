"""Compact paper console; full observations remain in report.json and SQLite."""
from collections import Counter


def format_report(report,events=()):
    lines=[]
    blockers=Counter()
    for event in events:
        action=event.get('action')
        mint=event.get('mint','')
        short=mint if len(mint)<=14 else mint[:6]+'…'+mint[-4:]
        if action=='paper_buy':
            lines.append(f"BUY {short} | ${event.get('budget_usd',0):.2f}")
        elif action=='paper_sell':
            lines.append(f"SELL {short} | net ${event['pnl_usd']:+.2f} | {event.get('reason','exit')}")
        elif action=='paper_balance_reset':
            lines.append('RESET | virtual balance refilled; completed trades retained for training')
        elif action in ('reject','watch'):
            reasons=event.get('reasons') or [event.get('reason')]
            blockers.update(reason for reason in reasons if reason)
    positions=report.get('positions') or ([report['position']] if report.get('position') else [])
    summary=(f"PAPER | equity ${report['marked_equity_usd']:.2f}"
             f" | realized ${report.get('realized_pnl_usd',0):+.2f}"
             f" | open {len(positions)}/{report.get('max_positions',1)}")
    training=report.get('neural_training')
    if report.get('drawdown_halted'):summary+=' | HALTED'
    remaining=max(0,int(report.get('loss_cooldown_until',0)-report.get('published_at',0)))
    if remaining:summary+=f' | cooldown {remaining}s'
    status=report.get('source_status') or {}
    if status.get('learning_error'):summary+=' | training unavailable'
    errors=status.get('sources') or {}
    if errors:summary+=f' | {len(errors)} source errors'
    elif status.get('status') not in (None,'ok','idle'):
        summary+=' | data: '+str(status['status'])
    lines.append(summary)
    if training:
        sources=training.get('source_samples',{})
        label='NN cached' if status.get('learning_error') else 'NN'
        lines.append(f"{label} | {training['samples']} samples | labels {training['wins']} positive / {training['losses']} negative"
            f" | paper {sources.get('bot_paper',0)} · live {sources.get('bot_live',0)} · wallets {sources.get('followed_wallet',0)}")
        exit_model=training.get('exit_model')
        if exit_model is not None:
            eligible=(exit_model['samples']>=30 and exit_model['trades']>=5
                and min(exit_model['wins'],exit_model['losses'])>=5)
            lines.append(f"EXIT NN | {exit_model['samples']}/30 quote pairs · {exit_model['trades']}/5 positions"
                f" | labels {exit_model['wins']} improving / {exit_model['losses']} worsening or flat"
                f" | {'eligible' if eligible else 'collecting data'}")
    if blockers and not remaining:
        lines.append('WATCH | '+'; '.join(f'{reason} ({count})' for reason,count in blockers.most_common(2)))
    return '\n'.join(lines)
