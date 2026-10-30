"""Keyless public observations for paper trading; no signing or wallet imports.

Pump.fun supplies discovery metadata. GeckoTerminal supplies provider market
snapshots for graduated PumpSwap pools. Safety/originality/ATH proofs remain
separate timestamped inputs; an incomplete source never becomes a passing check.
"""
from datetime import datetime
import json
import socket
import ssl
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, urlencode
from urllib.request import Request, build_opener
from meme_evidence import number
from data_policy import require_permitted_url,SourceAccessBlocked
from provider_limits import BUDGET,ProviderBackoff
from pump_narratives import ROOT, NoRedirect, parse_coin, valid_mint

class UnsupportedMarket(ValueError):
    """Known support boundary, not a failed connection or bad data response."""


class SourceCooldown(ValueError):
    def __init__(self, seconds):
        self.seconds = max(1, int(seconds))
        super().__init__('Public provider cooldown active')


GECKO = 'https://api.geckoterminal.com/api/v2/networks/solana'


class PublicSources:
    def __init__(self, evidence_dir=None, fixed_cost_per_side_usd=.001, fetch=None, interval=3):
        self.evidence_dir = Path(evidence_dir) if evidence_dir else None
        self.fixed_cost = number(fixed_cost_per_side_usd)
        self.override_fetch = fetch
        self.interval = interval
        self.last_request = 0
        self.cooldown_until = {}
        self.cache={}

    def fetch(self, url):
        if self.override_fetch:
            return self.override_fetch(url)
        require_permitted_url(url)
        parsed = urlparse(url)
        if parsed.scheme != 'https' or parsed.netloc not in ('api.geckoterminal.com', 'frontend-api-v3.pump.fun', 'swap-api.pump.fun', 'advanced-indexer.pump.fun'):
            raise ValueError('Unreviewed paper data host')
        cached=self.cache.get(url)
        ttl=30 if '/ohlcv/' in parsed.path else 5
        if cached and time.monotonic()-cached[0]<ttl:return json.loads(cached[1])
        try:BUDGET.reserve(url,self.interval)
        except ProviderBackoff as error:raise SourceCooldown(error.seconds) from None
        remaining = self.cooldown_until.get(parsed.netloc, 0)-time.monotonic()
        if remaining > 0:
            raise SourceCooldown(remaining)
        delay = self.interval-(time.monotonic()-self.last_request)
        if delay > 0:
            time.sleep(delay)
        self.last_request = time.monotonic()
        request = Request(url, headers={'Accept': 'application/json', 'User-Agent': 'SolanaPaperResearch/0.1'})
        try:
            with build_opener(NoRedirect).open(request, timeout=15) as response:
                raw = response.read(2_000_001)
                cache_control=getattr(response,'headers',{}).get('Cache-Control','')
        except HTTPError as error:
            BUDGET.failure(url,error.code,error.headers)
            error.close()
            raise
        except URLError:
            BUDGET.failure(url,503)
            raise
        if len(raw) > 2_000_000:
            raise ValueError('Excessive provider response')
        BUDGET.success(url)
        if not isinstance(cache_control,str) or 'no-store' not in cache_control.lower():
            self.cache[url]=(time.monotonic(),raw)
        return json.loads(raw)

    def discover(self):
        url = ROOT+'/coins?'+urlencode({'offset': 0, 'limit': 5, 'sort': 'created_timestamp', 'order': 'DESC', 'includeNsfw': 'false'})
        coins = self.fetch(url)
        if not isinstance(coins, list):
            raise ValueError('Unexpected pump.fun discovery schema')
        return [parse_coin(coin, int(time.time())) for coin in coins[:5]]

    def observation(self, mint, now=None, pool=None):
        if not valid_mint(mint) or pool is not None and not valid_mint(pool):
            raise ValueError('Invalid mint or pool')
        pool_url = GECKO+'/pools/'+pool if pool else GECKO+'/tokens/'+mint+'/pools'
        payload = self.fetch(pool_url)
        pools = [payload['data']] if pool else payload['data']
        eligible = []
        for record in pools:
            attrs, relations = record['attributes'], record['relationships']
            if relations['dex']['data']['id'] != 'pumpswap':
                continue
            if relations['base_token']['data']['id'] != 'solana_'+mint:
                continue
            if relations['quote_token']['data']['id'] not in (
                'solana_So11111111111111111111111111111111111111112',
                'solana_EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'):
                continue
            if pool and attrs['address'] != pool:
                continue
            if not valid_mint(attrs['address']):
                raise ValueError('Invalid provider pool address')
            eligible.append(record)
        if not eligible:
            raise ValueError('No identity-matched graduated PumpSwap pool; bonding-curve trading unsupported')
        selected = max(eligible, key=lambda row: float(row['attributes']['reserve_in_usd']))
        attrs = selected['attributes']
        pool = attrs['address']
        trades_url = GECKO+'/pools/'+pool+'/trades?trade_volume_in_usd_greater_than=0'
        trade_payload = self.fetch(trades_url)
        candle_url=GECKO+'/pools/'+pool+'/ohlcv/minute?aggregate=1&limit=12&currency=usd&token=base&include_empty_intervals=false'
        candle_payload=None
        candle_error=None
        try:
            candle_payload=self.fetch(candle_url)
        except Exception as error:
            candle_error=source_error(error)
        # Timestamp the completed retrieval; attached proof timestamps are never renewed.
        now = int(time.time()) if now is None else now
        volume, timestamps, ids = 0, [], set()
        for trade in trade_payload['data']:
            identifier = trade['id']
            if identifier in ids:
                continue
            ids.add(identifier)
            values = trade['attributes']
            timestamp = datetime.fromisoformat(values['block_timestamp'].replace('Z', '+00:00')).timestamp()
            if not 0 <= timestamp <= now:
                raise ValueError('Invalid or future provider trade time')
            timestamps.append(timestamp)
            amount = number(float(values['volume_in_usd']))
            if values['kind'] not in ('buy', 'sell'):
                raise ValueError('Unknown provider trade direction')
            if now-3600 <= timestamp <= now and values['kind'] == 'buy':
                volume += amount
        # A truncated page ending inside the hour is not full-hour coverage.
        coverage = 100 if timestamps and min(timestamps) <= now-3600 else 0
        snapshot = {'mint': mint, 'pool': pool, 'observed_at': now,
                    'price_usd': number(float(attrs['base_token_price_usd']), .000000000000001),
                    'liquidity_usd': number(float(attrs['reserve_in_usd'])),
                    'buy_volume_usd': volume, 'buy_volume_window_seconds': 3600,
                    'buy_volume_coverage_pct': coverage, 'fixed_cost_per_side_usd': self.fixed_cost,
                    'market_source_url': pool_url, 'trades_source_url': trades_url,
                    'market_verification': 'provider pair/schema only; not independent chain reconstruction'}
        if candle_payload is not None:
            from candle_quality import provider_history
            try:
                snapshot['candle_history']=provider_history(candle_payload,mint,pool,candle_url,now)
            except (KeyError,TypeError,ValueError) as error:
                candle_error=source_error(error)
        if candle_error:
            snapshot['candle_history_error']=candle_error
        if self.evidence_dir:
            path = self.evidence_dir/(mint+'.json')
            if path.exists():
                proof = json.loads(path.read_text())
                if proof['mint'] != mint or proof['pool'] != pool:
                    raise ValueError('Supplemental proof mint/pool mismatch')
                for key in ('ath_price_usd', 'ath_checked_at', 'ath_source_url', 'ath_history_complete_since_launch',
                            'token_safety_passed', 'safety_checked_at', 'evidence', 'execution'):
                    if key in proof:
                        snapshot[key] = proof[key]
        return snapshot


