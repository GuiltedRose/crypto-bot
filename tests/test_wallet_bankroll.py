import unittest
from types import SimpleNamespace
from solders.pubkey import Pubkey
from run_meme_live import wallet_equity
from meme_execution import TOKEN,TOKEN2022
from meme_dip import USDC_MINT


class WalletBankrollTests(unittest.TestCase):
    def test_total_counts_manual_tokens_from_both_token_programs(self):
        user=str(Pubkey.new_unique());mint=str(Pubkey.new_unique())
        def token(mint,units,decimals): return {'account':{'data':{'parsed':{'info':{'owner':user,'mint':mint,'tokenAmount':{'amount':str(units),'decimals':decimals}}}}}}
        class RPC:
            def call(self,method,params):
                if method=='getBalance': return {'value':1000000000}
                if method=='getTokenAccountsByOwner': return {'value':[token(USDC_MINT,20_000000,6)] if params[1]['programId']==str(TOKEN) else [token(mint,5_000000,6)]}
                raise AssertionError(method)
        source=SimpleNamespace(fetch=lambda url:{'mint':mint,'price_usd':10,'chain_id':'solana'})
        value=wallet_equity(RPC(),user,100,None,source)
        self.assertEqual(value,170)
        self.assertTrue(source._bankroll_valuation['complete'])
    def test_unpriceable_asset_is_unknown_not_silently_valued_as_zero(self):
        user=str(Pubkey.new_unique());mint=str(Pubkey.new_unique())
        class RPC:
            def call(self,method,params):
                if method=='getBalance': return {'value':1000000000}
                return {'value':[{'account':{'data':{'parsed':{'info':{'owner':user,'mint':mint,'tokenAmount':{'amount':'100','decimals':0}}}}}}] if params[1]['programId']==str(TOKEN2022) else []}
        def fail(url): raise ConnectionError()
        source=SimpleNamespace(fetch=fail)
        self.assertEqual(wallet_equity(RPC(),user,100,None,source),100)
        self.assertFalse(source._bankroll_valuation['complete']);self.assertIn(mint,source._bankroll_valuation['unpriced_mints'])
