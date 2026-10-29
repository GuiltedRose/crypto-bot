import copy
import json
from pathlib import Path
import tempfile
import unittest
from solders.pubkey import Pubkey
from pump_hot_tokens import parse_hot_tokens,eligible_hot_tokens,HOT_TOKENS_URL,HotTokens
from wallet_signals import rank_candidate_mints,read_signals,digest,collect_once
from trend_pullback import social_interest
from meme_dip import DipRules

MINT=str(Pubkey.new_unique());WALLET=str(Pubkey.new_unique())


def payload():
    return {'board':'movers','version':0,'serverTs':1000000,'entries':[
        {'m':MINT,'c':'solana:mainnet','pg':'pump','t':'HOT','kol':7},
        {'m':'0x1234','c':'eip155:1','pg':'uniswap_v4','t':'OTHER','kol':0}]}


class HotTokenTests(unittest.TestCase):
    def test_keeps_served_board_label_and_filters_other_chains_and_duplicates(self):
        p=payload();p['entries'].append(copy.deepcopy(p['entries'][0]))
        rows,rejected=parse_hot_tokens(p,1001)
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['board'],'movers')
        self.assertEqual(rows[0]['provider_reported_kol_count'],7)
        self.assertFalse(rows[0]['kol_identities_verified']);self.assertFalse(rows[0]['automatic_trade_approval'])
    def test_stale_future_wrong_schema_and_wrong_program_are_not_trending_proof(self):
        for now in [999,1121]:
            with self.assertRaises(ValueError): parse_hot_tokens(payload(),now)
        p=payload();p['version']=1
        with self.assertRaises(ValueError): parse_hot_tokens(p,1001)
        p=payload();p['entries'][0]['pg']='other'
        self.assertEqual(parse_hot_tokens(p,1001)[0],[])
    def test_reader_does_not_refresh_old_hot_membership(self):
        rows,_=parse_hot_tokens(payload(),1001)
        self.assertEqual(len(eligible_hot_tokens(rows,1100)),1)
        self.assertEqual(eligible_hot_tokens(rows,1121),[])
    def test_hot_wallet_overlap_is_prioritized_but_hot_alone_is_not_social_approval(self):
        rows,_=parse_hot_tokens(payload(),1001);other=str(Pubkey.new_unique())
        report={'candidate_mints':[other,MINT],'hot_tokens':rows,'signals':[{'mint':other,'watched_buyers':2},{'mint':MINT,'watched_buyers':1}]}
        self.assertEqual(rank_candidate_mints(report)[0],MINT)
        row={'mint':MINT,'research_context':{'hot_tokens':rows}}
        interest=social_interest(row,1002,DipRules())
        self.assertTrue(interest['hot_board']);self.assertFalse(interest['present']);self.assertFalse(interest['hot_watched_overlap'])
        row['research_context']['wallet_signals']=[{'mint':MINT,'observations':[{'mint':MINT,'wallet':WALLET,'action':'buy','observed_at':1001}]}]
        interest=social_interest(row,1002,DipRules())
        self.assertTrue(interest['hot_watched_overlap']);self.assertTrue(interest['present'])
        self.assertFalse(interest['independent_human_verified'])
    def test_shared_reader_adds_fresh_hot_coins_without_inventing_wallet_buys(self):
        with tempfile.TemporaryDirectory() as root:
            rows,_=parse_hot_tokens(payload(),1001)
            report={'schema':1,'watchlist_digest':digest([WALLET]),'published_at':1001,
                'wallet_checks':{},'signals':[],'callouts':[],'hot_tokens':rows,'candidate_mints':[],'errors':{},'status':'bounded_sample'}
            (Path(root)/'status.json').write_text(json.dumps(report))
            read=read_signals([WALLET],root,now=1002)
            self.assertIn(MINT,read['candidate_mints']);self.assertEqual(read['signals'],[])
            self.assertFalse(read['automatic_trade_approval'])
    def test_hot_source_failure_does_not_erase_existing_wallet_feed(self):
        class Watcher:
            def poll(self,wallets,now=None): return {'status':'bounded_sample','wallet_checks':{},'signals':[],'candidate_mints':['existing'],'errors':{}}
        class Failed:
            def poll(self,now=None): raise ConnectionError()
        with tempfile.TemporaryDirectory() as root:
            result=collect_once(Watcher(),[WALLET],root,now=1001,hot_source=Failed())
            self.assertEqual(result['candidate_mints'],['existing']);self.assertEqual(result['hot_tokens'],[])
            self.assertIn('hot_token_source',result)
