import copy
import json
from pathlib import Path
import tempfile
import unittest

from meme_evidence import MemeEvidencePolicy, load_meme_evidence


class MemeEvidenceTests(unittest.TestCase):
    def setUp(self):
        # Synthetic fixtures only: never publish these as market observations.
        self.evidence = {
            'mint': 'Mint', 'pool': 'Pool', 'observed_at': 1000,
            'narrative': {'observed_at': 1000, 'source_url': 'https://example.com/narrative',
                          'topic': 'test topic', 'independent_sources': ['a', 'b', 'c'],
                          'unique_authors': 25, 'organic_engagement_pct': 90},
            'distribution': {'observed_at': 1000, 'source_url': 'https://example.com/distribution',
                             'launch_history_complete': True, 'supply_coverage_pct': 100,
                             'bundled_supply_pct': 0, 'linked_supply_pct': 5},
            'trading': {'observed_at': 1000, 'source_url': 'https://example.com/trading',
                        'buyer_analysis_coverage_pct': 100, 'independent_buyers': 30,
                        'suspected_bot_buy_volume_pct': 10, 'wash_volume_pct': 1, 'price_change_1h_pct': 10, 'liquidity_drop_1h_pct': 0}}

    def assess(self, evidence):
        return MemeEvidencePolicy().assess('Mint', 'Pool', evidence, 1100)

    def test_complete_evidence_and_identity(self):
        self.assertTrue(self.assess(self.evidence)['passed'])
        self.evidence['mint'] = 'Other'
        self.assertFalse(self.assess(self.evidence)['passed'])

    def test_originality_is_not_an_entry_requirement(self):
        self.assertTrue(self.assess(self.evidence)['passed'])
        for value in (None, {}, {'status': 'copycat'}, {'status': 'unverified', 'observed_at': 0}):
            evidence = copy.deepcopy(self.evidence)
            evidence['originality'] = value
            self.assertTrue(self.assess(evidence)['passed'])

    def test_unknown_bundle_coverage_is_not_zero_bundles(self):
        for key, value in [('launch_history_complete', False), ('supply_coverage_pct', 50),
                           ('bundled_supply_pct', .01), ('linked_supply_pct', 11)]:
            evidence = copy.deepcopy(self.evidence)
            evidence['distribution'][key] = value
            self.assertFalse(self.assess(evidence)['passed'], key)

    def test_refreshing_envelope_does_not_refresh_measurements(self):
        for section in ('narrative', 'distribution', 'trading'):
            for timestamp in (0, 1200):
                evidence = copy.deepcopy(self.evidence)
                evidence[section]['observed_at'] = timestamp
                self.assertFalse(self.assess(evidence)['passed'])

    def test_repeated_sources_and_coordinated_trading_block(self):
        for section, key, value in [('narrative', 'independent_sources', ['a', 'A', ' a ']),
                                    ('narrative', 'organic_engagement_pct', 20),
                                    ('trading', 'wash_volume_pct', 50),
                                    ('trading', 'price_change_1h_pct', 80),
                                    ('trading', 'liquidity_drop_1h_pct', 30)]:
            evidence = copy.deepcopy(self.evidence)
            evidence[section][key] = value
            self.assertFalse(self.assess(evidence)['passed'])

    def test_many_wallets_do_not_replace_independent_buyer_analysis(self):
        for key, value in [('buyer_analysis_coverage_pct', 90), ('independent_buyers', 2),
                           ('suspected_bot_buy_volume_pct', 80)]:
            evidence = copy.deepcopy(self.evidence)
            evidence['trading'][key] = value
            self.assertFalse(self.assess(evidence)['passed'], key)
        del self.evidence['trading']['independent_buyers']
        self.assertFalse(self.assess(self.evidence)['passed'])

    def test_invalid_measurements_fail_closed(self):
        for value in (None, True, '0', float('nan'), float('inf'), -1, 101):
            evidence = copy.deepcopy(self.evidence)
            evidence['distribution']['bundled_supply_pct'] = value
            self.assertFalse(self.assess(evidence)['passed'])
        self.assertFalse(self.assess({})['passed'])

    def test_cache_missing_malformed_and_present(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertFalse(load_meme_evidence('Mint', 'Pool', 1100, root)['passed'])
            path = Path(root) / 'Mint' / 'meme_evidence.json'
            path.parent.mkdir()
            path.write_text('{broken')
            self.assertFalse(load_meme_evidence('Mint', 'Pool', 1100, root)['passed'])
            path.write_text(json.dumps(self.evidence))
            self.assertTrue(load_meme_evidence('Mint', 'Pool', 1100, root)['passed'])
            self.assertFalse(load_meme_evidence('../Mint', 'Pool', 1100, root)['passed'])


if __name__ == '__main__':
    unittest.main()
