"""Scan reviewed Solana tokens and mature-meme candidates across DEX pools.

Read-only. Never treats token verification/popularity as a non-meme label.
Execution remains constrained by the installed venue adapters.
"""
import argparse
from dataclasses import asdict
from datetime import datetime,timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import time
import urllib.error
from urllib.parse import quote
from solders.pubkey import Pubkey
from strategy import Candle,validate,decide,WARMUP_BARS
from market import USDC_MINT
from bot import SOL_MINT
from pumpswap import DEFAULT_POOL
from history import fetch_json,load_history
from meme_policy import MatureMemePolicy

ROOT='https://api.geckoterminal.com/api/v2/networks/solana'


def fetch_scan_json(url):
    try:
        return fetch_json(url)
    except urllib.error.HTTPError as error:
        if error.code!=429:
            raise
        delay=min(60,max(10,int(error.headers.get('Retry-After','30'))))
        error.close()
        print(json.dumps({'status':'public API rate limit; backing off','seconds':delay}),flush=True)
        time.sleep(delay)
        return fetch_json(url)


def load_assets(path='assets.json'):
    document=json.loads(Path(path).read_text())
    result=[]
    seen=set()
    for asset in document['assets']:
        if not asset.get('approved_for_scanning') or asset.get('classification') not in ('native','non_meme','mature_meme_candidate'):
            continue
        mint=asset['mint']
        Pubkey.from_string(mint)
        if mint in seen:
            raise ValueError('Duplicate asset mint')
        seen.add(mint)
        result.append(asset)
    return result


def select_pool(pools,mint,min_liquidity=100000,min_volume=10000):
    eligible=[]
    for pool in pools:
        relations=pool['relationships']
        base=relations['base_token']['data']['id'].removeprefix('solana_')
        counter=relations['quote_token']['data']['id'].removeprefix('solana_')
        if mint not in (base,counter):
            continue
        quote_mint=counter if base==mint else base
        if quote_mint not in (SOL_MINT,USDC_MINT) or quote_mint==mint:
            continue
        attrs=pool['attributes']
        liquidity=float(attrs.get('reserve_in_usd') or 0)
        volume=float(attrs.get('volume_usd',{}).get('h24') or 0)
        if not all(math.isfinite(v) for v in (liquidity,volume)) or liquidity<min_liquidity or volume<min_volume:
            continue
        address=attrs['address']
        Pubkey.from_string(address)
        eligible.append({'pool':address,'mint':mint,'quote_mint':quote_mint,
                         'token_side':'base' if base==mint else 'quote',
                         'dex':relations['dex']['data']['id'],
                         'liquidity_usd':liquidity,'volume_24h_usd':volume,
                         'pool_created_at':attrs.get('pool_created_at')})
    if not eligible:
        raise ValueError('No sufficiently liquid SOL/USDC-quoted pool discovered')
    # USD quotes minimize cross-asset conversion; then prefer deeper liquidity.
    return max(eligible,key=lambda p:(p['quote_mint']==USDC_MINT,p['liquidity_usd']))


def parse_candles(payload,mint,now):
    if payload.get('meta',{}).get('base',{}).get('address')!=mint:
        raise ValueError('Historical response token orientation mismatch')
    rows=payload['data']['attributes']['ohlcv_list']
    seen={}
    candles=[]
    for row in rows:
        if len(row)!=6 or not math.isfinite(row[0]) or row[0]!=int(row[0]) or row[0]%3600:
            raise ValueError('Invalid historical candle timestamp')
        timestamp=int(row[0])
        if timestamp in seen:
            if tuple(row)!=seen[timestamp]:
                raise ValueError('Conflicting duplicate candle')
            continue
        seen[timestamp]=tuple(row)
        if timestamp+3600<=now:
            candles.append(Candle(timestamp,*(float(v) for v in row[1:])))
    candles.sort(key=lambda b:b.timestamp)
    start=0
    for i,(a,b) in enumerate(zip(candles,candles[1:])):
        if b.timestamp-a.timestamp!=3600:
            start=i+1
    candles=candles[start:]
    validate(candles,now)
    if len(candles)<WARMUP_BARS:
        raise ValueError('Insufficient continuous completed pool history')
    return candles


