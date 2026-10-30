"""Paper-only settled bankroll milestones; virtual funding is never trading P&L."""
import math

RESET_FLOOR_USD=6.
RESET_RETAINED_FRACTION=.85


def confirmed_milestone(bankroll):
    if not math.isfinite(bankroll) or bankroll<0:
        raise ValueError('Finite nonnegative settled bankroll required')
    result=RESET_FLOOR_USD
    scale=10.
    while scale<=bankroll:
        for multiple in (1,2.5,5):
            milestone=scale*multiple
            if milestone<=bankroll:result=max(result,milestone)
        scale*=10
    return result


def reset_target(reference):
    if not math.isfinite(reference) or reference<0:
        raise ValueError('Finite nonnegative reset reference required')
    return round(max(RESET_FLOOR_USD,reference*RESET_RETAINED_FRACTION),2)
