"""Exact-pool, provenance-preserving historical candle import.

Provider OHLC is checked against on-chain identity and sampled real swaps;
this is not independent reconstruction of every historical transaction.
"""
import base64
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import time
import urllib.request
from strategy import Candle,validate
from pumpswap import DEFAULT_POOL
from bot import SOL_MINT
from market import USDC_MINT
from collect_pool import decode_logs

API='https://api.geckoterminal.com/api/v2/networks/solana/pools/'+DEFAULT_POOL
HISTORY_URL=API+'/ohlcv/hour?aggregate=1&limit=1000&currency=token&token=quote&include_empty_intervals=false'


def fetch_json(url):
    request=urllib.request.Request(url,headers={'Accept':'application/json','User-Agent':'solana-spot-bot/0.1'})
    with urllib.request.urlopen(request,timeout=30) as response:
        raw=response.read(10_000_001)
    if len(raw)>10_000_000:
        raise ValueError('Excessive history payload')
    return json.loads(raw),raw


def normalize(metadata,payload,now):
    pool=metadata['data']
    relationships=pool['relationships']
    if (pool['id']!='solana_'+DEFAULT_POOL or pool['attributes']['address']!=DEFAULT_POOL or
            relationships['dex']['data']['id']!='pumpswap' or
            relationships['base_token']['data']['id']!='solana_'+USDC_MINT or
            relationships['quote_token']['data']['id']!='solana_'+SOL_MINT):
        raise ValueError('Provider pool identity mismatch')
    if payload['meta']['base']['address']!=SOL_MINT or payload['meta']['quote']['address']!=USDC_MINT:
        raise ValueError('History price orientation mismatch')
    rows=payload['data']['attributes']['ohlcv_list']
    candles=[]
    seen=set()
    for row in rows:
        if len(row)!=6 or not math.isfinite(row[0]) or int(row[0])!=row[0] or row[0]%3600:
            raise ValueError('Invalid provider timestamp/row')
        timestamp=int(row[0])
        if timestamp in seen:
            raise ValueError('Duplicate provider candle')
        seen.add(timestamp)
        if timestamp+3600>now:
            continue
        candles.append(Candle(timestamp,*(float(v) for v in row[1:])))
    candles.sort(key=lambda c:c.timestamp)
    last_gap=0
    gaps=[]
    for index,(a,b) in enumerate(zip(candles,candles[1:])):
        if b.timestamp-a.timestamp!=3600:
            gaps.append([a.timestamp,b.timestamp])
            last_gap=index+1
    candles=candles[last_gap:]
    validate(candles,now)
    if len(candles)<56:
        raise ValueError('Insufficient contiguous source history')
    if candles[-1].timestamp != int(now)//3600*3600-3600:
        raise ValueError('Provider does not yet have the latest complete hour')
    return candles,gaps


