"""Shared cross-process provider pacing; never retries a transaction submission."""
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlparse
from email.utils import parsedate_to_datetime
from data_policy import require_permitted_url,SourceAccessBlocked


class ProviderBackoff(ValueError):
    def __init__(self,seconds):
        self.seconds=seconds
        super().__init__('Provider cooldown active')


class ProviderBudget:
    def __init__(self,path=None,clock=None,sleep=None):
        self.path=Path(path or Path(__file__).parent/'state/provider_limits.sqlite')
        self.clock=clock or time.time;self.sleep=sleep or time.sleep

    def key(self,url):
        host=(urlparse(url).hostname or '').lower()
        if not host:raise ValueError('Provider hostname required')
        return 'solana-public-rpc' if host in ('api.mainnet-beta.solana.com','api.mainnet.solana.com') else host

    def connect(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        db=sqlite3.connect(self.path,timeout=30)
        db.execute('CREATE TABLE IF NOT EXISTS providers(host TEXT PRIMARY KEY,next_at REAL,blocked REAL,attempts INTEGER,denied INTEGER)')
        return db

    def reserve(self,url,interval=0):
        require_permitted_url(url)
        host=self.key(url);minimum=6. if host=='api.geckoterminal.com' else .5
        db=self.connect()
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                row=db.execute('SELECT next_at,blocked,attempts,denied FROM providers WHERE host=?',(host,)).fetchone() or (0,0,0,0)
                now=self.clock()
                if row[3]:raise SourceAccessBlocked('Provider access denied; manual authorization review required')
                if row[1]>now:raise ProviderBackoff(row[1]-now)
                at=max(now,row[0])
                db.execute('INSERT OR REPLACE INTO providers VALUES(?,?,?,?,?)',(host,at+max(minimum,interval),row[1],row[2],0))
        finally:db.close()
        if at>now:self.sleep(at-now)

    def failure(self,url,status,headers=None):
        if status not in (401,403,429,500,502,503,504):return
        db=self.connect();host=self.key(url)
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                row=db.execute('SELECT next_at,blocked,attempts,denied FROM providers WHERE host=?',(host,)).fetchone() or (0,0,0,0)
                attempts=row[2]+1;wait=min(600,2**min(attempts,9))
                if status==429:wait=max(wait,60)
                if status==503:wait=max(wait,5)
                retry=(headers or {}).get('Retry-After')
                if retry:
                    try:wait=max(wait,float(retry))
                    except ValueError:
                        try:wait=max(wait,parsedate_to_datetime(retry).timestamp()-self.clock())
                        except (ValueError,TypeError,OverflowError):wait=max(wait,60)
                db.execute('INSERT OR REPLACE INTO providers VALUES(?,?,?,?,?)',
                    (host,row[0],max(row[1],self.clock()+wait),attempts,int(status in (401,403) or row[3])))
        finally:db.close()

    def success(self,url):
        db=self.connect()
        try:
            with db:db.execute('UPDATE providers SET attempts=0 WHERE host=?',(self.key(url),))
        finally:db.close()


BUDGET=ProviderBudget()
