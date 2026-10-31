"""UNTESTED staged observer-history retention. Never manages native node files.

Only duplicate raw_blocks rows may be deleted. Events, transaction receipts,
coverage, conversions, candles and model/trade evidence are never deleted here.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import time
from observer_archive import export_slot, verify_archive, MAX_FRAME
from onchain_store import EventStore

DEFAULTS = dict(hot_seconds=86400, warm_seconds=2592000, os_reserve_bytes=50_000_000_000,
                emergency_bytes=50_000_000_000, archive_bytes=100_000_000_000,
                database_bytes=25_000_000_000, max_batch=32, compression_level=3)


def policy(values):
    result = {**DEFAULTS, **values}
    for key in DEFAULTS:
        if type(result[key]) is not int or result[key] <= 0:
            raise ValueError('Invalid retention limit: ' + key)
    if result['hot_seconds'] > result['warm_seconds'] or result['max_batch'] > 256 or result['compression_level'] > 6:
        raise ValueError('Invalid bounded retention tiers')
    return result


def tree_bytes(directory):
    root = Path(directory)
    if root.is_symlink():
        raise ValueError('Archive root must not be a symlink')
    total = 0
    if root.exists():
        for parent, dirs, files in os.walk(root, followlinks=False):
            if any((Path(parent)/name).is_symlink() for name in dirs + files):
                raise ValueError('Archive symlinks are not permitted')
            total += sum((Path(parent)/name).stat().st_size for name in files)
    return total


def accounting(store, directory, limits):
    limits = policy(limits)
    database = sum(p.stat().st_size for p in [store.path, Path(str(store.path)+'-wal'), Path(str(store.path)+'-shm')] if p.exists())
    archive = tree_bytes(directory)
    root = Path(directory)
    while not root.exists(): root = root.parent
    free = min(shutil.disk_usage(root).free, shutil.disk_usage(store.path.parent).free)
    alerts = []
    if free < limits['os_reserve_bytes'] + limits['emergency_bytes'] + 2*MAX_FRAME + 262144:
        alerts.append('disk reserve reached')
    if database >= limits['database_bytes']: alerts.append('observer database budget reached')
    if archive + 2*MAX_FRAME + 262144 > limits['archive_bytes']: alerts.append('archive budget reached')
    return {'database_bytes': database, 'archive_bytes': archive, 'free_bytes': free,
            'pause_nonessential_writes': bool(alerts), 'alerts': alerts,
            'active_node_state_bytes': None, 'native_state_managed': False}


def initialize(store):
    with store.connect() as db:
        db.executescript('''
          CREATE TABLE IF NOT EXISTS retention_pins(id TEXT PRIMARY KEY,first_slot INTEGER,last_slot INTEGER,reason TEXT);
          CREATE TABLE IF NOT EXISTS retention_archives(slot INTEGER PRIMARY KEY,path TEXT,raw_hash TEXT,state TEXT);
        ''')


def pin(store, identifier, first, last, reason):
    if type(first) is not int or type(last) is not int or first < 0 or last < first or not identifier or not reason:
        raise ValueError('Explicit dependency range and reason required')
    initialize(store)
    with store.connect() as db:
        db.execute('INSERT INTO retention_pins VALUES(?,?,?,?)', (identifier, first, last, reason))


def blockers(db, row, cutoff, safe_before):
    reasons = []
    if row['time'] >= cutoff: reasons.append('hot history')
    if row['slot'] >= safe_before: reasons.append('recovery boundary')
    if row['issues'] != '[]': reasons.append('decoder quarantine or unresolved evidence')
    checkpoint = db.execute("SELECT value FROM checkpoints WHERE name='next_slot'").fetchone()
    if not checkpoint or row['slot'] >= checkpoint[0]: reasons.append('unfinished recovery')
    if not db.execute('SELECT 1 FROM ranges WHERE start<=? AND end>=?', (row['slot'], row['slot'])).fetchone():
        reasons.append('missing finalized range')
    minute = row['time']//60*60
    left = db.execute('SELECT slot FROM blocks WHERE time<=? ORDER BY time DESC,slot DESC LIMIT 1', (minute,)).fetchone()
    right = db.execute('SELECT slot FROM blocks WHERE time>=? ORDER BY time,slot LIMIT 1', (minute+60,)).fetchone()
    if not left or not right:
        reasons.append('incomplete minute interval')
    else:
        if db.execute("SELECT 1 FROM blocks WHERE slot BETWEEN ? AND ? AND issues!='[]'", (left[0], right[0])).fetchone():
            reasons.append('unresolved interval evidence')
        cursor = left[0]
        for interval in db.execute('SELECT start,end FROM ranges WHERE end>=? AND start<=? ORDER BY start', (left[0],right[0])):
            if interval[0] > cursor: break
            cursor = max(cursor, interval[1]+1)
        if cursor <= right[0]: reasons.append('incomplete interval manifest')
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'retention_pins' in tables and db.execute('SELECT 1 FROM retention_pins WHERE first_slot<=? AND last_slot>=?', (row['slot'], row['slot'])).fetchone():
        reasons.append('dependency pin')
    if 'stream_hints' in tables and db.execute('SELECT 1 FROM stream_hints WHERE slot=?', (row['slot'],)).fetchone():
        reasons.append('pending recovery hint')
    return reasons


def plan(store, config, now=None):
    limits = policy(config)
    now = time.time() if now is None else now
    safe = config.get('safe_before_slot', 0)
    if type(safe) is not int or safe < 0: raise ValueError('Invalid recovery boundary')
    reviewed = config.get('dependencies_reviewed') is True
    cutoff = now - limits['hot_seconds']
    with store.connect() as db:
        # Bounded planning; no schema writes, materialization or full history load.
        rows = db.execute('SELECT b.* FROM blocks b JOIN raw_blocks r ON b.slot=r.slot WHERE b.time<? ORDER BY b.slot LIMIT ?',
                          (cutoff, limits['max_batch'])).fetchall()
        decisions = []
        for row in rows:
            reasons = blockers(db, row, cutoff, safe)
            if not reviewed: reasons.append('external trade/training/replay dependencies not reviewed')
            decisions.append({'slot': row['slot'], 'raw_hash': row['raw_hash'], 'blockers': reasons,
                              'action': 'retain' if reasons else 'archive_then_verify_duplicate'})
    return {'dry_run': True, 'cutoff': cutoff, 'safe_before_slot': safe, 'decisions': decisions,
            'deletes': 'only verified duplicate raw_blocks payloads; never native node state',
            'maintenance': 'freelist pages are reusable; offline VACUUM requires exclusive maintenance and temporary disk budget',
            'warm_eviction': 'disabled: dependent receipts/events/cold aggregates retained; capacity pause applies'}


def apply(store, directory, config, delete_duplicates=False, now=None):
    """Archives first. Deletion requires explicit opt-in and fresh transactional checks."""
    limits = policy(config)
    report = plan(store, config, now)
    report['dry_run'] = False
    report['results'] = []
    initialize(store)
    for decision in report['decisions']:
        if decision['blockers']: continue
        usage = accounting(store, directory, limits)
        if usage['pause_nonessential_writes']:
            report['storage'] = usage
            break
        slot = decision['slot']
        # export_slot is atomic, fsynced and idempotent. Crash before metadata
        # leaves an orphaned archive and an untouched source; retry verifies it.
        export_slot(store, slot, directory, level=limits['compression_level'])
        target = (Path(directory)/str(slot)).resolve()
        raw, manifest = verify_archive(target)
        from onchain_hour import unpack_block
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM blocks WHERE slot=?', (slot,)).fetchone()
            original = db.execute('SELECT raw,raw_hash FROM raw_blocks WHERE slot=?', (slot,)).fetchone()
            if row is None or original is None or blockers(db, row, report['cutoff'], report['safe_before_slot']):
                raise ValueError('Retention eligibility changed; original retained')
            if (manifest['raw_sha256'] != row['raw_hash'] or original['raw_hash'] != row['raw_hash']
                    or raw != unpack_block(original['raw']) or manifest['source_id'] != row['source']
                    or manifest['blockhash'] != row['hash'] or manifest['parent_slot'] != row['parent']):
                raise ValueError('Archive/source equivalence failed; original retained')
            db.execute('INSERT OR REPLACE INTO retention_archives VALUES(?,?,?,?)',
                       (slot, str(target), row['raw_hash'], 'verified'))
            if delete_duplicates:
                db.execute('DELETE FROM raw_blocks WHERE slot=?', (slot,))
                db.execute("UPDATE retention_archives SET state='duplicate_removed' WHERE slot=?", (slot,))
        report['results'].append({'slot': slot, 'duplicate_removed': delete_duplicates})
    return report


def restore(store, slot):
    import zlib
    raw = store.retained_block(slot)
    if raw is None: raise ValueError('No verified retained evidence')
    with store.connect() as db:
        row = db.execute('SELECT raw_hash FROM blocks WHERE slot=?', (slot,)).fetchone()
        db.execute('INSERT OR IGNORE INTO raw_blocks VALUES(?,?,?)', (slot, zlib.compress(raw), row['raw_hash']))
    return {'slot': slot, 'restored': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--archive-directory', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--delete-duplicates', action='store_true')
    args = parser.parse_args()
    if args.delete_duplicates and not args.apply: parser.error('Deletion requires --apply')
    # Opening readonly first prevents accidental initialization of an unrelated path.
    store = EventStore(args.db, readonly=True)
    config = json.loads(args.config.read_text())
    result = plan(store, config)
    result['storage'] = accounting(store, args.archive_directory, config)
    if args.apply:
        with store.connect() as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'blocks','raw_blocks','ranges','transactions','sources'}.issubset(tables):
            raise ValueError('Not an observer database')
        result = apply(EventStore(args.db), args.archive_directory, config, args.delete_duplicates)
    print(json.dumps(result, indent=2))


if __name__ == '__main__': main()
