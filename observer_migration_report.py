"""Operator-run, bounded offline differential report. NO tests/network by default.

Optional benchmarks run existing offline workers on captured fixtures only.
Never writes production databases or imports captured data into learning.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from onchain_decode import Decoder


def report(benchmarks=False):
    root=Path(__file__).resolve().parent
    fixtures=root/'tests/fixtures/onchain'
    manifest=json.loads((fixtures/'manifest.json').read_text())
    if len(manifest['fixtures'])>16:raise ValueError('Too many differential fixtures')
    try:
        from _solana_binary import NativeBorshSchema
        available=True
    except ImportError:available=False
    decoders={'python':Decoder()}
    if available: decoders.update(rust=Decoder('rust'),compare=Decoder('compare'))
    rows=[]
    for item in manifest['fixtures']:
        path=(fixtures/item['file']).resolve()
        if path.parent!=fixtures.resolve():raise ValueError('Fixture path outside approved directory')
        with path.open('rb') as stream:raw=stream.read(8000001)
        if len(raw)>8000000 or hashlib.sha256(raw).hexdigest()!=item['sha256']:raise ValueError('Captured fixture integrity failure')
        tx=json.loads(raw)
        outputs={name:decoder.transaction(tx,item['slot'],item['transaction_index'],tx['blockTime']) for name,decoder in decoders.items()}
        expected=outputs['python']
        row={'file':item['file'],'sha256':item['sha256'],'source':manifest['source'],
             'expected_quarantine':item['expected'],'slot':item['slot'],'transaction_index':item['transaction_index'],
             'backends':{name:{'event_count':len(value[0]),'issues':value[1],
                 'equal_to_python':value==expected,'quarantined':bool(value[1]),'market_eligible':False} for name,value in outputs.items()},
             'native_status':'compared' if available else 'unavailable_not_compared'}
        if benchmarks:
            command=[sys.executable,str(root/'benchmark_event_pipeline.py'),str(path),'--slot',str(item['slot']),
                     '--block-time',str(tx['blockTime']),'--iterations','10']
            result=subprocess.run(command,check=True,text=True,stdout=subprocess.PIPE,timeout=120,cwd=root)
            row['benchmarks']=json.loads(result.stdout)
        rows.append(row)
    return {'status':'operator_offline_replay','unit_tests_executed_by_report':0,
            'native_available':available,'fixtures':rows,'benchmarks_requested':benchmarks,
            'network_executed':False,'independent_coverage_verified':False,
            'source_ml_rights':manifest['ml_rights'],'production_ready':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--benchmarks',action='store_true')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    # Refuse overwrite so successive operator evidence is preserved.
    with args.output.open('x') as stream:
        json.dump(report(args.benchmarks),stream,indent=2)
        stream.write('\n')


if __name__=='__main__':main()