def sample_chain(rpc,candles,pool=DEFAULT_POOL):
    """Check sample executed swap prices against matching provider OHLC ranges."""
    by_hour={c.timestamp:c for c in candles}
    records=rpc.call('getSignaturesForAddress',[pool,{'limit':1000,'commitment':'confirmed'}])
    candidates=[r for r in records if r.get('blockTime') is not None and r.get('err') is None
                and r['blockTime']//3600*3600 in by_hour]
    samples=[]
    # Try a bounded number: a transaction can touch this pool without a swap.
    for record in candidates[:12]:
        tx=rpc.call('getTransaction',[record['signature'],{'encoding':'json','commitment':'confirmed','maxSupportedTransactionVersion':1}])
        if not tx or not tx.get('meta') or tx['meta']['err'] is not None:
            continue
        for event in decode_logs(tx['meta'].get('logMessages') or [],record['signature'],pool):
            hour=int(event['timestamp']/1000)//3600*3600
            if hour not in by_hour:
                continue
            price=event['tokenAmount']/event['quoteAmount']
            bar=by_hour[hour]
            # Small tolerance for provider trade filtering/rounding; record it.
            if not bar.low*.995 <= price <= bar.high*1.005:
                raise ValueError('Sampled on-chain swap price disagrees with historical OHLC')
            samples.append({'signature':record['signature'],'hour':hour,'sol_usdc_price':price})
        if len(samples)>=3:
            break
    if not samples:
        raise ValueError('Could not verify any complete-hour swaps against history')
    return samples


def publish(adapter,destination='data/verified_pool_hourly.csv'):
    now=int(time.time())
    snapshot=adapter.snapshot() # validates on-chain owner, PDA, pair and vaults
    metadata,raw_metadata=fetch_json(API)
    payload,raw_history=fetch_json(HISTORY_URL)
    candles,gaps=normalize(metadata,payload,now)
    samples=sample_chain(adapter.rpc,candles)
    output=io.StringIO(newline='')
    writer=csv.writer(output)
    writer.writerow(['timestamp','open','high','low','close','volume'])
    for bar in candles:
        writer.writerow([bar.timestamp,bar.open,bar.high,bar.low,bar.close,bar.volume])
    raw_csv=output.getvalue().encode()
    destination=Path(destination)
    destination.parent.mkdir(exist_ok=True,parents=True)
    manifest={'schema':1,'network':'solana-mainnet','pool':str(adapter.pool),
        'base_mint':SOL_MINT,'quote_mint':USDC_MINT,'price_unit':'USDC per SOL',
        'volume_unit':'provider USD volume; unused by Donchian signal',
        'source':'GeckoTerminal exact-pool OHLCV','source_url':HISTORY_URL,
        'retrieved_at':now,'chain_slot':snapshot['slot'],'candles':len(candles),
        'first_timestamp':candles[0].timestamp,'last_timestamp':candles[-1].timestamp,
        'discarded_gaps':gaps,'chain_samples':samples,'price_sample_tolerance_fraction':.005,
        'verification':'pool identity, mint orientation, timestamps, OHLC invariants and sampled chain swaps; not full chain reconstruction',
        'csv_sha256':hashlib.sha256(raw_csv).hexdigest(),
        'source_sha256':hashlib.sha256(raw_history).hexdigest(),
        'metadata_sha256':hashlib.sha256(raw_metadata).hexdigest()}
    # Preserve raw source for audit; install CSV then manifest atomically. A
    # reader seeing an intermediate hash mismatch blocks entries and retries.
    for path,raw in [(destination.with_suffix('.source.json'),raw_history),
                     (destination.with_suffix('.metadata.json'),raw_metadata),
                     (destination,raw_csv),
                     (destination.with_suffix('.manifest.json'),json.dumps(manifest,indent=2).encode())]:
        temp=path.with_suffix(path.suffix+'.tmp')
        temp.write_bytes(raw)
        os.replace(temp,path)
    return manifest


def load_history(path,now,pool=DEFAULT_POOL):
    path=Path(path)
    manifest=json.loads(path.with_suffix('.manifest.json').read_text())
    raw=path.read_bytes()
    if (manifest.get('pool')!=pool or manifest.get('network')!='solana-mainnet' or
            manifest.get('base_mint')!=SOL_MINT or manifest.get('quote_mint')!=USDC_MINT or
            manifest.get('csv_sha256')!=hashlib.sha256(raw).hexdigest() or not manifest.get('chain_samples')):
        raise ValueError('History provenance/hash mismatch')
    candles=[Candle(int(r['timestamp']),*(float(r[k]) for k in ('open','high','low','close','volume')))
             for r in csv.DictReader(io.StringIO(raw.decode()))]
    validate(candles,now)
    if (not candles or len(candles)!=manifest['candles'] or
            candles[0].timestamp!=manifest['first_timestamp'] or candles[-1].timestamp!=manifest['last_timestamp']):
        raise ValueError('History manifest bounds mismatch')
    return candles
