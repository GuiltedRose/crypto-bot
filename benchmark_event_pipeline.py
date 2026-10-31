"""UNEXECUTED authentic JSON receipt -> validated decoder fields -> compact batch.

No RPC, DB writes or execution/provenance certification. Separate worker RSS.
"""
import argparse
import hashlib
import json
from pathlib import Path
import resource
import statistics
import subprocess
import sys
import time
from solana_rpc_compat import extract_event_batch


def worker(args):
    with Path(args.file).open('rb') as stream: raw=stream.read(64000001)
    if len(raw)>64000000: raise ValueError('Input exceeds limit')
    value=json.loads(raw); value=value.get('result',value)
    transactions=value.get('transactions',[value])
    if not isinstance(transactions,list) or not 1<=len(transactions)<=64: raise ValueError('Supply 1–64 original JSON receipts')
    if any(not isinstance(tx.get('transaction'),dict) for tx in transactions):
        return {'status':'unsupported','reason':'This event decoder requires original JSON execution receipts'}
    context=dict(slot=args.slot,block_time=args.block_time,source_id='operator-offline-capture')
    expected=extract_event_batch(transactions,backend='python',**context)
    try: result=extract_event_batch(transactions,backend=args.worker,**context)
    except ImportError: return {'status':'unsupported','reason':'Native extension missing'}
    if result!=expected: return {'status':'unsupported','reason':'Event/quarantine parity failed'}
    if args.worker=='rust':
        try: from _solana_binary import NativeBorshSchema
        except ImportError:return {'status':'unsupported','reason':'Native field backend missing'}
    latencies=[]; started=time.perf_counter()
    for _ in range(args.iterations):
        before=time.perf_counter_ns()
        extract_event_batch(transactions,backend=args.worker,**context)
        latencies.append(time.perf_counter_ns()-before)
    elapsed=time.perf_counter()-started
    peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {'status':'measured','backend':args.worker,'input_sha256':hashlib.sha256(raw).hexdigest(),
            'transactions_per_second':len(transactions)*args.iterations/elapsed,
            'batch_median_ns':statistics.median(latencies),'batch_p95_ns':sorted(latencies)[int((len(latencies)-1)*.95)],
            'process_peak_rss_bytes':peak if sys.platform=='darwin' else peak*1024,'allocations':None,
            'events_per_batch':len(result['rows']),'quarantined_transactions':len(result['issues']),
            'scope':'Python context checks/normalization + optional Rust fields + compact projection; schemas compiled each batch'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file');parser.add_argument('--slot',type=int,required=True);parser.add_argument('--block-time',type=int,required=True)
    parser.add_argument('--iterations',type=int,default=10);parser.add_argument('--worker',choices=['python','rust'])
    args=parser.parse_args()
    if not 1<=args.iterations<=100:raise ValueError('Invalid iteration count')
    if args.worker:print(json.dumps(worker(args)));return
    reports=[]
    for backend in ('python','rust'):
        command=[sys.executable,__file__,args.file,'--slot',str(args.slot),'--block-time',str(args.block_time),'--iterations',str(args.iterations),'--worker',backend]
        reports.append(json.loads(subprocess.run(command,check=True,text=True,stdout=subprocess.PIPE,timeout=120).stdout))
    hashes=[row['input_sha256'] for row in reports if row['status']=='measured']
    if len(set(hashes))>1:raise ValueError('Input changed between workers')
    print(json.dumps(reports,indent=2))


if __name__=='__main__':main()
