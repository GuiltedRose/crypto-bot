"""UNEXECUTED offline binary benchmark. Never re-serializes JSON transactions."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import resource
import statistics
import subprocess
import sys
import time
from solana_binary import parse_transaction, parse_reference


def inputs(paths):
    supported, unsupported = [], []
    for name in paths:
        path = Path(name)
        with path.open('rb') as stream: raw = stream.read(8_000_001)
        if len(raw)>8_000_000: raise ValueError('Benchmark input file exceeds limit')
        if path.suffix == '.bin':
            supported.append(raw)
        else:
            data = json.loads(raw)
            value = data.get('result',data).get('transaction')
            if isinstance(value,list) and len(value)==2 and value[1]=='base64':
                supported.append(base64.b64decode(value[0],validate=True))
            else: unsupported.append({'file':name,'reason':'JSON fixture has no original base64 wire transaction'})
    return supported,unsupported


def worker(args):
    packets,unsupported = inputs(args.files)
    if len(packets)>64: raise ValueError('At most 64 benchmark packets')
    if not packets: return {'status':'unsupported','inputs':unsupported}
    try:
        for raw in packets: parse_transaction(raw,args.worker)
    except (ValueError,RuntimeError) as error:
        return {'status':'unsupported','backend':args.worker,'reason':str(error)}
    if args.scope=='parser' and args.worker=='rust':
        try:
            from _solana_binary import benchmark_parse
        except ImportError:
            return {'status':'unsupported','reason':'Native benchmark API unavailable'}
        elapsed,median,p95=benchmark_parse(packets,args.iterations,args.batch)
        peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return {'status':'measured','backend':'rust','scope':'Rust parser loop; input/output FFI outside timing',
                'input_sha256':[hashlib.sha256(raw).hexdigest() for raw in packets],
                'transactions_per_second':args.iterations*args.batch/(elapsed/1e9),
                'median_latency_ns':median,'p95_latency_ns':p95,'batch':args.batch,
                'process_peak_rss_bytes':peak if sys.platform=='darwin' else peak*1024,
                'allocations':None,'allocation_reason':'Native allocator instrumentation not installed'}
    operation=lambda raw:parse_transaction(raw,args.worker)
    if args.scope=='parser': operation=parse_reference
    if args.scope=='binding':
        if args.worker!='rust': return {'status':'unsupported','reason':'No Python FFI boundary'}
        from _solana_binary import binding_input_len
        operation=binding_input_len
    samples=[]
    started=time.perf_counter()
    for _ in range(args.iterations):
        for index in range(args.batch):
            before=time.perf_counter_ns()
            operation(packets[index % len(packets)])
            samples.append(time.perf_counter_ns()-before)
    elapsed=time.perf_counter()-started
    peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {'status':'measured','input_sha256':[hashlib.sha256(raw).hexdigest() for raw in packets],'backend':args.worker,'batch':args.batch,'transactions':len(samples),
            'transactions_per_second':len(samples)/elapsed,'median_latency_ns':statistics.median(samples),
            'p95_latency_ns':sorted(samples)[int((len(samples)-1)*.95)],
            'process_peak_rss_bytes':peak if sys.platform=='darwin' else peak*1024,
            'scope':args.scope, 'allocations':None,
            'allocation_reason':'No reliable cross-language allocation counter',
            'unsupported_inputs':unsupported}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('files',nargs='+')
    parser.add_argument('--iterations',type=int,default=100)
    parser.add_argument('--batch',type=int,default=1)
    parser.add_argument('--worker',choices=['python','rust'])
    parser.add_argument('--scope',choices=['adapter','parser','binding'],default='adapter')
    args=parser.parse_args()
    if not 1<=args.iterations<=1000 or args.batch not in (1,64): raise ValueError('Invalid bounded benchmark size')
    if args.worker: print(json.dumps(worker(args))); return
    results=[]
    for backend in ('python','rust'):
        for batch in (1,64):
            command=[sys.executable,__file__,'--worker',backend,'--batch',str(batch),'--iterations',str(args.iterations),'--scope',args.scope,*args.files]
            result=subprocess.run(command,check=True,text=True,stdout=subprocess.PIPE,timeout=120)
            results.append(json.loads(result.stdout))
    hashes=[result['input_sha256'] for result in results if result.get('status')=='measured']
    if hashes and any(value!=hashes[0] for value in hashes): raise ValueError('Benchmark inputs changed between workers')
    print(json.dumps(results,indent=2))


if __name__=='__main__': main()
