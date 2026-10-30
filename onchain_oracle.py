"""Pyth Core finalized account snapshots: no HTTP price API or inferred USD peg.

Layout is pinned to the official receiver SDK. A snapshot is RPC evidence, not
an independently verified Solana state proof. Historic rates require historic
snapshots; querying a current account cannot retrieve its past values.
"""
import base64
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import struct
import time
from solders.pubkey import Pubkey
from wallet_watch import SOL

SPEC = json.loads(Path(__file__).with_name('vendor').joinpath('pyth_receiver.manifest.json').read_text())
DISCRIMINATOR = hashlib.sha256(b'account:PriceUpdateV2').digest()[:8]
MESSAGE = struct.Struct('<32sqQiqqqQ')


def sol_account(shard=0):
    if type(shard) is not int or not 0 <= shard < 65536:
        raise ValueError('Invalid oracle shard')
    return str(Pubkey.find_program_address(
        [shard.to_bytes(2, 'little'), bytes.fromhex(SPEC['sol_usd_feed_id'])],
        Pubkey.from_string(SPEC['push_program']))[0])


def capture_sol(rpc, shard=0):
    rpc.validate()
    address = sol_account(shard)
    response = rpc.call('getAccountInfo', [address, {'encoding': 'base64', 'commitment': 'finalized'}])
    slot = response['context']['slot']
    block_time = rpc.call('getBlockTime', [slot])
    return {'address': address, 'shard': shard, 'source_id': rpc.source,
            'commitment': 'finalized', 'response': response, 'block_time': block_time,
            'retrieved_at': int(time.time())}


def decode_sol(snapshot, max_age=60):
    if snapshot.get('commitment') != 'finalized':
        raise ValueError('Finalized oracle snapshot required')
    slot, block_time = snapshot['response']['context']['slot'], snapshot['block_time']
    if any(type(v) is not int or v < 0 for v in (slot, block_time, snapshot['retrieved_at'])):
        raise ValueError('Invalid oracle slot/time')
    if block_time > snapshot['retrieved_at']:
        raise ValueError('Oracle context time is in the future')
    if snapshot['address'] != sol_account(snapshot.get('shard', 0)):
        raise ValueError('Oracle account/feed identity mismatch')
    account = snapshot['response']['value']
    if not account or account['owner'] != SPEC['receiver_program'] or account.get('executable') is not False:
        raise ValueError('Oracle account owner/state unavailable')
    if account['data'][1] != 'base64':
        raise ValueError('Oracle encoding must be base64')
    raw = base64.b64decode(account['data'][0], validate=True)
    # Official LEN reserves 2 bytes for VerificationLevel's largest variant.
    # Full uses one byte. The single zero allocation byte is retained in proof;
    # no unrecognized extension or nonzero tail is accepted.
    if len(raw) != SPEC['allocated_length'] or raw[-1:] != b'\0':
        raise ValueError('Unreviewed oracle allocation/layout')
    if raw[:8] != DISCRIMINATOR or raw[40] != SPEC['full_verification_tag']:
        raise ValueError('Wrong oracle discriminator or partial verification')
    if str(Pubkey.from_bytes(raw[8:40])) != snapshot['address']:
        raise ValueError('Oracle write authority does not match feed PDA')
    feed, price, conf, exponent, published, previous, ema, ema_conf = MESSAGE.unpack_from(raw, 41)
    posted_slot = int.from_bytes(raw[41 + MESSAGE.size:41 + MESSAGE.size + 8], 'little')
    if feed.hex() != SPEC['sol_usd_feed_id']:
        raise ValueError('Wrong oracle price feed')
    if not -18 <= exponent <= 18 or price <= 0 or conf > price:
        raise ValueError('Invalid oracle price/confidence')
    if not 0 <= previous <= published <= block_time or block_time-published > max_age:
        raise ValueError('Stale, future or invalid oracle publish time')
    if posted_slot > slot:
        raise ValueError('Oracle posting occurs after snapshot context')
    usd = Decimal(price) * Decimal(10) ** exponent
    return {'mint': SOL, 'publish_time': published, 'posted_slot': posted_slot,
            'usd': str(usd), 'confidence_bps': str(Decimal(conf) * 10000 / price),
            'proof': {'method': 'pyth_price_update_v2', 'feed_id': feed.hex(),
                      'context_slot': slot, 'posted_slot': posted_slot,
                      'block_time': block_time, 'account': snapshot['address'],
                      'raw_sha256': hashlib.sha256(raw).hexdigest(), 'snapshot': snapshot,
                      'layout_sources': SPEC['sources'], 'verification': 'Full',
                      'ema_price': ema, 'ema_confidence': ema_conf}}


def import_sol(store, snapshot):
    """Explicit reviewed-source import; never upgrades source rights automatically."""
    source = snapshot['source_id']
    policy = store.policy(source)
    limit = policy.get('oracle_max_confidence_bps')
    if policy.get('oracle_data_rights_verified') is not True or type(limit) is not int or not 0 <= limit <= 10000:
        raise ValueError('Reviewed oracle rights and confidence policy required')
    row = decode_sol(snapshot)
    if Decimal(row['confidence_bps']) > limit:
        raise ValueError('Oracle confidence exceeds reviewed source policy')
    store.add_conversion(row['mint'], row['publish_time'], row['usd'], source, row['proof'])
    return {k: v for k, v in row.items() if k != 'proof'}
