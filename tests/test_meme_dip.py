import copy
import unittest
import test_meme_evidence as evidence_fixtures
from meme_dip import DipRules, PaperDipTrader
from test_candle_quality import rising_history


class DipTests(unittest.TestCase):
    def setUp(self):
        fixture = evidence_fixtures.MemeEvidenceTests()
        fixture.setUp()
        self.snapshot = {'mint': 'Mint', 'pool': 'Pool', 'observed_at': 1000,
                         'price_usd': .60, 'ath_price_usd': 1,
                         'ath_history_complete_since_launch': True, 'ath_checked_at': 1000,
                         'ath_source_url': 'https://example.com/history',
                         'token_safety_passed': True, 'safety_checked_at': 1000,
                         'buy_volume_window_seconds': 3600, 'buy_volume_coverage_pct': 100,
                         'buy_volume_usd': 5000, 'liquidity_usd': 100000,
                         'fixed_cost_per_side_usd': .001, 'evidence': fixture.evidence}
        self.snapshot['candle_history']=rising_history()
        self.trader = PaperDipTrader()

    def next(self, price, **changes):
        self.snapshot['observed_at'] += 10
        self.snapshot['price_usd'] = price
        self.snapshot.update(changes)
        if 'candle_history' not in changes:
            self.snapshot['candle_history']=rising_history(self.snapshot['observed_at'],price)
        return self.trader.on_snapshot(copy.deepcopy(self.snapshot))

    def test_dip_then_rebound_required(self):
        self.assertEqual(self.next(.8)['action'], 'watch')
        self.assertEqual(self.next(.60)['action'], 'watch')
        self.assertEqual(self.next(.50)['action'], 'watch')
        self.assertEqual(self.next(.52)['action'], 'watch')
        result = self.next(.53)
        self.assertEqual(result['action'], 'paper_buy')
        self.assertLessEqual(result['budget_usd'], 1)
        self.assertGreater(self.trader.position['entry_price'], .53)

    def test_screenshot_sized_pullback_can_qualify_without_touching_40_percent(self):
        self.assertEqual(self.next(.6313)['action'], 'watch')  # 36.87% dip
        self.assertEqual(self.next(.615)['action'], 'watch')
        result = self.next(.646)
        self.assertEqual(result['action'], 'paper_buy')
        self.assertAlmostEqual(result['pullback_fraction'], .354)

    def test_both_zone_edges_and_configurable_range(self):
        for price in (.5, .65):
            trader = PaperDipTrader(rules=DipRules(rebound_fraction=0))
            self.assertEqual(trader.on_snapshot(dict(self.snapshot, price_usd=price,candle_history=rising_history(price=price)))['action'], 'paper_buy')
        for price in (.49, .651):
            trader = PaperDipTrader(rules=DipRules(rebound_fraction=0))
            self.assertNotEqual(trader.on_snapshot(dict(self.snapshot, price_usd=price,candle_history=rising_history(price=price)))['action'], 'paper_buy')
        self.trader = PaperDipTrader(rules=DipRules(min_dip_fraction=.2, max_dip_fraction=.3, rebound_fraction=0))
        self.assertEqual(self.next(.75)['action'], 'paper_buy')

    def test_deeper_collapse_clears_setup_before_recovery(self):
        self.next(.6)
        self.assertEqual(self.next(.4)['action'], 'reject')
        self.assertEqual(self.next(.55)['action'], 'watch')
        self.assertEqual(self.next(.58)['action'], 'paper_buy')

    def test_safety_filters_still_apply_without_rebound(self):
        self.trader = PaperDipTrader(rules=DipRules(rebound_fraction=0))
        self.assertEqual(self.next(.6, buy_volume_usd=100)['action'], 'reject')
        self.assertIsNone(self.trader.position)

    def test_allocation_scales_with_bankroll_and_stop_risk(self):
        budgets = []
        for balance in (10, 100):
            trader = PaperDipTrader(balance, DipRules(rebound_fraction=0))
            result = trader.on_snapshot(self.snapshot)
            self.assertEqual(result['action'], 'paper_buy')
            budgets.append(result['budget_usd'])
            self.assertLessEqual(result['budget_usd']*.15, balance*.0025)
        self.assertAlmostEqual(budgets[1]/budgets[0], 10)
        trader = PaperDipTrader(.01, DipRules(rebound_fraction=0))
        self.assertEqual(trader.on_snapshot(self.snapshot)['action'], 'reject')

    def test_invalid_zone_configuration(self):
        for overrides in ({'min_dip_fraction': .5, 'max_dip_fraction': .35},
                          {'min_dip_fraction': .4, 'max_dip_fraction': .4},
                          {'max_dip_fraction': 1}, {'rebound_fraction': 1}):
            with self.assertRaises(ValueError):
                DipRules(**overrides)

    def test_pump_narrative_does_not_require_paid_social_metrics(self):
        narrative = self.snapshot['evidence']['narrative']
        for key in ('independent_sources', 'unique_authors', 'organic_engagement_pct'):
            del narrative[key]
        self.assertEqual(self.next(.6)['action'], 'watch')
        self.assertEqual(self.next(.63)['action'], 'paper_buy')

    def test_volume_is_buy_volume_and_ath_must_be_complete(self):
        for field, value in [('buy_volume_usd', 4999), ('buy_volume_window_seconds', 86400),
                             ('ath_history_complete_since_launch', False),
                             ('token_safety_passed', False)]:
            trader = PaperDipTrader()
            snapshot = dict(self.snapshot, **{field: value})
            self.assertEqual(trader.on_snapshot(snapshot)['action'], 'reject', field)
        snapshot = dict(self.snapshot, volume_24h_usd=100000)
        del snapshot['buy_volume_usd']
        self.assertEqual(PaperDipTrader().on_snapshot(snapshot)['action'], 'reject')

    def test_originality_no_longer_blocks_entries_and_exits_ignore_entry_history(self):
        self.snapshot['evidence']['originality']={'status':'unverified'}
        self.next(.6)
        self.assertEqual(self.next(.63)['action'],'paper_buy')
        self.assertEqual(self.next(.1,candle_history={})['action'],'paper_sell')

    def test_pump_market_cap_ath_is_not_treated_as_token_price(self):
        self.snapshot.update(ath_source_kind='pump_provider',ath_market_cap_usd=100000,market_cap_usd=60000,
                             ath_source_url='https://swap-api.pump.fun/v1/coins/Mint/ath?currency=USD',
                             ath_history_complete_since_launch=False)
        del self.snapshot['ath_price_usd']
        self.assertEqual(self.next(.6)['action'],'watch')
        result=self.next(.63,market_cap_usd=63000)
        self.assertEqual(result['action'],'paper_buy')
        self.assertEqual(result['pullback_measure'],'market_cap_usd')
        self.assertAlmostEqual(result['pullback_fraction'],.37)
        self.assertLess(result['entry_price'],1)

    def test_exploratory_paper_marks_unknown_safety_without_fabricating_proof(self):
        self.trader=PaperDipTrader(6.5,DipRules(evidence_mode='market_only',rebound_fraction=0))
        del self.snapshot['token_safety_passed']
        del self.snapshot['safety_checked_at']
        self.snapshot['evidence']={}
        result=self.next(.6)
        self.assertEqual(result['action'],'paper_buy')
        self.assertFalse(result['safety_qualified'])
        self.assertEqual(self.snapshot['evidence'],{})
        strict=PaperDipTrader(6.5,DipRules(rebound_fraction=0))
        self.assertEqual(strict.on_snapshot(self.snapshot)['action'],'reject')

    def test_exploratory_paper_keeps_chart_volume_and_cost_limits(self):
        self.trader=PaperDipTrader(6.5,DipRules(evidence_mode='market_only',rebound_fraction=0))
        self.assertEqual(self.next(.6,buy_volume_usd=1)['action'],'reject')
        self.assertEqual(self.next(.6,buy_volume_usd=5000,fixed_cost_per_side_usd=1)['action'],'reject')
        with self.assertRaises(ValueError): DipRules(evidence_mode='ignore_everything')

    def test_missing_candles_block_entry(self):
        self.assertEqual(self.next(.6,candle_history={})['action'],'reject')
        self.assertIsNone(self.trader.position)

    def test_buyer_quality_blocks_entry(self):
        self.snapshot['evidence']['trading']['suspected_bot_buy_volume_pct'] = 90
        self.assertEqual(self.next(.6)['action'], 'reject')
        self.assertIsNone(self.trader.position)

    def test_entry_evidence_failure_does_not_disable_exits(self):
        self.next(.6)
        self.assertEqual(self.next(.63)['action'], 'paper_buy')
        self.snapshot['evidence'] = {}
        result = self.next(.1)
        self.assertEqual(result['action'], 'paper_sell')
        self.assertLess(result['pnl_usd'], -.8)  # gap fills below stop, not at it
        self.assertIsNone(self.trader.position)

    def test_stale_recovery_and_excessive_rebound_do_not_buy(self):
        self.next(.6)
        self.assertEqual(self.next(.63, evidence={})['action'], 'reject')
        self.assertEqual(self.next(.9)['action'], 'reject')
        self.assertIsNone(self.trader.position)
        self.setUp()
        self.next(.6)
        self.assertEqual(self.next(.8)['action'], 'reject')

    def test_costs_and_ordering(self):
        self.next(.6)
        self.assertEqual(self.next(.63, fixed_cost_per_side_usd=1)['action'], 'reject')
        with self.assertRaises(ValueError):
            self.trader.on_snapshot(self.snapshot)
        with self.assertRaises(ValueError):
            DipRules(stop_fraction=0)

    def test_single_position_and_global_halt(self):
        self.next(.6)
        self.next(.63)
        self.assertEqual(self.next(.63, mint='Other')['action'], 'hold')
        self.trader.peak = 200  # simulate an already latched portfolio high
        self.snapshot['mint'] = 'Mint'
        self.assertEqual(self.next(.62)['action'], 'paper_sell')
        self.assertTrue(self.trader.halted)
        self.assertEqual(self.next(.6)['action'], 'reject')


if __name__ == '__main__':
    unittest.main()
