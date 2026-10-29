"""Live Solana submission and durable recovery. No simulated fills.

Accepts locally signed, independently validated transactions from the execution
adapter. This module never obtains wallet secrets or builds strategy orders.
"""
import base64
import json
import sqlite3
import time
import urllib.request


class Rpc:
    def __init__(self, endpoint):
        if not endpoint.startswith('https://'):
            raise ValueError('RPC endpoint must use HTTPS')
        self.endpoint = endpoint

    def call(self, method, params):
        body = json.dumps(dict(jsonrpc='2.0', id=1, method=method, params=params)).encode()
        request = urllib.request.Request(self.endpoint, data=body, headers={'Content-Type': 'application/json', 'Accept':'application/json', 'User-Agent':'SolanaSpotBot/0.1'})
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.load(response)
        if 'error' in result:
            raise RuntimeError('RPC rejected ' + method)
        return result['result']


class LiveOrders:
    def __init__(self, rpc, state_path):
        self.rpc = rpc
        self.db = sqlite3.connect(state_path)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('''CREATE TABLE IF NOT EXISTS orders (
            order_id TEXT PRIMARY KEY, signature TEXT UNIQUE NOT NULL,
            wire TEXT NOT NULL, state TEXT NOT NULL, created REAL NOT NULL)''')
        self.db.commit()

    def submit(self, order_id, signature, signed_bytes):
        """Journal before send. Never automatically retry an ambiguous send.

        Caller must derive signature from signed_bytes, validate instructions,
        and reserve portfolio exposure before invoking this method.
        """
        if not order_id or not signature or not signed_bytes:
            raise ValueError('Order ID, derived signature and signed transaction required')
        wire = base64.b64encode(signed_bytes).decode()
        # A pending/uncertain order blocks subsequent orders until reconciled.
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if self.db.execute("SELECT 1 FROM orders WHERE state IN ('pending','uncertain')").fetchone():
                raise RuntimeError('Unresolved live order; reconcile before another submission')
            self.db.execute('INSERT INTO orders VALUES (?,?,?,?,?)',
                            (order_id, signature, wire, 'pending', time.time()))
        try:
            returned = self.rpc.call('sendTransaction', [wire, {
                'encoding': 'base64', 'skipPreflight': False,
                'preflightCommitment': 'confirmed', 'maxRetries': 0}])
            if returned != signature:
                raise RuntimeError('Returned signature differs from locally signed transaction')
        except Exception:
            with self.db:
                self.db.execute("UPDATE orders SET state='uncertain' WHERE order_id=?", (order_id,))
            raise
        return self.reconcile(order_id)

    def reconcile(self, order_id):
        row = self.db.execute('SELECT signature,state FROM orders WHERE order_id=?', (order_id,)).fetchone()
        if row is None:
            raise ValueError('Unknown order')
        signature, previous = row
        if previous in ('confirmed', 'failed'):
            return previous
        result = self.rpc.call('getSignatureStatuses', [[signature], {'searchTransactionHistory': True}])
        status = result['value'][0]
        state = 'uncertain'
        if status and status.get('confirmationStatus') in ('confirmed', 'finalized'):
            state = 'failed' if status.get('err') is not None else 'confirmed'
        with self.db:
            self.db.execute('UPDATE orders SET state=? WHERE order_id=?', (state, order_id))
        return state

    def unresolved(self):
        return self.db.execute("SELECT order_id,signature,state FROM orders WHERE state IN ('pending','uncertain')").fetchall()
