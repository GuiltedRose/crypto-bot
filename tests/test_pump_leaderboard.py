import copy
import json
from pathlib import Path
import tempfile
import unittest
from solders.pubkey import Pubkey
from pump_leaderboard import parse_leaderboard,eligible_leaderboard_wallets,effective_wallets,LEADERBOARD_URL
from wallet_signals import collect_once,read_signals

PINNED=str(Pubkey.new_unique());LEADER=str(Pubkey.new_unique());MINT=str(Pubkey.new_unique())


def payload():
    return {'periodType':'daily','periodLabel':'Today','entries':[{'walletAddress':LEADER,'rank':1,
            'lastRefreshedAtMs':1000000,'pnlUsd':1000,'realizedPnlUsd':-100,'unrealizedPnlUsd':1100}]}


class LeaderboardTests(unittest.TestCase):
    def test_only_top_ten_valid_fresh_daily_wallets_are_discovered(self):
        p=payload();p['entries'] += [dict(p['entries'][0],walletAddress=str(Pubkey.new_unique()),rank=11),
                                     dict(p['entries'][0],walletAddress='0x123',rank=2)]
        rows,rejected=parse_leaderboard(p,1001)
        self.assertEqual(len(rows),1);self.assertGreater(rejected,0)
        self.assertEqual(rows[0]['provider_realized_pnl_usd'],-100)
        self.assertFalse(rows[0]['profitability_verified'])
    def test_actual_provider_daily_enum_is_accepted_without_treating_other_periods_as_daily(self):
        p=payload();p.update(periodType=3,periodLabel='24h')
        self.assertEqual(len(parse_leaderboard(p,1001)[0]),1)
        p['periodLabel']='7d'
        with self.assertRaises(ValueError): parse_leaderboard(p,1001)

    def test_stale_and_duplicate_wallets_are_not_admitted(self):
        p=payload();p['entries'].append(copy.deepcopy(p['entries'][0]));rows,_=parse_leaderboard(p,1001)
        self.assertEqual(len(rows),1)
        self.assertEqual(eligible_leaderboard_wallets(rows,3000),[])
    def test_original_wallets_stay_pinned_and_total_is_bounded(self):
        rows,_=parse_leaderboard(payload(),1001)
        merged=effective_wallets([PINNED,LEADER],rows,1001)
        self.assertEqual(merged,[PINNED,LEADER])
        pinned=[str(Pubkey.new_unique()) for _ in range(32)]
        self.assertEqual(effective_wallets(pinned,rows,1001),pinned)
    def test_collector_polls_discovered_wallet_but_reader_requires_fresh_confirmed_observation(self):
        rows,_=parse_leaderboard(payload(),1001);received=[]
        class Board:
            def poll(self,now=None): return {'leaderboard_wallets':rows,'status':'ok'}
        class Watcher:
            def poll(self,wallets,now=None):
                received.extend(wallets)
                return {'status':'bounded_sample','wallet_checks':{LEADER:{'status':'bounded_sample','checked_at':1001}},
                    'errors':{},'signals':[{'mint':MINT,'observations':[{'wallet':LEADER,'mint':MINT,'action':'buy','observed_at':1001}]}]}
        with tempfile.TemporaryDirectory() as root:
            collect_once(Watcher(),[PINNED],root,now=1001,leaderboard_source=Board())
            self.assertEqual(received,[PINNED,LEADER])
            read=read_signals([PINNED],root,now=1002)
            self.assertEqual(read['signals'][0]['observations'][0]['wallet_origin'],'leaderboard')
            self.assertFalse(read['automatic_trade_approval'])
            report=json.loads((Path(root)/'status.json').read_text());report['wallet_checks'][LEADER]['status']='unavailable'
            (Path(root)/'status.json').write_text(json.dumps(report))
            self.assertEqual(read_signals([PINNED],root,now=1002)['signals'],[])
    def test_board_failure_preserves_pinned_watchlist(self):
        received=[]
        class Board:
            def poll(self,now=None): raise ConnectionError()
        class Watcher:
            def poll(self,wallets,now=None): received.extend(wallets);return {'status':'bounded_sample','signals':[],'wallet_checks':{},'errors':{}}
        with tempfile.TemporaryDirectory() as root:
            report=collect_once(Watcher(),[PINNED],root,now=1001,leaderboard_source=Board())
            self.assertEqual(received,[PINNED]);self.assertEqual(report['leaderboard_wallets'],[])
