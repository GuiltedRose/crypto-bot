"""Compare three paper portfolios using one recorded/live observation stream.

No .env, wallet loading, signing or transaction submission. Same shared inputs,
starting inventory, fees, slippage, reserves and exits for all three profiles.
"""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import time
from meme_dip import DipRules
from paper_compare import Comparison,profiles
from pump_market import ModeledPumpSources
from pump_narratives import ROOT,valid_mint
from paper_sources import source_error,UnsupportedMarket
from wallet_signals import SharedWalletFeed,load_watchlist,ensure_worker,rank_candidate_mints
from meme_evidence import number


def market_cycle(comparison,sources,feed,wallets,mints=(),progress=None):
    errors={};skipped={};processed=0;seen=set()
    def observe(mint):
        nonlocal processed
        if mint in seen: return
        seen.add(mint)
        if progress: progress(mint)
        try:
            row=sources.observation(mint)
            context=feed.read_context(wallets)
            row['research_context']={'source_status':context.get('status','unknown'),
                'callouts':[c for c in context.get('callouts',[]) if c['mint']==mint],
                'wallet_signals':[g for g in context.get('signals',[]) if g['mint']==mint]}
            if comparison.process(row) is not None:
                processed+=1
                comparison.publish({'status':'collecting_shared_data','last_mint':mint,'observations_added_this_cycle':processed})
        except UnsupportedMarket: skipped[mint]='unsupported market'
        except Exception as error: errors[mint]=source_error(error)
    # Exit quotes for the UNION of held mints precede discovery and entries.
    held=list(dict.fromkeys(s.trader.position['mint'] for s in comparison.sessions.values() if s.trader.position))
    for mint in held: observe(mint)
    context=feed.poll(wallets);ranked=rank_candidate_mints(context)
    discovered=[]
    try: discovered=[r['mint'] for r in sources.discover()]
    except Exception as error: errors['discovery']=source_error(error)
    pinned=list(dict.fromkeys(list(mints)+ranked[:2]))[:10]
    remaining=[m for m in dict.fromkeys(ranked+discovered) if m not in pinned and m not in seen]
    row=comparison.db.execute("SELECT value FROM metadata WHERE key='cursor'").fetchone()
    cursor=json.loads(row[0]) if row else 0
    count=min(10-len(pinned),len(remaining))
    queue=pinned+([remaining[(cursor+i)%len(remaining)] for i in range(count)] if remaining else [])
    with comparison.db:
        comparison.db.execute("INSERT OR REPLACE INTO metadata VALUES('cursor',?)",(json.dumps((cursor+count)%len(remaining) if remaining else 0),))
    for mint in queue: observe(mint)
    return {'status':'incomplete' if errors else 'ok','observations_added':processed,'sources':errors,'skipped':skipped,
            'candidate_mints':queue,'held_mints':held,'wallet_source_status':context.get('status','unknown')}


def replay(comparison,feed=None,database=None,run_id=None):
    count=0
    if feed:
        with Path(feed).open() as source:
            for index,line in enumerate(source,1):
                if not line.endswith('\n'): return {'status':'waiting_for_complete_record','processed':count,'line':index}
                if line.strip() and comparison.process(json.loads(line)) is not None: count+=1
    else:
        db=sqlite3.connect('file:'+str(Path(database).resolve())+'?mode=ro',uri=True)
        try:
            if run_id is None:
                row=db.execute('SELECT id FROM paper_runs WHERE archived IS NULL').fetchone()
                if not row: raise ValueError('Select an existing recorded run ID')
                run_id=row[0]
            if not db.execute('SELECT 1 FROM paper_runs WHERE id=?',(run_id,)).fetchone(): raise ValueError('Recorded run ID does not exist')
            for raw, in db.execute('SELECT snapshot FROM paper_events WHERE run_id=? ORDER BY sequence',(run_id,)):
                row=json.loads(raw)
                if not isinstance(row,dict) or 'mint' not in row: continue
                if comparison.process(row) is not None: count+=1
        finally: db.close()
    return {'status':'replay_complete','processed':count,'source_run_id':run_id}


