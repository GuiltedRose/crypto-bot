"""Persistent paper loop, offline feed replay, status and archived reset.

This entry point has no wallet/key/transaction imports and never reads .env.
"""
import argparse
from dataclasses import replace
import json
import sqlite3
from pathlib import Path
import time
from meme_dip import DipRules
from paper_runtime import PaperSession
from paper_console import format_report
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


def candidate_queue(session,position,mints,ranked,discovered,limit=6):
    held=[position['mint']] if position else []
    pinned=list(dict.fromkeys(held+list(mints)+ranked[:2]))[:limit]
    remaining=[mint for mint in dict.fromkeys(ranked+discovered) if mint not in pinned]
    if not remaining or len(pinned)>=limit:
        return pinned
    row=session.db.execute("SELECT cursor FROM paper_source_cursors WHERE run_id=? AND source='candidates'",(session.run_id,)).fetchone()
    cursor=(row[0] if row else 0)%len(remaining)
    count=min(limit-len(pinned),len(remaining))
    rotated=[remaining[(cursor+i)%len(remaining)] for i in range(count)]
    with session.db:
        session.db.execute("INSERT OR REPLACE INTO paper_source_cursors VALUES(?,'candidates',?)",(session.run_id,(cursor+count)%len(remaining)))
    return pinned+rotated


