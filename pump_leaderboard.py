"""Top daily Pump leaderboard wallets: bounded discovery, not profit validation."""
import math
import time
from paper_sources import PublicSources
from pump_narratives import valid_mint

LEADERBOARD_URL='https://frontend-api-v3.pump.fun/pnl-leaderboard?period=daily&limit=10'


def eligible_leaderboard_wallets(rows,now):
    if not isinstance(rows,list): return []
    result=[];seen=set();ranks=set()
    for row in sorted((r for r in rows if isinstance(r,dict)),key=lambda r:r.get('rank') if type(r.get('rank')) is int else 999):
        try:
            address=row['wallet'];rank=row['rank']
            if not valid_mint(address) or address in seen or rank in ranks: continue
            if type(rank) is not int or not 1<=rank<=10 or row['period']!='daily' or row['source_url']!=LEADERBOARD_URL: continue
            if type(row['observed_at']) not in (int,float) or not 0<=now-row['observed_at']<=120: continue
            if type(row['refreshed_at']) not in (int,float) or not 0<=now-row['refreshed_at']<=1800: continue
            result.append(dict(row,profitability_verified=False,independent_human_verified=False));seen.add(address);ranks.add(rank)
        except (KeyError,TypeError,ValueError): continue
    return result


def parse_leaderboard(payload,now):
    daily=payload['periodType']=='daily' or (type(payload['periodType']) is int and payload['periodType']==3 and payload.get('periodLabel')=='24h')
    if not daily or not isinstance(payload['entries'],list): raise ValueError('Daily leaderboard schema required')
    result=[];rejected=0
    for row in payload['entries'][:100]:
        try:
            refreshed=row['lastRefreshedAtMs']
            if type(refreshed) not in (float,int) or not math.isfinite(refreshed): raise ValueError('Invalid rank refresh')
            item={'wallet':row['walletAddress'],'rank':row['rank'],'period':'daily','source_url':LEADERBOARD_URL,
                  'observed_at':now,'refreshed_at':refreshed/1000,'profitability_verified':False,'independent_human_verified':False}
            for target,field in [('provider_pnl_usd','pnlUsd'),('provider_realized_pnl_usd','realizedPnlUsd'),('provider_unrealized_pnl_usd','unrealizedPnlUsd')]:
                value=row.get(field)
                if value is not None and (type(value) not in (int,float) or not math.isfinite(value)): raise ValueError('Invalid PnL metric')
                item[target]=value
            result.append(item)
        except (KeyError,ValueError,TypeError): rejected+=1
    eligible=eligible_leaderboard_wallets(result,now)
    return eligible,rejected+len(result)-len(eligible)


def effective_wallets(pinned,leaders,now):
    discovered=eligible_leaderboard_wallets(leaders,now)
    return list(dict.fromkeys(list(pinned)+[row['wallet'] for row in discovered]))[:32]


class Leaderboard:
    def __init__(self,fetch=None): self.fetch=fetch or PublicSources().fetch
    def poll(self,now=None):
        payload=self.fetch(LEADERBOARD_URL);now=time.time() if now is None else now
        wallets,rejected=parse_leaderboard(payload,now)
        return {'status':'partial_schema' if rejected else 'ok','leaderboard_wallets':wallets,
                'period':'daily','period_label':payload.get('periodLabel'),'source_url':LEADERBOARD_URL,
                'rejected_records':rejected,'limitation':'Provider PnL can include unrealized positions; rank is not verified profit or human identity'}
