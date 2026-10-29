import copy
from pathlib import Path
import unittest
from pump_market import PumpMarketSources, pump_history

MINT='TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA'
POOL='11111111111111111111111111111111'


class PumpMarketTests(unittest.TestCase):
    def setUp(self):
        self.coin={'mint':MINT,'chain_id':'solana','pool_address':POOL,'complete':True,'program':'pump',
                   'price_usd':.6,'market_cap_usd':60000,'canonical_pool_liquidity_usd':100000}
        self.candles={'served':{'res':'1m','measure':'price_usd'},'hasOlder':True,
                      'candles':[{'openTimeMs':(600+i*60)*1000,'open':str(.57+i*.006),'high':str(.575+i*.006),
                                  'low':str(.565+i*.006),'close':str(.57+i*.006),'volumeUsd':'100','final':True} for i in range(6)]}

    def fetch(self,url):
        if '/coins-v3/' in url: return self.coin
        if '/candles/' in url: return self.candles
        if '/ath?' in url: return {'athMarketCap':100000}
        if '/market-activity?' in url: return {'1h':{'buyVolumeUSD':6000,'sellVolumeUSD':3000}}
        if '/token-holders/' in url: return {'mint':MINT,'chain':'solana','holderCount':250}
        raise AssertionError('Unexpected source '+url)

    def test_all_market_fields_come_from_pump_with_explicit_units(self):
        row=PumpMarketSources(fetch=self.fetch).observation(MINT,now=1000)
        self.assertEqual(row['price_usd'],.6)
        self.assertEqual(row['market_cap_usd'],60000)
        self.assertEqual(row['ath_market_cap_usd'],100000)
        self.assertEqual(row['ath_source_kind'],'pump_provider')
        self.assertFalse(row['ath_history_complete_since_launch'])
        self.assertEqual(row['candle_history']['interval_seconds'],60)
        self.assertEqual(row['candle_history']['measure'],'price_usd')
        self.assertEqual(row['holder_count'],250)
        self.assertEqual(row['buy_volume_usd'],6000)
        self.assertNotIn('token_safety_passed',row)
        self.assertNotIn('evidence',row)

    def test_market_cap_candles_and_five_minute_responses_are_not_price_minutes(self):
        for served in ({'res':'5m','measure':'price_usd'},{'res':'1m','measure':'market_cap_usd'}):
            bad=copy.deepcopy(self.candles)
            bad['served']=served
            with self.assertRaises(ValueError): pump_history(bad,MINT,POOL,'https://example.com',1000)

    def test_partial_candles_are_excluded_and_auxiliary_failure_keeps_quote(self):
        self.candles['candles'][-1]['final']=False
        self.assertEqual(len(pump_history(self.candles,MINT,POOL,'https://example.com',1000)['candles']),5)
        def outage(url):
            if '/candles/' in url: raise ConnectionError()
            return self.fetch(url)
        observed=PumpMarketSources(fetch=outage).observation(MINT,now=1000)
        self.assertEqual(observed['price_usd'],.6)
        self.assertNotIn('candle_history',observed)
        self.assertIn('candles',observed['source_errors'])

    def test_wrong_identity_and_changed_pool_are_rejected(self):
        with self.assertRaises(ValueError): PumpMarketSources(fetch=self.fetch).observation(MINT,1000,pool=MINT)
        self.coin['mint']=POOL
        with self.assertRaises(ValueError): PumpMarketSources(fetch=self.fetch).observation(MINT,1000)


if __name__=='__main__': unittest.main()
