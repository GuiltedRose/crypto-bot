"""Operator-run hardware/compression/query diagnostics. No network or live trading.

No benchmark is performed unless an input or synthetic size is explicitly given.
"""
import argparse
import json
import os
from pathlib import Path
import resource
import tempfile
import time
from observer_archive import pack, unpack, MAX_RAW
from onchain_store import EventStore


def hardware(directory):
    directory = Path(directory)
    cpu = Path('/proc/cpuinfo').read_text() if Path('/proc/cpuinfo').exists() else ''
    flags = next((line.split(':',1)[1].split() for line in cpu.splitlines() if line.startswith('flags')), [])
    memory = Path('/proc/meminfo').read_text() if Path('/proc/meminfo').exists() else ''
    ram_kib = next((int(line.split()[1]) for line in memory.splitlines() if line.startswith('MemTotal:')), None)
    disk = os.statvfs(directory)
    return {'logical_cpus': os.cpu_count(), 'ram_kib': ram_kib,
            'avx2': 'avx2' in flags, 'sha_ni': 'sha_ni' in flags,
            'free_filesystem_bytes': disk.f_bavail*disk.f_frsize,
            'validator_sync_proven': False, 'network_connectivity': None,
            'note': 'Filesystem free space is not an IOPS, BTRFS compression or validator throughput test'}


def measure(raw, scratch, iterations=1):
    if not 1 <= iterations <= 5 or len(raw)>MAX_RAW:
        raise ValueError('Bounded benchmark requires 1..5 iterations and <=64MB input')
    measurements=[]
    before=resource.getrusage(resource.RUSAGE_CHILDREN)
    for _ in range(iterations):
        started=time.perf_counter();frame,manifest=pack(raw);compressed=time.perf_counter()-started
        started=time.perf_counter();recovered=unpack(frame,manifest);decoded=time.perf_counter()-started
        if recovered!=raw:raise ValueError('Archive round-trip mismatch')
        with tempfile.TemporaryDirectory(prefix='observer-benchmark-',dir=scratch) as root:
            started=time.perf_counter()
            with (Path(root)/'sample.zst').open('xb') as out:
                out.write(frame);out.flush();os.fsync(out.fileno())
            write_seconds=time.perf_counter()-started
        measurements.append({'raw_bytes':len(raw),'frame_bytes':len(frame),
                             'ratio':len(frame)/len(raw) if raw else None,
                             'compress_seconds':compressed,'decompress_seconds':decoded,
                             'archive_write_fsync_seconds':write_seconds})
    after=resource.getrusage(resource.RUSAGE_CHILDREN)
    return {'samples':measurements,'codec_child_cpu_seconds':after.ru_utime+after.ru_stime-before.ru_utime-before.ru_stime,
            'process_peak_rss_platform_units':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            'child_peak_rss_platform_units':after.ru_maxrss,
            'network_measured':False,'validator_measured':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scratch-directory',type=Path,required=True)
    group=parser.add_mutually_exclusive_group()
    group.add_argument('--input',type=Path)
    group.add_argument('--synthetic-bytes',type=int)
    parser.add_argument('--iterations',type=int,default=1)
    parser.add_argument('--index-db',type=Path)
    args=parser.parse_args()
    report={'hardware':hardware(args.scratch_directory),'benchmark_status':'not_requested'}
    raw=None
    if args.input:
        with args.input.open('rb') as stream:raw=stream.read(MAX_RAW+1)
        if len(raw)>MAX_RAW:raise ValueError('Input exceeds raw evidence limit')
        report['input_kind']='operator supplied; real-chain provenance not inferred'
    if args.synthetic_bytes is not None:
        if not 0<=args.synthetic_bytes<=MAX_RAW:raise ValueError('Synthetic byte bound exceeded')
        raw=b'x'*args.synthetic_bytes;report['input_kind']='synthetic; not a real compression ratio'
    if raw is not None:
        report['benchmark']=measure(raw,args.scratch_directory,args.iterations)
        report['benchmark_status']='completed locally; not a validator/network benchmark'
    if args.index_db:
        store=EventStore(args.index_db,readonly=True)
        started=time.perf_counter()
        with store.connect() as db:
            rows=db.execute('SELECT slot,time FROM blocks ORDER BY slot DESC LIMIT 64').fetchall()
        report['indexed_header_query']={'rows':len(rows),'seconds':time.perf_counter()-started,
                                       'scope':'warm SQLite header query, not cold archive lookup'}
    print(json.dumps(report))


if __name__=='__main__':
    main()
