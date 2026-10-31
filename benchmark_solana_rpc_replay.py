"""UNEXECUTED offline RPC JSON -> original binary -> summary benchmark, no I/O transport.

Input provenance must be reviewed by operator. No synthetic speedup claim.
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
from solana_rpc_replay import replay_binary_response


def worker(args):
    with Path(args.file).open('rb') as stream: raw=stream.read(64000001)
    if len(raw)>64000000: raise ValueError('replay exceeds bounded response limit')
    text=raw.decode()
    operation=replay_binary_response
    if args.worker=='rust':
        try: from _solana_binary import replay_binary_response as operation
        except ImportError: return {'status':'unsupported','reason':'rpc-engine extension unavailable'}
    expected=replay_binary_response(text)
    if not any(row[1]=='parsed_unverified' for row in expected):
        return {'status':'unsupported','reason':'no supported original binary transactions'}
    if operation(text)!=expected: raise ValueError('Replay differential mismatch')
    samples=[];start=time.perf_counter()
    for _ in range(args.iterations):
        for _ in range(args.batch):
            before=time.perf_counter_ns(); operation(text); samples.append(time.perf_counter_ns()-before)
    elapsed=time.perf_counter()-start
    peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {'status':'measured','backend':args.worker,'batch_documents':args.batch,
            'input_sha256':hashlib.sha256(raw).hexdigest(),'transactions_per_second':len(expected)*len(samples)/elapsed,
            'document_median_ns':statistics.median(samples),'document_p95_ns':sorted(samples)[int((len(samples)-1)*.95)],
            'mean_transaction_ns':sum(samples)/len(samples)/len(expected),
            'process_peak_rss_bytes':peak if sys.platform=='darwin' else peak*1024,'allocations':None,
            'scope':'JSON parsing, original wire decode, status summary and Python binding; no HTTP, decompression or DB',
            'quarantined_per_document':sum(row[1]=='quarantined' for row in expected),
            'unsupported_per_document':sum(row[1]=='unsupported_json' for row in expected)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file');parser.add_argument('--worker',choices=['python','rust'])
    parser.add_argument('--batch',type=int,default=1);parser.add_argument('--iterations',type=int,default=10)
    args=parser.parse_args()
    if args.batch not in (1,8) or not 1<=args.iterations<=100: raise ValueError('invalid bounded benchmark')
    if args.worker: print(json.dumps(worker(args)));return
    results=[]
    for backend in ('python','rust'):
        for batch in (1,8):
            result=subprocess.run([sys.executable,__file__,args.file,'--worker',backend,'--batch',str(batch),'--iterations',str(args.iterations)],
                                  text=True,check=True,stdout=subprocess.PIPE,timeout=120)
            results.append(json.loads(result.stdout))
    hashes=[row['input_sha256'] for row in results if row['status']=='measured']
    if hashes and any(value!=hashes[0] for value in hashes): raise ValueError('Input changed between workers')
    print(json.dumps(results,indent=2))


if __name__=='__main__':main()
