"""UNEXECUTED budget and generated firewall tests; no privileged commands."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from solana_network_budget import Budget, NORMAL, HARD, SAFETY_MARGIN, firewall, timestamp, sample_os, projected_exhaustion


class ProjectionTests(unittest.TestCase):
    def test_zero_tiny_and_nonfinite_rates_return_null(self):
        for rate in (0, -1, 1e-300, float('nan'), float('inf'), -float('inf')):
            with self.subTest(rate=rate):
                self.assertIsNone(projected_exhaustion(100, 200, 1000, rate))

    def test_projection_is_limited_to_billing_cycle(self):
        self.assertEqual(projected_exhaustion(100, 200, 50, 1), '1970-01-01T00:02:30+00:00')
        self.assertEqual(projected_exhaustion(100, 200, 100, 1), '1970-01-01T00:03:20+00:00')
        self.assertIsNone(projected_exhaustion(100, 200, 101, 1))

    def test_nonfinite_inputs_and_unrepresentable_dates_return_null(self):
        for args in [(float('inf'),200,1,1), (100,float('nan'),1,1),
                     (100,200,float('inf'),1), (1e20,1e21,0,1)]:
            with self.subTest(args=args):
                self.assertIsNone(projected_exhaustion(*args))


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'network.sqlite'
        self.now=timestamp('2026-10-15T00:00:00-04:00')
        self.budget=Budget(self.path)
        self.budget.initialize('2026-10-01T00:00:00-04:00','2026-11-01T00:00:00-04:00',self.now)

    def charge(self, amount):
        # Model already issued grants without thousands of setup iterations.
        with self.budget.connect() as db:
            db.execute('INSERT INTO grants VALUES(?,?,?,0,0)',('prior',amount,self.now))

    def test_report_no_traffic_and_tiny_traffic_have_no_in_cycle_projection(self):
        self.assertIsNone(self.budget.report(self.now)['projected_exhaustion_utc'])
        self.budget.grant(1,self.now)
        report=self.budget.report(self.now)
        self.assertIsNone(report['projected_exhaustion_utc'])
        self.assertEqual(report['budget_debited_bytes'],1)

    def test_normal_cutoff_reserve_and_margin(self):
        self.charge(NORMAL)
        with self.assertRaises(ValueError):self.budget.grant(1,self.now)
        self.budget.authorize_reserve(self.now)
        grant=self.budget.grant(10,self.now)
        self.assertEqual(grant['bytes'],10)
        with self.budget.connect() as db:
            db.execute('UPDATE grants SET bytes=? WHERE id=?',(HARD-SAFETY_MARGIN,'prior'))
        with self.assertRaises(ValueError):self.budget.grant(1,self.now)
        self.assertTrue(self.budget.report(self.now)['optional_collection_suspended'])

    def test_restart_and_failed_install_never_refund(self):
        self.budget.grant(1000,self.now)
        recovered=Budget(self.path)
        self.assertEqual(recovered.report(self.now)['budget_debited_bytes'],1000)
        with self.assertRaises(FileExistsError):
            recovered.initialize('2026-10-01T00:00:00-04:00','2026-11-01T00:00:00-04:00',self.now)
        with self.assertRaises(ValueError):recovered.grant(100,self.now+40*86400)

    def test_upload_download_and_retransmissions_share_one_os_quota(self):
        grant=self.budget.grant(1000,self.now)
        rules=firewall(grant,now=self.now)
        self.assertEqual(rules.count('quota shared {'),1)
        self.assertEqual(rules.count('quota name shared accept'),2)
        self.assertIn('timeout 60s',rules)
        self.assertIn('policy drop',rules)
        self.budget.sample(grant['id'],400,300,self.now)
        self.assertEqual(self.budget.report(self.now)['observed_ip_upload_bytes'],300)
        with self.assertRaises(ValueError):self.budget.sample(grant['id'],0,0,self.now)
        self.budget.sample(grant['id'],1200,300,self.now)
        self.assertEqual(self.budget.report(self.now)['budget_debited_bytes'],1500)

    def test_os_snapshot_is_required_and_no_real_commands_run(self):
        grant=self.budget.grant(1000,self.now)
        data={'nftables':[{'counter':{'family':'inet','table':'solana_budget','name':name,'bytes':count,'comment':'grant:'+grant['id']}}
                          for name,count in [('download',12),('upload',34)]]}
        result=type('Result',(),{'stdout':json.dumps(data).encode()})()
        with patch('subprocess.run',return_value=result):
            sample_os(self.budget,grant['id'],'solana-budget',self.now)
        self.assertEqual(self.budget.report(self.now)['observed_ip_download_bytes'],12)
