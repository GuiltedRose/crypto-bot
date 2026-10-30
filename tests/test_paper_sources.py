import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import socket
from urllib.error import HTTPError, URLError
from paper_sources import PublicSources, source_error, SourceCooldown

MINT = 'So11111111111111111111111111111111111111112'
POOL = '11111111111111111111111111111111'
USDC = 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'


class PublicSourceTests(unittest.TestCase):
    def setUp(self):
        self.pool = {'attributes': {'address': POOL, 'base_token_price_usd': '.6', 'reserve_in_usd': '100000'},
                     'relationships': {'dex': {'data': {'id': 'pumpswap'}},
                                       'base_token': {'data': {'id': 'solana_'+MINT}},
                                       'quote_token': {'data': {'id': 'solana_'+USDC}}}}
        self.trades = []
        for identifier, timestamp, kind, amount in [('1', 9900, 'buy', 5000), ('2', 9800, 'sell', 9000), ('3', 6000, 'buy', 1000)]:
            self.trades.append({'id': identifier, 'attributes': {'block_timestamp': datetime.fromtimestamp(timestamp, timezone.utc).isoformat(),
                                                               'kind': kind, 'volume_in_usd': str(amount)}})

    def fetch(self, url):
        if '/ohlcv/minute?' in url:
            return {'meta':{'base':{'address':MINT}},'data':{'attributes':{'ohlcv_list':[[9900-index*60,.6,.61,.59,.6,100] for index in range(6)]}}}
        if '/trades?' in url:
            return {'data': self.trades}
        return {'data': [self.pool]}

    def test_buy_volume_excludes_sells_old_trades_and_duplicates(self):
        self.trades.append(copy.deepcopy(self.trades[0]))
        observed = PublicSources(fetch=self.fetch).observation(MINT, now=10000)
        self.assertEqual(observed['buy_volume_usd'], 5000)
        self.assertEqual(observed['buy_volume_coverage_pct'], 100)
        self.assertNotIn('evidence', observed)
        self.assertNotIn('ath_history_complete_since_launch', observed)

    def test_dns_failure_is_not_reported_as_a_bad_market(self):
        error = source_error(URLError(socket.gaierror(-3, 'temporary resolution failure')))
        self.assertEqual(error['category'], 'dns_resolution')
        self.assertTrue(error['retryable'])
        self.assertNotIn('http_status', error)
        missing = HTTPError('https://example.invalid', 404, 'missing', {}, None)
        error = source_error(missing)
        missing.close()
        self.assertEqual(error['category'], 'not_found_or_not_indexed')
        self.assertEqual(error['http_status'], 404)

    def test_rate_limit_enters_host_cooldown_without_hammering_provider(self):
        source = PublicSources(interval=0)
        error = HTTPError('https://api.geckoterminal.com', 429, 'rate limited', {'Retry-After': '120'}, None)
        from provider_limits import ProviderBudget
        with tempfile.TemporaryDirectory() as root,patch('paper_sources.BUDGET',ProviderBudget(Path(root)/'budget.sqlite',clock=lambda:1000)),patch('paper_sources.build_opener') as opener:
            opener.return_value.open.side_effect = error
            with self.assertRaises(HTTPError):
                source.fetch('https://api.geckoterminal.com/test')
            with self.assertRaises(SourceCooldown):
                source.fetch('https://api.geckoterminal.com/test')
            self.assertEqual(opener.return_value.open.call_count, 1)
        error.close()

    def test_short_trade_page_is_not_full_coverage(self):
        self.trades = self.trades[:2]
        observed = PublicSources(fetch=self.fetch).observation(MINT, now=10000)
        self.assertEqual(observed['buy_volume_coverage_pct'], 0)

    def test_wrong_venue_or_pair_fails(self):
        for group, value in [('dex', 'raydium'), ('base_token', 'solana_'+USDC)]:
            before = copy.deepcopy(self.pool)
            self.pool['relationships'][group]['data']['id'] = value
            with self.assertRaises(ValueError):
                PublicSources(fetch=self.fetch).observation(MINT, now=10000)
            self.pool = before

    def test_external_proofs_cannot_rewrite_market_observations_or_timestamps(self):
        with tempfile.TemporaryDirectory() as root:
            proof = {'mint': MINT, 'pool': POOL, 'price_usd': 999, 'observed_at': 10000,
                     'ath_checked_at': 10, 'ath_history_complete_since_launch': True}
            path = Path(root)/(MINT+'.json')
            path.write_text(json.dumps(proof))
            source = PublicSources(root, fetch=self.fetch)
            observed = source.observation(MINT, now=10000)
            self.assertEqual(observed['price_usd'], .6)
            self.assertEqual(observed['ath_checked_at'], 10)
            proof['pool'] = MINT
            path.write_text(json.dumps(proof))
            with self.assertRaises(ValueError):
                source.observation(MINT, now=10000)


if __name__ == '__main__':
    unittest.main()
