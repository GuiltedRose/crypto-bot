"""Atomic finalized-block journal. No access to trading/evaluation databases."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import zlib


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


class EventStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS sources(
                    id TEXT PRIMARY KEY, policy TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS blocks(
                    slot INTEGER PRIMARY KEY, hash TEXT NOT NULL, parent INTEGER NOT NULL,
                    time INTEGER NOT NULL, source TEXT NOT NULL, issues TEXT NOT NULL,
                    raw_hash TEXT NOT NULL, transactions INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS block_times ON blocks(time);
                CREATE TABLE IF NOT EXISTS transactions(
                    signature TEXT PRIMARY KEY, slot INTEGER NOT NULL, tx_index INTEGER NOT NULL,
                    raw BLOB NOT NULL, raw_hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events(
                    signature TEXT NOT NULL, outer_index INTEGER NOT NULL, event_index INTEGER NOT NULL,
                    slot INTEGER NOT NULL, tx_index INTEGER NOT NULL, time INTEGER NOT NULL,
                    mint TEXT, pool TEXT, wallet TEXT, kind TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(signature,outer_index,event_index));
                CREATE INDEX IF NOT EXISTS market_events ON events(mint,pool,time);
                CREATE INDEX IF NOT EXISTS wallet_events ON events(wallet,time);
                CREATE TABLE IF NOT EXISTS ranges(
                    start INTEGER PRIMARY KEY, end INTEGER NOT NULL, source TEXT NOT NULL,
                    slots TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS checkpoints(name TEXT PRIMARY KEY, value INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS conversions(
                    mint TEXT NOT NULL, time INTEGER NOT NULL, usd TEXT NOT NULL,
                    source TEXT NOT NULL, proof TEXT NOT NULL, PRIMARY KEY(mint,time,source));
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA synchronous=FULL')
        try:
            with db:
                yield db
        finally:
            db.close()

    def register_source(self, identifier, policy):
        if not policy.get('storage_verified') or not policy.get('evidence'):
            raise ValueError('Verified storage policy/evidence required')
        with self.connect() as db:
            row = db.execute('SELECT policy FROM sources WHERE id=?', (identifier,)).fetchone()
            if row and row[0] != canonical(policy):
                raise ValueError('Source policy immutable; use a new versioned source ID')
            db.execute('INSERT OR IGNORE INTO sources VALUES(?,?)', (identifier, canonical(policy)))

    def policy(self, identifier):
        with self.connect() as db:
            row = db.execute('SELECT policy FROM sources WHERE id=?', (identifier,)).fetchone()
        if row is None:
            raise ValueError('Unregistered source')
        return json.loads(row[0])

    def next_slot(self):
        with self.connect() as db:
            row = db.execute("SELECT value FROM checkpoints WHERE name='next_slot'").fetchone()
        return row[0] if row else None

    def commit_range(self, start, end, slots, decoded, source):
        """Persist manifest, all produced blocks and checkpoint in one transaction.

        getBlocks supplies skipped-slot evidence; RPC failures never count as skips.
        decoded: [(slot, full block, events, issues, relevant raw transactions)].
        """
        self.policy(source)
        if type(start) is not int or end < start or slots != sorted(set(slots)):
            raise ValueError('Invalid finalized slot manifest')
        if any(not start <= slot <= end for slot in slots) or [x[0] for x in decoded] != slots:
            raise ValueError('Incomplete manifest blocks')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            checkpoint = db.execute("SELECT value FROM checkpoints WHERE name='next_slot'").fetchone()
            existing = db.execute('SELECT end,source,slots FROM ranges WHERE start=?', (start,)).fetchone()
            if existing:
                if tuple(existing) != (end, source, canonical(slots)):
                    raise ValueError('Conflicting finalized manifest')
                # Still verify every supplied block below; never overwrite history.
            elif checkpoint and checkpoint[0] != start:
                raise ValueError('Noncontiguous ingestion; recover checkpoint first')
            previous = db.execute('SELECT slot,time,hash FROM blocks WHERE slot<? ORDER BY slot DESC LIMIT 1', (start,)).fetchone()
            for slot, block, events, issues, transactions in decoded:
                stamp = block['blockTime']
                if type(stamp) is not int or not block.get('blockhash'):
                    raise ValueError('Finalized block time/hash unavailable')
                if previous and (block['parentSlot'] != previous[0] or stamp < previous[1]
                                 or block['previousBlockhash'] != previous[2]):
                    raise ValueError('Finalized parent/time continuity mismatch')
                digest = hashlib.sha256(canonical(block).encode()).hexdigest()
                row = db.execute('SELECT raw_hash FROM blocks WHERE slot=?', (slot,)).fetchone()
                if row and row[0] != digest:
                    raise ValueError('Conflicting finalized block')
                db.execute('INSERT OR IGNORE INTO blocks VALUES(?,?,?,?,?,?,?,?)',
                           (slot, block['blockhash'], block['parentSlot'], stamp, source,
                            canonical(issues), digest, len(block['transactions'])))
                for index, tx in transactions:
                    raw = canonical(tx).encode()
                    signature = tx['transaction']['signatures'][0]
                    old = db.execute('SELECT raw_hash FROM transactions WHERE signature=?', (signature,)).fetchone()
                    raw_hash = hashlib.sha256(raw).hexdigest()
                    if old and old[0] != raw_hash:
                        raise ValueError('Conflicting transaction provenance')
                    db.execute('INSERT OR IGNORE INTO transactions VALUES(?,?,?,?,?)',
                               (signature, slot, index, zlib.compress(raw), raw_hash))
                for event in events:
                    payload = canonical(event)
                    key = (event['signature'], event['outer_index'], event['event_index'])
                    old = db.execute('SELECT payload FROM events WHERE signature=? AND outer_index=? AND event_index=?', key).fetchone()
                    if old and old[0] != payload:
                        raise ValueError('Conflicting canonical event')
                    db.execute('INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                               (*key, slot, event['tx_index'], stamp, event.get('mint'),
                                event.get('pool'), event.get('wallet'), event['kind'], payload))
                previous = (slot, stamp, block['blockhash'])
            db.execute('INSERT OR IGNORE INTO ranges VALUES(?,?,?,?)',
                       (start, end, source, canonical(slots)))
            if not existing:
                db.execute("INSERT OR REPLACE INTO checkpoints VALUES('next_slot',?)", (end + 1,))

    def coverage(self, start, end):
        if end <= start:
            raise ValueError('Invalid coverage interval')
        with self.connect() as db:
            left = db.execute('SELECT slot FROM blocks WHERE time<=? ORDER BY time DESC,slot ASC LIMIT 1', (start,)).fetchone()
            right = db.execute('SELECT slot,time FROM blocks WHERE time>=? ORDER BY time,slot LIMIT 1', (end,)).fetchone()
            if not left or not right:
                return {'complete': False, 'coverage_pct': 0, 'reason': 'window not bracketed by finalized blocks'}
            blocks = db.execute('SELECT * FROM blocks WHERE slot BETWEEN ? AND ? ORDER BY slot', (left[0], right[0])).fetchall()
            reasons = sorted({reason for b in blocks for reason in json.loads(b['issues'])})
            sources = sorted({b['source'] for b in blocks})
            # Ranges are contiguous by construction; detect any damaged manifest too.
            ranges = db.execute('SELECT start,end FROM ranges WHERE end>=? AND start<=? ORDER BY start', (left[0], right[0])).fetchall()
            cursor = left[0]
            for row in ranges:
                if row[0] > cursor:
                    reasons.append('missing finalized slot range')
                cursor = max(cursor, row[1] + 1)
            if cursor <= right[0]:
                reasons.append('missing finalized slot range')
        return {'complete': not reasons, 'coverage_pct': 0 if reasons else 100,
                'reason': '; '.join(reasons) if reasons else 'continuous finalized block manifests',
                'sources': sources, 'through_time': right[1],
                'first_slot': left[0], 'last_slot': right[0]}

    def events(self, mint=None, pool=None, start=0, end=2**63-1, wallet=None):
        query = 'SELECT payload FROM events WHERE time>=? AND time<?'
        args = [start, end]
        for key, value in (('mint', mint), ('pool', pool), ('wallet', wallet)):
            if value is not None:
                query += ' AND ' + key + '=?'
                args.append(value)
        query += ' ORDER BY slot,tx_index,outer_index,event_index'
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute(query, args)]

    def add_conversion(self, mint, timestamp, usd, source, proof):
        from decimal import Decimal
        price = Decimal(str(usd))
        if not price.is_finite() or price <= 0 or type(timestamp) is not int or not proof:
            raise ValueError('Invalid event-time conversion/proof')
        policy = self.policy(source)
        if policy.get('usd_conversion_verified') is not True:
            raise ValueError('USD conversion method/rights have not been reviewed')
        with self.connect() as db:
            old = db.execute('SELECT usd,proof FROM conversions WHERE mint=? AND time=? AND source=?', (mint, timestamp, source)).fetchone()
            if old and tuple(old) != (str(price), canonical(proof)):
                raise ValueError('Immutable conversion conflict')
            db.execute('INSERT OR IGNORE INTO conversions VALUES(?,?,?,?,?)',
                       (mint, timestamp, str(price), source, canonical(proof)))

    def conversion(self, mint, timestamp, max_age=60):
        from decimal import Decimal
        with self.connect() as db:
            rows = db.execute('SELECT * FROM conversions WHERE mint=? AND time<=? AND time>=? ORDER BY time DESC',
                              (mint, timestamp, timestamp-max_age)).fetchall()
        if not rows:
            raise ValueError('Fresh historical USD conversion unavailable')
        newest = [row for row in rows if row['time'] == rows[0]['time']]
        if len({Decimal(row['usd']) for row in newest}) != 1:
            raise ValueError('Conflicting USD conversion sources')
        return Decimal(rows[0]['usd']), rows[0]['source'], rows[0]['time']

    def watermark(self):
        with self.connect() as db:
            return db.execute('SELECT max(time) FROM blocks').fetchone()[0]

    def metrics(self):
        with self.connect() as db:
            result = {table: db.execute('SELECT count(*) FROM ' + table).fetchone()[0]
                      for table in ('blocks', 'events', 'transactions', 'ranges', 'conversions')}
            result['blocks_with_issues'] = db.execute("SELECT count(*) FROM blocks WHERE issues!='[]'").fetchone()[0]
            result['oldest_time'], result['latest_time'] = db.execute('SELECT min(time),max(time) FROM blocks').fetchone()
        result['next_slot'] = self.next_slot()
        return result
