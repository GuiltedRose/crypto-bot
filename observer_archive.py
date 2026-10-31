"""Copy-only bounded Zstandard evidence archives; never prunes a source database."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import tempfile
import time
from onchain_hour import unpack_block
from onchain_store import EventStore, canonical

MAX_RAW = 64_000_000
MAX_FRAME = MAX_RAW + 1_000_000
FORMAT = 'solana-observer-block-zstd-v1'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def codec(raw, decompress=False, limit=MAX_FRAME, level=3, timeout=30):
    """One worker, bounded input/output/window/time; no shell or unbounded capture."""
    if len(raw) > MAX_FRAME or type(limit) is not int or not 0 <= limit <= MAX_FRAME:
        raise ValueError('Archive byte limit exceeded')
    if type(level) is not int or not 1 <= level <= 6 or not 0 < timeout <= 120:
        raise ValueError('Invalid bounded codec settings')
    if not decompress:
        import io
        from observer_zstd import compress_stream, CHUNK
        output = io.BytesIO()
        compress_stream((memoryview(raw)[offset:offset+CHUNK] for offset in range(0,len(raw),CHUNK)),
                        output, level=level, timeout=timeout)
        frame = output.getvalue()
        if len(frame) > limit: raise ValueError('Compressed output limit exceeded')
        return frame
    executable = shutil.which('zstd')
    if executable is None:
        raise RuntimeError('Install zstd or enter nix develop; no codec fallback is used')
    flags = ['--decompress', '--memory=64MB'] if decompress else ['-'+str(level), '-T1', '--zstd=wlog=23']
    with tempfile.TemporaryFile() as source:
        source.write(raw); source.seek(0)
        proc = subprocess.Popen([executable, '-q', '-c', *flags], stdin=source,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        selector = selectors.DefaultSelector()
        selector.register(proc.stdout, selectors.EVENT_READ)
        output = bytearray(); deadline = time.monotonic()+timeout
        try:
            while True:
                remaining = deadline-time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise TimeoutError('Archive codec deadline exceeded')
                chunk = os.read(proc.stdout.fileno(), min(65536, limit-len(output)+1))
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > limit:
                    raise ValueError('Archive decompression/output limit exceeded')
            if proc.wait(timeout=max(.001, deadline-time.monotonic())) != 0:
                raise ValueError('Invalid archive frame or codec failure')
            return bytes(output)
        finally:
            selector.close()
            if proc.poll() is None: proc.kill()
            proc.wait()
            proc.stdout.close()


def pack(raw, level=3):
    if len(raw) > MAX_RAW:
        raise ValueError('Raw evidence exceeds limit')
    frame = codec(raw, level=level)
    manifest = {'format': FORMAT, 'raw_bytes': len(raw), 'raw_sha256': digest(raw),
                'frame_bytes': len(frame), 'frame_sha256': digest(frame), 'level': level}
    return frame, manifest


def unpack(frame, manifest):
    count = manifest.get('raw_bytes')
    if manifest.get('format') != FORMAT or type(count) is not int or not 0 <= count <= MAX_RAW:
        raise ValueError('Unrecognized archive format or raw size')
    if len(frame) > MAX_FRAME or len(frame) != manifest.get('frame_bytes') or digest(frame) != manifest.get('frame_sha256'):
        raise ValueError('Compressed evidence hash/length mismatch')
    raw = codec(frame, decompress=True, limit=count)
    if len(raw) != count or digest(raw) != manifest.get('raw_sha256'):
        raise ValueError('Raw evidence hash/length mismatch')
    return raw


def export_slot(store, slot, directory, level=3):
    if type(slot) is not int or slot < 0:
        raise ValueError('Invalid slot')
    with store.connect() as db:
        row = db.execute('SELECT b.*,r.raw,r.raw_hash AS retained_hash FROM blocks b JOIN raw_blocks r ON b.slot=r.slot WHERE b.slot=? AND length(r.raw)<=?', (slot,MAX_FRAME)).fetchone()
    if row is None:
        raise ValueError('Full retained block required; selected transactions alone are insufficient')
    raw = unpack_block(row['raw'])
    if digest(raw) != row['raw_hash'] or row['retained_hash'] != row['raw_hash']:
        raise ValueError('Retained block integrity mismatch')
    frame, manifest = pack(raw, level)
    # Column-oriented sidecar removes repeated event keys; SQL events and candles
    # remain immediately queryable. Full block evidence is separately retained.
    with store.connect() as db:
        events = [json.loads(item[0]) for item in db.execute(
            'SELECT payload FROM events WHERE slot=? ORDER BY tx_index,outer_index,event_index', (slot,))]
        columns = sorted({key for event in events for key in event})
        conversions = [list(item) for item in db.execute('SELECT * FROM conversions WHERE time BETWEEN ? AND ? ORDER BY mint,time,source', (row['time']-3600,row['time']))]
        policies = [list(item) for item in db.execute('SELECT id,policy FROM sources ORDER BY id')]
    compact = canonical({'format':'observer-market-columns-v1','columns':columns,
                         'rows':[[event.get(key) for key in columns] for event in events],
                         'presence':[[index for index,key in enumerate(columns) if key in event] for event in events],
                         'conversion_columns':['mint','time','usd','source','proof'],
                         'conversions':conversions,'source_policies':policies}).encode()
    market_frame, market_manifest = pack(compact, level)
    manifest['market_archive'] = market_manifest
    manifest['market_event_count'] = len(events)
    manifest['transaction_count'] = row['transactions']
    manifest['slot_range'] = [slot,slot]
    manifest.update(slot=slot, blockhash=row['hash'], parent_slot=row['parent'],
                    time=row['time'], source_id=row['source'], issues=json.loads(row['issues']),
                    retention='copy only; source evidence and evaluation references retained')
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = directory/str(slot)
    if target.exists():
        with (target/'manifest.json').open('rb') as stream:
            metadata = stream.read(262145)
        if len(metadata)>262144:raise ValueError('Archive manifest exceeds limit')
        old = json.loads(metadata)
        with (target/'block.zst').open('rb') as stream:
            existing = stream.read(MAX_FRAME+1)
        if old != manifest or unpack(existing, old) != raw:
            raise ValueError('Conflicting existing archive')
        return manifest
    temporary = Path(tempfile.mkdtemp(prefix='.pending-', dir=directory))
    try:
        for name, content in [('block.zst', frame), ('market.zst', market_frame), ('manifest.json', (canonical(manifest)+'\n').encode())]:
            with (temporary/name).open('xb') as output:
                output.write(content); output.flush(); os.fsync(output.fileno())
        # Flush the directory as well as file contents before publishing the pair.
        fd = os.open(temporary, os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
        os.rename(temporary, target)
        fd = os.open(directory, os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
    finally:
        if temporary.exists(): shutil.rmtree(temporary)
    return manifest


def verify_archive(directory):
    """Read back bounded bytes and independently validate decoded block identity."""
    directory = Path(directory)
    if directory.is_symlink():
        raise ValueError('Archive directory symlink rejected')
    for name in ('manifest.json', 'block.zst'):
        if (directory/name).is_symlink():
            raise ValueError('Archive file symlink rejected')
    with (directory/'manifest.json').open('rb') as stream:
        metadata = stream.read(262145)
    if len(metadata) > 262144:
        raise ValueError('Oversized archive manifest')
    manifest = json.loads(metadata)
    with (directory/'block.zst').open('rb') as stream:
        frame = stream.read(MAX_FRAME + 1)
    raw = unpack(frame, manifest)
    block = json.loads(raw)
    if (block['blockhash'], block['parentSlot'], block['blockTime']) != (
            manifest['blockhash'], manifest['parent_slot'], manifest['time']):
        raise ValueError('Archive block identity mismatch')
    if not isinstance(block['transactions'], list):
        raise ValueError('Invalid archived transaction ordering')
    if 'market_archive' in manifest:
        if (directory/'market.zst').is_symlink(): raise ValueError('Market archive symlink rejected')
        with (directory/'market.zst').open('rb') as stream:
            market_frame = stream.read(MAX_FRAME+1)
        market = json.loads(unpack(market_frame, manifest['market_archive']))
        if market['format'] != 'observer-market-columns-v1' or len(market['rows']) != manifest['market_event_count']:
            raise ValueError('Market archive count/format mismatch')
        if len(market.get('presence', [])) != len(market['rows']):
            raise ValueError('Market archive key-presence mismatch')
        if any(len(row) != len(market['columns']) for row in market['rows']):
            raise ValueError('Market archive column mismatch')
    return raw, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--slot', type=int, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(export_slot(EventStore(args.db, readonly=True), args.slot, args.output)))


if __name__ == '__main__':
    main()