def source_error(error):
    result = {'status': 'unavailable', 'error_type': type(error).__name__}
    if isinstance(error,SourceAccessBlocked):
        result.update(category='source_policy_blocked',retryable=False,reason=str(error))
    elif isinstance(error,ProviderBackoff):
        result.update(category='provider_backoff',retryable=True,retry_after_seconds=error.seconds if error.seconds!=float('inf') else None)
    elif isinstance(error, HTTPError):
        result['http_status'] = error.code
        result['category'] = 'rate_limited' if error.code == 429 else 'not_found_or_not_indexed' if error.code == 404 else 'http_error'
        result['retryable'] = error.code in (429, 500, 502, 503, 504)
    elif isinstance(error, UnsupportedMarket):
        result.update(status='unsupported',category='unsupported_market',reason=str(error),retryable=False)
    elif isinstance(error, SourceCooldown):
        result.update(category='rate_limited', retryable=True, retry_after_seconds=error.seconds)
    elif isinstance(error, URLError):
        reason = error.reason
        category = 'dns_resolution' if isinstance(reason, socket.gaierror) else 'tls_failure' if isinstance(reason, ssl.SSLError) else 'timeout' if isinstance(reason, TimeoutError) else 'network_connection'
        result.update(category=category, retryable=True)
    elif isinstance(error, (TimeoutError, ConnectionError)):
        result.update(category='network_connection', retryable=True)
    elif type(getattr(error, 'rpc_code', None)) is int:
        result.update(category='rpc_error', rpc_code=error.rpc_code, retryable=error.rpc_code not in (-32600, -32601, -32602))
    elif isinstance(error, ValueError):
        result['reason'] = str(error)
    return result
