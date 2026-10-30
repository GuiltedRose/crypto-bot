"""Isolated deterministic tests; synthetic events are not chain observations."""
import base64
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import patch
from solders.pubkey import Pubkey
from onchain_decode import Decoder, EVENT_IX
from onchain_store import EventStore
from onchain_ingest import Ingester, IndexRpc
from onchain_market import OnchainMarket, OnchainPaperSources
from data_policy import require_learning_rights, SourceAccessBlocked
from wallet_watch import SOL

MINT = str(Pubkey.from_bytes(bytes([7])*32))
POOL = str(Pubkey.from_bytes(bytes([8])*32))
USER = str(Pubkey.from_bytes(bytes([9])*32))
POLICY = {'storage_verified': True, 'ml_verified': False, 'evaluation_verified': False,
          'evidence': 'synthetic unit fixtures only; no external provider license asserted',
          'usd_conversion_verified': True}


def encode58(raw):
    alphabet = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
    n, value = int.from_bytes(raw, 'big'), ''
    while n:
        n, r = divmod(n, 58)
        value = alphabet[r] + value
    return '1' * (len(raw)-len(raw.lstrip(b'\0'))) + value


def encode(kind, value, types):
    if kind == 'pubkey':
        return bytes(Pubkey.from_string(value or str(Pubkey.default())))
    if kind == 'bool':
        return bytes([bool(value)])
    if kind == 'string':
        value = (value or '').encode()
        return len(value).to_bytes(4, 'little') + value
    if isinstance(kind, str):
        return int(value or 0).to_bytes(int(kind[1:])//8, 'little', signed=kind[0]=='i')
    if 'defined' in kind:
        fields = types[kind['defined']['name']]['fields']
        return b''.join(encode(f, None, types) if isinstance(f, str) else encode(f['type'], None, types) for f in fields)
    if 'vec' in kind:
        return b'\0'*4
    raise ValueError(kind)


def transaction(venue='pump', buy=True, amount=1_000_000, quote=2_000_000_000, signature='synthetic', name=None):
    decoder = Decoder()
    program, spec = next((p, s) for p, s in decoder.specs.items() if s['venue'] == venue)
    instruction_name = 'buy' if buy else 'sell'
    if name:
        instruction_name = {'CreateEvent': 'create', 'CreatePoolEvent': 'create_pool',
                            'CompletePumpAmmMigrationEvent': 'migrate', 'PostCompleteBuyEvent': 'buy_v3'}[name]
    ix = next(i for i in spec['instructions'].values() if i['name'] == instruction_name)
    names = [a['name'] for a in ix['accounts']]
    bindings = {n: str(Pubkey.from_bytes(hashlib.sha256(n.encode()).digest())) for n in names}
    bindings.update({n: MINT for n in ('mint', 'base_mint') if n in names})
    bindings.update({n: POOL for n in ('pool', 'bonding_curve') if n in names})
    bindings.update({n: USER for n in ('user', 'creator') if n in names})
    if 'quote_mint' in names:
        bindings['quote_mint'] = SOL
    authority = str(Pubkey.find_program_address([b'__event_authority'], Pubkey.from_string(program))[0])
    keys = list(dict.fromkeys([program, authority] + [bindings[n] for n in names]))
    event_name = name or ('TradeEvent' if venue == 'pump' else 'BuyEvent' if buy else 'SellEvent')
    fields = {'mint': MINT, 'base_mint': MINT, 'quote_mint': SOL, 'pool': POOL,
              'bonding_curve': POOL, 'user': USER, 'creator': USER, 'is_buy': buy,
              'token_amount': amount, 'sol_amount': quote, 'quote_amount': quote,
              'base_amount_out': amount, 'base_amount_in': amount, 'base_out': amount, 'quote_in': quote,
              'quote_amount_in': quote, 'quote_amount_out': quote,
              'fee': 20, 'creator_fee': 30, 'lp_fee': 40, 'protocol_fee': 50,
              'coin_creator_fee': 60, 'real_sol_reserves': 100_000_000_000,
              'pool_quote_token_reserves': 100_000_000_000}
    disc = next(d for d, n in spec['events'].items() if n == event_name)
    raw = EVENT_IX + disc + b''.join(encode(f['type'], fields.get(f['name']), spec['types'])
                                    for f in spec['types'][event_name]['fields'])
    tx = {'transaction': {'signatures': [signature], 'message': {'accountKeys': keys,
          'instructions': [{'programIdIndex': keys.index(program), 'accounts': [keys.index(bindings[n]) for n in names],
                            'data': encode58(bytes(ix['discriminator']) + b''.join(encode(f['type'], fields.get(f['name']), spec['types']) for f in ix['args']))}]}},
          'meta': {'err': None, 'fee': 5000, 'preTokenBalances': [],
                   'postTokenBalances': [{'accountIndex': 0, 'mint': MINT,
                                          'uiTokenAmount': {'decimals': 6, 'amount': str(amount)}}],
                   'innerInstructions': [{'index': 0, 'instructions': [
                       {'programIdIndex': keys.index(program), 'accounts': [keys.index(authority)],
                        'stackHeight': 2, 'data': encode58(raw)}]}]}}
    return tx


def block(slot, stamp, txs=None, parent=None):
    return {'blockTime': stamp, 'blockhash': 'block-'+str(slot),
            'previousBlockhash': 'block-'+str(slot-1 if parent is None else parent),
            'parentSlot': slot-1 if parent is None else parent, 'transactions': txs or []}


class IndexerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'index.sqlite'
        self.store = EventStore(self.path)
        self.store.register_source('fixture', POLICY)
        self.decoder = Decoder()

    def decoded(self, slot, stamp, txs=None):
        b = block(slot, stamp, txs)
        events, issues = [], []
        for i, tx in enumerate(txs or []):
            e, errors = self.decoder.transaction(tx, slot, i, stamp)
            events.extend(e); issues.extend(errors)
        return slot, b, events, issues, list(enumerate(txs or []))

    def test_real_finalized_fixtures_are_hash_verified_and_unknown_tail_quarantined(self):
        root = Path(__file__).parent/'fixtures/onchain'
        manifest = json.loads((root/'manifest.json').read_text())
        for item in manifest['fixtures']:
            raw = (root/item['file']).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), item['sha256'])
            tx = json.loads(raw)
            self.assertEqual(tx['transaction']['signatures'][0], item['signature'])
            events, issues = self.decoder.transaction(tx, item['slot'], item['transaction_index'], tx['blockTime'])
            self.assertFalse(events)
            self.assertIn('Unreviewed event layout/version', issues)

    def test_curve_and_amm_direction_amounts_fees(self):
        for venue in ('pump', 'pump_amm'):
            for buy in (True, False):
                events, issues = self.decoder.transaction(transaction(venue, buy), 1, 0, 100)
                self.assertEqual(issues, [])
                e = events[0]
                self.assertEqual((e['mint'], e['pool'], e['wallet']), (MINT, POOL, USER))
                self.assertEqual(e['direction'], 'buy' if buy else 'sell')
                self.assertEqual((e['base_units'], e['quote_units']), ('1000000', '2000000000'))
                self.assertEqual(e['transaction_fee_lamports'], 5000)
                self.assertEqual(e['fees']['fee'] if venue == 'pump' else e['fees']['protocol_fee'], 20 if venue == 'pump' else 50)

    def test_multiple_swaps_have_distinct_event_identity_and_order(self):
        tx = transaction()
        tx['transaction']['message']['instructions'] *= 2
        other = copy.deepcopy(tx['meta']['innerInstructions'][0]); other['index'] = 1
        tx['meta']['innerInstructions'].append(other)
        events, issues = self.decoder.transaction(tx, 1, 0, 100)
        self.assertEqual(issues, [])
        self.assertEqual([e['outer_index'] for e in events], [0, 1])
        d = (1, block(1, 100, [tx]), events, [], [(0, tx)])
        self.store.commit_range(1, 1, [1], [d], 'fixture')
        self.store.commit_range(1, 1, [1], [d], 'fixture')
        self.assertEqual(self.store.metrics()['events'], 2)

    def test_identity_failure_failed_tx_unknown_instruction_and_decimals(self):
        tx = transaction(); tx['meta']['err'] = {'InstructionError': [0, 'failure']}
        self.assertEqual(self.decoder.transaction(tx, 1, 0, 100), ([], []))
        tx = transaction(); tx['meta']['postTokenBalances'] = []
        self.assertIn('Mint decimals unavailable from transaction', self.decoder.transaction(tx, 1, 0, 100)[1])
        tx = transaction(); tx['transaction']['message']['instructions'][0]['data'] = encode58(b'unknown!')
        self.assertTrue(self.decoder.transaction(tx, 1, 0, 100)[1])
        tx = transaction(); tx['meta']['innerInstructions'][0]['instructions'][0]['accounts'] = [0]
        self.assertIn('Event authority mismatch', self.decoder.transaction(tx, 1, 0, 100)[1])

    def test_creation_and_migration_are_lifecycle_events(self):
        for venue, name in [('pump', 'CreateEvent'), ('pump', 'CompletePumpAmmMigrationEvent'), ('pump_amm', 'CreatePoolEvent')]:
            events, issues = self.decoder.transaction(transaction(venue, name=name), 1, 0, 100)
            self.assertEqual(issues, [])
            self.assertEqual(events[0]['kind'], 'lifecycle')
            self.assertEqual(events[0]['mint'], MINT)

    def test_atomic_restart_interruption_and_out_of_order(self):
        first = self.decoded(1, 100)
        self.store.commit_range(1, 1, [1], [first], 'fixture')
        reopened = EventStore(self.path)
        self.assertEqual(reopened.next_slot(), 2)
        bad = self.decoded(3, 120); bad[1]['parentSlot'] = 999
        with self.assertRaises(ValueError):
            reopened.commit_range(2, 3, [2, 3], [self.decoded(2, 110), bad], 'fixture')
        self.assertEqual(EventStore(self.path).metrics()['blocks'], 1)
        self.assertEqual(EventStore(self.path).next_slot(), 2)
        with self.assertRaises(ValueError):
            reopened.commit_range(3, 3, [3], [self.decoded(3, 120)], 'fixture')
        reopened.commit_range(2, 2, [2], [self.decoded(2, 110)], 'fixture')
        self.assertEqual(reopened.next_slot(), 3)

    def test_missing_window_unknown_versions_and_real_skipped_slots(self):
        self.assertFalse(self.store.coverage(100, 200)['complete'])
        self.store.commit_range(1, 1, [1], [self.decoded(1, 100)], 'fixture')
        self.store.commit_range(2, 2, [], [], 'fixture')
        third = self.decoded(3, 200); third[1]['parentSlot'] = 1; third[1]['previousBlockhash'] = 'block-1'
        self.store.commit_range(3, 3, [3], [third], 'fixture')
        self.assertTrue(self.store.coverage(100, 200)['complete'])
        self.assertFalse(self.store.coverage(99, 200)['complete'])

    def populated(self):
        records = [self.decoded(1, 0), self.decoded(2, 3420, [transaction(signature='a')]),
                   self.decoded(3, 3480, [transaction(buy=False, quote=3_000_000_000, signature='b')]),
                   self.decoded(4, 3540, [transaction(quote=4_000_000_000, signature='c')]),
                   self.decoded(5, 3600)]
        self.store.commit_range(1, 5, [1, 2, 3, 4, 5], records, 'fixture')
        for stamp in (3420, 3480, 3540, 3600):
            self.store.add_conversion(SOL, stamp, '100', 'fixture', {'kind': 'synthetic unit test'})
        return OnchainMarket(self.store)

    def test_candles_hourly_direction_volume_and_missing_versus_zero(self):
        market = self.populated()
        row = market.observation(MINT, now=3600)
        self.assertEqual(row['buy_volume_usd'], 600)
        self.assertEqual(row['sell_volume_usd'], 300)
        self.assertEqual(row['buy_volume_coverage_pct'], 100)
        self.assertEqual([b['close'] for b in row['candle_history']['candles']], [200, 300, 400])
        self.assertEqual(market.minute_status(MINT, POOL, 3360), 'zero_trades')
        self.assertEqual(market.minute_status(MINT, POOL, -60), 'missing')
        self.assertEqual(market.minute_status(MINT, POOL, 3420), 'traded')
        self.assertEqual(len(row['candle_history']['candles']), 3)
        self.assertFalse(row['data_rights']['learning_allowed'])
        with self.assertRaises(SourceAccessBlocked):
            require_learning_rights(row)

    def test_conversion_no_future_no_peg_stale_conflict_and_missing_hour(self):
        self.store.add_conversion(SOL, 100, 100, 'fixture', {'test': True})
        with self.assertRaises(ValueError): self.store.conversion(SOL, 99)
        with self.assertRaises(ValueError): self.store.conversion(SOL, 161)
        with self.assertRaises(ValueError): self.store.conversion('USDC', 100)
        self.store.register_source('other', POLICY)
        self.store.add_conversion(SOL, 100, 101, 'other', {'test': True})
        with self.assertRaises(ValueError): self.store.conversion(SOL, 100)

    def test_unknown_deployment_prevents_complete_window(self):
        records = [self.decoded(1, 0), self.decoded(2, 3600)]
        records[1][3].append('unreviewed deployment')
        self.store.commit_range(1, 2, [1, 2], records, 'fixture')
        self.assertFalse(self.store.coverage(0, 3600)['complete'])
        ingester = Ingester(self.store, [FakeRpc()])
        self.assertEqual(len(ingester.version_issues(1, {p: 0 for p in self.decoder.specs})), 2)

    def test_ingestion_disconnect_restart_and_no_access_denial_failover(self):
        first, fallback = FakeRpc(fail=True), FakeRpc()
        ingester = Ingester(self.store, [first, fallback])
        with patch.object(ingester, 'deployments', return_value={p: 0 for p in self.decoder.specs}):
            ingester.step(1, 2)
        self.assertEqual(self.store.next_slot(), 3)
        self.assertEqual(self.store.metrics()['blocks_with_issues'], 2)
        first.failure = SourceAccessBlocked('denied')
        with self.assertRaises(SourceAccessBlocked): ingester.step(count=1)
        self.assertEqual(self.store.next_slot(), 3)

    def test_process_death_during_state_write_recovers_previous_checkpoint(self):
        self.store.commit_range(1, 1, [1], [self.decoded(1, 100)], 'fixture')
        script = "import sqlite3,os,sys; d=sqlite3.connect(sys.argv[1]); d.execute('BEGIN IMMEDIATE'); d.execute(\"UPDATE checkpoints SET value=999 WHERE name='next_slot'\"); os._exit(17)"
        result = subprocess.run([sys.executable, '-c', script, str(self.path)], check=False)
        self.assertEqual(result.returncode, 17)
        self.assertEqual(EventStore(self.path).next_slot(), 2)
        self.store.commit_range(2, 2, [2], [self.decoded(2, 110)], 'fixture')
        self.assertEqual(self.store.next_slot(), 3)

    def test_same_minute_order_does_not_use_insertion_order(self):
        txs = [transaction(quote=2_000_000_000, signature='first'),
               transaction(quote=3_000_000_000, signature='last')]
        middle = self.decoded(2, 60, txs)
        middle[2].reverse()
        self.store.commit_range(1, 3, [1, 2, 3], [self.decoded(1, 0), middle, self.decoded(3, 120)], 'fixture')
        self.store.add_conversion(SOL, 60, 100, 'fixture', {'test': True})
        bars = OnchainMarket(self.store).candles(MINT, POOL, 60, 120)
        self.assertEqual((bars[0]['open'], bars[0]['close'], bars[0]['volume_usd']), (200, 300, 500))

    def test_hour_uses_fresh_finalized_watermark_not_unavailable_wall_clock(self):
        market = self.populated()
        row = market.observation(MINT, now=3610)
        self.assertEqual(row['buy_volume_as_of'], 3600)
        self.assertEqual(row['buy_volume_coverage_pct'], 100)
        self.assertEqual(row['buy_volume_usd'], 600)

    def test_missing_usd_in_hour_blocks_volume_without_resetting_history(self):
        market = self.populated()
        with self.store.connect() as db:
            db.execute('DELETE FROM conversions WHERE time IN (3420,3480)')
        row = market.observation(MINT, now=3600)
        self.assertEqual(row['buy_volume_coverage_pct'], 0)
        self.assertTrue(row['data_errors'])
        self.assertFalse(row['data_rights']['learning_allowed'])
        self.assertEqual(self.store.metrics()['events'], 3)

    def test_spl_checked_transfer_mint_and_amount(self):
        tx = transaction()
        keys = tx['transaction']['message']['accountKeys']
        token = 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA'
        keys.extend([token, MINT, USER])
        ix = {'programIdIndex': len(keys)-3, 'accounts': [0, len(keys)-2, len(keys)-1],
              'data': encode58(bytes([12]) + (1234).to_bytes(8, 'little') + bytes([6])), 'stackHeight': 2}
        tx['meta']['innerInstructions'][0]['instructions'].append(ix)
        events, issues = self.decoder.transaction(tx, 1, 0, 100)
        self.assertEqual(issues, [])
        self.assertEqual(events[0]['transaction_token_transfers'][0]['amount_units'], '1234')
        ix['data'] = encode58(bytes([12]) + (1234).to_bytes(8, 'little') + bytes([9]))
        self.assertIn('SPL checked transfer identity mismatch', self.decoder.transaction(tx, 1, 0, 100)[1])

    def test_indexer_respects_http_retry_after_without_extra_request(self):
        from urllib.error import HTTPError
        from provider_limits import ProviderBudget, ProviderBackoff
        budget = ProviderBudget(Path(self.temp.name)/'limits.sqlite', clock=lambda: 1000)
        rpc = IndexRpc({'url': 'https://example.invalid', 'source_id': 'fixture'}, budget)
        error = HTTPError('https://example.invalid', 429, 'limited', {'Retry-After': '120'}, None)
        with patch('onchain_ingest.build_opener') as opener:
            opener.return_value.open.side_effect = error
            with self.assertRaises(ValueError): rpc.call('getSlot', [])
            with self.assertRaises(ProviderBackoff): rpc.call('getSlot', [])
            self.assertEqual(opener.return_value.open.call_count, 1)
        error.close()

    def test_partial_network_batch_preserves_durable_prefix(self):
        rpc = FakeRpc()
        original = rpc.call
        def call(method, params):
            if method == 'getBlock' and params[0] == 2:
                raise ConnectionError('disconnected mid-batch')
            return original(method, params)
        rpc.call = call
        ingester = Ingester(self.store, [rpc])
        with patch.object(ingester, 'deployments', return_value={p: 0 for p in self.decoder.specs}):
            with self.assertRaises(ConnectionError): ingester.step(1, 2)
            self.assertEqual(self.store.next_slot(), 2)
            self.assertEqual(self.store.metrics()['blocks'], 1)
            rpc.call = original
            ingester.step(count=2)
        self.assertEqual(self.store.next_slot(), 3)

    def test_post_completion_volume_is_counted_separately_without_duplication(self):
        tx = transaction(name='PostCompleteBuyEvent')
        primary = transaction()['meta']['innerInstructions'][0]['instructions'][0]
        extra = tx['meta']['innerInstructions'][0]['instructions'][0]
        first = copy.deepcopy(extra)
        first['data'] = primary['data']
        tx['meta']['innerInstructions'][0]['instructions'] = [first, extra]
        events, issues = self.decoder.transaction(tx, 1, 0, 100)
        self.assertEqual(issues, [])
        self.assertEqual([e['event_name'] for e in events], ['TradeEvent', 'PostCompleteBuyEvent'])
        self.assertEqual(sum(int(e['quote_units']) for e in events), 4_000_000_000)
        tx['meta']['innerInstructions'][0]['instructions'] = [extra]
        self.assertIn('post-completion buy without primary trade event', self.decoder.transaction(tx, 1, 0, 100)[1])

    def test_documented_v3_flag_omission_and_borsh_enum_option(self):
        from onchain_decode import read_value
        tx = transaction(name='PostCompleteBuyEvent')
        from wallet_watch import decode58
        ix = tx['transaction']['message']['instructions'][0]
        ix['data'] = encode58(decode58(ix['data'])[:-1])
        events, issues = self.decoder.transaction(tx, 1, 0, 100)
        self.assertNotIn('Truncated Borsh field', issues)
        self.assertIsNone(events[0]['instruction_args']['partial_fill'])
        types = {'Status': {'kind': 'enum', 'variants': [{'name': 'Paused'}, {'name': 'Active'}]}}
        self.assertEqual(read_value(b'\1', {'defined': {'name': 'Status'}}, 0, types), ('Active', 1))
        self.assertEqual(read_value(b'\0', {'option': 'u64'}, 0, {}), (None, 1))
        with self.assertRaises(ValueError): read_value(b'\2', {'option': 'u64'}, 0, {})

    def test_local_adapter_is_read_only_and_never_uses_http_fallback(self):
        from onchain_market import OnchainSources
        from pump_narratives import ROOT
        import sqlite3
        self.populated()
        with patch('onchain_market.time.time', return_value=3600), patch('paper_sources.build_opener') as http:
            source = OnchainSources(self.path)
            self.assertEqual(source.fetch(ROOT+'/sol-price')['solPrice'], 100)
            # Old held prices must not be renewed by a fresh local retrieval.
            with self.assertRaises(ValueError): source.fetch(ROOT+'/coins-v3/'+MINT)
            with self.assertRaises(ValueError): source.fetch('https://frontend-api-v3.pump.fun/coins')
            self.assertFalse(http.called)
        with source.market.store.connect() as db:
            with self.assertRaises(sqlite3.OperationalError): db.execute('DELETE FROM events')
        with self.assertRaises(ValueError): OnchainSources(Path(self.temp.name)/'missing.sqlite')
        self.assertFalse((Path(self.temp.name)/'missing.sqlite').exists())

    def test_catchup_is_bounded_skips_idle_and_preserves_checkpoint(self):
        from onchain_ingest import follow
        rpc = FakeRpc()
        ingester = Ingester(self.store, [rpc])
        rpc.call = lambda method, params: (100 if method == 'getSlot' else
            list(range(params[0], params[1]+1)) if method == 'getBlocks' else
            block(params[0], params[0]*60))
        with patch.object(ingester, 'deployments', return_value={p: 0 for p in self.decoder.specs}), patch('onchain_ingest.time.sleep') as sleep:
            stream = follow(ingester, start=1, count=1, max_catchup_slots=4)
            rows = [next(stream) for _ in range(4)]
            stream.close()
        self.assertEqual([row['progress']['processed_slots'] for row in rows], [1, 2, 4, 4])
        self.assertFalse(sleep.called)
        self.assertEqual(EventStore(self.path).next_slot(), 12)
        self.assertEqual(self.store.progress()['next_slot'], 12)

    def test_growing_lag_and_slower_than_production_are_reported(self):
        ingester = Ingester(self.store, [FakeRpc()])
        self.store.save_progress({'finalized_head': 10, 'head_sampled_at': 100,
                                 'next_slot': 2, 'lag_slots': 9})
        with patch('onchain_ingest.time.monotonic', return_value=110):
            row = ingester.record_progress(50, 110, 1, 100, 'indexed')
        self.assertTrue(row['progress']['lag_growing'])
        self.assertEqual(row['progress']['observed_production_slots_per_second'], 4)
        self.assertIsNotNone(row['progress']['warning'])

    def test_full_hour_and_three_candles_keep_existing_minimum_volume(self):
        from dataclasses import replace
        from meme_dip import DipRules
        from volume_candles import assess_volume_candles
        self.populated()
        # Synthetic rates scale unit-test notional only; no operational source.
        with self.store.connect() as db:
            db.execute("UPDATE conversions SET usd='1000'")
        row = OnchainMarket(self.store).observation(MINT, now=3570)
        # Three closed bars require finality through the third minute's end.
        self.assertEqual(len(row['candle_history']['candles']), 2)
        row = OnchainMarket(self.store).observation(MINT, now=3600)
        rules = replace(DipRules(evidence_mode='market_only', entry_style='volume_candles'), trend_min_bars=3)
        self.assertEqual(row['buy_volume_usd'], 6000)
        result = assess_volume_candles(row, 3600, rules)
        self.assertNotIn('recent buy volume below threshold', result['reasons'])
        self.assertFalse(any('completed 1m candles' in r for r in result['reasons']))
        self.assertEqual(rules.min_buy_volume_usd, 5000)
        with self.store.connect() as db:
            db.execute("UPDATE blocks SET issues='[\"historical gap\"]' WHERE slot=2")
        self.assertEqual(OnchainMarket(self.store).observation(MINT, now=3600)['buy_volume_coverage_pct'], 0)

    def test_live_source_cannot_enable_execution_or_load_signer(self):
        from run_meme_live import main
        import sys
        with patch.object(sys, 'argv', ['run_meme_live.py', '--onchain-db', str(self.path), '--execute']), patch('config.LiveConfig.from_env') as signer, patch('sys.stderr'):
            with self.assertRaises(SystemExit): main()
            self.assertFalse(signer.called)

    def test_external_provider_budget_path(self):
        import os
        from provider_limits import ProviderBudget
        path = str(Path(self.temp.name)/'external.sqlite')
        with patch.dict(os.environ, {'CRYPTO_BOT_PROVIDER_LIMITS': path}):
            self.assertEqual(ProviderBudget().path, Path(path))

    def test_json_access_denial_is_persistent_without_retry(self):
        from provider_limits import ProviderBudget
        budget = ProviderBudget(Path(self.temp.name)/'limits-json.sqlite', clock=lambda: 1000)
        rpc = IndexRpc({'url': 'https://example.invalid', 'source_id': 'fixture'}, budget)
        with patch('onchain_ingest.build_opener') as opener:
            response = opener.return_value.open.return_value.__enter__.return_value
            response.headers = {}
            response.read.return_value = json.dumps({'error': {'code': 403}}).encode()
            with self.assertRaises(SourceAccessBlocked): rpc.call('getSlot', [])
            with self.assertRaises(SourceAccessBlocked): rpc.call('getSlot', [])
            self.assertEqual(opener.return_value.open.call_count, 1)

    def test_unreviewed_block_events_cannot_supply_even_a_spot_price(self):
        self.populated()
        with self.store.connect() as db:
            db.execute("UPDATE blocks SET issues='[\"unknown schema\"]'")
        self.assertEqual(len(self.store.events(mint=MINT)), 3)
        self.assertEqual(self.store.events(mint=MINT, verified_only=True), [])
        with self.assertRaises(ValueError): OnchainMarket(self.store).observation(MINT, now=3600)

    def test_source_registration_requires_rights_and_write_methods_forbidden(self):
        with self.assertRaises(ValueError): self.store.register_source('unknown', {'storage_verified': False})
        rpc = IndexRpc({'url': 'https://example.invalid', 'source_id': 'fixture'})
        with self.assertRaises(ValueError): rpc.call('sendTransaction', [])

    def test_isolated_paper_shadow_adapter_and_training_quarantine(self):
        market = self.populated()
        from meme_learning import decision_context, update_weighted_model, new_weighted_model
        from meme_dip import DipRules
        from volume_candles import assess_volume_candles
        from paper_runtime import PaperSession
        rules = DipRules(evidence_mode='market_only', entry_style='volume_candles')
        adapter = OnchainPaperSources(self.path)
        with patch('onchain_market.time.time', return_value=3600), patch('pump_market.time.time', return_value=3600):
            row = adapter.observation(MINT, now=3600)
        self.assertTrue(row['execution']['model_only'])
        self.assertEqual(row['execution']['fee_per_side'], .0125)
        self.assertIn('recent buy volume below threshold', assess_volume_candles(row, 3600, rules)['reasons'])
        context = decision_context(row, rules)
        model = new_weighted_model()
        updated, status = update_weighted_model(model, context, 1)
        self.assertEqual(updated, model)
        self.assertFalse(status['updated'])
        state = Path(self.temp.name)/'paper'
        session = PaperSession(state, 6, rules)
        try:
            result = session.process(row)
            self.assertIsNotNone(result)
            self.assertEqual(session.trader.all_positions(), [])
        finally:
            session.close()


class FakeRpc:
    source = 'fixture'
    def __init__(self, fail=False):
        self.failure = ConnectionError('offline') if fail else None
    def validate(self):
        if self.failure: raise self.failure
    def call(self, method, params):
        if method == 'getSlot': return 2
        if method == 'getBlocks': return list(range(params[0], params[1]+1))
        if method == 'getBlock': return block(params[0], params[0]*60)
        raise ValueError(method)


if __name__ == '__main__':
    unittest.main()
