"""One credential-free wallet collector; shared read-only context for paper/live.

Watchlist activity can prioritize already approved assets. It never grants live
approval, supplies independent-buyer counts, changes risk or creates an order.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sqlite3
import sys
import time
from pump_narratives import valid_mint

DEFAULT_DIRECTORY = Path('state/wallet_watch')
DEFAULT_WATCHLIST = Path('paper_watchlist.json')  # Historical filename, used by both modes.


def load_watchlist(path):
    document = json.loads(Path(path).read_text())
    wallets = document['wallets']
    if not isinstance(wallets, list) or not 1 <= len(wallets) <= 32 or any(not valid_mint(w) for w in wallets):
        raise ValueError('Watchlist requires one through 32 public addresses')
    return list(dict.fromkeys(wallets))


def digest(wallets):
    return hashlib.sha256(json.dumps(sorted(set(wallets))).encode()).hexdigest()


def unavailable(reason):
    return {'status': reason, 'candidate_mints': [], 'signals': [], 'errors': {},
            'automatic_trade_approval': False, 'independent_human_verified': False}


def read_signals(wallets, directory=DEFAULT_DIRECTORY, now=None):
    """Reject old reports and observations; never renew receipt timestamps."""
    now = time.time() if now is None else now
    try:
        report = json.loads((Path(directory)/'status.json').read_text())
        if report['schema'] != 1 or report['watchlist_digest'] != digest(wallets):
            return unavailable('watchlist_or_schema_mismatch')
        stamp = report['published_at']
        if isinstance(stamp, bool) or not isinstance(stamp, (int, float)) or not 0 <= now-stamp <= 120:
            return unavailable('stale_wallet_source')
        checks = report['wallet_checks']
        signals = []
        seen = set()
        for group in report['signals']:
            mint = group['mint']
            if not valid_mint(mint) or mint in seen:
                continue
            seen.add(mint)
            observations = {}
            for observation in group['observations']:
                wallet = observation['wallet']
                checked = checks.get(wallet, {})
                stamp = observation['observed_at']
                checked_at = checked.get('checked_at')
                if (wallet not in wallets or observation['mint'] != mint or observation['action'] != 'buy'
                    or checked.get('status') != 'bounded_sample'
                    or type(checked_at) not in (int, float) or not 0 <= now-checked_at <= 300
                    or type(stamp) not in (int, float) or not 0 <= now-stamp <= 300):
                    continue
                observations[wallet] = observation
            if observations:
                signals.append({'mint': mint, 'watched_buyers': len(observations),
                                'observations': list(observations.values()), 'independent_human_verified': False})
        from pump_callouts import eligible_callouts
        callouts=eligible_callouts(report.get('callouts',[]),now,wallets)
        candidates={g['mint'] for g in signals}|{row['mint'] for row in callouts}
        return dict(report, signals=signals, callouts=callouts, candidate_mints=sorted(candidates),
                    automatic_trade_approval=False, independent_human_verified=False)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError):
        return unavailable('wallet_source_unavailable')


def rank_candidate_mints(report):
    """Research priority, with views separate from confirmed buy observations."""
    buys={group['mint']:group['watched_buyers'] for group in report.get('signals',[])}
    authors,watched,views={},{},{}
    for row in report.get('callouts',[]):
        mint=row['mint']
        authors.setdefault(mint,set()).add(row['author_wallet'])
        if row.get('watched_author'):
            watched.setdefault(mint,set()).add(row['author_wallet'])
        views[mint]=views.get(mint,0)+(row.get('views') or 0)
    return sorted(report.get('candidate_mints',[]),key=lambda mint:(-buys.get(mint,0),
                  -len(watched.get(mint,())),-len(authors.get(mint,())),-views.get(mint,0),mint))


def prioritize_assets(assets, report):
    """Stable research priority only: preserve every approval and safety field."""
    counts = {group['mint']: group['watched_buyers'] for group in report.get('signals', [])}
    authors,watched={},{}
    for row in report.get('callouts',[]):
        authors.setdefault(row['mint'],set()).add(row['author_wallet'])
        if row.get('watched_author'):
            watched.setdefault(row['mint'],set()).add(row['author_wallet'])
    return sorted(assets,key=lambda asset:(-counts.get(asset['mint'],0),-len(watched.get(asset['mint'],())),
                                           -len(authors.get(asset['mint'],()))))


def live_context(report, assets):
    registered = {asset['mint'] for asset in assets}
    candidates = set(report.get('candidate_mints', []))
    return {'status': report.get('status', 'unavailable'), 'published_at': report.get('published_at'),
            'registered_candidates': sorted(candidates & registered),
            'unreviewed_candidates': sorted(candidates-registered),
            'signals': report.get('signals', []), 'callouts':report.get('callouts',[]),
            'callout_source':report.get('callout_source'), 'errors': report.get('errors', {}),
            'automatic_trade_approval': False,
            'use': 'research and priority among registry assets; all normal entry checks remain required'}


def ensure_worker(watchlist=DEFAULT_WATCHLIST, directory=DEFAULT_DIRECTORY, rpc='https://api.mainnet-beta.solana.com'):
    """Nonblocking startup; the child holds the single-writer lock for its life."""
    load_watchlist(watchlist)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory/'collector.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
    environment = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'LANG': 'C.UTF-8', 'PYTHONUNBUFFERED': '1'}
    with (directory/'collector.log').open('a') as log:
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--watchlist', str(Path(watchlist).resolve()),
                          '--state-dir', str(directory.resolve()), '--rpc', rpc, '--loop'],
                         stdout=log, stderr=subprocess.STDOUT, env=environment, start_new_session=True, close_fds=True)
    return True


class SharedWalletFeed:
    """Adapter with the paper watcher's interface, performing no network calls."""
    def __init__(self, directory=DEFAULT_DIRECTORY):
        self.directory = directory

    def poll(self, wallets):
        return read_signals(wallets, self.directory)

    def read_context(self,wallets):
        return read_signals(wallets,self.directory)


