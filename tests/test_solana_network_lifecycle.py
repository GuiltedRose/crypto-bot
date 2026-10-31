"""UNEXECUTED controller regression tests; all OS calls mocked."""
import unittest
from unittest.mock import patch, MagicMock
from solana_network_lifecycle import host_rules, cleanup, start


class LifecycleTests(unittest.TestCase):
    def test_host_guard_cannot_be_injected_and_blocks_host_proxy_paths(self):
        for value in ('eth0; flush ruleset','', 'a'*16):
            with self.assertRaises(ValueError): host_rules(value)
        rules=host_rules('wan0')
        self.assertIn('iifname "solana-host" drop',rules)
        self.assertIn('oifname "solana-host" drop',rules)
        self.assertIn('iifname "solana-host" oifname != "wan0" drop',rules)
        self.assertIn('meta nfproto ipv6 drop',rules)
        self.assertNotIn('flush ruleset',rules)

    def test_unowned_namespace_is_not_removed(self):
        with patch('solana_network_lifecycle.STATUS'), patch('solana_network_lifecycle.OWNED') as owned, patch('solana_network_lifecycle.run') as run:
            owned.exists.return_value=False
            cleanup()
            run.assert_not_called()

    def test_missing_accounting_prevents_any_network_setup(self):
        with patch('solana_network_lifecycle.Path') as path, patch('solana_network_lifecycle.run') as run:
            path.return_value.is_symlink.return_value=False
            path.return_value.stat.side_effect=FileNotFoundError('missing ledger')
            with self.assertRaises(FileNotFoundError):
                start({'ledger':'/private/missing.sqlite'})
            run.assert_not_called()

    def test_cleanup_disables_link_before_destroying_namespace(self):
        with patch('solana_network_lifecycle.STATUS'), patch('solana_network_lifecycle.OWNED') as owned, patch('solana_network_lifecycle.run') as run:
            owned.exists.return_value=True
            cleanup()
            calls=[call.args for call in run.call_args_list]
            self.assertEqual(calls[0],('ip','link','set','solana-host','down'))
            self.assertEqual(calls[-1],('ip','netns','del','solana-budget'))
