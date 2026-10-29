"""Fail-closed meme entry evidence. This validates supplied evidence, not truth.

Collectors must independently verify their sources. Never infer zero bundles
from a provider that does not measure them, or from an empty transaction list.
"""
from dataclasses import dataclass
import json
import math
from pathlib import Path
from urllib.parse import urlparse


def number(value, low=0, high=math.inf):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('Expected numeric evidence')
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError('Evidence out of range')
    return value


@dataclass(frozen=True)
class MemeEvidencePolicy:
    require_social_corroboration: bool = True
    max_age_seconds: int = 300
    min_sources: int = 3
    min_unique_authors: int = 20
    min_organic_engagement_pct: float = 70
    min_coverage_pct: float = 95
    max_linked_supply_pct: float = 10
    min_independent_buyers: int = 20
    max_suspected_bot_buy_volume_pct: float = 30
    max_wash_volume_pct: float = 10
    max_price_change_1h_pct: float = 50
    max_liquidity_drop_1h_pct: float = 10

    def assess(self, mint, pool, evidence, now):
        reasons = []
        try:
            number(now)
            if evidence['mint'] != mint or evidence['pool'] != pool:
                reasons.append('evidence mint/pool mismatch')
            checked = number(evidence['observed_at'])
            if not 0 <= now - checked <= self.max_age_seconds:
                reasons.append('evidence stale or future dated')
            for section in ('narrative', 'distribution', 'trading'):
                item = evidence[section]
                observed = number(item['observed_at'])
                if not 0 <= now - observed <= self.max_age_seconds:
                    reasons.append(section + ' evidence stale or future dated')
                source = urlparse(item['source_url'])
                if source.scheme != 'https' or not source.hostname:
                    raise ValueError('Evidence source URL required')
            narrative = evidence['narrative']
            if not isinstance(narrative['topic'], str) or not narrative['topic'].strip():
                reasons.append('narrative topic missing')
            if self.require_social_corroboration:
                sources = narrative['independent_sources']
                if not isinstance(sources, list) or any(not isinstance(s, str) or not s.strip() for s in sources):
                    raise ValueError('Invalid narrative sources')
                if len({s.strip().lower() for s in sources}) < self.min_sources:
                    reasons.append('insufficient independent narrative sources')
                if number(narrative['unique_authors']) < self.min_unique_authors:
                    reasons.append('insufficient narrative participation')
                if number(narrative['organic_engagement_pct'], 0, 100) < self.min_organic_engagement_pct:
                    reasons.append('narrative engagement appears coordinated')
            distribution = evidence['distribution']
            if distribution['launch_history_complete'] is not True:
                reasons.append('launch/bundle history incomplete')
            if number(distribution['supply_coverage_pct'], 0, 100) < self.min_coverage_pct:
                reasons.append('insufficient supply analysis coverage')
            if number(distribution['bundled_supply_pct'], 0, 100) > 0:
                reasons.append('bundled supply detected')
            if number(distribution['linked_supply_pct'], 0, 100) > self.max_linked_supply_pct:
                reasons.append('linked-wallet supply concentration')
            trading = evidence['trading']
            if number(trading['buyer_analysis_coverage_pct'], 0, 100) < self.min_coverage_pct:
                reasons.append('insufficient buyer analysis coverage')
            if number(trading['independent_buyers']) < self.min_independent_buyers:
                reasons.append('insufficient independent buying after wallet clustering')
            if number(trading['suspected_bot_buy_volume_pct'], 0, 100) > self.max_suspected_bot_buy_volume_pct:
                reasons.append('buying dominated by suspected automation')
            if number(trading['wash_volume_pct'], 0, 100) > self.max_wash_volume_pct:
                reasons.append('suspected wash trading')
            if number(trading['price_change_1h_pct'], -100) > self.max_price_change_1h_pct:
                reasons.append('price spike; avoid chasing pump')
            if number(trading['liquidity_drop_1h_pct'], 0, 100) > self.max_liquidity_drop_1h_pct:
                reasons.append('liquidity withdrawal detected')
        except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
            reasons.append('required meme evidence missing or invalid')
        return {'passed': not reasons, 'reasons': reasons, 'checked_at': now,
                'note': 'Supplied evidence only; source independence and bundle coverage require a verified collector. Passing does not prove absence of manipulation.'}


def load_meme_evidence(mint, pool, now, root='data/markets'):
    # Mints come from the validated registry; reject paths even for direct callers.
    if not isinstance(mint, str) or not mint.isalnum():
        return {'passed': False, 'reasons': ['invalid evidence mint']}
    try:
        evidence = json.loads((Path(root) / mint / 'meme_evidence.json').read_text())
    except (OSError, ValueError):
        evidence = {}
    return MemeEvidencePolicy().assess(mint, pool, evidence, now)
