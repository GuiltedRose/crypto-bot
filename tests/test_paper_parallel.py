import copy
import hashlib
import tempfile
import unittest
from paper_runtime import PaperSession
from meme_learning import new_weighted_model,paper_outcomes
from test_volume_candles import row,rules


def observation(mint,now=1200,price=.4):
    result=copy.deepcopy(row());result.update(mint=mint,observed_at=now,price_usd=price)
    result['candle_history'].update(mint=mint,retrieved_at=now)
    for candle in result['candle_history']['candles']:candle['timestamp']+=now-1200
    result['execution'].update(mint=mint,observed_at=now)
    return result


def model(bias):
    result=new_weighted_model();result['samples']=30
    result['weights']['output_weights']=[0.]*8;result['weights']['output_bias']=bias
    return result


class ParallelPaperTests(unittest.TestCase):
    def test_positions_share_funds_survive_restart_and_close_out_of_order(self):
        with tempfile.TemporaryDirectory() as root:
            session=PaperSession(root,0,rules(),.3,100)
            session.configure_positions(2);session.configure_loss_cooldown(0)
            for mint in ('first','second'):
                self.assertEqual(session.process(observation(mint))['action'],'paper_buy')
            self.assertEqual(len(session.trader.all_positions()),2)
            self.assertLess(session.trader.native_sol,.3)
            self.assertLessEqual(session.report()['marked_equity_usd'],30)
            self.assertEqual(session.process(observation('third'))['reason'],'paper position limit reached')
            self.assertIsNone(session.auto_reset_if_exhausted())
            session.close();session=PaperSession(root)
            try:
                self.assertEqual(len(session.trader.all_positions()),2)
                self.assertEqual(session.process(observation('second',1260,2))['action'],'paper_sell')
                self.assertEqual([p['mint'] for p in session.trader.all_positions()],['first'])
                self.assertEqual(session.process(observation('first',1260,2))['action'],'paper_sell')
                closed,opened,_,unmatched=paper_outcomes(session.directory/'paper.sqlite')
                self.assertEqual((len(closed),len(opened),unmatched),(2,0,0))
                self.assertEqual(session.refresh_learning()['samples'],2)
            finally:session.close()

    def test_online_veto_never_spends_funds_and_never_blocks_exit(self):
        with tempfile.TemporaryDirectory() as root:
            session=PaperSession(root,0,rules(),.3,100)
            try:
                session.neural_mode='learning';session.learning_model=model(-8.)
                mint=next('blocked'+str(i) for i in range(10)
                    if int(hashlib.sha256(('blocked'+str(i)+'1200').encode()).hexdigest()[:8],16)%4!=0)
                before=session.trader.native_sol
                result=session.process(observation(mint))
                self.assertEqual(result['reason'],'paper neural entry veto')
                self.assertEqual(session.trader.native_sol,before)
                session.learning_model=model(8.)
                self.assertEqual(session.process(observation('allowed'))['action'],'paper_buy')
                session.learning_model=model(-8.)
                self.assertEqual(session.process(observation('allowed',1260,2))['action'],'paper_sell')
            finally:session.close()


    def test_market_cycle_quotes_every_open_position_before_new_candidates(self):
        from run_paper import markets_once
        with tempfile.TemporaryDirectory() as root:
            session=PaperSession(root,0,rules(),.3,100)
            try:
                session.configure_positions(2)
                for mint in ('first','second'):
                    session.process(observation(mint))
                seen=[]
                class Sources:
                    def observation(self,mint,pool=None):
                        seen.append(mint)
                        return observation(mint,1260,2 if mint in ('first','second') else .4)
                markets_once(session,Sources(),['candidate'])
                self.assertEqual(seen[:2],['first','second'])
                self.assertEqual(len(paper_outcomes(session.directory/'paper.sqlite')[0]),2)
            finally:session.close()
