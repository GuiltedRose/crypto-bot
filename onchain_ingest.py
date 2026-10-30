"""Bounded, read-only finalized block polling with transactional recovery."""
import base64
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, build_opener
from data_policy import SourceAccessBlocked
from provider_limits import BUDGET
from pump_narratives import NoRedirect
from wallet_watch import MAINNET, PublicRpcError
from onchain_decode import Decoder

READ_METHODS = {'getGenesisHash', 'getSlot', 'getBlocks', 'getBlock',
                'getAccountInfo', 'getSignaturesForAddress', 'getTransaction'}


class IndexRpc:
    def __init__(self, endpoint, budget=None):
        self.url = endpoint['url']
        parsed = urlparse(self.url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('HTTPS RPC endpoint required')
        self.source = endpoint['source_id']
        self.interval = max(.5, float(endpoint.get('interval_seconds', 2)))
        self.budget = budget or BUDGET
        self.validated = False

    def call(self, method, params):
        if method not in READ_METHODS:
            raise ValueError('Indexer RPC is strictly read-only')
        self.budget.reserve(self.url, self.interval)
        request = Request(self.url, data=json.dumps({'jsonrpc': '2.0', 'id': 1,
                          'method': method, 'params': params}).encode(),
                          headers={'Content-Type': 'application/json', 'User-Agent': 'SolanaOnchainIndex/1'})
        try:
            with build_opener(NoRedirect).open(request, timeout=30) as response:
                raw = response.read(64_000_001)
        except HTTPError as error:
            self.budget.failure(self.url, error.code, error.headers)
            # Do not expose endpoint paths, which may contain credentials.
            if error.code in (401, 403):
                raise SourceAccessBlocked('RPC access denied; review authorization') from None
            raise ValueError('RPC HTTP error ' + str(error.code)) from None
        except (URLError, TimeoutError, OSError):
            self.budget.failure(self.url, 503)
            raise ConnectionError('RPC transport unavailable') from None
        if len(raw) > 64_000_000:
            raise ValueError('RPC block response exceeds configured safety bound')
        payload = json.loads(raw)
        if 'error' in payload:
            code = payload['error'].get('code')
            if code in (401, 403):
                self.budget.failure(self.url, code)
                raise SourceAccessBlocked('RPC access denied; review authorization')
            if code in (429, -32005):
                self.budget.failure(self.url, 429 if code == 429 else 503)
            raise PublicRpcError(code)
        self.budget.success(self.url)
        return payload['result']

    def validate(self):
        if not self.validated:
            if self.call('getGenesisHash', []) != MAINNET:
                raise ValueError('Mainnet genesis mismatch')
            self.validated = True


class Ingester:
    def __init__(self, store, endpoints, versions=None, decoder=None):
        if not endpoints:
            raise ValueError('At least one explicitly authorized RPC required')
        self.store, self.endpoints = store, endpoints
        self.decoder = decoder or Decoder()
        self.versions = versions or {}
        for rpc in endpoints:
            self.store.policy(rpc.source)

    def deployments(self, rpc):
        """Read upgradeable-loader ProgramData, without assuming today's IDL is historical."""
        from solders.pubkey import Pubkey
        loader = 'BPFLoaderUpgradeab1e11111111111111111111111'
        result = {}
        for program in self.decoder.specs:
            account = rpc.call('getAccountInfo', [program, {'encoding': 'base64', 'commitment': 'finalized'}])['value']
            if not account or account['owner'] != loader or not account['executable']:
                raise ValueError('Unrecognized program loader/identity')
            raw = base64.b64decode(account['data'][0], validate=True)
            if len(raw) != 36 or int.from_bytes(raw[:4], 'little') != 2:
                raise ValueError('Invalid program account')
            address = str(Pubkey.from_bytes(raw[4:36]))
            data = rpc.call('getAccountInfo', [address, {'encoding': 'base64', 'commitment': 'finalized',
                                                       'dataSlice': {'offset': 0, 'length': 12}}])['value']
            if not data or data['owner'] != loader:
                raise ValueError('Invalid ProgramData owner')
            raw = base64.b64decode(data['data'][0], validate=True)
            if len(raw) != 12 or int.from_bytes(raw[:4], 'little') != 3:
                raise ValueError('Invalid ProgramData layout')
            result[program] = int.from_bytes(raw[4:12], 'little')
        return result

    def version_issues(self, slot, deployments):
        issues = []
        for program, spec in self.decoder.specs.items():
            approved = [v for v in self.versions.get(program, [])
                        if v['from_slot'] <= slot <= v['through_slot']
                        and v['idl_hash'] == spec['hash']
                        and v['deployment_slot'] == deployments[program]]
            if len(approved) != 1:
                issues.append('unreviewed program deployment/version: ' + program)
        return issues

    def step(self, start=None, count=8):
        if not 1 <= count <= 64:
            raise ValueError('Slot batch must be 1..64')
        cursor = self.store.next_slot()
        if cursor is not None and start is not None and cursor != start:
            raise ValueError('Resume saved checkpoint; use another database for another start')
        initial = cursor if cursor is not None else start
        if type(initial) is not int or initial < 0:
            raise ValueError('Explicit initial finalized start slot required')
        started = time.monotonic()
        # Failover resumes the durable prefix, never restarts it or evades throttles.
        for index, rpc in enumerate(self.endpoints):
            start = self.store.next_slot()
            start = initial if start is None else start
            try:
                rpc.validate()
                finalized = rpc.call('getSlot', [{'commitment': 'finalized'}])
                sampled_at = time.time()
                end = min(start + count - 1, finalized)
                if end < start:
                    return self.record_progress(finalized, sampled_at, initial, started, 'caught_up')
                deployments = self.deployments(rpc)
                slots = rpc.call('getBlocks', [start, end, {'commitment': 'finalized'}])
                if not isinstance(slots, list) or slots != sorted(set(slots)) or any(
                        type(slot) is not int or not start <= slot <= end for slot in slots):
                    raise ValueError('Invalid finalized slot manifest')
                next_slot = start
                for slot in slots:
                    block = rpc.call('getBlock', [slot, {'commitment': 'finalized', 'encoding': 'json',
                                     'transactionDetails': 'full', 'maxSupportedTransactionVersion': 1,
                                     'rewards': False}])
                    if not block or not isinstance(block.get('transactions'), list):
                        raise ValueError('Missing finalized block; durable prefix preserved')
                    events, issues, relevant = [], self.version_issues(slot, deployments), []
                    for tx_index, tx in enumerate(block['transactions']):
                        try:
                            found, errors = self.decoder.transaction(tx, slot, tx_index, block['blockTime'])
                        except (ValueError, KeyError, TypeError, IndexError) as error:
                            found, errors = [], ['transaction decode failure: ' + type(error).__name__]
                        if found or errors:
                            relevant.append((tx_index, tx))
                        events.extend(found)
                        issues.extend(errors)
                    block['_indexer_deployments'] = deployments
                    decoded = (slot, block, events, sorted(set(issues)), relevant)
                    # One block in memory. Commit known skipped slots plus this block
                    # atomically, so interruptions lose at most the in-flight request.
                    self.store.commit_range(next_slot, slot, [slot], [decoded], rpc.source)
                    next_slot = slot + 1
                if next_slot <= end:
                    self.store.commit_range(next_slot, end, [], [], rpc.source)
                return self.record_progress(finalized, sampled_at, initial, started, 'indexed')
            except ConnectionError:
                if index + 1 == len(self.endpoints):
                    raise
        raise ValueError('No authorized endpoint available')

    def record_progress(self, finalized, sampled_at, initial, started, status):
        previous = self.store.progress()
        cursor = self.store.next_slot()
        cursor = initial if cursor is None else cursor
        elapsed = max(time.monotonic() - started, .000001)
        processed = max(0, cursor - initial)
        production = None
        if previous:
            span = sampled_at - previous['head_sampled_at']
            delta = finalized - previous['finalized_head']
            if 0 < span <= 300 and delta >= 0:
                production = delta / span
        lag = max(0, finalized - cursor + 1)
        growing = bool(previous and lag > previous['lag_slots'])
        rate = processed / elapsed
        progress = {'finalized_head': finalized, 'head_sampled_at': sampled_at,
                    'next_slot': cursor, 'lag_slots': lag,
                    'lag_growing': growing, 'processed_slots': processed,
                    'elapsed_seconds': elapsed, 'indexed_slots_per_second': rate,
                    'observed_production_slots_per_second': production,
                    'keeping_up': rate >= production if production and processed else None,
                    'warning': 'Indexing lag exceeds available throughput; coverage gates remain enforced'
                               if growing or production and processed and rate < production else None}
        self.store.save_progress(progress)
        return {'status': status, 'progress': progress, **self.store.metrics()}


def follow(ingester, start=None, count=8, interval=5, max_failures=6, max_catchup_slots=64):
    if not 1 <= count <= max_catchup_slots <= 64:
        raise ValueError('Catch-up slot bounds must be 1..64')
    failures = 0
    batch = count
    while True:
        try:
            result = ingester.step(start, batch)
            if ingester.store.next_slot() is not None:
                start = None
            failures = 0
            yield result
            lag = result['progress']['lag_slots']
            batch = min(max_catchup_slots, max(count, batch * 2)) if lag else count
            # ProviderBudget still paces every RPC; only redundant idle rest is removed.
            if not lag:
                time.sleep(max(1, interval))
        except SourceAccessBlocked:
            raise
        except (ConnectionError, ValueError) as error:
            if ingester.store.next_slot() is not None:
                start = None
            failures += 1
            delay = min(60, max(1, interval) * 2 ** min(failures, 6))
            yield {'status': 'paused', 'reason': str(error), 'error_type': type(error).__name__,
                   'rpc_code': getattr(error, 'rpc_code', None), 'retry_seconds': delay,
                   'checkpoint': ingester.store.next_slot()}
            if failures >= max_failures:
                raise ValueError('Bounded reconnect attempts exhausted; restart from saved checkpoint') from None
            time.sleep(delay)
