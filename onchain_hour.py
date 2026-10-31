"""Read-only frozen-hour audit. Replays full retained blocks, never trains models."""
import argparse
from collections import Counter
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import zlib
from onchain_decode import Decoder
from onchain_market import OnchainMarket
from onchain_store import EventStore, canonical


def unpack_block(raw):
    decoder = zlib.decompressobj()
    value = decoder.decompress(raw, 64_000_001)
    if len(value) > 64_000_000 or decoder.unconsumed_tail or not decoder.eof or decoder.unused_data:
        raise ValueError('Oversized or incomplete retained block')
    return value


def audit_hour(store, start, end, mint, pool):
    if type(start) is not int or type(end) is not int or end-start != 3600 or start % 60:
        raise ValueError('An aligned, exact 3600-second frozen window is required')
    coverage = store.coverage(start, end)
    errors, replayed, retained_bytes, replay_count = [], [], 0, 0
    with store.connect() as db:
        has_raw = bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='raw_blocks'").fetchone())
        if coverage.get('first_slot') is not None:
            blocks = db.execute('SELECT * FROM blocks WHERE slot BETWEEN ? AND ? ORDER BY slot',
                                (coverage['first_slot'], coverage['last_slot'])).fetchall()
        else:
            blocks = []
        decoder = Decoder()
        for row in blocks:
            try:
                retained = store.retained_block(row['slot'])
            except (ValueError, OSError):
                errors.append('retained archive invalid: ' + str(row['slot']))
                continue
            raw = {'raw': retained, 'raw_hash': row['raw_hash']} if retained is not None else None
            if raw is None:
                errors.append('full block not retained: ' + str(row['slot']))
                continue
            retained_bytes += len(raw['raw'])
            try:
                payload = raw['raw']
                digest = hashlib.sha256(payload).hexdigest()
                if digest != raw['raw_hash'] or digest != row['raw_hash']:
                    raise ValueError('retained block hash mismatch')
                block = json.loads(payload)
                if (block['blockhash'], block['parentSlot'], block['blockTime']) != (row['hash'], row['parent'], row['time']):
                    raise ValueError('retained header mismatch')
                events = []
                for index, tx in enumerate(block['transactions']):
                    found, issues = decoder.transaction(tx, row['slot'], index, row['time'])
                    events.extend(found)
                    if issues:
                        errors.extend('slot ' + str(row['slot']) + ': ' + reason for reason in issues)
                saved = [json.loads(x[0]) for x in db.execute(
                    'SELECT payload FROM events WHERE slot=? ORDER BY tx_index,outer_index,event_index', (row['slot'],))]
                if canonical(events) != canonical(saved):
                    raise ValueError('canonical events differ from full-block replay')
                replayed.extend(events)
                replay_count += 1
            except (ValueError, KeyError, TypeError, IndexError, zlib.error) as error:
                errors.append('slot ' + str(row['slot']) + ': ' + str(error))
    replay_ok = bool(blocks) and not errors
    decode_error_count = len(errors)
    market = OnchainMarket(store)
    buy, sell, conversion_sources = Decimal(0), Decimal(0), set()
    relevant = [e for e in replayed if start <= e['block_time'] < end]
    kinds = Counter(e['event_name'] for e in relevant)
    swaps = [e for e in relevant if e['kind'] == 'swap' and e['mint'] == mint and e['pool'] == pool]
    for event in swaps:
        try:
            _, volume, source, _ = market.valued(event)
            conversion_sources.add(source)
            if event['direction'] == 'buy':
                buy += volume
            else:
                sell += volume
        except ValueError as error:
            errors.append(str(error))
    try:
        candles = market.candles(mint, pool, start, end)
    except ValueError as error:
        candles = []
        errors.append(str(error))
    minute_states = Counter(market.minute_status(mint, pool, stamp) for stamp in range(start, end, 60))
    conversions_ok = len(errors) == decode_error_count
    sources = set(coverage.get('sources', [])) | conversion_sources
    policies = {source: store.policy(source) for source in sorted(sources)}
    rights_ok = bool(policies) and all(p.get('storage_verified') is True for p in policies.values())
    rights_ok = rights_ok and all(policies[source].get('usd_conversion_verified') is True for source in conversion_sources)
    content = {'start': start, 'end': end, 'mint': mint, 'pool': pool,
               'events': relevant, 'candles': candles, 'buy_volume_usd': str(buy), 'sell_volume_usd': str(sell)}
    return {'scope': 'global finalized block replay; analytics for requested mint/pool only',
            'start': start, 'end': end, 'mint': mint, 'pool': pool, 'coverage': coverage,
            'blocks_required': len(blocks), 'blocks_replayed': replay_count, 'retained_raw_bytes': retained_bytes,
            'replay_consistent': replay_ok, 'usd_conversions_complete': conversions_ok, 'replay_sha256': hashlib.sha256(canonical(content).encode()).hexdigest(),
            'event_counts': dict(sorted(kinds.items())), 'target_swaps': len(swaps),
            'buy_volume_usd': str(buy), 'sell_volume_usd': str(sell), 'completed_candles': len(candles),
            'minute_states': dict(sorted(minute_states.items())), 'source_rights_reviewed': rights_ok,
            'complete_hour_for_independent_review': bool(coverage['complete'] and replay_ok and conversions_ok and rights_ok),
            'independently_verified': False,
            'independent_verification_blocker': 'Requires matching finalized evidence from an independent authorized ledger source',
            'errors': sorted(set(errors))}


