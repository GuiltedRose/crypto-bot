"""UNEXECUTED budget and generated firewall tests; no privileged commands."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from solana_network_budget import Budget, NORMAL, HARD, SAFETY_MARGIN, firewall, timestamp, sample_os


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
