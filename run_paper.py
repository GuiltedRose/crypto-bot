"""Persistent paper loop, offline feed replay, status and archived reset.

This entry point has no wallet/key/transaction imports and never reads .env.
"""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import time
from meme_dip import DipRules
from paper_runtime import PaperSession
from paper_sources import source_error, UnsupportedMarket
from pump_market import PumpMarketSources
from pump_narratives import NarrativeStore, valid_mint


def feed_once(session, path):
    count = 0
    pending = False
    with Path(path).open() as source:
        for index, line in enumerate(source, 1):
            if not line.endswith('\n'):
                pending = True  # Wait for producer to finish writing the record.
                break
            if not line.strip():
                continue
            try:
                snapshot = json.loads(line)
                if session.process(snapshot) is not None:
                    count += 1
            except (TypeError, ValueError):
                return {'status': 'invalid_feed', 'line': index, 'processed': count}
    return {'status': 'waiting_for_complete_record' if pending else 'ok', 'processed': count}


def candidate_queue(session,position,mints,ranked,discovered):
    held=[position['mint']] if position else []
    pinned=list(dict.fromkeys(held+list(mints)+ranked[:2]))[:6]
    remaining=[mint for mint in dict.fromkeys(ranked+discovered) if mint not in pinned]
    if not remaining or len(pinned)>=6:
        return pinned
    row=session.db.execute("SELECT cursor FROM paper_source_cursors WHERE run_id=? AND source='candidates'",(session.run_id,)).fetchone()
    cursor=(row[0] if row else 0)%len(remaining)
    count=min(6-len(pinned),len(remaining))
    rotated=[remaining[(cursor+i)%len(remaining)] for i in range(count)]
    with session.db:
        session.db.execute("INSERT OR REPLACE INTO paper_source_cursors VALUES(?,'candidates',?)",(session.run_id,(cursor+count)%len(remaining)))
    return pinned+rotated


