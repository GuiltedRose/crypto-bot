"""Anonymous Pump hot/movers discovery, never an entry approval.

The trending route currently reports board='movers'; keep that served label.
Other chains/programs, old reports, and ambiguous identities are excluded.
"""
import time
from meme_evidence import number
from paper_sources import PublicSources
from pump_narratives import valid_mint

HOT_TOKENS_URL='https://advanced-indexer.pump.fun/boards/trending?limit=20'


def parse_hot_tokens(payload,now):
    board=payload['board']
    if board not in ('trending','movers') or payload['version']!=0:
        raise ValueError('Unknown hot-board version or kind')
    stamp=number(payload['serverTs'])/1000
    if not 0<=now-stamp<=120: raise ValueError('Hot-board timestamp is stale or in the future')
    if not isinstance(payload['entries'],list): raise ValueError('Hot-board entries required')
    result=[];seen=set();rejected=0
    for rank,row in enumerate(payload['entries'][:200],1):
        try:
            if row['c']!='solana:mainnet' or row['pg']!='pump': continue
            mint=row['m']
            if not valid_mint(mint): raise ValueError('Hot token mint invalid')
            if mint in seen: continue
            seen.add(mint)
            symbol=row.get('t');symbol=symbol[:80] if isinstance(symbol,str) else None
            kol=row.get('kol')
            if kol is not None: kol=number(kol)
            result.append({'mint':mint,'symbol':symbol,'rank':rank,'board':board,'source_url':HOT_TOKENS_URL,
                'source_kind':'pump_provider_hot_board','server_at':stamp,'observed_at':now,
                'provider_reported_kol_count':kol,'kol_identities_verified':False,'automatic_trade_approval':False})
        except (TypeError,KeyError,ValueError): rejected+=1
    return result,rejected


def eligible_hot_tokens(rows,now):
    result=[];seen=set()
    if not isinstance(rows,list): return result
    for row in rows:
        try:
            mint=row['mint']
            if not valid_mint(mint) or mint in seen or row['source_url']!=HOT_TOKENS_URL: continue
            if not 0<=now-number(row['observed_at'])<=120 or not 0<=now-number(row['server_at'])<=120: continue
            if row['board'] not in ('movers','trending') or type(row['rank']) is not int or not 1<=row['rank']<=200: continue
            result.append(dict(row,kol_identities_verified=False,automatic_trade_approval=False));seen.add(mint)
        except (TypeError,KeyError,ValueError): continue
    return result


class HotTokens:
    def __init__(self,fetch=None): self.fetch=fetch or PublicSources().fetch
    def poll(self,now=None):
        payload=self.fetch(HOT_TOKENS_URL)
        now=time.time() if now is None else now
        rows,rejected=parse_hot_tokens(payload,now)
        return {'status':'partial_schema' if rejected else 'ok','hot_tokens':rows,'board':payload['board'],
                'observed_at':now,'source_url':HOT_TOKENS_URL,'rejected_records':rejected,
                'limitation':'Provider-selected popularity, not independent buyer quality, profitability or bundle safety'}
