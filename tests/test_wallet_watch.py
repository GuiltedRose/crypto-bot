import copy
import json
from pathlib import Path
import tempfile
import unittest
from wallet_watch import (ALPHABET,SOL,SPECS,MAINNET,WalletWatcher,classify_transaction,
                          followed_wallet_outcomes)
from meme_dip import DipRules
from meme_live import MemeLiveStore

WALLET = '11111111111111111111111111111111'
MINT = 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA'
PROGRAM = 'pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA'


def encode58(raw):
    n = int.from_bytes(raw, 'big')
    text = ''
    while n:
        n, index = divmod(n, 58)
        text = ALPHABET[index]+text
    return '1'*(len(raw)-len(raw.lstrip(b'\0')))+text


def transaction(action='buy', timestamp=1000, signature='sig'):
    spec = next(spec for spec in SPECS[PROGRAM] if spec['name'] == action)
    indexes = [spec['user_index'], spec['mint_index'], spec['quote_index']]
    accounts = [WALLET]*(max(indexes)+1)
    accounts[spec['user_index']] = WALLET
    accounts[spec['mint_index']] = MINT
    accounts[spec['quote_index']] = SOL
    def balance(amount):
        return {'owner': WALLET, 'mint': MINT, 'accountIndex': 1,
                'uiTokenAmount': {'amount': str(amount), 'decimals': 6}}
    return {'blockTime': timestamp,
            'transaction': {'signatures': [signature], 'message': {
                'accountKeys': [{'pubkey': WALLET, 'signer': True}],
                'instructions': [{'programId': PROGRAM, 'accounts': accounts,
                                  'data': encode58(bytes(spec['discriminator'])+bytes(16))}]}},
            'meta': {'err': None, 'fee': 5000, 'preBalances': [2000000000],
                     'postBalances': [1900000000 if action == 'buy' else 2100000000],
                     'preTokenBalances': [balance(0 if action == 'buy' else 100)],
                     'postTokenBalances': [balance(100 if action == 'buy' else 0)]}}


