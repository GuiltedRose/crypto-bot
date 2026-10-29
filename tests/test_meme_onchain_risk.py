import base64
import copy
import unittest
from unittest.mock import patch
from solders.pubkey import Pubkey
from meme_onchain_risk import OnchainRisk
from test_meme_live import CurveRpc,token


class RiskTests(unittest.TestCase):
    def setUp(self):
        rpc=CurveRpc();self.mint=rpc.mint;self.pool=rpc.pool
        raw=bytearray(base64.b64decode(rpc.records[str(rpc.mint)]['data'][0]));raw[36:44]=(1000).to_bytes(8,'little')
        rpc.records[str(rpc.mint)]['data'][0]=base64.b64encode(raw).decode()
        vault=Pubkey.new_unique();self.keys=[str(vault)];self.accounts=[token(rpc.mint,rpc.pool,800)]
        for _ in range(3):
            self.keys.append(str(Pubkey.new_unique()));self.accounts.append(token(rpc.mint,Pubkey.new_unique(),10))
        self.transactions={f'sig{i}':{'slot':i,'transaction':{'message':{'accountKeys':[{'pubkey':str(Pubkey.new_unique()),'signer':True}]}}} for i in range(3)}
        self.missing=False
        test=self
        class RPC:
            def call(self,method,params):
                if method=='getTokenLargestAccounts': return {'value':[{'address':k} for k in test.keys]}
                if method=='getSignaturesForAddress': return [{'signature':s,'blockTime':1000,'err':None} for s in test.transactions]
                if method=='getTransaction': return None if test.missing else test.transactions[params[0]]
                raise AssertionError(method)
        class Adapter:
            mint=rpc.mint;pool=rpc.pool
            def accounts(self,keys,slot): return test.accounts,100
        self.adapter=Adapter();self.rpc=RPC();self.market={'records':[None,None,None,rpc.records[str(rpc.mint)]],
                'slot':100,'base_vault':vault,'token_program':__import__('meme_execution').TOKEN}
        self.risk=OnchainRisk(self.rpc,clock=lambda:1100)
    def assess(self):
        with patch('meme_onchain_risk.classify_transaction',side_effect=lambda tx,w,s,n:{'signals':[{'mint':str(self.mint),'action':'buy'}]}):
            return self.risk.assess(self.adapter,self.market)
    def test_bounded_pass_does_not_certify_bundle_or_human_safety(self):
        report=self.assess();self.assertTrue(report['passed'],report)
        self.assertEqual(report['largest_sampled_owner_pct'],1)
        self.assertEqual(report['pool_vault_supply_pct'],80)
        self.assertFalse(report['launch_history_complete']);self.assertFalse(report['independent_human_verified'])
        self.assertIsNone(report['bundled_supply_pct']);self.assertIsNone(report['bot_buy_volume_pct'])
    def test_concentrated_owner_is_rejected_without_counting_pool_as_whale(self):
        self.accounts[1]=token(self.mint,Pubkey.new_unique(),150)
        report=self.assess();self.assertFalse(report['passed']);self.assertEqual(report['status'],'flagged')
    def test_empty_or_unavailable_transaction_sample_does_not_pass(self):
        self.transactions={};self.assertEqual(self.assess()['status'],'insufficient_coverage')
        self.setUp();self.missing=True;self.assertFalse(self.assess()['passed'])
    def test_distinct_wallet_buys_in_same_slot_are_flagged_as_possible_coordination(self):
        self.transactions['sig3']=copy.deepcopy(self.transactions['sig0'])
        self.transactions['sig3']['transaction']['message']['accountKeys'][0]['pubkey']=str(Pubkey.new_unique())
        for tx in self.transactions.values(): tx['slot']=1
        report=self.assess();self.assertFalse(report['passed']);self.assertTrue(any('slot' in r for r in report['reasons']))
    def test_bad_mint_identity_fails_closed(self):
        self.accounts[1]=token(Pubkey.new_unique(),Pubkey.new_unique(),10)
        self.assertEqual(self.assess()['status'],'unavailable')
