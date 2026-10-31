"""UNEXECUTED opt-in loopback HTTP transport benchmark, never external RPC.

Separate processes, identical fixed HTTP/1.1 responses. Fixture-only policy mocks
remove pacing for BOTH clients; never import this harness into a live runner.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import statistics
import subprocess
import sys
import time
from unittest.mock import MagicMock, patch
sys.path.insert(0,str(Path(__file__).resolve().parent/'tests'))
from rpc_loopback_fixture import LocalRpcServer, RESULTS
from onchain_ingest import IndexRpc
from solana_rpc_compat import RustIndexRpc


def worker(args):
    if sys.platform!='linux':return {'status':'unsupported','reason':'Approved native network boundary is Linux-only'}
    try:
        from _solana_binary import NativeHttp
        if not hasattr(NativeHttp,'request_sync'):raise ImportError
    except ImportError:return {'status':'unsupported','reason':'Rebuild rpc-engine with request_sync'}
    latencies=[]
    with LocalRpcServer() as server,patch('solana_rpc_compat.check_boundary',return_value=os.stat('/proc/self/ns/net').st_ino):
        endpoint={'url':server.url,'source_id':'isolated-transport-benchmark','allow_loopback_http':True}
        rpc=(RustIndexRpc if args.worker=='rust' else IndexRpc)(endpoint,MagicMock())
        try:
            assert rpc.call('getSlot',[])==RESULTS['getSlot'] # warmup outside timing
            started=time.perf_counter()
            for _ in range(args.iterations):
                for _ in range(args.batch):
                    before=time.perf_counter_ns()
                    result=rpc.call('getSlot',[])
                    latencies.append(time.perf_counter_ns()-before)
                    if result!=RESULTS['getSlot']:raise ValueError('Response parity failed')
            elapsed=time.perf_counter()-started
            connections=server.connections
        finally:
            if isinstance(rpc,RustIndexRpc):rpc.close()
    peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {'status':'measured','backend':args.worker,'batch':args.batch,'calls':len(latencies),
            'calls_per_second':len(latencies)/elapsed,'median_ns':statistics.median(latencies),
            'p95_ns':sorted(latencies)[int((len(latencies)-1)*.95)],'tcp_connections_with_warmup':connections,
            'response_sha256':hashlib.sha256(json.dumps({'jsonrpc':'2.0','id':1,'result':RESULTS['getSlot']}).encode()).hexdigest(),
            'peak_process_rss_bytes':peak if sys.platform=='darwin' else peak*1024,
            'allocations':None,'scope':'loopback HTTP+JSON result validation; fixture server included in RSS; no ingestion/storage',
            'pacing':'disabled equally inside fixture harness only'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-loopback',action='store_true',required=True)
    parser.add_argument('--worker',choices=['python','rust'])
    parser.add_argument('--iterations',type=int,default=10)
    parser.add_argument('--batch',type=int,default=1)
    args=parser.parse_args()
    if args.batch not in (1,16) or not 1<=args.iterations<=100:raise ValueError('Invalid bounded benchmark size')
    if args.worker:print(json.dumps(worker(args)));return
    results=[]
    for backend in ('python','rust'):
        for batch in (1,16):
            command=[sys.executable,__file__,'--allow-loopback','--worker',backend,'--batch',str(batch),'--iterations',str(args.iterations)]
            results.append(json.loads(subprocess.run(command,check=True,text=True,stdout=subprocess.PIPE,timeout=120).stdout))
    hashes={row['response_sha256'] for row in results if row['status']=='measured'}
    if len(hashes)>1:raise ValueError('Benchmark responses differed')
    print(json.dumps(results,indent=2))


if __name__=='__main__':main()
