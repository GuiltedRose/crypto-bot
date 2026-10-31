"""UNEXECUTED: pure capacity assessment and mocked archive benchmark tests."""
import unittest
from unittest.mock import patch
from node_capacity import assess
from benchmark_observer_archive import benchmark


class CapacityTests(unittest.TestCase):
    def test_monolith_rejected_without_claiming_a_measurement(self):
        report = assess({'ram_gb': 64, 'total_free_gb': 884})
        self.assertEqual(report['assessment'], 'NO_GO_CURRENT_HARDWARE')
        self.assertEqual({b['field'] for b in report['blockers']}, {'ram_gb', 'total_free_gb'})
        self.assertFalse(report['node_started'])
        self.assertIn('sha_extensions', report['unknown'])

    def test_missing_inventory_never_passes(self):
        self.assertEqual(assess({})['assessment'], 'UNPROVEN')
        for value in (True, -1, float('nan'), '64'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                assess({'ram_gb': value})

    def test_benchmark_checks_roundtrip_and_does_not_write(self):
        with patch('benchmark_observer_archive.pack', return_value=(b'x', {})), \
             patch('benchmark_observer_archive.unpack', return_value=b'abc'):
            result = benchmark(b'abc', levels=(3,))
        self.assertEqual(result[0]['raw_bytes'], 3)
        self.assertAlmostEqual(result[0]['ratio'], 1/3)
        with patch('benchmark_observer_archive.pack', return_value=(b'x', {})), \
             patch('benchmark_observer_archive.unpack', return_value=b'wrong'):
            with self.assertRaises(ValueError):
                benchmark(b'abc')
