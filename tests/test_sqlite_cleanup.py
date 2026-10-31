"""UNEXECUTED connection ownership regressions; no real operational databases."""
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from onchain_store import EventStore
from provider_limits import ProviderBudget


class CleanupTests(unittest.TestCase):
    def test_event_store_closes_connection_if_durability_setup_fails(self):
        store=object.__new__(EventStore)
        store.path=Path('unused.sqlite');store.readonly=False
        connection=MagicMock()
        connection.execute.side_effect=sqlite3.OperationalError('setup failure')
        with patch('onchain_store.sqlite3.connect',return_value=connection):
            with self.assertRaises(sqlite3.OperationalError):
                with store.connect():self.fail('setup should fail before yielding')
        connection.close.assert_called_once_with()

    def test_provider_budget_closes_connection_if_schema_setup_fails(self):
        with tempfile.TemporaryDirectory() as root:
            connection=MagicMock()
            connection.execute.side_effect=sqlite3.OperationalError('schema failure')
            with patch('provider_limits.sqlite3.connect',return_value=connection):
                with self.assertRaises(sqlite3.OperationalError):ProviderBudget(Path(root)/'test.sqlite').connect()
            connection.close.assert_called_once_with()