class WalletWatchTests(unittest.TestCase):
    def test_known_trade_instruction_and_wallet_balance_direction_required(self):
        tx = transaction()
        event = classify_transaction(tx, WALLET, 'sig', 1100)
        self.assertEqual(event['classification'], 'pump_trade_candidate')
        self.assertEqual(event['signals'][0]['mint'], MINT)
        self.assertFalse(event['independent_human_verified'])
        tx['transaction']['message']['instructions'] = []
        self.assertEqual(classify_transaction(tx, WALLET, 'sig', 1100)['signals'], [])

    def test_followed_wallet_receipt_cycle_is_labeled_only_after_forward_baseline(self):
        class Rpc:
            records=[{'signature':'baseline','blockTime':1000,'err':None}]
            txs={'baseline':transaction('buy',1000,'baseline')}
            def call(self,method,params):
                if method=='getGenesisHash': return MAINNET
                if method=='getSignaturesForAddress': return self.records
                return copy.deepcopy(self.txs[params[0]])
        with tempfile.TemporaryDirectory() as root:
            rpc=Rpc();watcher=WalletWatcher(Path(root)/'watch.sqlite',rpc)
            watcher.set_followed_wallets([WALLET])
            watcher.poll([WALLET],now=1000)
            self.assertEqual(followed_wallet_outcomes(Path(root)/'watch.sqlite',[WALLET]),[])
            buy=transaction('buy',1010,'buy')
            buy['meta']['postBalances'][0]=1_899_995_000
            sell=transaction('sell',1020,'sell')
            sell['meta']['postBalances'][0]=2_119_995_000
            rpc.txs.update(buy=buy,sell=sell)
            rpc.records=[{'signature':'buy','blockTime':1010,'err':None},
                         {'signature':'baseline','blockTime':1000,'err':None}]
            watcher.poll([WALLET],now=1010)
            rpc.records=[{'signature':'sell','blockTime':1020,'err':None},
                         {'signature':'buy','blockTime':1010,'err':None},
                         {'signature':'baseline','blockTime':1000,'err':None}]
            watcher.poll([WALLET],now=1020)
            outcomes=followed_wallet_outcomes(Path(root)/'watch.sqlite',[WALLET])
            self.assertEqual(len(outcomes),1)
            self.assertEqual(outcomes[0]['label_kind'],'receipt_reconstructed_flat_to_flat')
            self.assertAlmostEqual(outcomes[0]['net_pnl_quote'],.01999)
            self.assertEqual(outcomes[0]['entry_context']['wallet_origin'],'configured_followed_wallet')
            self.assertEqual(followed_wallet_outcomes(Path(root)/'watch.sqlite',['other-wallet']),[])
            live=MemeLiveStore(Path(root)/'live','bot-wallet',DipRules(),
                learning_wallet_db=Path(root)/'watch.sqlite',followed_wallets=[WALLET])
            self.assertEqual(live.state['learning_model_live']['samples'],1)
            self.assertEqual(live.state['learning_model_live']['wins'],1)
            live.close()
            watcher.db.close()

    def test_followed_wallet_signature_gap_prevents_outcome_labels(self):
        class Rpc:
            records=[{'signature':'baseline','blockTime':1000,'err':None}]
            def call(self,method,params):
                if method=='getGenesisHash': return MAINNET
                if method=='getSignaturesForAddress': return self.records
                return transaction('buy',1010,params[0])
        with tempfile.TemporaryDirectory() as root:
            rpc=Rpc();watcher=WalletWatcher(Path(root)/'watch.sqlite',rpc)
            watcher.set_followed_wallets([WALLET]);watcher.poll([WALLET],now=1000)
            rpc.records=[{'signature':'new-'+str(i),'blockTime':1010+i,'err':None} for i in range(20)]
            watcher.poll([WALLET],now=1030)
            self.assertTrue(watcher.db.execute(
                "SELECT 1 FROM followed_wallet_events WHERE wallet=? AND kind='gap'",(WALLET,)).fetchone())
            self.assertEqual(followed_wallet_outcomes(Path(root)/'watch.sqlite',[WALLET]),[])
            watcher.db.close()

    def test_wrong_signer_wrong_user_wrong_signature_failed_or_old_receipt_rejected(self):
        mutations = [lambda tx: tx['transaction']['message']['accountKeys'][0].update(signer=False),
                     lambda tx: tx['transaction'].update(signatures=['different']),
                     lambda tx: tx['meta'].update(err={'error': True}),
                     lambda tx: tx.update(blockTime=0),
                     lambda tx: tx['transaction']['message']['instructions'][0]['accounts'].__setitem__(1, MINT)]
        for mutate in mutations:
            tx = transaction()
            mutate(tx)
            self.assertEqual(classify_transaction(tx, WALLET, 'sig', 1100)['signals'], [])

    def test_inner_pump_trade_supported_but_ambiguous_multi_trade_not_credited(self):
        tx = transaction()
        ix = tx['transaction']['message']['instructions'].pop()
        tx['meta']['innerInstructions'] = [{'index': 0, 'instructions': [ix]}]
        self.assertEqual(classify_transaction(tx, WALLET, 'sig', 1100)['classification'], 'pump_trade_candidate')
        tx['transaction']['message']['instructions'] = transaction('sell')['transaction']['message']['instructions']
        self.assertEqual(classify_transaction(tx, WALLET, 'sig', 1100)['signals'], [])

    def test_persistent_dedup_and_sell_removes_buy_signal(self):
        class Rpc:
            records = [{'signature': 'buy', 'blockTime': 1000, 'err': None}]
            calls = 0
            def call(self, method, params):
                if method == 'getGenesisHash': return MAINNET
                if method == 'getSignaturesForAddress': return self.records
                self.calls += 1
                return transaction('sell' if params[0] == 'sell' else 'buy', 1100 if params[0] == 'sell' else 1000, params[0])
        with tempfile.TemporaryDirectory() as root:
            rpc = Rpc()
            path = Path(root)/'watch.sqlite'
            watcher = WalletWatcher(path, rpc)
            first = watcher.poll([WALLET], now=1100)
            self.assertEqual(first['candidate_mints'], [MINT])
            watcher.db.close()
            watcher = WalletWatcher(path, rpc)
            watcher.poll([WALLET], now=1100)
            self.assertEqual(rpc.calls, 1)
            rpc.records.insert(0, {'signature': 'sell', 'blockTime': 1100, 'err': None})
            self.assertEqual(watcher.poll([WALLET], now=1200)['candidate_mints'], [])
            watcher.db.close()

    def test_eight_wallet_rotation_is_bounded_and_survives_restart(self):
        wallets = json.loads(Path('paper_watchlist.json').read_text())['wallets']
        class Rpc:
            calls = []
            def call(self, method, params):
                if method == 'getGenesisHash': return MAINNET
                self.calls.append(params[0])
                return []
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'watch.sqlite'
            rpc = Rpc()
            watcher = WalletWatcher(path, rpc)
            report = watcher.poll(wallets, now=1100)
            self.assertEqual(report['wallets_polled'], wallets[:3])
            self.assertEqual(report['wallet_checks'][wallets[-1]]['status'], 'not_yet_polled')
            watcher.db.close()
            watcher = WalletWatcher(path, rpc)
            self.assertEqual(watcher.poll(wallets, now=1200)['wallets_polled'], wallets[3:6])
            watcher.poll(wallets, now=1300)
            self.assertEqual(set(rpc.calls), set(wallets))
            self.assertEqual(len(rpc.calls), 9)
            watcher.db.close()

    def test_unavailable_receipt_retries_and_wrong_network_blocks(self):
        class Rpc:
            genesis = MAINNET
            def call(self, method, params):
                if method == 'getGenesisHash': return self.genesis
                if method == 'getSignaturesForAddress': return [{'signature': 'sig', 'blockTime': 1000, 'err': None}]
                return None
        with tempfile.TemporaryDirectory() as root:
            rpc = Rpc()
            watcher = WalletWatcher(Path(root)/'watch.sqlite', rpc)
            self.assertEqual(watcher.poll([WALLET], now=1100)['deferred'], 1)
            self.assertEqual(watcher.db.execute('SELECT COUNT(*) FROM watch_events').fetchone()[0], 0)
            watcher.checked_network = False
            rpc.genesis = 'wrong'
            with self.assertRaises(ValueError): watcher.poll([WALLET], now=1100)
            watcher.db.close()


if __name__ == '__main__':
    unittest.main()
