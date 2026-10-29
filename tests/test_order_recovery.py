import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from solders.pubkey import Pubkey
from live import LiveOrders,RpcError
from meme_live import MemeLiveEngine

class SubmissionTests(unittest.TestCase):
    def test_preflight_rejection_is_terminal_but_timeout_is_uncertain(self):
        for error,expected in ((RpcError('sendTransaction',-32002),'rejected'),
                               (RpcError('sendTransaction',-32003),'rejected'),
                               (RpcError('sendTransaction',-32005),'uncertain'),
                               (TimeoutError(),'uncertain')):
            with self.subTest(expected=expected,error=type(error).__name__),tempfile.TemporaryDirectory() as root:
                class RPC:
                    def call(self,*args): raise error
                orders=LiveOrders(RPC(),root+'/orders.sqlite')
                if expected=='rejected': self.assertEqual(orders.submit('id','sig',b'wire'),'rejected')
                else:
                    with self.assertRaises(type(error)): orders.submit('id','sig',b'wire')
                self.assertEqual(orders.db.execute('select state from orders').fetchone()[0],expected)
                self.assertEqual(len(orders.unresolved()),int(expected=='uncertain'))
                orders.db.close()

class ExpirationTests(unittest.TestCase):
    def setUp(self):
        self.engine=object.__new__(MemeLiveEngine)
        self.engine.user=str(Pubkey.new_unique())
        self.pending={'id':'id','side':'buy','last_valid_block_height':100,
                      'pre_inventory':{'native_lamports':1000,'base_balance':0,'quote_balance':0},
                      'base_account':str(Pubkey.new_unique()),'quote_account':str(Pubkey.new_unique()),
                      'mint':str(Pubkey.new_unique()),'token_program':str(Pubkey.new_unique())}
        self.results={'getBlockHeight':101,'getSignatureStatuses':{'context':{'slot':200},'value':[None]},
                      'getTransaction':None,'getMultipleAccounts':{'value':[{'lamports':1000},None,None]}}
        outer=self
        class RPC:
            def call(self,method,params):
                value=outer.results[method]
                if isinstance(value,Exception): raise value
                return value
        self.engine.rpc=RPC()
        self.engine.orders=type('Orders',(),{'db':sqlite3.connect(':memory:')})()
        self.engine.orders.db.execute('create table orders(order_id text,state text,created real)')
        self.engine.orders.db.execute("insert into orders values('id','uncertain',1000)")
    def tearDown(self): self.engine.orders.db.close()
    def test_expired_absent_and_unchanged_releases_once(self):
        self.assertEqual(self.engine.resolve_expired(self.pending,'sig'),'expired_unfilled')
        self.assertEqual(self.engine.orders.db.execute('select state from orders').fetchone()[0],'expired_unfilled')
    def test_not_expired_missing_baseline_or_sell_stays_blocked(self):
        for override in ({'last_valid_block_height':101},{'side':'sell'}):
            self.assertEqual(self.engine.resolve_expired(dict(self.pending,**override),'sig'),'uncertain')
    def test_seen_status_or_receipt_stays_blocked(self):
        for method,value in [('getSignatureStatuses',{'context':{'slot':200},'value':[{'confirmationStatus':'processed'}]}),('getTransaction',{'slot':190})]:
            old=self.results[method];self.results[method]=value
            self.assertEqual(self.engine.resolve_expired(self.pending,'sig'),'uncertain')
            self.results[method]=old
    def test_changed_balances_block_replacement(self):
        self.results['getMultipleAccounts']['value'][0]['lamports']=999
        self.assertEqual(self.engine.resolve_expired(self.pending,'sig'),'uncertain')
        self.results['getMultipleAccounts']['value'][0]['lamports']=1000
        with patch('meme_live.token_balance',side_effect=[1,0]):
            self.assertEqual(self.engine.resolve_expired(self.pending,'sig'),'uncertain')
    def test_rpc_error_does_not_clear_order(self):
        self.results['getTransaction']=TimeoutError()
        with self.assertRaises(TimeoutError):self.engine.resolve_expired(self.pending,'sig')
        self.assertEqual(self.engine.orders.db.execute('select state from orders').fetchone()[0],'uncertain')

    def test_legacy_order_clears_only_when_finalized_history_spans_submission(self):
        self.pending.pop('pre_inventory')
        self.results['getSignaturesForAddress']=[{'signature':'older','blockTime':800,'slot':150}]
        self.results['getFirstAvailableBlock']=100
        self.assertEqual(self.engine.resolve_expired(self.pending,'sig'),'expired_unfilled')

    def test_legacy_history_missing_seen_or_pruned_stays_blocked(self):
        self.pending.pop('pre_inventory')
        self.results['getFirstAvailableBlock']=100
        for rows in ([],[{'signature':'sig','blockTime':800,'slot':150}],
                     [{'signature':'recent','blockTime':990,'slot':190}],
                     [{'signature':'old','blockTime':None,'slot':150}]):
            self.results['getSignaturesForAddress']=rows
            self.assertEqual(self.engine.resolve_expired(self.pending,'sig'),'uncertain')
        self.results['getSignaturesForAddress']=[{'signature':'old','blockTime':800,'slot':150}]
        self.results['getFirstAvailableBlock']=160
        self.assertEqual(self.engine.resolve_expired(self.pending,'sig'),'uncertain')
