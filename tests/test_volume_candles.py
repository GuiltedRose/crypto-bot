import copy
import tempfile
import unittest
from meme_dip import DipRules,PaperDipTrader
from meme_live import MemeLiveStore,MemeLiveEngine
from volume_candles import assess_volume_candles
from test_meme_live import FakeAdapter
from test_paper_accounting import funded_snapshot
from paper_runtime import PaperSession


def rules():
    return DipRules(entry_style='volume_candles',entry_profile_mode='fixed',evidence_mode='market_only',
                    trend_min_bars=3,min_buy_volume_usd=5000,max_position_usd=1.78,take_profit_usd=1,max_loss_usd=.2)


def row():
    result=funded_snapshot();result['research_context']={};result.pop('liquidity_usd',None)
    result['candle_history']['candles']=result['candle_history']['candles'][-3:]
    # Deliberately volatile/downward candles, valid data. These are no longer
    # market disqualifiers under the explicitly selected minimal policy.
    result['candle_history']['candles'][0].update(open=1.2,high=2.,low=.3,close=.64)
    result['candle_history']['candles'][-1].update(open=.63,high=.90,low=.30,close=.61)
    result['price_usd']=.40
    result['buy_volume_usd']=5000
    return result


class MinimalPolicyTests(unittest.TestCase):
    def test_volatile_downward_setup_without_social_liquidity_or_pullback_can_qualify(self):
        self.assertEqual(assess_volume_candles(row(),1200,rules())['action'],'buy')
    def test_low_volume_two_candles_stale_and_wrong_mint_do_not_qualify(self):
        a=row();a['buy_volume_usd']=4999
        b=row();b['candle_history']['candles']=b['candle_history']['candles'][-2:]
        c=row();c['candle_history']['pool']='OtherPool'
        for r in [a,b,c]: self.assertEqual(assess_volume_candles(r,1200,rules())['action'],'reject')
        self.assertEqual(assess_volume_candles(row(),1500,rules())['action'],'reject')
    def test_sparse_real_candles_are_counted_without_inventing_missing_intervals(self):
        r=row();r['candle_history']['candles'][0]['timestamp']-=60
        self.assertEqual(assess_volume_candles(r,1200,rules())['action'],'buy')
    def test_paper_preserves_small_size_and_net_tp_sl(self):
        trader=PaperDipTrader(0,rules(),.065,100)
        bought=trader.on_snapshot(row())
        self.assertEqual(bought['action'],'paper_buy',bought)
        self.assertAlmostEqual(bought['budget_usd'],1.78)
        self.assertEqual(trader.position['take_profit_usd'],1)
        self.assertEqual(trader.position['max_loss_usd'],.2)
        next_row=row();next_row['observed_at']=1210;next_row['execution']['observed_at']=1210
        next_row['price_usd']=trader.position['stop']*.99
        sold=trader.on_snapshot(next_row)
        self.assertEqual(sold['action'],'paper_sell',sold)
        self.assertLessEqual(sold['pnl_usd'],-.2)
    def test_live_skips_concentration_gate_but_keeps_execution_simulation(self):
        class Risk:
            def assess(self,*args): raise AssertionError('Removed entry filter must not run')
        with tempfile.TemporaryDirectory() as root:
            store=MemeLiveStore(root,'user',rules())
            try:
                engine=MemeLiveEngine({},'user',store,rules(),adapter_factory=FakeAdapter,risk_assessor=Risk())
                r=row();r.pop('execution');r['market_kind']='bonding_curve'
                result=engine.prepare(r,100,6.5,1200)
                self.assertEqual(result['status'],'unsigned_simulation_passed',result)
                self.assertEqual(result['intent']['input_units'],17800000)
                self.assertIsNone(store.state['position'])
            finally: store.close()
    def test_unallocated_virtual_sol_movement_cannot_trip_bot_only_halt(self):
        trader=PaperDipTrader(0,rules(),.065,100)
        for value in [100,120,70]:
            trader.sol_price_usd=value;trader.mark_risk(trader.equity())
        self.assertFalse(trader.halted)
    def test_paper_bot_loss_state_survives_restart(self):
        with tempfile.TemporaryDirectory() as root:
            session=PaperSession(root,0,rules(),.065,100)
            session.trader.strategy_realized_pnl_usd=-.4
            session.trader.mark_risk(session.trader.equity())
            # Real processing checkpoints the actual risk state, not file edits.
            session.process(row());session.close()
            session=PaperSession(root)
            self.assertTrue(session.trader.halted)
            self.assertEqual(session.trader.strategy_realized_pnl_usd,-.4)
            session.close()

    def test_paper_consecutive_loss_cooldown_is_saved_across_restart(self):
        from dataclasses import replace
        with tempfile.TemporaryDirectory() as root:
            configured=replace(rules(),loss_streak_limit=1)
            session=PaperSession(root,0,configured,.065,100)
            session.trader.consecutive_losses=1
            session.trader.loss_cooldown_until=1300
            session.process(row())
            self.assertEqual(session.trader.loss_cooldown_until,1300)
            session.close()
            session=PaperSession(root)
            self.assertEqual(session.trader.consecutive_losses,1)
            self.assertEqual(session.trader.loss_cooldown_until,1300)
            retry=row();retry['observed_at']=1210;retry['execution']['observed_at']=1210
            self.assertEqual(session.process(retry)['action'],'reject')
            session.close()

    def test_confirmed_tp_uses_unchanged_one_dollar_net_target(self):
        trader=PaperDipTrader(0,rules(),.065,100);trader.on_snapshot(row())
        r=row();r['observed_at']=1210;r['execution']['observed_at']=1210
        r['price_usd']=trader.position['target']*1.001
        sold=trader.on_snapshot(r)
        self.assertEqual(sold['action'],'paper_sell');self.assertGreaterEqual(sold['pnl_usd'],1)
    def test_two_completed_candles_can_be_explicitly_configured_without_changing_default_three(self):
        from dataclasses import replace
        r=row();r['candle_history']['candles']=r['candle_history']['candles'][-2:]
        self.assertEqual(assess_volume_candles(r,1200,replace(rules(),trend_min_bars=2))['action'],'buy')
        self.assertEqual(rules().trend_min_bars,3)