def display(report):
    lines=['PAIRED PAPER COMPARISON','─'*70,'Profile       Entries  Closed   Net realized   Max drawdown   Slip model']
    for name,r in report['profiles'].items():
        lines.append(f"{name:<14}{r['entries']:>7}{r['closed_trades']:>8}{r['realized_pnl_usd']:>+15.4f}{r['max_marked_drawdown_usd']:>15.4f}{r['modeled_slippage_usd']:>13.4f}")
    lines.append(f"Shared observations: {report['shared_observations']} | Paper only; no automatic live promotion")
    return '\n'.join(lines)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir',type=Path,default=Path('state/paper_comparison'))
    parser.add_argument('--rules',type=Path,default=Path('meme_live_rules.json'))
    parser.add_argument('--profiles',type=Path,default=Path('paper_profiles.json'))
    parser.add_argument('--bankroll-usd',type=float,help='NEW experiment: this total virtual USD value, entirely SOL; not a real wallet balance')
    parser.add_argument('--balance',type=float,help='Alternative starting virtual USDC; combine with native SOL/valuation for actual inventory proportions')
    parser.add_argument('--native-sol',type=float)
    parser.add_argument('--sol-price',type=float)
    group=parser.add_mutually_exclusive_group()
    group.add_argument('--feed',type=Path,help='Replay completed JSONL observations identically across profiles')
    group.add_argument('--replay-db',type=Path,help='Replay a recorded paper.sqlite run; never overwrites it')
    parser.add_argument('--run-id')
    parser.add_argument('--watchlist',type=Path,default=Path('paper_watchlist.json'))
    parser.add_argument('--wallet-source-dir',type=Path,default=Path('state/wallet_watch'))
    parser.add_argument('--mint',action='append',default=[])
    parser.add_argument('--loop',action='store_true')
    parser.add_argument('--interval',type=float,default=30)
    parser.add_argument('--status',action='store_true')
    parser.add_argument('--no-selector',action='store_true',help='Compare only the three fixed profiles; otherwise also test the adaptive per-coin selector')
    parser.add_argument('--json',action='store_true')
    args=parser.parse_args()
    if not 10<=args.interval<=3600: parser.error('Interval must be 10 through 3600 seconds')
    if any(not valid_mint(m) for m in args.mint) or len(args.mint)>5: parser.error('At most five valid explicit mints')
    if args.run_id and not args.replay_db: parser.error('--run-id requires --replay-db')
    if args.bankroll_usd is not None and (args.balance is not None or args.native_sol is not None): parser.error('Use total all-SOL bankroll OR explicit balances')
    if args.status:
        report_path=args.state_dir/'report.json'
        if not report_path.exists(): parser.error('No saved comparison report yet')
        report=json.loads(report_path.read_text())
        print(json.dumps(report,indent=2) if args.json else display(report))
        return
    exists=(args.state_dir/'manifest.json').exists()
    sources=ModeledPumpSources()
    native,price,balance=args.native_sol,args.sol_price,args.balance
    if args.bankroll_usd is not None:
        number(args.bankroll_usd,0.01)
        if price is None and not exists:
            if args.feed or args.replay_db: parser.error('Historical replay needs explicit --sol-price for the START of that period')
            quote=sources.fetch(ROOT+'/sol-price')
            if quote.get('stale') is not False or not 0<=time.time()-number(quote['asOfTimestamp'])/1000<=120: parser.error('Fresh SOL valuation unavailable')
            price=number(quote['solPrice'],1e-15)
        if exists and price is None:
            initial=json.loads((args.state_dir/'manifest.json').read_text())['initial']
            price=initial['sol_price']
        native=args.bankroll_usd/number(price,1e-15);balance=0
    if not exists and (balance is None or native is None or price is None): parser.error('New experiment requires --bankroll-usd or explicit --balance/--native-sol/--sol-price')
    if (native is None)!=(price is None): parser.error('--native-sol and --sol-price must be supplied together')
    variants=None if args.status and exists else profiles(DipRules(**json.loads(args.rules.read_text())),args.profiles)
    if variants is not None and not args.no_selector:
        base=DipRules(**json.loads(args.rules.read_text()))
        variants['adaptive']=replace(base,entry_profile_mode='adaptive')
    comparison=Comparison(args.state_dir,variants,balance,native,price)
    try:
        if args.status:
            report=comparison.publish({'status':'status_only'});print(json.dumps(report,indent=2) if args.json else display(report));return
        wallets=load_watchlist(args.watchlist);feed=SharedWalletFeed(args.wallet_source_dir)
        if not args.feed and not args.replay_db:
            try: ensure_worker(args.watchlist,args.wallet_source_dir)
            except (OSError,ValueError): pass
        comparison.publish({'status':'starting_shared_sources'})
        print(json.dumps({'mode':'paired_paper_only','status':'started','profiles':list(comparison.sessions)}) if args.json else 'Paper comparison started: '+', '.join(comparison.sessions)+'. Shared data; no real trades.',flush=True)
        progress=None if args.json else lambda mint: print('Collecting once for all profiles: '+mint,flush=True)
        while True:
            status=replay(comparison,args.feed,args.replay_db,args.run_id) if args.feed or args.replay_db else market_cycle(comparison,sources,feed,wallets,args.mint,progress)
            report=comparison.publish(status);print(json.dumps(report) if args.json else display(report),flush=True)
            if not args.loop: break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        comparison.publish({'status':'stopped; all paper positions preserved'})
    finally: comparison.close()


if __name__=='__main__': main()
