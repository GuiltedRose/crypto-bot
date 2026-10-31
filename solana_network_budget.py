"""UNTESTED server-only network budget control; no node startup or auto-refill.

Grants are debited durably before firewall installation. Lost/unused grants are
never refunded. This bounds authorized IP-layer traffic, NOT ISP wire billing.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import uuid

NORMAL = 120_000_000_000
HARD = 150_000_000_000
SAFETY_MARGIN = 5_000_000_000
MAX_GRANT = 10_000_000


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None: raise ValueError('Explicit billing-cycle timezone required')
    return parsed.timestamp()


class Budget:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def connect(self):
        # No implicit recreation: losing accounting must stop authorization.
        if not self.path.is_file(): raise ValueError('Explicit budget initialization required')
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                yield db
        finally: db.close()

    def initialize(self, start, end, now):
        begin, finish = timestamp(start), timestamp(end)
        if not begin <= now < finish or not 20*86400 <= finish-begin <= 35*86400:
            raise ValueError('Explicit active billing cycle of 20–35 days required')
        # Exclusive creation prevents silently resetting an existing monthly ledger.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open('xb'): pass
        try:
            with self.connect() as db:
                db.executescript('''
                  CREATE TABLE cycle(start REAL,end REAL,reserve_authorized INTEGER);
                  CREATE TABLE grants(id TEXT PRIMARY KEY,bytes INTEGER,created REAL,rx INTEGER,tx INTEGER);
                ''')
                db.execute('INSERT INTO cycle VALUES(?,?,0)', (begin,finish))
        except BaseException:
            # Leave the failed ledger in place, fail closed; no automatic reset.
            raise
        self.path.chmod(0o600)

    @staticmethod
    def cycle(db, now):
        row = db.execute('SELECT * FROM cycle').fetchone()
        if not row or not row['start'] <= now < row['end']:
            raise ValueError('Cycle expired or inactive; no automatic rollover')
        return row

    def authorize_reserve(self, now):
        with self.connect() as db:
            self.cycle(db, now)
            db.execute('UPDATE cycle SET reserve_authorized=1')

    def grant(self, amount, now):
        if type(amount) is not int or not 1 <= amount <= MAX_GRANT:
            raise ValueError('Grant must be 1–10,000,000 bytes; no unlimited catch-up')
        with self.connect() as db:
            cycle = self.cycle(db, now)
            charged = db.execute('SELECT coalesce(sum(max(bytes,rx+tx)),0) FROM grants').fetchone()[0]
            limit = HARD-SAFETY_MARGIN if cycle['reserve_authorized'] else NORMAL
            if charged+amount > limit: raise ValueError('Network budget exhausted')
            identifier = uuid.uuid4().hex
            db.execute('INSERT INTO grants VALUES(?,?,?,0,0)', (identifier,amount,now))
        return {'id':identifier, 'bytes':amount, 'cycle_end':cycle['end']}

    def sample(self, identifier, rx, tx, now):
        if any(type(value) is not int or value < 0 for value in (rx,tx)):
            raise ValueError('Invalid OS byte counters')
        with self.connect() as db:
            self.cycle(db, now)
            row = db.execute('SELECT * FROM grants WHERE id=?', (identifier,)).fetchone()
            if not row or rx < row['rx'] or tx < row['tx']:
                raise ValueError('Unknown/reset OS counters; stop namespace, do not refund grant')
            db.execute('UPDATE grants SET rx=?,tx=? WHERE id=?', (rx,tx,identifier))

    def report(self, now):
        with self.connect() as db:
            cycle = self.cycle(db, now)
            row = db.execute('SELECT coalesce(sum(max(bytes,rx+tx)),0),coalesce(sum(rx),0),coalesce(sum(tx),0) FROM grants').fetchone()
        rate = row[0]/max(1,now-cycle['start'])
        return {'budget_debited_bytes':row[0], 'observed_ip_download_bytes':row[1],
                'observed_ip_upload_bytes':row[2], 'normal_remaining_bytes':max(0,NORMAL-row[0]),
                'hard_remaining_bytes':max(0,HARD-row[0]), 'safety_margin_bytes':SAFETY_MARGIN,
                'operational_cutoff_remaining_bytes':max(0,HARD-SAFETY_MARGIN-row[0]), 'reserve_authorized':bool(cycle['reserve_authorized']),
                'optional_collection_suspended':row[0]>=NORMAL,
                'projected_exhaustion_utc':datetime.fromtimestamp(now+max(0,HARD-SAFETY_MARGIN-row[0])/rate,timezone.utc).isoformat() if rate else None,
                'cycle_end_utc':datetime.fromtimestamp(cycle['end'],timezone.utc).isoformat(),
                'isp_wire_budget_guaranteed':False, 'automatic_grants':False}


def firewall(grant, upload_kbps=16, download_kbps=16, now=None):
    """Render ONLY; install inside a dedicated, otherwise unrouted net namespace.

    The same named quota charges BOTH directions, all protocols/retransmissions.
    Counters precede quota/drop: inbound over-quota packets still cost WAN bytes.
    Limits are policing (drop), not smooth shaping. Host/router tc must shape.
    """
    import time
    now=time.time() if now is None else now
    lease_seconds=int(min(60,grant['cycle_end']-now))
    if lease_seconds < 1: raise ValueError('Billing cycle expired')
    identifier = grant['id']
    if len(identifier)!=32 or any(c not in '0123456789abcdef' for c in identifier):
        raise ValueError('Invalid grant ID')
    if type(grant['bytes']) is not int or not 1 <= grant['bytes'] <= MAX_GRANT:
        raise ValueError('Invalid OS quota')
    for value in (upload_kbps,download_kbps):
        if type(value) is not int or not 1 <= value <= 1024: raise ValueError('Invalid bandwidth limit')
    # Single grant, fresh table. Never reinstall/reuse a grant after reset/reboot.
    return f'''# grant {identifier}; installer must enforce cycle expiry {grant['cycle_end']}
# UNTESTED. No routes or namespace are created by this renderer.
table inet solana_budget {{
 quota shared {{ until {grant['bytes']} bytes }}
 set active {{ type mark; flags timeout; elements = {{ 0x534f timeout {lease_seconds}s }}; }}
 counter download {{ comment "grant:{identifier}"; }}
 counter upload {{ comment "grant:{identifier}"; }}
 chain input {{
  type filter hook input priority -150; policy drop;
  iifname "lo" accept
  counter name download
  limit rate over {download_kbps} kbytes/second drop
  meta mark set 0x534f
  meta mark @active quota name shared accept
 }}
 chain output {{
  type filter hook output priority -150; policy drop;
  oifname "lo" accept
  counter name upload
  limit rate over {upload_kbps} kbytes/second drop
  meta nfproto ipv4 ip dscp set cs1
  meta nfproto ipv6 ip6 dscp set cs1
  meta mark set 0x534f
  meta mark @active quota name shared accept
 }}
 chain forward {{ type filter hook forward priority -150; policy drop; }}
}}
'''


def shaping_commands(namespace='solana-budget', root_veth='solana-host', upload_kbytes=16, download_kbytes=16):
    """Render server-only tc commands. Router SQM remains required for household priority."""
    for name in (namespace,root_veth):
        if not name or len(name)>15 or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-_' for c in name):
            raise ValueError('Invalid interface/namespace')
    for value in (upload_kbytes,download_kbytes):
        if type(value) is not int or not 1<=value<=1024: raise ValueError('Invalid shaper rate')
    return [f'ip netns exec {namespace} tc qdisc replace dev eth0 root tbf rate {upload_kbytes*8}kbit burst 16kb latency 100ms',
            f'tc qdisc replace dev {root_veth} root tbf rate {download_kbytes*8}kbit burst 16kb latency 100ms']


def sample_os(budget, identifier, namespace, now):
    """Server operator command: sample ALL namespace IP traffic, not app counters."""
    import subprocess
    if not namespace or len(namespace)>40 or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-_' for c in namespace):
        raise ValueError('Invalid namespace name')
    result=subprocess.run(['ip','netns','exec',namespace,'nft','-j','list','table','inet','solana_budget'],
                          check=True,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=10)
    if len(result.stdout)>65536: raise ValueError('Oversized kernel accounting response')
    data=json.loads(result.stdout)
    counts={}
    for item in data['nftables']:
        counter=item.get('counter',{})
        if counter.get('family')=='inet' and counter.get('table')=='solana_budget' and counter.get('name') in ('download','upload'):
            if counter.get('comment') != 'grant:'+identifier: raise ValueError('OS grant identity mismatch')
            counts[counter['name']]=counter['bytes']
    if set(counts)!={'download','upload'}: raise ValueError('Missing OS counters; stop collection')
    budget.sample(identifier,counts['download'],counts['upload'],now)


def main():
    import time
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ledger', type=Path, required=True)
    sub = parser.add_subparsers(dest='action', required=True)
    init = sub.add_parser('initialize'); init.add_argument('--start',required=True); init.add_argument('--end',required=True)
    reserve = sub.add_parser('authorize-reserve'); reserve.add_argument('--confirm-reserve',action='store_true',required=True)
    grant = sub.add_parser('grant'); grant.add_argument('--bytes',type=int,required=True)
    grant.add_argument('--upload-kbps',type=int,default=16); grant.add_argument('--download-kbps',type=int,default=16)
    sub.add_parser('report')
    sub.add_parser('render-shaping')
    os_sample=sub.add_parser('sample-os'); os_sample.add_argument('--grant-id',required=True)
    os_sample.add_argument('--namespace',default='solana-budget')
    sample = sub.add_parser('sample'); sample.add_argument('--grant-id',required=True)
    sample.add_argument('--rx',type=int,required=True); sample.add_argument('--tx',type=int,required=True)
    args = parser.parse_args(); now=time.time(); budget=Budget(args.ledger)
    if args.action=='render-shaping': print('\n'.join(shaping_commands())); return
    if args.action=='initialize': budget.initialize(args.start,args.end,now)
    elif args.action=='authorize-reserve': budget.authorize_reserve(now)
    elif args.action=='grant':
        # Charge before emitting any usable firewall configuration.
        print(firewall(budget.grant(args.bytes,now),args.upload_kbps,args.download_kbps)); return
    elif args.action=='sample-os': sample_os(budget,args.grant_id,args.namespace,now)
    elif args.action=='sample': budget.sample(args.grant_id,args.rx,args.tx,now)
    print(json.dumps(budget.report(now),indent=2))


if __name__=='__main__': main()
