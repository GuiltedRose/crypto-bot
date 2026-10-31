"""UNEXECUTED server-only archive benchmark; no network, databases or deletion.

Supply a copied evidence file. Bounds match the existing archive codec. Results
measure one sample, not independent node feasibility or guaranteed compression.
"""
import argparse
import json
from pathlib import Path
import time
from observer_archive import MAX_RAW, pack, unpack


def benchmark(raw, levels=(1, 3, 6)):
    if not raw or len(raw) > MAX_RAW:
        raise ValueError('Supply a nonempty bounded evidence sample')
    results = []
    for level in levels:
        start = time.perf_counter()
        frame, manifest = pack(raw, level=level)
        encode = time.perf_counter() - start
        start = time.perf_counter()
        if unpack(frame, manifest) != raw:
            raise ValueError('Archive roundtrip mismatch')
        results.append({'level': level, 'raw_bytes': len(raw), 'frame_bytes': len(frame),
                        'ratio': len(frame) / len(raw), 'encode_seconds': encode,
                        'decode_seconds': time.perf_counter() - start})
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server-only', action='store_true', required=True,
                        help='Explicit operator acknowledgment; run on the monolith only')
    parser.add_argument('sample', type=Path)
    args = parser.parse_args()
    with args.sample.open('rb') as stream:
        raw = stream.read(MAX_RAW + 1)
    print(json.dumps(benchmark(raw), indent=2))


if __name__ == '__main__':
    main()
