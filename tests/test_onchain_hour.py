import base64
import copy
import json
import zlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from onchain_store import EventStore
from onchain_hour import audit_hour
from onchain_oracle import SPEC, MESSAGE, DISCRIMINATOR, sol_account, decode_sol, import_sol
import test_onchain_indexer as fixtures
from test_onchain_indexer import MINT, POOL, SOL, POLICY


def snapshot():
    address = sol_account()
    from solders.pubkey import Pubkey
    raw = (DISCRIMINATOR + bytes(Pubkey.from_string(address)) + b'\1' +
           MESSAGE.pack(bytes.fromhex(SPEC['sol_usd_feed_id']), 100_00000000, 1000, -8,
                        1000, 999, 100_00000000, 1000) + (50).to_bytes(8, 'little') + b'\0')
    return {'address': address, 'source_id': 'fixture', 'commitment': 'finalized', 'block_time': 1001,
            'retrieved_at': 1002, 'response': {'context': {'slot': 51}, 'value': {
                'owner': SPEC['receiver_program'], 'executable': False,
                'data': [base64.b64encode(raw).decode(), 'base64']}}}


class HourTests(unittest.TestCase):
    def setUp(self):
        # Reuse the established synthetic block builder, not operational state.
        self.helper = fixtures.IndexerTests()
        self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)
        self.store = self.helper.store

    def test_replay_requires_full_blocks_not_just_hashes_and_selected_transactions(self):
        self.helper.populated()
        result = audit_hour(self.store, 0, 3600, MINT, POOL)
        self.assertFalse(result['replay_consistent'])
        self.assertFalse(result['complete_hour_for_independent_review'])
        self.assertTrue(any('full block not retained' in e for e in result['errors']))

    def test_deterministic_full_hour_replay_and_read_only_restart(self):
        self.store.retain_blocks = True
        self.helper.populated()
        result = audit_hour(self.store, 0, 3600, MINT, POOL)
        reopened = EventStore(self.store.path, readonly=True)
        again = audit_hour(reopened, 0, 3600, MINT, POOL)
        self.assertEqual(result, again)
        self.assertTrue(result['replay_consistent'])
        self.assertEqual(result['blocks_replayed'], 5)
        self.assertEqual(result['target_swaps'], 3)
        self.assertEqual(result['completed_candles'], 3)
        self.assertEqual(result['buy_volume_usd'], '600')
        self.assertEqual(result['sell_volume_usd'], '300')
        self.assertEqual(sum(result['minute_states'].values()), 60)
        self.assertFalse(result['independently_verified'])  # synthetic consistency is not independent evidence

    def test_missing_usd_and_retained_corruption_never_certify(self):
        self.store.retain_blocks = True
        self.helper.populated()
        with self.store.connect() as db:
            db.execute('DELETE FROM conversions')
        row = audit_hour(self.store, 0, 3600, MINT, POOL)
        self.assertTrue(row['replay_consistent'])
        self.assertFalse(row['usd_conversions_complete'])
        self.assertFalse(row['complete_hour_for_independent_review'])
        with self.store.connect() as db:
            db.execute("UPDATE raw_blocks SET raw_hash='bad' WHERE slot=2")
        self.assertFalse(audit_hour(self.store, 0, 3600, MINT, POOL)['replay_consistent'])

    def test_retained_payload_and_both_metadata_hashes_are_independently_checked(self):
        self.store.retain_blocks = True
        self.helper.populated()
        with self.store.connect() as db:
            original=db.execute('SELECT raw,raw_hash FROM raw_blocks WHERE slot=2').fetchone()
            canonical=db.execute('SELECT raw_hash FROM blocks WHERE slot=2').fetchone()[0]
        cases=[(b'corrupt zlib',original['raw_hash'],canonical),
               (zlib.compress(b'{}'),original['raw_hash'],canonical),
               (original['raw'],'wrong-retained-hash',canonical),
               (original['raw'],original['raw_hash'],'wrong-canonical-hash')]
        for payload,retained_hash,canonical_hash in cases:
            with self.subTest(retained=retained_hash,canonical=canonical_hash):
                with self.store.connect() as db:
                    db.execute('UPDATE raw_blocks SET raw=?,raw_hash=? WHERE slot=2',(payload,retained_hash))
                    db.execute('UPDATE blocks SET raw_hash=? WHERE slot=2',(canonical_hash,))
                with self.assertRaises(ValueError): self.store.retained_block(2)
                self.assertFalse(audit_hour(self.store,0,3600,MINT,POOL)['replay_consistent'])
        with self.store.connect() as db:
            db.execute('UPDATE raw_blocks SET raw=?,raw_hash=? WHERE slot=2',tuple(original))
            db.execute('UPDATE blocks SET raw_hash=? WHERE slot=2',(canonical,))
        self.assertTrue(audit_hour(self.store,0,3600,MINT,POOL)['replay_consistent'])

    def test_altered_canonical_events_and_unbracketed_windows_fail(self):
        self.store.retain_blocks = True
        self.helper.populated()
        with self.store.connect() as db:
            db.execute("DELETE FROM events WHERE signature='a'")
        row = audit_hour(self.store, 0, 3600, MINT, POOL)
        self.assertTrue(any('canonical events differ' in e for e in row['errors']))
        self.assertFalse(audit_hour(self.store, 60, 3660, MINT, POOL)['coverage']['complete'])
        with self.assertRaises(ValueError): audit_hour(self.store, 0, 3599, MINT, POOL)

    def test_real_finalized_sol_usd_snapshot(self):
        import hashlib
        root = Path(__file__).parent/'fixtures/onchain'
        raw = (root/'sol_usd_snapshot.json').read_bytes()
        manifest = json.loads((root/'sol_usd_snapshot.manifest.json').read_text())
        self.assertEqual(hashlib.sha256(raw).hexdigest(), manifest['sha256'])
        row = decode_sol(json.loads(raw))
        self.assertEqual(row['usd'], manifest['expected_usd'])
        self.assertEqual(row['publish_time'], manifest['publish_time'])
        self.assertEqual(row['posted_slot'], manifest['posted_slot'])

    def test_selective_comparison_deduplicates_successes_and_skips_failed_receipts(self):
        from onchain_hour import compare_indexing
        good = fixtures.transaction(signature='good')
        bad = fixtures.transaction(signature='bad');bad['meta']['err'] = 'failed'
        result = compare_indexing([fixtures.block(1, 0, [good, bad])])
        self.assertEqual(result['transactions'], 2)
        self.assertEqual(result['program_referencing_transactions'], 2)
        self.assertEqual(result['successful_candidates'], 1)
        self.assertEqual(result['full_block_requests_excluding_common_overhead'], 1)
        self.assertEqual(result['selective_requests_lower_bound'], 4)

    def test_gzip_rpc_decode_is_bounded_and_never_ignores_trailing_bytes(self):
        import gzip
        from onchain_ingest import decode_rpc_body
        raw = b'{"result":1}'
        self.assertEqual(decode_rpc_body(gzip.compress(raw), 'gzip'), raw)
        self.assertEqual(decode_rpc_body(raw, 'identity'), raw)
        with self.assertRaises(ValueError): decode_rpc_body(gzip.compress(b'x'*1000), 'gzip', max_bytes=100)
        with self.assertRaises(ValueError): decode_rpc_body(gzip.compress(raw)+b'tail', 'gzip')
        with self.assertRaises(ValueError): decode_rpc_body(b'bad', 'gzip')
        with self.assertRaises(ValueError): decode_rpc_body(raw, 'unknown')

    def test_oracle_full_identity_price_and_proof(self):
        row = decode_sol(snapshot())
        self.assertEqual(row['usd'], '100.00000000')
        self.assertEqual(row['posted_slot'], 50)
        self.assertEqual(row['proof']['verification'], 'Full')
        self.assertEqual(row['mint'], SOL)
        self.assertEqual(row['proof']['snapshot'], snapshot())

    def test_wrong_owner_feed_partial_verification_and_unknown_tail_reject(self):
        for kind in ('owner', 'feed', 'partial', 'tail', 'address'):
            value = snapshot()
            if kind == 'owner': value['response']['value']['owner'] = SOL
            elif kind == 'address': value['address'] = SOL
            else:
                raw = bytearray(base64.b64decode(value['response']['value']['data'][0]))
                if kind == 'partial': raw[40] = 0
                elif kind == 'feed': raw[41] ^= 1
                else: raw.extend(b'\0')
                value['response']['value']['data'][0] = base64.b64encode(raw).decode()
            with self.subTest(kind=kind), self.assertRaises(ValueError): decode_sol(value)

    def test_oracle_time_context_future_and_stale_fail(self):
        for field, value in [('block_time', 1061), ('block_time', 999), ('retrieved_at', 1000)]:
            record = snapshot(); record[field] = value
            if field == 'block_time' and value == 1061: record['retrieved_at'] = 1062
            with self.assertRaises(ValueError): decode_sol(record)
        record = snapshot();record['response']['context']['slot'] = 49
        with self.assertRaises(ValueError): decode_sol(record)

    def test_rights_and_causal_posting_required_for_conversion_import(self):
        with self.assertRaises(ValueError): import_sol(self.store, snapshot())
        policy = {**POLICY, 'oracle_data_rights_verified': True, 'oracle_max_confidence_bps': 100}
        self.store.register_source('reviewed-test-oracle', policy)
        value = snapshot();value['source_id'] = 'reviewed-test-oracle'
        import_sol(self.store, value)
        self.assertEqual(self.store.conversion(SOL, 1001, max_slot=50)[0], 100)
        with self.assertRaises(ValueError): self.store.conversion(SOL, 1001, max_slot=49)
        with self.assertRaises(ValueError): self.store.conversion(SOL, 999, max_slot=60)
        self.assertFalse(self.store.policy('reviewed-test-oracle')['ml_verified'])


if __name__ == '__main__':
    unittest.main()
