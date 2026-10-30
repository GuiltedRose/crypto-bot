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
        start = cursor if cursor is not None else start
        if type(start) is not int or start < 0:
            raise ValueError('Explicit initial finalized start slot required')
        # Failover only on transport failure, never on 429/access denial/quota errors.
        for index, rpc in enumerate(self.endpoints):
            try:
                rpc.validate()
                finalized = rpc.call('getSlot', [{'commitment': 'finalized'}])
                end = min(start + count - 1, finalized)
                if end < start:
                    return {'status': 'caught_up', **self.store.metrics()}
                deployments = self.deployments(rpc)
                slots = rpc.call('getBlocks', [start, end, {'commitment': 'finalized'}])
                if not isinstance(slots, list):
                    raise ValueError('Finalized slot manifest unavailable')
                decoded = []
                for slot in slots:
                    block = rpc.call('getBlock', [slot, {'commitment': 'finalized', 'encoding': 'json',
                                     'transactionDetails': 'full', 'maxSupportedTransactionVersion': 1,
                                     'rewards': False}])
                    if not block or not isinstance(block.get('transactions'), list):
                        raise ValueError('Missing finalized block; checkpoint unchanged')
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
                    decoded.append((slot, block, events, sorted(set(issues)), relevant))
                self.store.commit_range(start, end, slots, decoded, rpc.source)
                return {'status': 'indexed', 'range': [start, end], **self.store.metrics()}
            except ConnectionError:
                if index + 1 == len(self.endpoints):
                    raise
        raise ValueError('No authorized endpoint available')


def follow(ingester, start=None, count=8, interval=5, max_failures=6):
    failures = 0
    while True:
        try:
            result = ingester.step(start, count)
            start = None
            failures = 0
            yield result
            time.sleep(max(1, interval))
        except SourceAccessBlocked:
            raise
        except (ConnectionError, ValueError) as error:
            failures += 1
            delay = min(60, max(1, interval) * 2 ** min(failures, 6))
            yield {'status': 'paused', 'reason': str(error), 'error_type': type(error).__name__,
                   'rpc_code': getattr(error, 'rpc_code', None), 'retry_seconds': delay,
                   'checkpoint': ingester.store.next_slot()}
            if failures >= max_failures:
                raise ValueError('Bounded reconnect attempts exhausted; restart from saved checkpoint') from None
            time.sleep(delay)