def markets_once(session, sources, mints, discover=False, narrative_store=None, watcher=None, wallets=()):
    errors, skipped, discovered, watched = {}, {}, [], []
    position = session.trader.position
    processed = 0
    watch_report = None
    queue = [position['mint']] if position else []

    def observe(mint, pool=None):
        nonlocal processed
        try:
            context=watcher.read_context(wallets) if watcher and wallets and hasattr(watcher,'read_context') else watch_report
            snapshot = sources.observation(mint, pool=pool)
            if context:
                snapshot['research_context']={'source_status':context.get('status','unknown'),
                                              'hot_tokens':[r for r in context.get('hot_tokens',[]) if r['mint']==mint],
                                              'hot_token_source':context.get('hot_token_source',{}),'wallet_signals':[group for group in context.get('signals',[]) if group['mint']==mint],
                                              'callouts':[row for row in context.get('callouts',[]) if row['mint']==mint]}
            if session.process(snapshot) is not None:
                processed += 1
        except UnsupportedMarket as error:
            skipped[mint]=source_error(error)
        except Exception as error:
            errors[mint] = source_error(error)

    # A discovery/RPC outage must not postpone the first open-position quote.
    if position:
        observe(position['mint'], position['pool'])
    if discover:
        try:
            leads = sources.discover()
            for lead in leads:
                narrative_store.ingest(lead)
            discovered = [lead['mint'] for lead in leads]
        except Exception as error:
            errors['discovery'] = source_error(error)
    watch_report = None
    if watcher and wallets:
        try:
            watch_report = watcher.poll(wallets)
            from wallet_signals import rank_candidate_mints
            watched = rank_candidate_mints(watch_report)
            if watch_report['errors'] or watch_report.get('status','bounded_sample') != 'bounded_sample':
                errors['wallet_watch'] = {'status': watch_report.get('status', 'incomplete'), 'wallets': watch_report['errors']}
        except Exception as error:
            errors['wallet_watch'] = source_error(error)
    queue = candidate_queue(session,position,mints,watched,discovered)
    for mint in queue:
        if position and mint == position['mint']:
            continue
        observe(mint)
    if errors:
        session.trader.armed={key:value for key,value in session.trader.armed.items() if key[0] not in errors}
        with session.db:
            from paper_runtime import encode_state
            session.db.execute('UPDATE paper_runs SET checkpoint=? WHERE id=?',
                               (json.dumps(encode_state(session.trader)), session.run_id))
    return {'status': 'incomplete' if errors else 'ok', 'processed': processed,
            'sources': errors, 'skipped_markets':skipped,'candidate_mints': queue, 'wallet_watch': watch_report,
            'open_position_quote_unavailable': bool(position and position['mint'] in errors)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, default=Path('state/paper_memes'))
    parser.add_argument('--total-equity',type=float,help='Total virtual wallet USD value including native SOL; subtracts its initial valuation from USDC')
    parser.add_argument('--balance', type=float, help='Starting virtual USDC; required for a new run/reset')
    parser.add_argument('--native-sol', type=float, help='Optional virtual native SOL; enables execution-cost/reserve ledger')
    parser.add_argument('--sol-price', type=float, help='Initial SOL/USD valuation when using --native-sol')
    parser.add_argument('--profile',choices=['current','moderate','aggressive','adaptive'],help='Shared entry-tolerance profile; existing runs require reset or a fresh state directory')
    parser.add_argument('--rules', type=Path)
    parser.add_argument('--exploratory',action='store_true',help='Paper-only market/chart experiment; unknown safety stays unqualified')
    parser.add_argument('--assume-usd-costs', action='store_true', help='Explicit scenario mode without a native-SOL ledger')
    parser.add_argument('--feed', type=Path, help='Append-only JSONL snapshots; supports recorded or external live collectors')
    parser.add_argument('--mint', action='append', default=[])
    parser.add_argument('--watch-wallet', action='append', default=[], help='Public trader address; candidate discovery only, at most 32')
    parser.add_argument('--wallet-source-dir', type=Path, default=Path('state/wallet_watch'))
    parser.add_argument('--watchlist', type=Path, help='JSON file containing a wallets array of public addresses')
    parser.add_argument('--watch-rpc', default='https://api.mainnet-beta.solana.com', help='Public HTTPS RPC; never used for transaction submission')
    parser.add_argument('--discover', action='store_true', help='Sample pump.fun launches; migrated PumpSwap pools only')
    parser.add_argument('--evidence-dir', type=Path, help='Supplemental identity-bound proof objects named <mint>.json')
    parser.add_argument('--loop', action='store_true')
    parser.add_argument('--interval', type=float, default=60)
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--reclaim-paper-rent',action='store_true',help='Return eligible simulated account rent to virtual SOL; never touches the real wallet')
    parser.add_argument('--reset', action='store_true', help='Archive current paper run and start fresh; never touches live state')
    args = parser.parse_args()
    if args.reset and args.reclaim_paper_rent:
        parser.error('Reset and paper rent reclaim are separate actions')
    if not 10 <= args.interval <= 3600:
        parser.error('--interval must be 10 through 3600 seconds')
    if any(not valid_mint(mint) for mint in args.mint) or len(args.mint) > 5:
        parser.error('Supply at most five valid Solana mints')
    if not args.feed and not args.watchlist and not args.watch_wallet and (args.mint or args.discover) and Path('paper_watchlist.json').exists():
        args.watchlist=Path('paper_watchlist.json')
    if args.watchlist:
        document = json.loads(args.watchlist.read_text())
        if not isinstance(document, dict) or not isinstance(document.get('wallets'), list):
            parser.error('--watchlist requires a JSON object with a wallets array')
        args.watch_wallet.extend(document['wallets'])
    if any(not valid_mint(wallet) for wallet in args.watch_wallet) or len(args.watch_wallet) > 32:
        parser.error('Watch at most 32 valid public wallet addresses')
    args.watch_wallet = list(dict.fromkeys(args.watch_wallet))
    if args.feed and (args.mint or args.discover or args.watch_wallet):
        parser.error('--feed and public-market collection are separate source modes')
    if (args.native_sol is None) != (args.sol_price is None):
        parser.error('--native-sol and --sol-price must be supplied together')
    if args.total_equity is not None:
        if args.balance is not None or args.native_sol is None or args.sol_price is None:
            parser.error('--total-equity requires --native-sol/--sol-price and cannot be combined with --balance')
        args.balance=args.total_equity-args.native_sol*args.sol_price
        if args.balance<0:
            parser.error('Total equity cannot be below native SOL valuation')
    if args.reset and args.balance is None:
        parser.error('--reset requires --balance')
    rules = DipRules(**json.loads(args.rules.read_text())) if args.rules else None
    if args.profile:
        from meme_profiles import selected
        rules=selected(rules or DipRules(entry_style='trend_pullback'),args.profile)
    if args.exploratory:
        rules=replace(rules or DipRules(),evidence_mode='market_only')
    exists = (args.state_dir/'paper.sqlite').exists()
    if (not exists or args.reset) and args.native_sol is None and not args.assume_usd_costs:
        parser.error('Supply --native-sol and --sol-price to match inventory, or explicitly choose --assume-usd-costs')
    session = PaperSession(args.state_dir, None if args.reset and exists else args.balance,
                           None if args.reset and exists else rules,
                           None if args.reset and exists else args.native_sol,
                           None if args.reset and exists else args.sol_price)
    narratives = None
    watcher = None
    try:
        if args.reset and exists:
            previous = session.reset(args.balance, rules, args.native_sol, args.sol_price)
            print(json.dumps({'mode': 'paper_only', 'archived_run': previous, 'new_run': session.run_id}))
        if args.reclaim_paper_rent:
            event=session.reclaim_rent()
            session.publish({'status':'paper_rent_reclaimed'})
            print(json.dumps(event))
            return
        if args.reset or args.status or not (args.feed or args.mint or args.discover or args.watch_wallet):
            report = session.publish({'status': 'idle'})
            print(json.dumps(report, indent=2))
            return
        session.publish({'status':'starting_sources'})
        if session.trader.rules.evidence_mode=='market_only':
            from pump_market import ModeledPumpSources
            sources=ModeledPumpSources(args.evidence_dir)
        else:
            sources=PumpMarketSources(args.evidence_dir)
        if args.watch_wallet:
            from wallet_signals import SharedWalletFeed, ensure_worker, digest
            watchlist = args.watchlist
            if not watchlist or args.watch_wallet != list(dict.fromkeys(json.loads(watchlist.read_text())['wallets'])):
                args.wallet_source_dir.mkdir(parents=True,exist_ok=True)
                watchlist = args.wallet_source_dir/('watchlist-'+digest(args.watch_wallet)[:12]+'.json')
                watchlist.write_text(json.dumps({'wallets':args.watch_wallet}))
            try:
                ensure_worker(watchlist,args.wallet_source_dir,args.watch_rpc)
            except (OSError,ValueError,KeyError,TypeError):
                pass
            watcher = SharedWalletFeed(args.wallet_source_dir)
        if args.discover:
            narratives = NarrativeStore(args.state_dir/'narratives.sqlite')
        while True:
            try:
                status = feed_once(session, args.feed) if args.feed else markets_once(session, sources, args.mint, args.discover, narratives, watcher, args.watch_wallet)
            except (OSError, ValueError) as error:
                status = source_error(error)
            report = session.publish(status)
            print(json.dumps({'mode': 'paper_only', 'run_id': session.run_id, 'source_status': status,
                              'equity_usd': report['marked_equity_usd'], 'events': report['event_counts']}), flush=True)
            if not args.loop:
                break
            # Short sleeps permit prompt interruption even with a long polling interval.
            deadline = time.monotonic()+args.interval
            while time.monotonic() < deadline:
                time.sleep(max(0, min(1, deadline-time.monotonic())))
    except KeyboardInterrupt:
        session.publish({'status': 'stopped; paper position preserved'})
    finally:
        if narratives:
            narratives.db.close()
        session.close()


if __name__ == '__main__':
    main()