def markets_once(session, sources, mints, discover=False, narrative_store=None, watcher=None, wallets=()):
    errors, skipped, discovered, watched = {}, {}, [], []
    position = session.trader.position
    held_positions=session.trader.all_positions()
    held_mints={p['mint'] for p in held_positions}
    processed = 0
    watch_report = None
    retained=None
    if session.trader.rules.entry_style=='volume_candles':
        from meme_watchlist import CandidateWatchlist
        retained=CandidateWatchlist(session.db)
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
            event=session.process(snapshot)
            if event is not None:
                processed += 1
                if retained and event['action'] in ('reject','watch','paper_buy'):
                    status={'reject':'entry_rejected','watch':'watch','paper_buy':'confirmed_buy'}[event['action']]
                    retained.observe(snapshot,dict(event,status=status),session.trader.rules)
        except UnsupportedMarket as error:
            if retained: retained.remove(mint)
            skipped[mint]=source_error(error)
        except Exception as error:
            if retained: retained.failed(mint)
            errors[mint] = source_error(error)

    # A discovery/RPC outage must not postpone the first open-position quote.
    for held in held_positions:
        observe(held['mint'],held['pool'])
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
    queue = candidate_queue(session,position,list(held_mints)+list(mints),watched,discovered,10 if session.trader.rules.entry_style=='volume_candles' else 6)
    if retained:
        queue=list(dict.fromkeys(([position['mint']] if position else [])+retained.due(10)+queue))[:10]
    for mint in queue:
        if mint in held_mints:
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
            'open_position_quote_unavailable': any(mint in errors for mint in held_mints)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, default=Path('state/paper_memes'))
    parser.add_argument('--total-equity',type=float,help='Total virtual wallet USD value including native SOL; subtracts its initial valuation from USDC')
    parser.add_argument('--balance', type=float, help='Starting virtual USDC; required for a new run/reset')
    parser.add_argument('--native-sol', type=float, help='Optional virtual native SOL; enables execution-cost/reserve ledger')
    parser.add_argument('--sol-price', type=float, help='Initial SOL/USD valuation when using --native-sol')
    parser.add_argument('--max-positions',type=int,default=4,help='Concurrent paper positions sharing one bankroll (1 through 8; default 4)')
    parser.add_argument('--neural-mode',choices=['learning','shadow'],default='learning',help='Online neural veto with one-quarter exploration, or shadow-only scoring')
    parser.add_argument('--profile',choices=['current','moderate','aggressive','adaptive'],help='Shared entry-tolerance profile; existing runs require reset or a fresh state directory')
    parser.add_argument('--adopt-rules',action='store_true',help='Explicitly adopt profile-mode/drawdown-schedule changes under identical other rules; preserves balances, positions, losses and existing halts')
    parser.add_argument('--rules', type=Path)
    parser.add_argument('--exploratory',action='store_true',help='Paper-only market/chart experiment; unknown safety stays unqualified')
    parser.add_argument('--assume-usd-costs', action='store_true', help='Explicit scenario mode without a native-SOL ledger')
    parser.add_argument('--onchain-db', type=Path, help='Opt-in local finalized index; modeled paper only')
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
    parser.add_argument('--json',action='store_true',help='Print full JSON reports instead of compact summaries')
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--reclaim-paper-rent',action='store_true',help='Return eligible simulated account rent to virtual SOL; never touches the real wallet')
    parser.add_argument('--loss-cooldown-seconds',type=int,default=0,help='Paper-only pause after consecutive losses (default 0; use 3600 to match live)')
    parser.add_argument('--no-auto-reset',action='store_true',help='Keep exhausted/drawdown-halted virtual runs stopped instead of archiving and refilling')
    parser.add_argument('--learning-live-db',type=Path,default=Path('state/meme_live/meme_live.sqlite'),help='Read-only confirmed bot outcomes for shared neural training')
    parser.add_argument('--reset', action='store_true', help='Archive current paper run and start fresh; never touches live state')
    args = parser.parse_args()
    if not 1<=args.max_positions<=8: parser.error('Paper positions must be 1 through 8')
    if not 0<=args.loss_cooldown_seconds<=86400: parser.error('Paper cooldown must be 0 through 86400 seconds')
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
    if args.adopt_rules and (not exists or args.reset or rules is None): parser.error('--adopt-rules requires an existing run and explicit rules without reset')
    session = PaperSession(args.state_dir, None if args.reset and exists else args.balance,
                           None if args.reset and exists or args.adopt_rules else rules,
                           None if args.reset and exists else args.native_sol,
                           None if args.reset and exists else args.sol_price)
    narratives = None
    watcher = None
    try:
        if args.adopt_rules: session.adopt_entry_mode(rules)
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
            print(json.dumps(report, indent=2) if args.json else format_report(report))
            return
        session.publish({'status':'starting_sources'})
        if args.onchain_db:
            if session.trader.rules.evidence_mode!='market_only':
                parser.error('--onchain-db requires existing market_only paper rules; strict safety proofs are not supplied')
            from onchain_market import OnchainPaperSources
            sources=OnchainPaperSources(args.onchain_db,args.evidence_dir)
        elif session.trader.rules.evidence_mode=='market_only':
            from pump_market import ModeledPumpSources
            sources=ModeledPumpSources(args.evidence_dir,include_legacy_metrics=session.trader.rules.entry_style!='volume_candles')
        else:
            sources=PumpMarketSources(args.evidence_dir,include_legacy_metrics=session.trader.rules.entry_style!='volume_candles')
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
        try:
            session.refresh_learning(args.learning_live_db,args.wallet_source_dir/'wallet_watch.sqlite',args.watch_wallet)
        except (OSError,ValueError,sqlite3.Error):
            pass
        event_cursor=session.db.execute('SELECT COALESCE(MAX(sequence),0) FROM paper_events').fetchone()[0]
        while True:
            reset_event=session.auto_reset_if_exhausted() if not args.no_auto_reset and not args.feed else None
            session.configure_loss_cooldown(args.loss_cooldown_seconds)
            session.configure_positions(args.max_positions)
            session.neural_mode=args.neural_mode
            try:
                status = feed_once(session, args.feed) if args.feed else markets_once(session, sources, args.mint, args.discover, narratives, watcher, args.watch_wallet)
            except (OSError, ValueError) as error:
                status = source_error(error)
            try:
                session.refresh_learning(args.learning_live_db,args.wallet_source_dir/'wallet_watch.sqlite',args.watch_wallet)
            except (OSError,ValueError,sqlite3.Error) as error:
                status=dict(status,learning_error=type(error).__name__)
            if reset_event: status=dict(status,balance_reset=reset_event)
            report = session.publish(status)
            rows=session.db.execute('SELECT sequence,event FROM paper_events WHERE sequence>? ORDER BY sequence',(event_cursor,)).fetchall()
            events=[json.loads(raw) for _,raw in rows]
            if rows:event_cursor=rows[-1][0]
            print(json.dumps(report) if args.json else format_report(report,events),flush=True)
            if not args.loop:
                break
            # Short sleeps permit prompt interruption even with a long polling interval.
            rest=args.interval
            if session.trader.position and session.trader.rules.entry_style=='volume_candles':
                from meme_profiles import response_rest
                rest=min(rest,response_rest(session.trader.position.get('entry_profile'),holding=True))
            deadline = time.monotonic()+rest
            while time.monotonic() < deadline:
                time.sleep(max(0, min(1, deadline-time.monotonic())))
    except KeyboardInterrupt:
        session.publish({'status': 'stopped; paper position preserved'})
        if not args.json:print('Stopped. Paper positions and training data saved.',flush=True)
    finally:
        if narratives:
            narratives.db.close()
        session.close()


if __name__ == '__main__':
    main()
