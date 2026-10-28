"""Offline Solana spot paper trader. No wallet access or live order submission."""
import argparse
import csv
import json
import math
from dataclasses import dataclass

SOL_MINT = "So11111111111111111111111111111111111111112"


@dataclass(frozen=True)
class Universe:
    # Mint identities, not token names/symbols. Additional tokens need review.
    established_mints: frozenset = frozenset({SOL_MINT})
    meme_mints: frozenset = frozenset()
    meme_min_equity_usd: float = 5000.0
    max_active_meme_coins: int = 2

    def eligible(self, mint, equity, active_mints=()):
        """Check new entries; active_mints must include pending buy orders.

        Existing token top-ups do not add another coin. Callers must serialize
        this check with position reservation when live execution is added.
        """
        if not math.isfinite(equity) or equity <= 0:
            return False, "invalid equity"
        if mint in self.meme_mints:
            if equity < self.meme_min_equity_usd:
                return False, "meme coins blocked below $5,000 equity"
            active_memes = set(active_mints) & self.meme_mints
            if mint not in active_memes and len(active_memes) >= self.max_active_meme_coins:
                return False, "maximum two active meme coins"
            return True, "reviewed meme token eligible"
        if mint in self.established_mints:
            return True, "approved established token"
        return False, "unreviewed token mint"


@dataclass(frozen=True)
class Risk:
    quarter_kelly: float = 0.25
    position_cap: float = 0.05
    reserve_usd: float = 1.0
    drawdown_limit: float = 0.10
    min_samples: int = 100
    fee_per_side: float = 0.0025
    slippage_per_side: float = 0.005
    fixed_round_trip_usd: float = 0.01


def size(equity, history, risk=Risk()):
    """Binary-outcome Kelly using a conservative win-probability bound.

    History contains gross fractional returns from completed strategy trades.
    Caps assume the entire position can be lost, rather than trusting stops.
    """
    if not math.isfinite(equity) or equity <= risk.reserve_usd:
        return 0.0, "reserve"
    if len(history) < risk.min_samples:
        return 0.0, "insufficient completed trades"
    if any(not math.isfinite(r) or r < -1 for r in history):
        raise ValueError("Returns must be finite and >= -1")
    wins = [r for r in history if r > 0]
    losses = [-r for r in history if r <= 0]
    if not wins or not losses or sum(losses) == 0:
        return 0.0, "need both winning and losing observations"
    n = len(history)
    p = len(wins) / n
    # Wilson lower 95% confidence bound; does not address regime changes.
    z = 1.96
    p = (p + z*z/(2*n) - z*math.sqrt(p*(1-p)/n + z*z/(4*n*n))) / (1+z*z/n)
    gain, loss = sum(wins)/len(wins), sum(losses)/len(losses)
    ceiling = min(equity*risk.position_cap, equity-risk.reserve_usd)
    cost = 2*(risk.fee_per_side+risk.slippage_per_side) + risk.fixed_round_trip_usd/ceiling
    net_gain, net_loss = gain-cost, loss+cost
    if net_gain <= 0 or p*net_gain-(1-p)*net_loss <= 0:
        return 0.0, "no conservative edge after costs"
    # f is allocation to an asset returning +gain or -loss, not a stop-risk budget.
    kelly = p/net_loss-(1-p)/net_gain
    amount = min(ceiling, equity*risk.quarter_kelly*max(0, kelly))
    # Recheck fixed costs at the actual, potentially smaller allocation.
    actual_cost = 2*(risk.fee_per_side+risk.slippage_per_side)+risk.fixed_round_trip_usd/amount if amount else math.inf
    if p*gain-(1-p)*loss <= actual_cost:
        return 0.0, "allocation too small for costs"
    return amount, "quarter Kelly with full-position loss cap"


def replay(prices, starting=4.0, risk=Risk(), mint=SOL_MINT, universe=Universe()):
    """Long on positive 20-bar momentum; exit next bar. One position at a time.

    Each observation is available only after its exit. Initial observations
    are shadow trades, allowing warmup without allocating paper capital.
    """
    if not math.isfinite(starting) or starting <= 0:
        raise ValueError("Starting balance must be positive and finite")
    if len(prices) < 22 or any(not math.isfinite(p) or p <= 0 for p in prices):
        raise ValueError("Need at least 22 positive finite prices")
    equity = peak = starting
    history, trades = [], []
    halted = False
    for i in range(20, len(prices)-1):
        if prices[i] <= prices[i-20]:
            continue
        eligible, reason = universe.eligible(mint, equity)
        if halted:
            amount, reason = 0, "drawdown halt"
        elif not eligible:
            amount = 0
        else:
            amount, reason = size(equity, history, risk)
        gross = prices[i+1]/prices[i]-1
        pnl = 0.0
        if amount:
            pnl = amount*(gross-2*(risk.fee_per_side+risk.slippage_per_side))-risk.fixed_round_trip_usd
            equity += pnl
            peak = max(peak, equity)
            halted = halted or equity <= peak*(1-risk.drawdown_limit)
        trades.append(dict(bar=i, allocation_usd=amount, pnl_usd=pnl, equity_usd=equity, reason=reason))
        history.append(gross)
    return dict(mode="paper", starting_usd=starting, ending_usd=equity, halted=halted, trades=trades)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", help="Chronologically ordered CSV with timestamp,close columns")
    parser.add_argument("--balance", type=float, default=4.0)
    parser.add_argument("--mint", default=SOL_MINT, help="Exact base-token mint; defaults to wrapped SOL")
    args = parser.parse_args()
    with open(args.csv, newline="") as source:
        rows = list(csv.DictReader(source))
    stamps = [row["timestamp"] for row in rows]
    if any(a >= b for a, b in zip(stamps, stamps[1:])):
        parser.error("timestamps must be unique, ascending, consistently formatted ISO-8601 UTC")
    print(json.dumps(replay([float(row["close"]) for row in rows], args.balance, mint=args.mint), indent=2))


if __name__ == "__main__":
    main()
