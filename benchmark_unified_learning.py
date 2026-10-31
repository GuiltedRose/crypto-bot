"""UNEXECUTED offline ML timing on a copied eligible cache, never original weights."""
import argparse
import json
from pathlib import Path
import resource
import subprocess
import sys
import tempfile
import time
from unified_observations import ObservationStore, eligible
from unified_learning import build_candidate


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',type=Path,required=True)
    parser.add_argument('--worker',choices=['svm','lstm','rag'])
    args=parser.parse_args()
    if args.worker:
        source=ObservationStore(args.db)
        records=[row for row in source.observations() if eligible(row)]
        with tempfile.TemporaryDirectory(prefix='unified-benchmark-') as root:
            copy=ObservationStore(Path(root)/'evidence.sqlite',create=True)
            for row in records:copy.add(row)
            started=time.perf_counter();candidate=build_candidate(copy,args.worker);elapsed=time.perf_counter()-started
            peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            print(json.dumps({'family':args.worker,'status':candidate['status'],'reason':candidate.get('reason'),
                              'seconds':elapsed,'source_rows':len(records),'peak_process_rss_bytes':peak if sys.platform=='darwin' else peak*1024,
                              'historical_evaluation':candidate.get('historical_evaluation'),'allocations':None,'source_modified':False}))
        return
    reports=[]
    for family in ('svm','lstm','rag'):
        result=subprocess.run([sys.executable,__file__,'--db',str(args.db),'--worker',family],check=True,text=True,stdout=subprocess.PIPE,timeout=300)
        reports.append(json.loads(result.stdout))
    print(json.dumps(reports,indent=2))


if __name__=='__main__':main()
