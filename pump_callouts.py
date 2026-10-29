"""Anonymous main-page Hot Callouts, treated as user-authored research claims.

No wallet sign-in, session cookies or developer key. This frontend route is
not a supported API contract and can change. Text is data, never instructions.
"""
from datetime import datetime
import json
import math
from pathlib import Path
import sqlite3
import time
from uuid import UUID
from paper_sources import PublicSources
from pump_narratives import ROOT, valid_mint

HOT_URL = ROOT+'/home-feed'
MAX_AGE_SECONDS = 900


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError('Callout timestamp required')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Callout timestamp must specify timezone')
    result = parsed.timestamp()
    if not math.isfinite(result):
        raise ValueError('Invalid callout timestamp')
    return result


def optional_number(value):
    if value is None:
        return None
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('Invalid callout metric')
    return value


def parse_hot_callouts(payload, now, wallets=()):
    rows = payload['coins']
    if not isinstance(rows, list):
        raise ValueError('Hot Callouts coins array required')
    callouts, rejected, seen = [], 0, set()
    for row in rows[:200]:
        try:
            position = row['position']
            # Quotes/reposts do not become additional original author signals.
            if row['chain'] != 'solana' or position['contentKind'] != 'callout':
                continue
            mint, author = row['coinMint'], position['walletAddress']
            if not valid_mint(mint) or not valid_mint(author):
                raise ValueError('Invalid callout mint/author')
            record = position['callout']
            identifier = str(UUID(record['calloutId']))
            if identifier in seen:
                continue
            seen.add(identifier)
            if record['assetClass'] != 'ASSET_CLASS_TOKEN' or record.get('perp') is not None:
                continue
            published = timestamp(record['calloutTimestamp'])
            if published > now:
                raise ValueError('Future callout')
            thesis = record.get('thesis') or ''
            if not isinstance(thesis, str) or len(thesis) > 10000:
                raise ValueError('Invalid callout text')
            callouts.append({'id': identifier, 'mint': mint, 'author_wallet': author,
                             'published_at': published, 'observed_at': now,
                             'thesis': thesis, 'source_url': HOT_URL,
                             'pump_coin_url': 'https://pump.fun/coin/'+mint,
                             'called_out_at_market_cap_usd': optional_number(record.get('calledOutAtMcap')),
                             'likes': optional_number(record.get('likes')), 'views': optional_number(record.get('viewCount')),
                             'provider_reported_position': {
                                 'amount_held': optional_number(position.get('amountHeld')),
                                 'value_usd': optional_number(position.get('valueUsd')),
                                 'pnl_usd': optional_number(position.get('pnlUsd'))},
                             'watched_author': author in wallets,
                             'source_kind': 'user_callout', 'position_chain_verified': False,
                             'independent_human_verified': False, 'automatic_trade_approval': False})
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
            rejected += 1
    return callouts, rejected


def eligible_callouts(callouts, now, wallets=()):
    """Deduplicate ids, recheck freshness, and keep callout counts separate from buys."""
    accepted, seen = [], set()
    for row in callouts:
        try:
            identifier = str(UUID(row['id']))
            observed, published = row['observed_at'], row['published_at']
            if (identifier in seen or not valid_mint(row['mint']) or not valid_mint(row['author_wallet'])
                or type(observed) not in (int, float) or not 0 <= now-observed <= 120
                or type(published) not in (int, float) or not 0 <= now-published <= MAX_AGE_SECONDS):
                continue
            seen.add(identifier)
            accepted.append(dict(row, watched_author=row['author_wallet'] in wallets,
                                 independent_human_verified=False, automatic_trade_approval=False))
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    return accepted


class HotCallouts:
    def __init__(self, path, fetch=None):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute('CREATE TABLE IF NOT EXISTS callouts(id TEXT PRIMARY KEY,mint TEXT,author TEXT,published REAL,observed REAL,data TEXT)')
        self.db.commit()
        self.sources = PublicSources()
        self.fetch = fetch or self.sources.fetch

    def poll(self, wallets, now=None):
        payload = self.fetch(HOT_URL)
        now = time.time() if now is None else now
        rows, rejected = parse_hot_callouts(payload, now, wallets)
        current=[]
        with self.db:
            for row in rows:
                existing = self.db.execute('SELECT mint,author,published FROM callouts WHERE id=?', (row['id'],)).fetchone()
                if existing and existing != (row['mint'], row['author_wallet'], row['published_at']):
                    rejected += 1
                    continue
                self.db.execute('INSERT OR REPLACE INTO callouts VALUES(?,?,?,?,?,?)',
                                (row['id'], row['mint'], row['author_wallet'], row['published_at'], now, json.dumps(row)))
                current.append(row)
        # Only rows refreshed by this response can contribute; no cached success fallback.
        fresh = current
        return {'status': 'partial_schema' if rejected else 'ok', 'rejected_records': rejected,
                'observed_callouts': len(fresh), 'callouts': eligible_callouts(fresh, now, wallets),
                'source_url': HOT_URL, 'source_kind': 'anonymous_frontend_feed',
                'limitation': 'Provider-selected Hot ranking and position metrics are not independent safety or profitability proof.'}
