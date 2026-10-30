"""Local finalized-event analytics; no HTTP, signing, synthetic bars or USD peg."""
from decimal import Decimal
import time
from onchain_store import EventStore
from pump_market import ModeledPumpSources
from wallet_watch import SOL

SOURCE = 'https://solana.com/docs/rpc#local-finalized-index'


class OnchainMarket:
    def __init__(self, store):
        self.store = store

    def valued(self, event):
        conversion, source, stamp = self.store.conversion(event['quote_mint'], event['block_time'])
        base = Decimal(event['base_units']) / 10 ** event['base_decimals']
        quote = Decimal(event['quote_units']) / 10 ** event['quote_decimals']
        return quote / base * conversion, quote * conversion, source, stamp

    def candles(self, mint, pool, start, end):
        bars = {}
        for event in self.store.events(mint, pool, start, end, verified_only=True):
            if event['kind'] != 'swap':
                continue
            minute = event['block_time'] // 60 * 60
            if minute < start or minute + 60 > end:
                continue
            if not self.store.coverage(minute, minute + 60)['complete']:
                continue
            price, volume, _, _ = self.valued(event)
            if minute not in bars:
                bars[minute] = {'timestamp': minute, 'open': price, 'high': price,
                                'low': price, 'close': price, 'volume_usd': Decimal(0), 'final': True}
            bar = bars[minute]
            bar.update(high=max(bar['high'], price), low=min(bar['low'], price), close=price)
            bar['volume_usd'] += volume
        return [{k: float(v) if isinstance(v, Decimal) else v for k, v in bar.items()}
                for _, bar in sorted(bars.items())]

    def minute_status(self, mint, pool, minute):
        coverage = self.store.coverage(minute, minute + 60)
        if not coverage['complete']:
            return 'missing'
        return 'traded' if any(e['kind'] == 'swap' for e in self.store.events(mint, pool, minute, minute + 60)) else 'zero_trades'

    def observation(self, mint, now=None, pool=None):
        now = int(time.time()) if now is None else int(now)
        candidates = [e for e in self.store.events(mint=mint, pool=pool, start=max(0, now-3600), end=now+1, verified_only=True) if e['kind'] == 'swap']
        if not candidates:
            raise ValueError('No finalized identity-matched swap')
        latest = candidates[-1]
        pool = latest['pool']
        price, _, conversion_source, _ = self.valued(latest)
        watermark = self.store.watermark()
        window_end = min(now, watermark) if watermark is not None else now
        hourly = self.store.coverage(window_end - 3600, window_end)
        buy, sell = Decimal(0), Decimal(0)
        sources = set(hourly.get('sources', [])) | {conversion_source}
        errors = []
        for e in self.store.events(mint, pool, window_end - 3600, window_end, verified_only=True):
            if e['kind'] != 'swap':
                continue
            try:
                _, volume, source, _ = self.valued(e)
                sources.add(source)
                if e['direction'] == 'buy':
                    buy += volume
                else:
                    sell += volume
            except ValueError as error:
                errors.append(str(error))
        try:
            bars = self.candles(mint, pool, now // 60 * 60 - 12 * 60, now // 60 * 60)
        except ValueError as error:
            bars = []
            errors.append(str(error))
        with self.store.connect() as db:
            row = db.execute('SELECT source FROM blocks WHERE slot=?', (latest['slot'],)).fetchone()
        if row:
            sources.add(row[0])
        policies = {source: self.store.policy(source) for source in sorted(sources)}
        learning_allowed = hourly['complete'] and not errors and bool(policies) and all(p.get('ml_verified') is True and p.get('evaluation_verified') is True for p in policies.values())
        complete = hourly['complete'] and not errors
        # Real reserves only. Never label virtual curve reserves as liquidity.
        reserves = latest['reserves']
        real_quote = reserves.get('real_quote_reserves')
        if latest['quote_mint'] == SOL:
            real_quote = reserves.get('real_sol_reserves', real_quote)
        if latest['venue'] == 'pump_amm':
            real_quote = reserves.get('pool_quote_token_reserves')
        liquidity = Decimal(0)
        if real_quote is not None:
            conversion, source, _ = self.store.conversion(latest['quote_mint'], latest['block_time'])
            # Quote-side reserves only: conservative, not a mark-to-market TVL.
            liquidity = Decimal(real_quote) / 10 ** latest['quote_decimals'] * conversion
        stamp = latest['block_time']
        return {'mint': mint, 'pool': pool, 'quote_mint': latest['quote_mint'],
                'observed_at': stamp, 'price_usd': float(price), 'liquidity_usd': float(liquidity),
                'liquidity_basis': 'event-reported real quote reserves only; not executable depth',
                'market_kind': 'pumpswap' if latest['venue'] == 'pump_amm' else 'bonding_curve',
                'buy_volume_usd': float(buy), 'sell_volume_usd': float(sell),
                'buy_volume_window_seconds': 3600, 'buy_volume_as_of': window_end, 'buy_volume_coverage_pct': 100 if complete else 0,
                'market_source_url': SOURCE, 'trades_source_url': SOURCE,
                'market_verification': 'finalized transaction event reconstruction; not executable fill',
                'coverage': hourly, 'data_errors': sorted(set(errors)),
                'data_rights': {'learning_allowed': learning_allowed, 'sources': policies},
                'data_provenance': {'database': str(self.store.path), 'sources': sorted(sources),
                                    'signature': latest['signature'], 'slot': latest['slot'],
                                    'idl_hash': latest['idl_hash'], 'usd_source': conversion_source},
                'shadow_only': True,
                'candle_history': {'mint': mint, 'pool': pool, 'source_url': SOURCE,
                                   'interval_seconds': 60, 'measure': 'price_usd',
                                   'retrieved_at': min(now, hourly.get('through_time', stamp)),
                                   'candles': bars}}


class OnchainSources:
    """Shared local observations for paper and unsigned live integration.

    Compatibility URLs are routed to local SQLite only. No HTTP client or
    provider fallback exists, including for SOL and portfolio valuations.
    """
    modeled_quotes_only = False

    def __init__(self, database):
        self.market = OnchainMarket(EventStore(database, readonly=True))

    def observation(self, mint, now=None, pool=None):
        return self.market.observation(mint, now, pool)

    def fetch(self, url):
        from pump_narratives import ROOT, valid_mint
        now = int(time.time())
        if url == ROOT + '/sol-price':
            price, _, stamp = self.market.store.conversion(SOL, now)
            return {'solPrice': float(price), 'stale': False, 'asOfTimestamp': stamp * 1000}
        prefix = ROOT + '/coins-v3/'
        if url.startswith(prefix):
            mint = url[len(prefix):].split('?', 1)[0]
            if not valid_mint(mint):
                raise ValueError('Invalid local holding mint')
            row = self.observation(mint, now)
            if not 0 <= now - row['observed_at'] <= 30:
                raise ValueError('Local holding valuation stale')
            return row
        raise ValueError('On-chain adapter has no website fallback')

    def discover(self):
        events = self.market.store.events(start=int(time.time())-3600, verified_only=True)
        return [{'mint': mint, 'name': '', 'symbol': '', 'description': '',
                 'observed_at': int(time.time()), 'source_url': SOURCE, 'x_anchor': None,
                 'description_fingerprint': None, 'hashtags': [],
                 'source_kind': 'finalized_chain_discovery', 'independent_endorsement': False,
                 'human_buying_verified': False, 'live_entry_authorized': False}
                for mint in sorted({e['mint'] for e in events if e.get('mint')})]


class OnchainPaperSources(ModeledPumpSources):
    """Opt-in paper adapter reusing existing conservative execution assumptions."""
    def __init__(self, database, evidence_dir=None):
        super().__init__(evidence_dir=evidence_dir)
        self.local = OnchainSources(database)
        self.market = self.local.market

    def market_observation(self, mint, now=None, pool=None):
        return self.local.observation(mint, now, pool)

    def fetch(self, url):
        return self.local.fetch(url)

    def discover(self):
        return self.local.discover()
