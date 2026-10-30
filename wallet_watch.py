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
SYSTEM_PROGRAM = '11111111111111111111111111111111'
ASSOCIATED_TOKEN_PROGRAM = 'ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL'


def decode58(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 2048:
        raise ValueError('Invalid instruction bytes')
    n = 0
    for char in value:
        n = n*58+ALPHABET.index(char)
    return bytes(len(value)-len(value.lstrip('1')))+n.to_bytes((n.bit_length()+7)//8, 'big')


def classify_transaction(tx, wallet, signature, now, allow_historical=False):
    """Conservative parsed-RPC classifier; ambiguous transfers remain unclassified."""
    base = {'wallet': wallet, 'signature': signature, 'classification': 'unclassified',
            'signals': [], 'independent_human_verified': False, 'automatic_trade_approval': False}
    try:
        meta, transaction = tx['meta'], tx['transaction']
        if meta['err'] is not None or transaction['signatures'][0] != signature:
            return base
        observed = tx['blockTime']
        age=now-observed
        if (isinstance(observed, bool) or not isinstance(observed, (int, float))
                or age<0 or (not allow_historical and age>300)):
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
        matched = []
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
                matched.append((mint, quote, action))
        # Reject routed/multi-trade ambiguity rather than crediting a transfer.
        if len(matched) != 1:
            return base
        mint, quote, action = matched[0]
        delta = deltas.get(mint, 0)
        quote_delta = deltas.get(quote, 0)
        if quote == SOL:
            quote_delta += meta['postBalances'][user_index]-meta['preBalances'][user_index]
        if action == 'buy' and not (delta > 0 and quote_delta < 0):
            return base
        if action == 'sell' and not (delta < 0 and quote_delta > 0):
            return base
        quote_decimals=9 if quote==SOL else 6
        base.update(classification='pump_trade_candidate', signals=[{
            'mint': mint, 'action': action, 'token_delta_units': delta, 'decimals': decimals[mint],
            'quote_mint': quote, 'quote_delta_units': quote_delta,
            'quote_decimals':quote_decimals,'quote_delta_amount':quote_delta/(10**quote_decimals),
            'quote_delta_note': 'net wallet change; may include rent or other transaction movements'}])
    except (KeyError, TypeError, ValueError, IndexError, StopIteration, OverflowError):
        pass
    return base


def _owner_mint_balances(tx,wallet):
    balances={}
    meta=tx.get('meta',{})
    for field in ('preTokenBalances','postTokenBalances'):
        values={}
        for row in meta.get(field,[]):
            if row.get('owner')!=wallet: continue
            mint=row.get('mint')
            amount=row.get('uiTokenAmount',{}).get('amount')
            if not isinstance(mint,str) or not isinstance(amount,str): continue
            try: values[mint]=values.get(mint,0)+int(amount)
            except ValueError: continue
        balances[field]=values
    return balances


def _verified_followed_swap(tx,wallet,event):
    if event.get('classification')!='pump_trade_candidate' or len(event.get('signals',[]))!=1:
        return None
    signal=event['signals'][0]
    instructions=tx.get('transaction',{}).get('message',{}).get('instructions',[])
    for instruction in instructions:
        if instruction.get('programId') not in (SYSTEM_PROGRAM,ASSOCIATED_TOKEN_PROGRAM):
            continue
        accounts=instruction.get('accounts',[])
        if wallet in accounts:
            return None
    for instruction in tx.get('transaction',{}).get('message',{}).get('instructions',[]):
        program=instruction.get('programId')
        if program in SPECS or program=='ComputeBudget111111111111111111111111111111':
            continue
        if wallet in instruction.get('accounts',[]):
            return None
    balances=_owner_mint_balances(tx,wallet)
    pre,post=balances['preTokenBalances'],balances['postTokenBalances']
    mint=signal['mint']
    pre_units=pre.get(mint)
    post_units=post.get(mint)
    if pre_units is None or post_units is None or post_units-pre_units!=signal['token_delta_units']:
        return None
    quote=signal['quote_mint']
    if quote!=SOL:
        keys=tx.get('transaction',{}).get('message',{}).get('accountKeys',[])
        if keys and isinstance(keys[0],dict) and keys[0].get('pubkey')==wallet:
            return None
    allowed={mint,quote}
    if any(pre.get(key,0)!=post.get(key,0) for key in set(pre)|set(post) if key not in allowed):
        return None
    if quote!=SOL and post.get(quote,0)-pre.get(quote,0)!=signal['quote_delta_units']:
        return None
    amount=signal.get('quote_delta_amount')
    if type(amount) not in (int,float):
        return None
    return dict(signal,pre_token_units=pre_units,post_token_units=post_units,
                quote_delta_amount=amount)


def followed_wallet_outcomes(database,followed_wallets):
    """Reconstruct forward-observed, flat-to-flat outcomes from followed-wallet receipts."""
    followed=set(followed_wallets)
    if not followed or not Path(database).exists(): return []
    db=sqlite3.connect('file:'+str(Path(database).resolve())+'?mode=ro',uri=True)
    try:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='followed_wallet_events'").fetchone():
            return []  # Older collector DB: no verified cycle labels yet.
        rows=db.execute('SELECT id,wallet,signature,observed,kind,event,raw FROM followed_wallet_events ORDER BY observed,id').fetchall()
    finally:
        db.close()
    cycles={};prior={};outcomes=[]
    for _,wallet,signature,observed,kind,event_raw,raw in rows:
        if wallet not in followed: continue
        if kind=='gap':
            cycles={key:value for key,value in cycles.items() if key[0]!=wallet}
            continue
        tx=json.loads(raw);event=json.loads(event_raw)
        balances=_owner_mint_balances(tx,wallet)
        pre,post=balances['preTokenBalances'],balances['postTokenBalances']
        signal=_verified_followed_swap(tx,wallet,event) if kind=='trade' else None
        matched_mint=signal.get('mint') if signal else None
        for key in list(cycles):
            if key[0]!=wallet: continue
            mint=key[1]
            if pre.get(mint,0)!=post.get(mint,0) and mint!=matched_mint:
                del cycles[key]
        if signal is None: continue
        mint=signal['mint'];key=(wallet,mint)
        if key not in cycles:
            if signal['action']!='buy' or signal['pre_token_units']!=0: continue
            count,wins=prior.get(wallet,(0,0))
            cycles[key]={'quote_mint':signal['quote_mint'],'quote_decimals':signal['quote_decimals'],
                'units':0,'net_quote_units':0,'entry_at':observed,'entry_signature':signature,
                'entry_context':{'followed_wallet_features':{
                    'buy_size_quote':-signal['quote_delta_amount'],
                    'prior_win_rate':wins/count if count else None,
                    'prior_closed_trades':count,'activity_count':1},
                    'wallet_origin':'configured_followed_wallet',
                    'label_basis':'receipt_reconstructed_flat_to_flat_quote_pnl'}}
        cycle=cycles[key]
        if cycle['quote_mint']!=signal['quote_mint'] or cycle['units']!=signal['pre_token_units']:
            del cycles[key]
            continue
        cycle['units']=signal['post_token_units']
        cycle['net_quote_units']+=signal['quote_delta_units']
        if cycle['units']==0 and signal['action']=='sell':
            net_quote=cycle['net_quote_units']/(10**cycle['quote_decimals'])
            result={'source':'followed_wallet','wallet':wallet,'mint':mint,
                'entry_at':cycle['entry_at'],'exit_at':observed,
                'entry_signature':cycle['entry_signature'],'exit_signature':signature,
                'quote_mint':cycle['quote_mint'],'net_pnl_quote':net_quote,
                'entry_context':cycle['entry_context'],'label_kind':'receipt_reconstructed_flat_to_flat'}
            outcomes.append(result)
            count,wins=prior.get(wallet,(0,0))
            prior[wallet]=(count+1,wins+int(cycle['net_quote_units']>0))
            del cycles[key]
    return outcomes


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
        self.db.execute('''CREATE TABLE IF NOT EXISTS followed_wallet_state(
            wallet TEXT PRIMARY KEY,cursor TEXT NOT NULL)''')
        self.db.execute('''CREATE TABLE IF NOT EXISTS followed_wallet_events(
            id INTEGER PRIMARY KEY,wallet TEXT NOT NULL,signature TEXT NOT NULL,
            observed REAL NOT NULL,kind TEXT NOT NULL,event TEXT NOT NULL,raw TEXT NOT NULL,
            UNIQUE(wallet,signature))''')
        self.db.commit()
        self.rpc, self.limit, self.checked_network = rpc or PublicRpc(), transactions_per_poll, False
        self.followed_wallets=set()

    def set_followed_wallets(self,wallets):
        self.followed_wallets=set(wallets)

    def _record_followed_signatures(self,wallet,records,now):
        if not records: return
        newest=records[0]['signature']
        row=self.db.execute('SELECT cursor FROM followed_wallet_state WHERE wallet=?',(wallet,)).fetchone()
        if row is None:
            with self.db:
                self.db.execute('INSERT INTO followed_wallet_state VALUES(?,?)',(wallet,newest))
            return
        cursor=row[0]
        indices=[i for i,item in enumerate(records) if item.get('signature')==cursor]
        if not indices:
            with self.db:
                marker='gap:'+str(records[0].get('blockTime',now))+':'+newest
                self.db.execute('INSERT OR IGNORE INTO followed_wallet_events(wallet,signature,observed,kind,event,raw) VALUES(?,?,?,?,?,?)',
                    (wallet,marker,records[0].get('blockTime',now),'gap','{}','{}'))
                self.db.execute('UPDATE followed_wallet_state SET cursor=? WHERE wallet=?',(newest,wallet))
            return
        fresh=records[:indices[0]]
        if len(fresh)>self.limit:
            with self.db:
                marker='gap:'+str(records[0].get('blockTime',now))+':'+newest
                self.db.execute('INSERT OR IGNORE INTO followed_wallet_events(wallet,signature,observed,kind,event,raw) VALUES(?,?,?,?,?,?)',
                    (wallet,marker,records[0].get('blockTime',now),'gap','{}','{}'))
                self.db.execute('UPDATE followed_wallet_state SET cursor=? WHERE wallet=?',(newest,wallet))
            return
        captured=[]
        for item in reversed(fresh):
            signature=item.get('signature')
            if not signature: return
            tx=self.rpc.call('getTransaction',[signature,{'encoding':'jsonParsed','commitment':'confirmed','maxSupportedTransactionVersion':1}])
            if tx is None: return
            event=classify_transaction(tx,wallet,signature,now,allow_historical=True)
            captured.append((wallet,signature,item.get('blockTime',now),
                'trade' if event.get('signals') else 'other',json.dumps(event),json.dumps(tx)))
        with self.db:
            self.db.executemany('INSERT OR IGNORE INTO followed_wallet_events(wallet,signature,observed,kind,event,raw) VALUES(?,?,?,?,?,?)',captured)
            self.db.execute('UPDATE followed_wallet_state SET cursor=? WHERE wallet=?',(newest,wallet))

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
                if wallet in self.followed_wallets:
                    self._record_followed_signatures(wallet,records,now)
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
