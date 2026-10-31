"""UNEXECUTED, explicit opt-in SERVER ONLY Linux kernel quota/lease smoke tests.

Creates two isolated namespaces connected only to each other. No default routes,
external connections or node software. Never enable on the laptop.
"""
import json
import os
import subprocess
import time
import unittest
import uuid
from solana_network_budget import firewall


@unittest.skipUnless(os.environ.get('SOLANA_SERVER_KERNEL_TESTS')=='I_AM_ON_THE_MONOLITH',
                     'Explicit server-only kernel test authorization required')
class KernelBudgetTests(unittest.TestCase):
    def command(self, *args, input=None, check=True):
        return subprocess.run(args,input=input,text=True,stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE,check=check,timeout=15)

    def setUp(self):
        if os.geteuid()!=0: self.skipTest('Root needed for isolated network namespaces')
        self.names=['sq-'+uuid.uuid4().hex[:8] for _ in range(2)]
        for name in self.names:
            self.command('ip','netns','add',name)
            self.addCleanup(self.command,'ip','netns','del',name,check=False)
        left,right=self.names
        self.command('ip','netns','exec',left,'ip','link','add','test-a','type','veth','peer','name','test-b')
        self.command('ip','netns','exec',left,'ip','link','set','test-b','netns',right)
        for name,interface,address in [(left,'test-a','10.203.0.1/30'),(right,'test-b','10.203.0.2/30')]:
            self.command('ip','netns','exec',name,'ip','addr','add',address,'dev',interface)
            self.command('ip','netns','exec',name,'ip','link','set',interface,'up')
            self.command('ip','netns','exec',name,'ip','link','set','lo','up')

    def install(self, amount, lifetime):
        rules=firewall({'id':'a'*32,'bytes':amount,'cycle_end':time.time()+lifetime})
        self.command('ip','netns','exec',self.names[1],'nft','-f','-',input=rules)

    def ping(self, count):
        return self.command('ip','netns','exec',self.names[0],'ping','-n','-c',str(count),
                            '-i','0.2','-W','1','10.203.0.2',check=False)

    def test_shared_ingress_egress_quota_and_persistable_counters(self):
        self.install(500,60)
        self.ping(10)
        result=self.command('ip','netns','exec',self.names[1],'nft','-j','list','table','inet','solana_budget')
        data=json.loads(result.stdout)
        counters={item['counter']['name']:item['counter']['bytes'] for item in data['nftables'] if 'counter' in item}
        self.assertGreater(counters['download'],0)
        self.assertGreater(counters['upload'],0)
        self.assertNotEqual(self.ping(1).returncode,0)

    def test_timed_lease_fails_closed_without_python_monitor(self):
        self.install(1000000,5)
        self.assertEqual(self.ping(1).returncode,0)
        time.sleep(6)
        self.assertNotEqual(self.ping(1).returncode,0)
