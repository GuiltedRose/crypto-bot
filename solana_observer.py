"""Pump observation entry point, extended from the original async subscription.

UNTESTED integration. Notifications are provisional; existing finalized indexing
supplies execution metadata, canonical order, recovery and candle completeness.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
from websockets.asyncio.client import connect
from onchain_candles import CandleEngine
from onchain_ingest import IndexRpc, Ingester
from onchain_store import EventStore
from onchain_stream import run_stream
from run_local_observer import open_isolated

SOLANA_WS = "wss://api.mainnet-beta.solana.com"
PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMPSWAP_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"


def prepare(config, database, resume=False):
    """Validate configuration before opening sockets or changing a database."""
    endpoints=[row for row in config.get('endpoints',[]) if row.get('enabled') is True]
    if not endpoints:
        raise ValueError('Explicit reviewed endpoint required; no default network connection')
    for row in endpoints:
        if row.get('source_kind','solana_rpc') not in ('solana_rpc','local_rpc'):
            raise ValueError('Only reviewed JSON-RPC sources are implemented')
    endpoint=endpoints[0]
    for key,default,maximum in [('pending_capacity',4096,100000),('observation_limit',100000,1000000)]:
        value=endpoint.get(key,default)
        if type(value) is not int or not 1<=value<=maximum:
            raise ValueError('Invalid observer storage budget')
    policy=endpoint.get('policy',{})
    if policy.get('storage_verified') is not True or policy.get('stream_verified') is not True or not policy.get('evidence'):
        raise ValueError('Reviewed storage and stream authorization required')
    from onchain_stream import validate_ws_endpoint
    validate_ws_endpoint(endpoint)
    mentions=endpoint.get('stream_mentions') or [PUMP_PROGRAM,PUMPSWAP_PROGRAM]
    from onchain_stream import SubscriptionSession
    SubscriptionSession(mentions)
    markets=config.get('markets',[])
    if not isinstance(markets,list) or not 1<=len(markets)<=32:
        raise ValueError('Configure one to 32 explicit mint/pool candle markets')
    from solders.pubkey import Pubkey
    for market in markets:
        Pubkey.from_string(market['mint']);Pubkey.from_string(market['pool'])
    # An unapproved fallback cannot sneak in through a second configured endpoint.
    for row in endpoints:
        if row.get('policy',{}).get('storage_verified') is not True:
            raise ValueError('Every recovery endpoint needs reviewed storage rights')
    clients=[IndexRpc(row) for row in endpoints]
    store=open_isolated(database,resume)
    for row in endpoints:store.register_source(row['source_id'],row['policy'])
    candles=CandleEngine(store)
    for market in markets:candles.watch(market['mint'],market['pool'])
    return Ingester(store,clients,config.get('reviewed_program_versions')),endpoint,candles


async def observe(config, database, start_slot=None, resume=False, batch=8, interval=5, max_cycles=None):
    if not 1<=batch<=64 or not 1<=interval<=3600 or max_cycles is not None and not 1<=max_cycles<=10000:
        raise ValueError('Invalid bounded observer settings')
    if not Path(database).exists() and (type(start_slot) is not int or start_slot<0):
        raise ValueError('Explicit initial finalized slot required')
    ingester,endpoint,candles=prepare(config,database,resume)
    start=None if ingester.store.next_slot() is not None else start_slot
    if start is None and ingester.store.next_slot() is None:
        raise ValueError('Explicit initial finalized slot required; no implicit history skipping')
    # Keep the user's async connect/observe integration, with shared validation,
    # acknowledgments and recovery rather than a second competing WS client.
    await run_stream(ingester,endpoint,start,candles,batch,interval,
                     connector=connect,max_cycles=max_cycles)


def status(database):
    store=EventStore(database,readonly=True)
    metrics=store.metrics()
    with store.connect() as db:
        tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        report={'notifications':{},'pending_hints':None,'status':'stream schema not initialized or legacy'}
        if {'stream_counts','stream_hints','stream_state','stream_observations'}.issubset(tables):
            from onchain_stream import StreamHints
            # The status path must never create tables or migrate the database.
            report=StreamHints.read_status(store)
    watermark=store.watermark()
    coverage=store.coverage(watermark-3600,watermark) if watermark is not None else {'complete':False,'reason':'no finalized window'}
    return {'mode':'read_only_observer_status','implementation_validation':'UNTESTED',
            'index':metrics,'stream':report,'progress':store.progress(),'last_hour':coverage,
            'live_execution_enabled':False,'independent_feed_proven':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path)
    parser.add_argument('--db',type=Path,required=True)
    parser.add_argument('--start-slot',type=int)
    parser.add_argument('--resume',action='store_true')
    parser.add_argument('--status',action='store_true')
    parser.add_argument('--batch-slots',type=int,default=8)
    parser.add_argument('--interval',type=float,default=5)
    parser.add_argument('--max-cycles',type=int,help='Bounded operator shadow validation; still requires explicit source rights')
    args=parser.parse_args()
    os.umask(0o077)
    if args.status:
        print(json.dumps(status(args.db)));return
    if args.config is None:parser.error('--config is required to connect')
    config=json.loads(args.config.read_text())
    try:
        asyncio.run(observe(config,args.db,args.start_slot,args.resume,args.batch_slots,args.interval,args.max_cycles))
    except KeyboardInterrupt:
        print('Stopped. Observer checkpoint retained; no trades submitted.')
    except Exception as error:
        # Endpoint paths/query strings can contain credentials; no raw traceback.
        raise SystemExit('Observer stopped ('+type(error).__name__+'); inspect isolated status/config locally') from None


if __name__ == '__main__':
    main()
