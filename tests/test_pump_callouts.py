import copy
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from pump_callouts import HotCallouts, eligible_callouts, parse_hot_callouts

MINT = 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA'
WALLET = '11111111111111111111111111111111'
OTHER = 'So11111111111111111111111111111111111111112'


def payload(timestamp=1000):
    return {'coins': [{'coinMint': MINT, 'chain': 'solana', 'position': {
        'walletAddress': WALLET, 'contentKind': 'callout', 'amountHeld': 100,
        'valueUsd': 50, 'pnlUsd': -5, 'callout': {
            'calloutId': '07fcb83e-75d8-4c04-b42d-aa8091a639df',
            'assetClass': 'ASSET_CLASS_TOKEN', 'perp': None,
            'calloutTimestamp': datetime.fromtimestamp(timestamp, timezone.utc).isoformat(),
            'thesis': 'user text is data', 'calledOutAtMcap': 50000, 'likes': 5, 'viewCount': 10}}}]}


class HotCalloutTests(unittest.TestCase):
    def test_anonymous_payload_binds_author_mint_and_original_timestamp(self):
        rows, rejected = parse_hot_callouts(payload(), 1100, [WALLET])
        self.assertEqual(rejected, 0)
        self.assertEqual(rows[0]['published_at'], 1000)
        self.assertEqual(rows[0]['mint'], MINT)
        self.assertTrue(rows[0]['watched_author'])
        self.assertFalse(rows[0]['position_chain_verified'])
        self.assertFalse(rows[0]['automatic_trade_approval'])

    def test_quotes_foreign_chain_perps_and_future_dates_do_not_become_signals(self):
        for modify in (lambda row: row.update(chain='ethereum'),
                       lambda row: row['position'].update(contentKind='quote'),
                       lambda row: row['position']['callout'].update(assetClass='ASSET_CLASS_PERP'),
                       lambda row: row['position']['callout'].update(calloutTimestamp='2099-01-01T00:00:00Z')):
            data = payload()
            modify(data['coins'][0])
            rows, _ = parse_hot_callouts(data, 1100)
            self.assertEqual(rows, [])

    def test_refresh_does_not_rejuvenate_old_callout(self):
        rows, _ = parse_hot_callouts(payload(), 2000)
        self.assertEqual(eligible_callouts(rows, 2000), [])
        rows, _ = parse_hot_callouts(payload(), 1100)
        self.assertEqual(eligible_callouts(rows, 1250), [])

    def test_ids_deduplicate_and_cannot_change_mint_or_author(self):
        with tempfile.TemporaryDirectory() as root:
            data = payload()
            data['coins'].append(copy.deepcopy(data['coins'][0]))
            source = HotCallouts(Path(root)/'callouts.sqlite', lambda _: data)
            report = source.poll([WALLET], now=1100)
            self.assertEqual(len(report['callouts']), 1)
            data['coins'][0]['coinMint'] = OTHER
            data['coins'] = data['coins'][:1]
            report = source.poll([WALLET], now=1110)
            self.assertEqual(report['rejected_records'], 1)
            self.assertEqual(report['callouts'], [])
            source.db.close()

    def test_missing_wallet_is_not_anonymous_identity_proof(self):
        data = payload()
        del data['coins'][0]['position']['walletAddress']
        rows, rejected = parse_hot_callouts(data, 1100)
        self.assertEqual(rows, [])
        self.assertEqual(rejected, 1)


if __name__ == '__main__':
    unittest.main()
