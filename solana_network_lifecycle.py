"""UNTESTED privileged NixOS lifecycle. Only run explicitly on the server.

One bounded grant per manual service start. No automatic renewal, node startup,
or accounting reset. Kernel lease remains the fail-closed backstop if Python dies.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time
from solana_network_budget import Budget, firewall, shaping_commands, sample_os

NS='solana-budget'
VETH='solana-host'
ADDRESS='169.254.203.2'
OWNED=Path('/run/solana-budget-owned')


def run(*args, input=None, check=True):
    return subprocess.run(args,input=input,text=True,check=check,stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE,timeout=15)


def ns(*args, **kwargs):
    return run('ip','netns','exec',NS,*args,**kwargs)


def host_rules(uplink):
    if not uplink or len(uplink)>15 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-' for c in uplink):
        raise ValueError('Invalid uplink interface')
    return f'''table inet solana_boundary {{
 chain input {{ type filter hook input priority -200; policy accept; iifname "{VETH}" drop; }}
 chain output {{ type filter hook output priority -200; policy accept; oifname "{VETH}" drop; }}
 chain forward {{
  type filter hook forward priority -200; policy accept;
  iifname "{VETH}" oifname != "{uplink}" drop
  oifname "{VETH}" iifname != "{uplink}" drop
  iifname "{VETH}" ip saddr != {ADDRESS} drop
  iifname "{VETH}" meta nfproto ipv6 drop
  oifname "{VETH}" meta nfproto ipv6 drop
 }}
}}
table ip solana_nat {{
 chain postrouting {{ type nat hook postrouting priority srcnat; policy accept;
  ip saddr {ADDRESS} oifname "{uplink}" masquerade
 }}
}}
'''


def notify(message):
    address=os.environ.get('NOTIFY_SOCKET')
    if address:
        with socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM) as channel:
            channel.connect('\0'+address[1:] if address.startswith('@') else address)
            channel.sendall(message.encode())


def close_boundary():
    # Down first; leave firewall protection in place until a fresh start verifies it.
    run('ip','link','set',VETH,'down',check=False)


def cleanup():
    if not OWNED.exists(): return
    close_boundary()
    run('ip','link','del',VETH,check=False)
    run('ip','netns','del',NS,check=False)
    OWNED.unlink(missing_ok=True)
    # No firewall-wide flush, no shared routing/firewall configuration removal.


def start(config):
    ledger=Path(config['ledger'])
    if ledger.is_symlink() or ledger.stat().st_uid!=0 or ledger.stat().st_mode & 0o077:
        raise ValueError('Root-owned owner-only persistent ledger required')
    budget=Budget(ledger)
    report=budget.report(time.time())
    if report['optional_collection_suspended'] and not config.get('reserveSession',False):
        raise ValueError('Normal budget reached; optional collection suspended')
    host=host_rules(config['uplink'])
    # Refuse unknown existing namespace ownership; cleanup is an explicit stop action.
    if Path('/run/netns/'+NS).exists():
        raise ValueError('Existing namespace requires stopped-service cleanup before restart')
    OWNED.write_text('solana-budget-network service owns this namespace\n')
    OWNED.chmod(0o600)
    grant=budget.grant(config['sessionBytes'],time.time())
    expiry=min(grant['cycle_end'],time.time()+55)
    audit=ledger.parent/'network-events.jsonl'
    def event(phase):
        with audit.open('a') as stream:
            stream.write(json.dumps({'time':time.time(),'grant':grant['id'],'phase':phase,'coverage':'gap until collector verifies recovery'})+'\n')
            stream.flush(); os.fsync(stream.fileno())
    event('session_start')
    stopping=False
    def stop(*_):
        nonlocal stopping
        stopping=True
    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    try:
        # Host-facing link stays DOWN until all enforcement and shaping succeed.
        run('ip','netns','add',NS)
        run('ip','link','add',VETH,'type','veth','peer','name','solana-peer')
        run('ip','link','set','solana-peer','netns',NS)
        ns('ip','link','set','solana-peer','name','eth0')
        ns('sysctl','-w','net.ipv6.conf.all.disable_ipv6=1')
        ns('sysctl','-w','net.ipv6.conf.default.disable_ipv6=1')
        ns('nft','-f','-',input=firewall(grant,config['uploadKbytes'],config['downloadKbytes']))
        # Atomic replacement of our tables only; deletion requires known existence.
        prefix=''
        for family,table in [('inet','solana_boundary'),('ip','solana_nat')]:
            if run('nft','list','table',family,table,check=False).returncode==0:
                prefix+=f'delete table {family} {table}\n'
        run('nft','-f','-',input=prefix+host)
        run('ip','addr','add','169.254.203.1/30','dev',VETH)
        ns('ip','addr','add',ADDRESS+'/30','dev','eth0')
        for command in shaping_commands(upload_kbytes=config['uploadKbytes'],download_kbytes=config['downloadKbytes']):
            run(*command.split())
        ns('ip','link','set','lo','up')
        ns('ip','link','set','eth0','up')
        ns('ip','route','add','default','via','169.254.203.1')
        sample_os(budget,grant['id'],NS,time.time())
        run('ip','link','set',VETH,'up')
        notify('READY=1\nSTATUS=Bounded Solana session; no automatic renewal')
        while not stopping and time.time()<expiry:
            sample_os(budget,grant['id'],NS,time.time())
            with budget.connect() as db:
                counters=db.execute('SELECT rx+tx FROM grants WHERE id=?',(grant['id'],)).fetchone()[0]
            if counters>=grant['bytes']: break
            time.sleep(1)
    finally:
        close_boundary()
        event('session_stopped_gap')
        try: sample_os(budget,grant['id'],NS,time.time())
        finally: cleanup()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path)
    parser.add_argument('--cleanup',action='store_true')
    args=parser.parse_args()
    if args.cleanup: cleanup(); return
    if args.config is None: parser.error('--config required')
    start(json.loads(args.config.read_text()))


if __name__=='__main__': main()
