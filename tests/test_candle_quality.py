import copy
import unittest
from candle_quality import CandleQualityPolicy, provider_history


def rising_history(now=1000,price=.6,mint='Mint',pool='Pool'):
    end=int(now)//60*60
    rows=[]
    for index in range(6):
        close=price*(.95+.01*index)
        rows.append({'timestamp':end-(6-index)*60,'open':close*.995,
                     'high':close*1.003,'low':close*.992,'close':close,'volume_usd':100})
    return {'mint':mint,'pool':pool,'source_url':'https://example.com/candles',
            'retrieved_at':now,'interval_seconds':60,'candles':rows}


class CandleQualityTests(unittest.TestCase):
    def setUp(self):
        self.snapshot={'mint':'Mint','pool':'Pool','price_usd':.6,'candle_history':rising_history()}

    def assess(self):
        return CandleQualityPolicy().assess(self.snapshot,1000)

    def test_rising_structure_and_active_candles_qualify(self):
        report=self.assess()
        self.assertTrue(report['passed'],report)
        self.assertEqual(report['metrics']['rising_close_fraction'],1)

    def test_falling_prices_do_not_pass_merely_because_they_are_in_dip_zone(self):
        rows=self.snapshot['candle_history']['candles']
        values=[(row['open'],row['high'],row['low'],row['close']) for row in rows][::-1]
        for row,prices in zip(rows,values):
            row.update(zip(('open','high','low','close'),prices))
        self.assertFalse(self.assess()['passed'])

    def test_one_red_close_is_allowed_in_a_sustained_uptrend(self):
        row=self.snapshot['candle_history']['candles'][3]
        row.update(open=.585,high=.59,low=.58,close=.582)
        self.assertTrue(self.assess()['passed'],self.assess())

    def test_wicks_extreme_ranges_gaps_and_inactivity_reject(self):
        original=copy.deepcopy(self.snapshot)
        for kind in ('wicks','range','gap','volume'):
            self.snapshot=copy.deepcopy(original)
            rows=self.snapshot['candle_history']['candles']
            if kind=='wicks':
                for row in rows: row.update(open=row['close'],high=row['close']*1.03,low=row['close']*.97)
            elif kind=='range': rows[-1]['high']=1.2
            elif kind=='gap': rows[3].update(open=.7,high=.71,low=.58)
            else: rows[-1]['volume_usd']=0
            self.assertFalse(self.assess()['passed'],kind)

    def test_missing_partial_stale_and_identity_mismatched_history_reject(self):
        original=copy.deepcopy(self.snapshot)
        for kind in ('missing','partial','stale','identity','gap'):
            self.snapshot=copy.deepcopy(original)
            history=self.snapshot['candle_history']
            if kind=='missing': self.snapshot.pop('candle_history')
            elif kind=='partial': history['candles'][-1]['timestamp']=960
            elif kind=='stale': history['retrieved_at']=0
            elif kind=='identity': history['pool']='Other'
            else: history['candles'][2]['timestamp']-=60
            self.assertFalse(self.assess()['passed'],kind)

    def test_provider_history_preserves_gaps_and_excludes_partial_bars(self):
        payload={'meta':{'base':{'address':'Mint'}},'data':{'attributes':{'ohlcv_list':[
            [960,1,1,1,1,1],[900,1,1,1,1,1],[780,1,1,1,1,1]]}}}
        result=provider_history(payload,'Mint','Pool','https://example.com',1000)
        self.assertEqual([row['timestamp'] for row in result['candles']],[780,900])
        with self.assertRaises(ValueError): provider_history(payload,'Other','Pool','https://example.com',1000)


if __name__=='__main__':
    unittest.main()
