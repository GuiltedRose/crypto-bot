"""UNEXECUTED experiment-design checks; never opens fixture sockets."""
import unittest
from benchmark_rpc_transport import CASES
from rpc_loopback_fixture import LocalRpcServer


class LatencyExperimentTests(unittest.TestCase):
    def test_each_ab_case_changes_one_variable(self):
        baseline=CASES['baseline']
        self.assertEqual(baseline,(None,'split',None))
        for name,case in CASES.items():
            if name!='baseline':
                with self.subTest(case=name):self.assertEqual(sum(a!=b for a,b in zip(baseline,case)),1)

    def test_fixture_defaults_preserve_previous_packetization(self):
        fixture=LocalRpcServer()
        self.assertIsNone(fixture.server_nodelay)
        self.assertEqual(fixture.response_write,'split')
        self.assertFalse(hasattr(fixture,'server'))
