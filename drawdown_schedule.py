"""Shared bot-only bankroll promotion; no wallet balance or order operations."""
from meme_evidence import number


def drawdown_state(capital,pnl,peak_pnl,base_fraction,promotion_at=0,promoted_fraction=.30,total_bankroll=None,previous_tier="base",total_complete=True):
    capital=number(capital,.01);number(abs(pnl));number(abs(peak_pnl))
    number(base_fraction,0,1);number(promoted_fraction,0,1);number(promotion_at)
    bankroll=max(0.,capital+pnl)
    peak_bankroll=capital+max(0.,peak_pnl,pnl)
    if total_bankroll is not None:
        total_bankroll=number(total_bankroll)
        funded=total_bankroll>=promotion_at
        promoted=(previous_tier=='promoted') if not total_complete else promotion_at>0 and funded and (previous_tier=='promoted' or bankroll>=promotion_at)
    else: promoted=promotion_at>0 and bankroll>=promotion_at
    fraction=promoted_fraction if promoted else base_fraction
    # Opt-in promotion uses peak bot-bankroll drawdown percentages. Legacy
    # experiments with promotion disabled retain their fixed-capital limit.
    reference=total_bankroll if total_bankroll is not None else peak_bankroll if promotion_at>0 else capital
    return {'bot_bankroll_usd':bankroll,'peak_bot_bankroll_usd':peak_bankroll,
            'total_bankroll_usd':total_bankroll,'total_bankroll_complete':total_complete,'tier':'promoted' if promoted else 'base','active_drawdown_fraction':fraction,
            'drawdown_usd':peak_bankroll-bankroll,'limit_usd':reference*fraction,
            'promotion_bankroll_usd':promotion_at,'limit_basis':'total_bankroll' if total_bankroll is not None else 'peak_bot_bankroll' if promotion_at>0 else 'initial_bot_capital'}
