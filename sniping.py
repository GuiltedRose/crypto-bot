"""Capital gate for a future new-launch meme strategy.

Excluded from the candle optimizer. Reaching $5k alone does not enable an
unvalidated feed/adapter or bypass the existing portfolio and screening rules.
"""
import math

CAPITAL_THRESHOLD_USD=5000.0
MAX_ACTIVE_MEMES=2


def admission(equity_usd,*,equity_verified=False,open_positions=0,position_capacity=1,
              active_meme_coins=0,launch_feed_validated=False,execution_adapter_validated=False,
              venue_eligible=False,token_screen_passed=False,review_halted=False,drawdown_halted=False):
    if not math.isfinite(equity_usd) or equity_usd<0:return False,'invalid wallet equity'
    if not equity_verified:return False,'verified wallet equity required'
    if equity_usd<CAPITAL_THRESHOLD_USD:return False,'sniping locked below $5,000 wallet equity'
    if review_halted or drawdown_halted:return False,'portfolio halt remains active'
    if type(position_capacity) is not int or position_capacity not in (1,2):return False,'invalid position capacity'
    if type(open_positions) is not int or type(active_meme_coins) is not int or min(open_positions,active_meme_coins)<0:
        return False,'invalid portfolio snapshot'
    if open_positions>=position_capacity:return False,'global open-position limit reached'
    if active_meme_coins>=MAX_ACTIVE_MEMES:return False,'maximum two active meme coins'
    if not launch_feed_validated or not execution_adapter_validated:
        return False,'validated launch feed and execution adapter required'
    if not venue_eligible:return False,'eligible venue required'
    if not token_screen_passed:return False,'new-token risk screening required'
    return True,'capital and admission gates passed; normal sizing and transaction checks still required'


def status(equity_usd):
    unlocked=math.isfinite(equity_usd) and equity_usd>=CAPITAL_THRESHOLD_USD
    return {'strategy':'new-meme-sniping','capital_threshold_usd':CAPITAL_THRESHOLD_USD,
            'capital_unlocked':unlocked,'live_execution_enabled':False,'max_active_meme_coins':MAX_ACTIVE_MEMES,
            'reason':'validated launch feed, token screening and execution adapter pending' if unlocked else 'locked below $5,000 verified wallet equity',
            'optimizer_can_change_threshold':False}
