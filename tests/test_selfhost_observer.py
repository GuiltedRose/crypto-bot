"""Operator-run tests. Not executed during prototype preparation."""
import copy
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import test_onchain_indexer as fixtures
from onchain_candles import CandleEngine
from onchain_ingest import IndexRpc
from onchain_store import EventStore
from run_local_observer import local_endpoint, open_isolated
from observer_archive import codec, digest, pack, unpack, export_slot, FORMAT, MAX_RAW


class BoundaryCoverageTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.IndexerTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.store=self.fixture.store;self.fixture.populated()
        self.engine=CandleEngine(self.store)
        self.engine.watch(fixtures.MINT,fixtures.POOL);self.engine.sync()

    def test_partial_boundary_cannot_claim_volume_beyond_verified_window(self):
        buy,sell,sources,errors=self.engine.rolling(fixtures.MINT,fixtures.POOL,3510,3610)
        self.assertEqual((buy,sell,sources),(0,0,set()))
        self.assertIn('rolling window coverage incomplete',errors)

    def test_middle_gap_also_closes_partial_boundaries(self):
        with self.store.connect() as db:
            db.execute("UPDATE blocks SET issues='[\"gap\"]' WHERE slot=2")
        buy,sell,_,errors=self.engine.rolling(fixtures.MINT,fixtures.POOL,3390,3570)
        self.assertEqual((buy,sell),(0,0));self.assertTrue(errors)

    def test_complete_partial_and_full_windows_still_count_exact_amounts(self):
        self.assertEqual(self.engine.rolling(fixtures.MINT,fixtures.POOL,3510,3570)[:2],(400,0))
        self.assertEqual(self.engine.rolling(fixtures.MINT,fixtures.POOL,0,3600)[:2],(600,300))
        with self.assertRaises(ValueError):self.engine.rolling(fixtures.MINT,fixtures.POOL,10,10)


class LocalBridgeTests(unittest.TestCase):
    def test_loopback_only_and_explicit_plaintext_opt_in(self):
        endpoint={'url':'http://127.0.0.1:8899','source_id':'local','allow_loopback_http':True}
        self.assertEqual(local_endpoint(endpoint),endpoint)
        self.assertEqual(IndexRpc(endpoint).url,endpoint['url'])
        for url in ['http://8.8.8.8:8899','https://example.invalid','http://localhost:8899',
                    'http://user:password@127.0.0.1:8899']:
            with self.subTest(url=url),self.assertRaises(ValueError):local_endpoint({**endpoint,'url':url})
        with self.assertRaises(ValueError):IndexRpc({'url':'http://127.0.0.1:8899','source_id':'local'})
        with self.assertRaises(ValueError):IndexRpc({**endpoint,'url':'http://8.8.8.8:8899'})
        with self.assertRaises(ValueError):IndexRpc(endpoint).call('sendTransaction',[])

    def test_prototype_rejects_existing_or_unrelated_state(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'observer.sqlite'
            store=open_isolated(path)
            with self.assertRaises(ValueError):open_isolated(path)
            self.assertEqual(open_isolated(path,resume=True).metrics(),store.metrics())
            other=Path(root)/'paper.sqlite'
            with sqlite3.connect(other) as db:db.execute('CREATE TABLE paper_positions(id INTEGER)')
            with self.assertRaises(ValueError):open_isolated(other,resume=True)
            with sqlite3.connect(other) as db:
                self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='blocks'").fetchone())


class ArchiveGuards(unittest.TestCase):
    def test_bounds_and_missing_codec_fail_without_fallback(self):
        with self.assertRaises(ValueError):unpack(b'',{'format':FORMAT,'raw_bytes':MAX_RAW+1})
        with self.assertRaises(ValueError):unpack(b'bad',{'format':FORMAT,'raw_bytes':0,'frame_bytes':3,'frame_sha256':'wrong'})
        with patch('observer_archive.shutil.which',return_value=None):
            with self.assertRaises(RuntimeError):codec(b'payload',decompress=True)
        with self.assertRaises(ValueError):codec(b'',level=99)


@unittest.skipUnless(shutil.which('zstd'),'zstd CLI required; available in nix develop')
class ZstdArchiveTests(unittest.TestCase):
    def test_exact_roundtrip_and_tamper_rejection(self):
        raw=b'{"verified_evidence":true}\n'*100
        frame,manifest=pack(raw)
        self.assertEqual(unpack(frame,manifest),raw)
        bad=dict(manifest);bad['raw_sha256']='0'*64
        with self.assertRaises(ValueError):unpack(frame,bad)
        with self.assertRaises(ValueError):unpack(frame+b'extra',manifest)

    def test_forged_small_size_cannot_expand_a_bomb(self):
        frame,manifest=pack(b'x'*1_000_000)
        manifest.update(raw_bytes=1,raw_sha256=digest(b'x'))
        with self.assertRaises(ValueError):unpack(frame,manifest)

    def test_copy_only_export_restarts_and_does_not_delete_evidence(self):
        helper=fixtures.IndexerTests();helper.setUp();self.addCleanup(helper.doCleanups)
        helper.store.retain_blocks=True;helper.populated()
        before=helper.store.metrics();output=Path(helper.temp.name)/'warm'
        manifest=export_slot(EventStore(helper.path,readonly=True),2,output)
        again=export_slot(EventStore(helper.path,readonly=True),2,output)
        self.assertEqual(manifest,again)
        self.assertEqual(helper.store.metrics(),before)
        self.assertTrue((output/'2'/'block.zst').is_file())
        saved=json.loads((output/'2'/'manifest.json').read_text())
        self.assertEqual(saved['slot'],2)
        (output/'2'/'block.zst').write_bytes(b'corrupt')
        with self.assertRaises(ValueError):export_slot(helper.store,2,output)
