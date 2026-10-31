"""UNEXECUTED retention regression tests. Temporary fixtures, no network/node."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import zlib
import test_onchain_indexer as fixtures
from onchain_store import EventStore
from observer_retention import plan, apply, pin, restore, accounting
from observer_archive import verify_archive


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.IndexerTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.store = self.fixture.store
        self.store.retain_blocks = True
        self.fixture.populated()
        self.archive = Path(self.fixture.temp.name)/'archives'
        self.config = {'safe_before_slot': 6, 'dependencies_reviewed': True}
        self.now = 200000
        # Exercise archive lifecycle without invoking a codec executable.
        def fake_codec(raw, decompress=False, **kwargs):
            return zlib.decompress(raw) if decompress else zlib.compress(raw)
        self.codec = patch('observer_archive.codec', side_effect=fake_codec)
        self.codec.start()
        self.addCleanup(self.codec.stop)
        self.space = patch('observer_retention.accounting', return_value={'pause_nonessential_writes': False})
        self.space.start()
        self.addCleanup(self.space.stop)

    def raw_count(self):
        with self.store.connect() as db:
            return db.execute('SELECT count(*) FROM raw_blocks').fetchone()[0]

    def test_default_plan_requires_dependency_review_and_does_not_write(self):
        before = self.raw_count()
        report = plan(self.store, {}, self.now)
        self.assertTrue(report['dry_run'])
        self.assertTrue(all(row['blockers'] for row in report['decisions']))
        self.assertEqual(self.raw_count(), before)
        self.assertFalse(self.archive.exists())

    def test_prune_restart_query_equivalence_restore_and_idempotency(self):
        raw = self.store.retained_block(2)
        events = self.store.events()
        report = apply(self.store, self.archive, self.config, True, self.now)
        self.assertTrue(any(row['duplicate_removed'] for row in report['results']))
        restarted = EventStore(self.store.path, readonly=True)
        self.assertEqual(restarted.retained_block(2), raw)
        self.assertEqual(restarted.events(), events)
        self.assertTrue(restarted.coverage(0,3600)['complete'])
        apply(self.store, self.archive, self.config, True, self.now)
        restore(self.store, 2)
        self.assertEqual(self.store.retained_block(2), raw)

    def test_pin_quarantine_and_unfinished_interval_block_pruning(self):
        pin(self.store, 'active-training', 2, 2, 'frozen challenger evidence')
        with self.store.connect() as db:
            db.execute("UPDATE blocks SET issues='[\"unknown layout\"]' WHERE slot=3")
        decisions = {row['slot']: row for row in plan(self.store, self.config, self.now)['decisions']}
        self.assertIn('dependency pin', decisions[2]['blockers'])
        self.assertIn('decoder quarantine or unresolved evidence', decisions[3]['blockers'])
        self.assertIn('incomplete minute interval', decisions[5]['blockers'])

    def test_interrupted_validation_keeps_original_and_restart_recovers(self):
        before = self.raw_count()
        with patch('observer_retention.verify_archive', side_effect=OSError('injected interruption')):
            with self.assertRaises(OSError):
                apply(self.store, self.archive, self.config, True, self.now)
        self.assertEqual(self.raw_count(), before)
        self.assertTrue((self.archive/'1'/'manifest.json').exists())
        apply(self.store, self.archive, self.config, True, self.now)
        self.assertIsNotNone(EventStore(self.store.path, readonly=True).retained_block(1))

    def test_corrupt_archive_prevents_deletion(self):
        apply(self.store, self.archive, self.config, False, self.now)
        before = self.raw_count()
        (self.archive/'1'/'block.zst').write_bytes(b'corrupt')
        with self.assertRaises(ValueError):
            apply(self.store, self.archive, self.config, True, self.now)
        self.assertEqual(self.raw_count(), before)

    def test_storage_pressure_preserves_source(self):
        before = self.raw_count()
        with patch('observer_retention.accounting', return_value={'pause_nonessential_writes': True, 'alerts':['disk reserve reached']}):
            report = apply(self.store, self.archive, self.config, True, self.now)
        self.assertEqual(report['results'], [])
        self.assertEqual(self.raw_count(), before)
