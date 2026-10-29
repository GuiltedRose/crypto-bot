import tempfile
import unittest
from pathlib import Path
from pump_narratives import NarrativeStore, parse_coin, valid_mint, x_link, duplicate_groups

MINT = 'So11111111111111111111111111111111111111112'
OTHER = '11111111111111111111111111111111'


class PumpNarrativeTests(unittest.TestCase):
    def test_mint_validation_and_binding(self):
        self.assertTrue(valid_mint(MINT))
        self.assertFalse(valid_mint('../secrets'))
        self.assertFalse(valid_mint('x' * 44))
        with self.assertRaises(ValueError):
            parse_coin({'mint': MINT}, 1000, OTHER)

    def test_only_canonical_x_links_and_distinct_anchor_types(self):
        self.assertEqual(x_link('https://twitter.com/Example/status/123?s=20'),
                         {'kind': 'post', 'url': 'https://x.com/example/status/123'})
        self.assertEqual(x_link('https://x.com/i/communities/123')['kind'], 'community')
        for link in ('https://x.com.evil.test/name', 'https://x.com@evil.test/name',
                     'http://x.com/name', 'https://x.com/search?q=coin', 'javascript:alert(1)', None):
            self.assertIsNone(x_link(link))

    def test_creator_claim_never_becomes_human_or_safety_evidence(self):
        lead = parse_coin({'mint': MINT, 'description': '#Dogs and #dogs',
                           'twitter': 'https://x.com/example'}, 1000)
        self.assertEqual(lead['hashtags'], ['dogs'])
        self.assertFalse(lead['human_buying_verified'])
        self.assertFalse(lead['independent_endorsement'])
        self.assertEqual(lead['bundle_analysis'], 'unknown')
        self.assertFalse(lead['live_entry_authorized'])

    def test_normalized_branding_collisions_are_unresolved_not_auto_approved(self):
        leads = [parse_coin({'mint': MINT, 'name': 'DOGE Coin', 'symbol': '$DOGE'}, 1000),
                 parse_coin({'mint': OTHER, 'name': 'ＤＯＧＥ-coin', 'symbol': 'doge'}, 1100)]
        groups = duplicate_groups(leads + leads)
        self.assertEqual({group['signal'] for group in groups}, {'name', 'symbol'})
        self.assertTrue(all(group['canonical_mint'] is None for group in groups))
        self.assertTrue(all(len(group['mints']) == 2 for group in groups))
        self.assertEqual(duplicate_groups([leads[0], leads[0]]), [])

    def test_old_coin_remains_a_duplicate_candidate_after_recent_expiry(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'leads.sqlite'
            store = NarrativeStore(path)
            store.ingest(parse_coin({'mint': MINT, 'name': 'Original Coin'}, 1000))
            store.db.close()
            store = NarrativeStore(path)
            store.ingest(parse_coin({'mint': OTHER, 'name': 'original-coin'}, 5000))
            report = store.report(5000)
            self.assertEqual(len(report['leads']), 1)
            self.assertEqual(len(report['leads'][0]['duplicate_signals']), 1)
            self.assertEqual(report['leads'][0]['originality_status'], 'unverified')
            self.assertIsNone(report['duplicate_candidates'][0]['canonical_mint'])
            store.db.close()

    def test_deduplication_copycats_expiry_and_first_seen(self):
        with tempfile.TemporaryDirectory() as root:
            store = NarrativeStore(Path(root) / 'leads.sqlite')
            for mint in (MINT, OTHER):
                store.ingest(parse_coin({'mint': mint, 'description': 'same claim',
                                         'twitter': 'https://x.com/example'}, 1000))
            store.ingest(parse_coin({'mint': MINT, 'description': 'same claim',
                                     'twitter': 'https://x.com/example'}, 1100))
            report = store.report(1200)
            self.assertEqual(len(report['leads']), 2)
            self.assertEqual(len(report['shared_x_anchors']), 1)
            self.assertEqual(len(report['copied_descriptions']), 1)
            self.assertTrue(all(lead['first_seen'] == 1000 for lead in report['leads']))
            self.assertEqual(store.report(5000)['leads'], [])
            store.db.close()


if __name__ == '__main__':
    unittest.main()
