"""Read-only, bounded public-wallet observations for paper candidate discovery.

Known Pump instruction + signer + matching net token/quote changes identifies
trade candidates, not profitable traders, independent people or safe tokens.
"""
import json
from pathlib import Path
import sqlite3
import time
from urllib.parse import urlparse
from urllib.request import Request, build_opener
from pump_narratives import NoRedirect, ALPHABET, valid_mint

SOL = 'So11111111111111111111111111111111111111112'
USDC = 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'
SPECS = json.loads(Path(__file__).with_name('pump_watch_specs.json').read_text())['programs']
MAINNET = '5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d'


def decode58(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 2048:
        raise ValueError('Invalid instruction bytes')
    n = 0
    for char in value:
        n = n*58+ALPHABET.index(char)
    return bytes(len(value)-len(value.lstrip('1')))+n.to_bytes((n.bit_length()+7)//8, 'big')


def classify_transaction(tx, wallet, signature, now):
    """Conservative parsed-RPC classifier; ambiguous transfers remain unclassified."""
    base = {'wallet': wallet, 'signature': signature, 'classification': 'unclassified',
            'signals': [], 'independent_human_verified': False, 'automatic_trade_approval': False}
    try:
        meta, transaction = tx['meta'], tx['transaction']
        if meta['err'] is not None or transaction['signatures'][0] != signature:
            return base
        observed = tx['blockTime']
        if isinstance(observed, bool) or not isinstance(observed, (int, float)) or not 0 <= now-observed <= 300:
            return base
        base['observed_at'] = observed
        keys = transaction['message']['accountKeys']
        if not all(isinstance(key, dict) for key in keys):
            return base
        user_index = next(i for i, key in enumerate(keys) if key['pubkey'] == wallet and key['signer'] is True)
        deltas, decimals = {}, {}
        for field, sign in (('preTokenBalances', -1), ('postTokenBalances', 1)):
            for record in meta.get(field, []):
                if record.get('owner') != wallet:
                    continue
                mint = record['mint']
                digits = record['uiTokenAmount']['decimals']
                if not valid_mint(mint) or type(digits) is not int or not 0 <= digits <= 18:
                    return base
                if mint in decimals and decimals[mint] != digits:
                    return base
                decimals[mint] = digits
                deltas[mint] = deltas.get(mint, 0)+sign*int(record['uiTokenAmount']['amount'])
        instructions = list(transaction['message'].get('instructions', []))
        for inner in meta.get('innerInstructions') or []:
            instructions.extend(inner['instructions'])
        matched = set()
        for ix in instructions:
            program = ix.get('programId')
            if program not in SPECS or 'data' not in ix:
                continue
            raw = decode58(ix['data'])
            for spec in SPECS[program]:
                if raw[:8] != bytes(spec['discriminator']):
                    continue
                accounts = ix['accounts']
                if accounts[spec['user_index']] != wallet:
                    continue
                mint = accounts[spec['mint_index']]
                quote = SOL if spec['quote_index'] is None else accounts[spec['quote_index']]
                if quote not in (SOL, USDC) or mint in (SOL, USDC):
                    continue
                action = 'buy' if spec['name'].startswith('buy') else 'sell'
                matched.add((mint, quote, action))
        # Reject routed/multi-trade ambiguity rather than crediting a transfer.
        if len(matched) != 1:
            return base
        mint, quote, action = matched.pop()
        delta = deltas.get(mint, 0)
        quote_delta = deltas.get(quote, 0)
        if quote == SOL:
            quote_delta += meta['postBalances'][user_index]-meta['preBalances'][user_index]
            # Remove this signer's network fee from the native delta.
            if user_index == 0:
                quote_delta += meta['fee']
        if action == 'buy' and not (delta > 0 and quote_delta < 0):
            return base
        if action == 'sell' and not (delta < 0 and quote_delta > 0):
            return base
        base.update(classification='pump_trade_candidate', signals=[{
            'mint': mint, 'action': action, 'token_delta_units': delta, 'decimals': decimals[mint],
            'quote_mint': quote, 'quote_delta_units': quote_delta,
            'quote_delta_note': 'net wallet change; may include rent or other transaction movements'}])
    except (KeyError, TypeError, ValueError, IndexError, StopIteration, OverflowError):
        pass
    return base


class PublicRpcError(ValueError):
    def __init__(self, code):
        self.rpc_code = code if type(code) is int else None
        super().__init__('Public RPC returned an error; wallet coverage unavailable')


class PublicRpc:
    def __init__(self, url='https://api.mainnet-beta.solana.com', interval=2):
        parsed = urlparse(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('Use an HTTPS public RPC endpoint')
        self.url, self.interval, self.last = url, interval, 0

    def call(self, method, params):
        if method not in ('getGenesisHash', 'getSignaturesForAddress', 'getTransaction'):
            raise ValueError('Read-only wallet RPC method required')
        wait = self.interval-(time.monotonic()-self.last)
        if wait > 0:
            time.sleep(wait)
        self.last = time.monotonic()
        request = Request(self.url, data=json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}).encode(),
                          headers={'Content-Type': 'application/json', 'User-Agent': 'SolanaPaperWalletWatch/0.1'})
        with build_opener(NoRedirect).open(request, timeout=15) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError('Excessive RPC response')
        payload = json.loads(raw)
        if 'error' in payload:
            raise PublicRpcError((payload['error'] or {}).get('code'))
        return payload['result']


class WalletWatcher:
    def __init__(self, path, rpc=None, transactions_per_poll=4):
        if type(transactions_per_poll) is not int or not 1 <= transactions_per_poll <= 8:
            raise ValueError('Use one through eight transactions per poll')
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute('CREATE TABLE IF NOT EXISTS watch_events(wallet TEXT,signature TEXT,observed REAL,event TEXT,PRIMARY KEY(wallet,signature))')
        self.db.execute('CREATE TABLE IF NOT EXISTS watch_meta(key TEXT PRIMARY KEY,value TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS watch_checks(wallet TEXT PRIMARY KEY,checked REAL,status TEXT)')
        self.db.commit()
        self.rpc, self.limit, self.checked_network = rpc or PublicRpc(), transactions_per_poll, False

    def poll(self, wallets, now=None):
        now = int(time.time()) if now is None else now
        wallets = list(dict.fromkeys(wallets))
        if not wallets or len(wallets) > 32 or any(not valid_mint(wallet) for wallet in wallets):
            raise ValueError('Watch one through 32 valid public wallet addresses')
        if not self.checked_network:
            if self.rpc.call('getGenesisHash', []) != MAINNET:
                raise ValueError('Wallet watcher requires mainnet RPC')
            self.checked_network = True
        row = self.db.execute("SELECT value FROM watch_meta WHERE key='cursor'").fetchone()
        cursor = (int(row[0]) if row else 0) % len(wallets)
        selected = [wallets[(cursor+index) % len(wallets)] for index in range(min(3, len(wallets)))]
        errors, scanned, deferred = {}, 0, 0
        for wallet in selected:
            try:
                records = self.rpc.call('getSignaturesForAddress', [wallet, {'limit': 20, 'commitment': 'confirmed'}])
                pending = [row for row in records if row.get('err') is None and type(row.get('blockTime')) is int
                           and 0 <= now-row['blockTime'] <= 300 and not self.db.execute(
                               'SELECT 1 FROM watch_events WHERE wallet=? AND signature=?', (wallet, row['signature'])).fetchone()]
                pending.sort(key=lambda row: (row['blockTime'], row['signature']), reverse=True)
                deferred += max(0, len(pending)-self.limit)
                for row in pending[:self.limit]:
                    tx = self.rpc.call('getTransaction', [row['signature'], {'encoding': 'jsonParsed', 'commitment': 'confirmed', 'maxSupportedTransactionVersion': 1}])
                    if tx is None:
                        deferred += 1  # Receipt unavailable: retry; never mark as analyzed.
                        continue
                    event = classify_transaction(tx, wallet, row['signature'], now)
                    with self.db:
                        self.db.execute('INSERT OR IGNORE INTO watch_events VALUES(?,?,?,?)',
                                        (wallet, row['signature'], row['blockTime'], json.dumps(event)))
                    scanned += 1
                with self.db:
                    self.db.execute('INSERT OR REPLACE INTO watch_checks VALUES(?,?,?)', (wallet, now, 'bounded_sample'))
            except Exception as error:
                from paper_sources import source_error
                errors[wallet] = source_error(error)
                with self.db:
                    self.db.execute('INSERT OR REPLACE INTO watch_checks VALUES(?,?,?)', (wallet, now, 'unavailable'))
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO watch_meta VALUES('cursor',?)", (str((cursor+len(selected)) % len(wallets)),))
        checks = {wallet: {'checked_at': checked, 'status': status, 'fresh': 0 <= now-checked <= 300} for wallet, checked, status in self.db.execute('SELECT wallet,checked,status FROM watch_checks') if wallet in wallets}
        for wallet in wallets:
            checks.setdefault(wallet, {'checked_at': None, 'status': 'not_yet_polled', 'fresh': False})
        # Last observed direction matters: a later sell removes a buy lead.
        latest = {}
        for wallet, raw in self.db.execute('SELECT wallet,event FROM watch_events WHERE observed>=? ORDER BY observed,signature', (now-300,)):
            if wallet not in wallets or wallet in errors or checks[wallet]['status'] != 'bounded_sample' or not checks[wallet]['fresh']:
                continue
            event = json.loads(raw)
            for signal in event['signals']:
                key = (wallet, signal['mint'])
                prior = latest.get(key)
                action = signal['action']
                if prior and prior['observed_at'] == event['observed_at'] and prior['action'] != action:
                    action = 'ambiguous'  # Timestamp ties do not establish execution order.
                latest[key] = dict(signal, action=action, wallet=wallet, signature=event['signature'], observed_at=event['observed_at'])
        buys = [signal for signal in latest.values() if signal['action'] == 'buy']
        by_mint = {}
        for signal in buys:
            by_mint.setdefault(signal['mint'], []).append(signal)
        unavailable = any(check['status'] == 'unavailable' for check in checks.values())
        unpolled = any(check['checked_at'] is None for check in checks.values())
        return {'status': 'incomplete' if errors or deferred or unavailable else 'partial_watchlist' if unpolled else 'bounded_sample',
                'wallets_requested': len(wallets), 'wallets_polled': selected, 'wallet_checks': checks,
                'transactions_analyzed': scanned, 'deferred': deferred,
                'errors': errors, 'candidate_mints': sorted(by_mint),
                'signals': [{'mint': mint, 'watched_buyers': len(signals), 'observations': signals,
                             'independent_human_verified': False} for mint, signals in sorted(by_mint.items())],
                'limitations': 'Recent wallet-referenced transactions only; capped history may miss trades. Wallets may be bots, linked or unprofitable. No full P&L or trader ranking.',
                'automatic_trade_approval': False}