def collect_once(watcher, wallets, directory, now=None, callout_source=None):
    from paper_sources import source_error
    try:
        report = watcher.poll(wallets, now=now)
    except Exception as error:
        report = dict(unavailable('incomplete'), wallet_checks={}, errors={'collector': source_error(error)})
    if callout_source is not None:
        try:
            social=callout_source.poll(wallets,now=now)
            report['callouts']=social.pop('callouts')
            report['callout_source']=social
        except Exception as error:
            report['callouts']=[]
            report['callout_source']=source_error(error)
            report.setdefault('errors',{})['callouts']=source_error(error)
    report.update(schema=1, watchlist_digest=digest(wallets), published_at=time.time() if now is None else now)
    directory = Path(directory)
    temporary = directory/'status.tmp'
    temporary.write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
    temporary.replace(directory/'status.json')
    return report


def migrate_legacy_cache(directory):
    """Preserve prior paper observations when adopting the default shared cache."""
    directory = Path(directory)
    legacy = Path('state/paper_memes/wallet_watch.sqlite')
    destination = directory/'wallet_watch.sqlite'
    if directory.resolve() != DEFAULT_DIRECTORY.resolve() or destination.exists() or not legacy.exists():
        return
    source = sqlite3.connect(legacy.resolve().as_uri()+'?mode=ro', uri=True)
    target = sqlite3.connect(destination)
    try:
        source.backup(target)
    finally:
        source.close()
        target.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--watchlist', type=Path, default=DEFAULT_WATCHLIST)
    parser.add_argument('--state-dir', type=Path, default=DEFAULT_DIRECTORY)
    parser.add_argument('--rpc', default='https://api.mainnet-beta.solana.com')
    parser.add_argument('--loop', action='store_true')
    args = parser.parse_args()
    args.state_dir.mkdir(parents=True, exist_ok=True)
    with (args.state_dir/'collector.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        migrate_legacy_cache(args.state_dir)
        from wallet_watch import WalletWatcher, PublicRpc
        watcher = WalletWatcher(args.state_dir/'wallet_watch.sqlite', PublicRpc(args.rpc))
        from pump_callouts import HotCallouts
        social=HotCallouts(args.state_dir/'callouts.sqlite')
        try:
            while True:
                wallets = load_watchlist(args.watchlist)
                report = collect_once(watcher, wallets, args.state_dir,callout_source=social)
                print(json.dumps({'status': report['status'], 'candidate_mints': report['candidate_mints']}), flush=True)
                if not args.loop:
                    return 0
                time.sleep(60)
        finally:
            watcher.db.close()
            social.db.close()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        pass
