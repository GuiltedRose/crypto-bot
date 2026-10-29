"""Durable research candidates, not copy-trade approvals or open positions."""
import time
from pump_narratives import valid_mint
from trend_pullback import social_interest


class CandidateWatchlist:
    def __init__(self,db,clock=None,ttl=1800,revisit=60,capacity=100):
        self.db,self.clock,self.ttl,self.revisit,self.capacity=db,clock or time.time,ttl,revisit,capacity
        self.db.execute('''CREATE TABLE IF NOT EXISTS candidate_watchlist(
            mint TEXT PRIMARY KEY,pool TEXT,symbol TEXT,first_seen REAL,last_seen REAL,
            last_checked REAL,expires REAL,reason TEXT,score REAL)''')
        self.db.commit()

    def prune(self):
        with self.db: self.db.execute('DELETE FROM candidate_watchlist WHERE expires<=?',(self.clock(),))

    def due(self,limit=3):
        self.prune()
        rows=self.db.execute('''SELECT mint FROM candidate_watchlist WHERE last_checked<=?
            ORDER BY last_checked ASC,COALESCE(score,-1) DESC,mint LIMIT ?''',(self.clock()-self.revisit,limit)).fetchall()
        return [row[0] for row in rows]

    def remove(self,mint):
        with self.db: self.db.execute('DELETE FROM candidate_watchlist WHERE mint=?',(mint,))

    def failed(self,mint):
        with self.db: self.db.execute('UPDATE candidate_watchlist SET last_checked=? WHERE mint=?',(self.clock(),mint))

    def observe(self,snapshot,result,rules):
        now=self.clock();mint=snapshot.get('mint');self.prune()
        if not valid_mint(mint): return {'retained':False}
        risk=result.get('risk',{})
        hard_reject=(result.get('status') in ('confirmed_buy','unsupported_market') or risk.get('status')=='flagged' or
                     any(any(flag in reason for flag in ('exceeds 10%','exceed 50%','dominates the recent','possible coordination'))
                         for reason in risk.get('reasons',[])))
        if hard_reject:
            self.remove(mint);return {'retained':False}
        previous=self.db.execute('SELECT first_seen,expires FROM candidate_watchlist WHERE mint=?',(mint,)).fetchone()
        try:
            fresh=0<=now-snapshot['observed_at']<=30
            interest=social_interest(snapshot,now,rules)['present']
            demand=snapshot['buy_volume_usd']>=rules.min_buy_volume_usd
        except (KeyError,TypeError,ValueError): fresh=interest=demand=False
        promising=fresh and (result.get('status')=='watch' or (interest and demand))
        if not promising and not previous: return {'retained':False}
        reason=(result.get('signal',{}).get('reason') or result.get('reason') or
                '; '.join(result.get('reasons') or risk.get('reasons') or ['waiting for fresh qualifying data']))[:256]
        score=result.get('scorecard',{}).get('score')
        if type(score) not in (int,float) or not 0<=score<=1: score=None
        symbol=snapshot.get('symbol');symbol=symbol[:40] if isinstance(symbol,str) else None
        first,expires=previous if previous else (now,now+self.ttl)
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO candidate_watchlist VALUES(?,?,?,?,?,?,?,?,?)',
                (mint,snapshot.get('pool'),symbol,first,now,now,expires,reason,score))
            self.db.execute('''DELETE FROM candidate_watchlist WHERE mint NOT IN
                (SELECT mint FROM candidate_watchlist ORDER BY COALESCE(score,-1) DESC,last_seen DESC,mint LIMIT ?)''',(self.capacity,))
        kept=self.db.execute('SELECT 1 FROM candidate_watchlist WHERE mint=?',(mint,)).fetchone() is not None
        return {'retained':kept,'revisit_after_seconds':self.revisit,'expires_at':expires if kept else None}

    def count(self):
        self.prune();return self.db.execute('SELECT COUNT(*) FROM candidate_watchlist').fetchone()[0]


def token_analysis(snapshot,result,rules,now):
    """Only descriptive labels supported by this observation; no fake trends."""
    context=snapshot.get('research_context',{});interest=social_interest(snapshot,now,rules)
    groups=result.get('scorecard',{}).get('groups',{})
    price=groups.get('price_structure',{});metrics=price.get('metrics',{})
    momentum=('Strong' if metrics.get('runup_gain_fraction',0)>=rules.trend_min_gain_fraction else
              'Developing' if price.get('score')==.5 else 'Not established')
    volume=snapshot.get('buy_volume_usd')
    buy_volume=f'${volume:,.0f} over {snapshot.get("buy_volume_window_seconds",0)/60:g}m' if type(volume) in (int,float) else 'Unavailable'
    depth=groups.get('market_depth',{})
    if not depth and type(snapshot.get('liquidity_usd')) in (int,float) and rules.max_position_usd>0:
        depth={'score':min(1,snapshot['liquidity_usd']*rules.max_liquidity_allocation_fraction/rules.max_position_usd)}
    liquidity='Acceptable for configured size' if depth.get('score')==1 else 'Limited' if depth.get('score') is not None else 'Not assessed'
    risk=result.get('risk') or result.get('intent',{}).get('onchain_risk')
    risk_label='Not checked yet'
    if isinstance(risk,dict):
        risk_label='Bounded checks passed' if risk.get('passed') is True else risk.get('status','Unknown').replace('_',' ').capitalize()
    status=result.get('status','unknown')
    decision='WATCH' if status=='watch' else 'WATCH — NOT ELIGIBLE' if result.get('watchlist',{}).get('retained') else 'BOUGHT' if status=='confirmed_buy' else 'SIMULATED' if status=='unsigned_simulation_passed' else 'SKIP'
    reason=(result.get('signal',{}).get('reason') or result.get('reason') or
            '; '.join(result.get('reasons') or (risk or {}).get('reasons') or [status.replace('_',' ')]))
    ids=set()
    for post in context.get('callouts',[]):
        try:
            if post['mint']==snapshot['mint'] and 0<=now-post['published_at']<=900 and 0<=now-post['observed_at']<=120:
                ids.add(post['id'])
        except (KeyError,TypeError): pass
    return {'status':'token_analysis','mint':snapshot.get('mint'),'symbol':snapshot.get('symbol'),
            'momentum':momentum,'buy_volume':buy_volume,'watched_buyers':interest['watched_buy_wallets'],
            'fresh_callouts':len(ids),'fresh_authors':interest['callout_authors'],'social_source_status':context.get('source_status','unknown'),
            'liquidity':liquidity,'onchain_risk':risk_label,'strategy':'Trend Pullback','decision':decision,'reason':reason,
            'profile_selection':result.get('profile_selection'),'entry_profile':result.get('intent',{}).get('entry_profile'),
            'watchlist':result.get('watchlist',{}),'scorecard':result.get('scorecard')}