def scan_one(asset,now,fetch=fetch_scan_json,request_delay=4):
    mint=asset['mint']
    screening=None
    if mint==SOL_MINT:
        bars=load_history('data/verified_pool_hourly.csv',now)
        pool={'pool':DEFAULT_POOL,'mint':mint,'quote_mint':USDC_MINT,'dex':'pumpswap'}
        verification='existing on-chain sampled SOL pool history'
    else:
        payload,_=fetch(ROOT+'/tokens/'+quote(mint,safe='')+'/pools')
        mature=asset['classification']=='mature_meme_candidate'
        needs_screen=mature or asset.get('screening_required',False)
        policy=MatureMemePolicy() if mature else MatureMemePolicy(token_observed_days=90,min_liquidity_usd=100000,min_daily_volume_usd=10000)
        pool=select_pool(payload['data'],mint,
                         policy.min_liquidity_usd if mature else 100000,
                         policy.min_daily_volume_usd if mature else 10000)
        if needs_screen:
            risk,raw_risk=fetch('https://api.rugcheck.xyz/v1/tokens/'+mint+'/report')
            screening=policy.assess(asset,pool,risk,now)
            risk_folder=Path('data/markets')/mint
            risk_folder.mkdir(parents=True,exist_ok=True)
            (risk_folder/'risk_source.json').write_bytes(raw_risk)
        if request_delay:
            time.sleep(request_delay)
        url=ROOT+'/pools/'+pool['pool']+'/ohlcv/hour?aggregate=1&limit=1000&currency=usd&token='+pool['token_side']+'&include_empty_intervals=false'
        payload,raw=fetch(url)
        bars=parse_candles(payload,mint,now)
        verification='provider pool metadata/schema checked; additional on-chain execution validation required'
        folder=Path('data/markets')/mint
        folder.mkdir(parents=True,exist_ok=True)
        (folder/'source.json').write_bytes(raw)
        (folder/'candles.json').write_text(json.dumps([asdict(c) for c in bars]))
        (folder/'provenance.json').write_text(json.dumps({'source_url':url,'sha256':hashlib.sha256(raw).hexdigest(),
            'pool':pool,'retrieved_at':now,'risk_retrieved_at':now if needs_screen else None,'verification':verification},indent=2))
    current=decide(bars,now,bars[-1].close)
    at_close=decide(bars,bars[-1].timestamp+3600,bars[-1].close)
    upper=max(b.high for b in bars[-21:-1])
    lower=min(b.low for b in bars[-21:-1])
    from venue_policy import entry_allowed
    execution_supported=asset.get('approved_for_live',False) and entry_allowed('pumpswap' if mint==SOL_MINT else 'jupiter')
    return {'symbol':asset['symbol'],'mint':mint,'classification':asset['classification'],**pool,'candles':len(bars),
        'mature_meme_screening':screening,
        'risk_screening_required':asset.get('screening_required',False) or asset['classification']=='mature_meme_candidate',
        'latest_close':bars[-1].close,'latest_candle_timestamp':bars[-1].timestamp,
        'upper_channel':upper,'lower_channel':lower,'current_signal':asdict(current),
        'signal_at_latest_close':asdict(at_close),
        'spot_execution_supported':execution_supported,
        'execution_adapter':'direct_pumpswap' if mint==SOL_MINT else 'keyless_jupiter',
        'execution_blocker':None if execution_supported else 'Venue eligibility or live approval blocks entry',
        'entry_eligible':execution_supported and (screening is None or screening['passed']),
        'execution_note':'Live portfolio router; fresh route, balances, screening and cost checks still required',
        'history_verification':verification}


def scan(path='assets.json',request_delay=4):
    assets=load_assets(path)
    report={'started_at':int(time.time()),'assets_requested':len(assets),'markets':[],'errors':[]}
    for asset in assets:
        try:
            result=scan_one(asset,int(time.time()),request_delay=request_delay)
            report['markets'].append(result)
            print(json.dumps({'scanned':asset['symbol'],'signal':result['current_signal']['reason'],
                'execution_supported':result['spot_execution_supported']}),flush=True)
        except Exception as error:
            detail={'symbol':asset['symbol'],'mint':asset['mint'],'error_type':type(error).__name__}
            if isinstance(error,ValueError):
                detail['reason']=str(error)
            if isinstance(error,urllib.error.HTTPError):
                detail['http_status']=error.code
                error.close()
            report['errors'].append(detail)
            print(json.dumps({'scanned':asset['symbol'],**detail}),flush=True)
        if request_delay:
            time.sleep(request_delay)
    report['completed_at']=int(time.time())
    target=Path('state/market_scan.json')
    target.parent.mkdir(exist_ok=True)
    temp=target.with_suffix('.tmp')
    temp.write_text(json.dumps(report,indent=2))
    os.replace(temp,target)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--assets',default='assets.json')
    parser.add_argument('--loop',action='store_true')
    args=parser.parse_args()
    os.umask(0o077)
    Path('state').mkdir(exist_ok=True)
    lock=open('state/market-scan.lock','w')
    try:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        while True:
            result=scan(args.assets)
            print(json.dumps({'scan_complete':len(result['markets']),'failed':len(result['errors'])}),flush=True)
            if not args.loop:
                break
            # Begin a new pass just after an hourly close, rather than sleeping
            # across the five-minute entry window.
            now=time.time()
            time.sleep(max(1,min(300,(int(now)//3600+1)*3600+1-now)))
    except KeyboardInterrupt:
        pass
    except Exception as error:
        print('Market scanner stopped ('+type(error).__name__+'); no trades submitted.')
        raise SystemExit(1)
