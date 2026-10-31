"""UNEXECUTED offline binary benchmark. Never re-serializes JSON transactions."""
import argparse
import base64
import json
from pathlib import Path
import resource
import statistics
import subprocess
import sys
import time
from solana_binary import parse_transaction


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
    if not packets: return {'status':'unsupported','inputs':unsupported}
    try:
        for raw in packets: parse_transaction(raw,args.worker)
    except (ValueError,RuntimeError) as error:
        return {'status':'unsupported','backend':args.worker,'reason':str(error)}
    samples=[]
    started=time.perf_counter()
    for _ in range(args.iterations):
        for index in range(args.batch):
            before=time.perf_counter_ns()
            parse_transaction(packets[index % len(packets)],args.worker)
            samples.append(time.perf_counter_ns()-before)
    elapsed=time.perf_counter()-started
    peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {'status':'measured','backend':args.worker,'batch':args.batch,'transactions':len(samples),
            'transactions_per_second':len(samples)/elapsed,'median_latency_ns':statistics.median(samples),
            'p95_latency_ns':sorted(samples)[int((len(samples)-1)*.95)],
            'process_peak_rss_bytes':peak if sys.platform=='darwin' else peak*1024,
            'scope':'isolated process including Python and imports; serial bounded batches',
            'unsupported_inputs':unsupported}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('files',nargs='+')
    parser.add_argument('--iterations',type=int,default=100)
    parser.add_argument('--batch',type=int,default=1)
    parser.add_argument('--worker',choices=['python','rust'])
    args=parser.parse_args()
    if not 1<=args.iterations<=1000 or args.batch not in (1,64): raise ValueError('Invalid bounded benchmark size')
    if args.worker: print(json.dumps(worker(args))); return
    results=[]
    for backend in ('python','rust'):
        for batch in (1,64):
            command=[sys.executable,__file__,'--worker',backend,'--batch',str(batch),'--iterations',str(args.iterations),*args.files]
            result=subprocess.run(command,check=True,text=True,stdout=subprocess.PIPE,timeout=120)
            results.append(json.loads(result.stdout))
    print(json.dumps(results,indent=2))


if __name__=='__main__': main()
