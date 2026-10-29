"""Drawdown attribution to confirmed bot P&L and journaled bot units only."""
import json
from meme_evidence import number


def mark_bot_risk(store,wallet_equity,drawdown_fraction,open_pnl=0.,has_orders=False,promotion_at=0,promoted_fraction=.30,total_bankroll=None,total_complete=True):
    wallet_equity=number(wallet_equity);s=store.state
    realized=s['realized_pnl_usd']
    risk=s.get('strategy_risk')
    if risk is None:
        first=store.db.execute("SELECT body FROM events WHERE kind='portfolio_mark' ORDER BY id LIMIT 1").fetchone()
        capital=json.loads(first[0]).get('equity_usd') if first else wallet_equity
        capital=max(.01,number(capital))
        total=peak=0.;activity=bool(s['position'] or s['pending'] or realized or has_orders)
        for kind,raw in store.db.execute("SELECT kind,body FROM events WHERE kind IN ('confirmed_buy','confirmed_sell','transaction_failed') ORDER BY id"):
            activity=True;event=json.loads(raw)
            if kind=='confirmed_sell': total+=event['pnl_usd']
            if kind=='transaction_failed': total-=event['network_cost_usd']
            peak=max(peak,total)
        risk={'version':1,'scope':'bot_positions','capital_usd':capital,'peak_pnl_usd':max(peak,realized),
              'drawdown_usd':0.,'bot_pnl_usd':realized,'legacy_halt_preserved':bool(s['halted'] and activity)}
        # A historical wallet halt can be cleared only when there is no bot
        # activity to have caused it. This migration is performed on user launch,
        # never by an external process editing a running trader's state.
        previous=bool(s['halted']);s['halted']=risk['legacy_halt_preserved'];s['strategy_risk']=risk
        store.save('drawdown_scope_migrated',{'previous_wallet_halt':previous,'preserved_halt':s['halted'],
                   'bot_activity_present':activity,'scope':'bot_positions','capital_usd':capital})
    if type(open_pnl) not in (int,float): raise ValueError('Finite bot-position PnL required')
    number(abs(open_pnl))
    pnl=realized+open_pnl
    risk['peak_pnl_usd']=max(risk['peak_pnl_usd'],pnl)
    from drawdown_schedule import drawdown_state
    previous_tier=risk.get('tier','base')
    risk['bot_pnl_usd']=pnl
    if total_bankroll is not None and not total_complete:
        total_bankroll=risk.get('last_complete_total_bankroll_usd',risk['capital_usd'])
    elif total_bankroll is not None: risk['last_complete_total_bankroll_usd']=total_bankroll
    risk.update(drawdown_state(risk['capital_usd'],pnl,risk['peak_pnl_usd'],drawdown_fraction,promotion_at,promoted_fraction,total_bankroll,previous_tier,total_complete))
    if previous_tier!=risk['tier']:
        store.save('drawdown_tier_changed',{'previous_tier':previous_tier,'new_tier':risk['tier'],
            'bot_bankroll_usd':risk['bot_bankroll_usd'],'active_drawdown_fraction':risk['active_drawdown_fraction'],'halt_preserved':s['halted']})
    risk['promotion_valuation_pending']=not total_complete and promotion_at>0 and (risk['tier']=='promoted' or risk['bot_bankroll_usd']>=promotion_at)
    s['halted']=s['halted'] or (risk['drawdown_usd']>0 and risk['drawdown_usd']>=risk['limit_usd'])
    store.save('portfolio_mark',{'equity_usd':wallet_equity,'halted':s['halted'],'halt_scope':'bot_positions',
                                'bot_pnl_usd':pnl,'strategy_drawdown_usd':risk['drawdown_usd'],'strategy_limit_usd':risk['limit_usd'],'active_drawdown_fraction':risk['active_drawdown_fraction'],'bot_bankroll_usd':risk['bot_bankroll_usd'],'drawdown_tier':risk['tier']})
    return risk
