"""Conservative mature-meme screening. Passing is not a rug-proof guarantee."""
from dataclasses import dataclass
from datetime import datetime,timezone
import math


@dataclass(frozen=True)
class MatureMemePolicy:
    token_observed_days: int = 180
    pool_age_days: int = 30
    min_liquidity_usd: float = 500000
    min_daily_volume_usd: float = 100000
    max_holder_pct: float = 20
    max_top_ten_pct: float = 60
    min_locked_lp_pct: float = 80
    max_normalized_risk: float = 15
    valid_seconds: int = 3600

    def assess(self,asset,pool,report,now):
        reasons=[]
        def number(value):
            value=float(value)
            if not math.isfinite(value):
                raise ValueError('Nonfinite screening value')
            return value
        try:
            if report['mint']!=asset['mint'] or report['rugged'] is not False:
                reasons.append('wrong mint or reported rug')
            token=report['token']
            if any(value is not None for value in (report['mintAuthority'],report['freezeAuthority'],
                                                   token['mintAuthority'],token['freezeAuthority'])):
                reasons.append('mint or freeze authority remains enabled')
            if report['tokenProgram'] not in ('TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA','TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb') or not token['isInitialized']:
                reasons.append('unsupported token program/state')
            observed=datetime.fromisoformat(report['detectedAt'].replace('Z','+00:00')).timestamp()
            created=datetime.fromisoformat(pool['pool_created_at'].replace('Z','+00:00')).timestamp()
            if now-observed<self.token_observed_days*86400:
                reasons.append('insufficient established token history')
            if now-created<self.pool_age_days*86400:
                reasons.append('pool too new')
            if number(pool['liquidity_usd'])<self.min_liquidity_usd:
                reasons.append('pool liquidity too low')
            if number(pool['volume_24h_usd'])<self.min_daily_volume_usd:
                reasons.append('pool volume too low')
            if number(report['score_normalised'])>self.max_normalized_risk:
                reasons.append('risk score too high')
            if any(str(r.get('level','')).lower() in ('danger','high','critical') for r in report['risks']):
                reasons.append('high severity risk warning')
            holders=sorted((number(h['pct']) for h in report['topHolders']),reverse=True)
            if len(holders)<10 or any(p<0 or p>100 for p in holders):
                reasons.append('holder coverage unavailable')
            elif holders[0]>self.max_holder_pct or sum(holders[:10])>self.max_top_ten_pct:
                reasons.append('holder concentration too high')
            market=next((m for m in report['markets'] if m['pubkey']==pool['pool']),None)
            if market is None or number(market['lp']['lpLockedPct'])<self.min_locked_lp_pct:
                reasons.append('selected-pool LP lock/burn evidence insufficient')
        except (ValueError,KeyError,TypeError,OverflowError):
            reasons.append('required screening data missing or invalid')
        return {'passed':not reasons,'reasons':reasons,'checked_at':now,
                'valid_until':now+self.valid_seconds,'mint':asset['mint'],
                'note':'Third-party risk data with conservative policy thresholds, not a guarantee against rugs or proof of executable liquidity.'}