class AdaptiveMinimalPolicyTests(unittest.TestCase):
    def test_all_three_profiles_share_minimums_and_never_restore_old_vetoes(self):
        from dataclasses import replace,asdict
        from meme_profiles import profiles,assess_profiles
        base=replace(rules(),entry_profile_mode='adaptive')
        for r in profiles(base).values():
            self.assertEqual(r.min_buy_volume_usd,5000)
            self.assertEqual(r.trend_min_bars,3)
            self.assertEqual(r.max_position_usd,1.78)
            self.assertEqual(r.take_profit_usd,1)
            self.assertEqual(r.max_loss_usd,.2)
        for fraction,expected in [(.20,'current'),(.40,'moderate'),(1.2,'aggressive')]:
            r=row()
            for candle in r['candle_history']['candles']:
                candle.update(open=1.,high=1.+fraction,low=1.,close=1.)
            selected=assess_profiles(r,1200,base)
            self.assertEqual(selected['action'],'buy',selected)
            self.assertEqual(selected['selected_profile'],expected)
        r['buy_volume_usd']=4999
        self.assertIsNone(assess_profiles(r,1200,base)['selected_profile'])
    def test_adaptive_live_attempts_qualifying_volatile_entry_without_dip_or_social(self):
        from dataclasses import replace
        base=replace(rules(),entry_profile_mode='adaptive')
        with tempfile.TemporaryDirectory() as root:
            store=MemeLiveStore(root,'user',base)
            try:
                engine=MemeLiveEngine({},'user',store,base,adapter_factory=FakeAdapter)
                r=row();r.pop('execution');r['market_kind']='bonding_curve'
                result=engine.prepare(r,100,6.5,1200)
                self.assertEqual(result['status'],'unsigned_simulation_passed',result)
                self.assertEqual(result['intent']['entry_profile'],'aggressive')
                self.assertEqual(result['intent']['input_units'],17800000)
            finally: store.close()
    def test_profiles_have_real_response_cadence_without_widening_slippage(self):
        from run_meme_live import cycle_rest
        for profile,expected in [('current',15),('moderate',12),('aggressive',10)]:
            self.assertEqual(cycle_rest({'position':{'entry_profile':profile}},30),expected)
    def test_paper_adoption_preserves_existing_balance_loss_and_halt(self):
        from dataclasses import replace
        with tempfile.TemporaryDirectory() as root:
            session=PaperSession(root,0,rules(),.065,100)
            session.trader.strategy_realized_pnl_usd=-.4;session.trader.halted=True
            before=session.trader.equity()
            session.adopt_entry_mode(replace(rules(),entry_profile_mode='adaptive'))
            self.assertEqual(session.trader.equity(),before)
            self.assertTrue(session.trader.halted)
            self.assertEqual(session.trader.strategy_realized_pnl_usd,-.4)
            with self.assertRaises(ValueError): session.adopt_entry_mode(replace(rules(),max_loss_usd=.3))
            session.close()