def compare_indexing(blocks):
    """Offline request/byte comparison for standard RPC, not a completeness claim."""
    programs = set(Decoder().specs)
    total, candidates, successful, byte_count, active_programs, ordered_blocks = 0, set(), set(), 0, set(), 0
    full_bytes, compressed_bytes = 0, 0
    for block in blocks:
        encoded = canonical(block).encode()
        full_bytes += len(encoded)
        compressed_bytes += len(zlib.compress(encoded))
        has_success = False
        for tx in block['transactions']:
            total += 1
            keys = tx['transaction']['message']['accountKeys']
            expanded = [key['pubkey'] if isinstance(key, dict) else key for key in keys]
            if not any(isinstance(key, dict) for key in keys):
                loaded = tx['meta'].get('loadedAddresses') or {}
                expanded += loaded.get('writable', []) + loaded.get('readonly', [])
            referenced = programs.intersection(expanded)
            if not referenced:
                continue
            active_programs.update(referenced)
            signature = tx['transaction']['signatures'][0]
            candidates.add(signature)
            if tx['meta']['err'] is None:
                if signature not in successful:
                    byte_count += len(canonical(tx).encode())
                successful.add(signature)
                has_success = True
        ordered_blocks += int(has_success)
    return {'blocks': len(blocks), 'transactions': total,
            'program_referencing_transactions': len(candidates), 'successful_candidates': len(successful),
            'full_block_requests_excluding_common_overhead': len(blocks),
            'selective_requests_lower_bound': len(successful) + len(programs) + ordered_blocks,
            'selective_ordering_block_requests': ordered_blocks,
            'full_json_bytes': full_bytes, 'full_zlib_bytes': compressed_bytes,
            'selected_successful_transaction_bytes': byte_count,
            'scope_note': 'Standard address pagination + deduplicated transaction reads + block signature lists for ordering; pagination beyond one page/program adds requests. No global completeness is asserted.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--start', type=int, required=True)
    parser.add_argument('--end', type=int, required=True)
    parser.add_argument('--mint', required=True)
    parser.add_argument('--pool', required=True)
    args = parser.parse_args()
    result = audit_hour(EventStore(args.db, readonly=True), args.start, args.end, args.mint, args.pool)
    print(json.dumps(result, allow_nan=False))
    return 0 if result['complete_hour_for_independent_review'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
