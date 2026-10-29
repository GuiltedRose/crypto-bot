"""Human-readable console output; durable status stays structured JSON."""
from collections import Counter
from datetime import datetime


def format_status(record,rules=None):
    clock=datetime.fromtimestamp(record['timestamp']).astimezone().strftime('%H:%M:%S %Z')
    prefix=f"[{clock} {record.get('mode','unknown').upper()}]"
    status=record.get('status','unknown')
    mint=record.get('mint','')
    short=mint[:6]+'…'+mint[-4:] if len(mint)>14 else mint
    if status=='token_analysis': return analysis_summary(record)
    if status=='started':
        return f"{prefix} Started | position: {'open' if record.get('position_open') else 'none'} | pending order: {'yes' if record.get('pending_order') else 'none'}"
    if status=='checking_wallet_and_market': return f'{prefix} Checking wallet and market data…'
    if status=='scanning_candidate': return f"{prefix} Scanning {record['candidate']}/{record['total_candidates']}: {short}"
    if status=='scan_complete':
        return scan_summary(record,clock)
    reasons=list(record.get('reasons',[])) or record.get('risk',{}).get('reasons',[])
    if not reasons and record.get('reason'): reasons=[record['reason']]
    if status=='entry_rejected':
        limit=getattr(rules,'max_candle_range_fraction',.30)*100
        volume=getattr(rules,'min_buy_volume_usd',5000)
        translations={'extreme candle range':f'a 1m candle range exceeded {limit:g}% of its open',
                      'quote moved too far from completed candles':'current price differs over 10% from the last completed close',
                      'recent buy volume below threshold':f'recent buy volume below ${volume:,.0f}'}
        reasons=[translations.get(reason,reason) for reason in reasons]
    if record.get('error_type'): reasons.append(record['error_type'])
    label={'entry_rejected':'SKIP','onchain_risk_rejected':'RISK SKIP','unsupported_market':'UNSUPPORTED',
           'candidate_source_error':'DATA ERROR','watch':'WAIT','holding':'HOLD',
           'confirmed_buy':'BOUGHT','confirmed_sell':'SOLD'}.get(status,status.replace('_',' ').upper())
    if status in ('confirmed_buy','confirmed_sell') and record.get('signature'): reasons.append('signature '+record['signature'])
    if 'pnl_usd' in record: reasons.append(f"net P&L ${record['pnl_usd']:+.2f}")
    detail='; '.join(str(reason) for reason in reasons)
    return f"{prefix} {label}{' '+short if short else ''}{': '+detail if detail else ''}"


def scan_summary(record,clock):
    decisions=record.get('decisions',[])
    counts=Counter(row.get('status','unknown') for row in decisions)
    rejected=[row for row in decisions if row.get('status') in ('entry_rejected','onchain_risk_rejected')]
    categories=Counter()
    for row in rejected:
        reasons=row.get('reasons') or row.get('risk',{}).get('reasons') or ['unspecified entry check']
        found=set()
        for reason in reasons:
            reason=reason.lower()
            if any(part in reason for part in ('extreme candle','wick-dominated','inter-candle price gap')): label='Extreme volatility'
            elif 'quote moved too far' in reason: label='Price deviation'
            elif 'buy volume below' in reason: label='Low buying volume'
            elif ('completed' in reason and ('need ' in reason or 'fewer than' in reason)): label='Insufficient history'
            elif any(part in reason for part in ('history','candle data','candle fields','candle values','candle intervals','timestamp','ohlc','numeric','prices violate','stale candle','stale completed')): label='Missing/invalid data'
            elif row.get('status')=='onchain_risk_rejected': label='On-chain risk checks'
            else: label='Other entry checks'
            found.add(label)
        categories.update(found)  # Count each category once per rejected coin.
    accepted=counts['confirmed_buy']+counts['unsigned_simulation_passed']
    waits=counts['watch']+counts['holding']
    unsupported=len(record.get('skipped_markets',{}))
    errors=len(record.get('source_errors',{}))
    def line(label,value): return f'{label+":":<24}{str(value):>8}'
    lines=['MEME MARKET SCAN','─'*32,f'Updated: {clock}',
           line('Candidates evaluated',len(decisions)),line('Entries accepted',accepted),
           line('Entries rejected',len(rejected)),line('Confirmed buys',counts['confirmed_buy'])]
    if waits: lines.append(line('Waiting',waits))
    other=len(decisions)-accepted-len(rejected)-waits
    if other: lines.append(line('Other outcomes',other))
    if unsupported: lines.append(line('Unsupported skipped',unsupported))
    lines.append(line('Source errors',errors))
    if 'watched_candidates' in record: lines.append(line('Watchlist size',record['watched_candidates']))
    lines+=['','REJECTION SUMMARY']
    labels=['Extreme volatility','Insufficient history','Price deviation','Low buying volume']
    labels+=[label for label in sorted(categories) if label not in labels]
    lines+=[line(label,categories[label]) for label in labels]
    if rejected: lines.append('(Categories may overlap per coin.)')
    pnl=record.get('realized_pnl_usd',0)
    lines+=['',line('Realized P&L',('-' if pnl<0 else '')+f'${abs(pnl):.2f}'),
            line('Live execution','ENABLED' if record.get('execution_enabled') is True else 'DISABLED')]
    if 'next_check_seconds' in record: lines.append(line('Next check',f"{record['next_check_seconds']:g}s"))
    if record.get('mode')=='unsigned_simulation': lines.append('Unsigned simulation; no trades submitted.')
    return '\n'.join(lines)


def analysis_summary(record):
    def safe(value): return ''.join(char for char in str(value) if char.isprintable())[:240]
    mint=record.get('mint','')
    title=safe(record.get('symbol') or (mint[:6]+'…'+mint[-4:] if len(mint)>14 else mint))
    def line(label,value): return f'{label+":":<24}{safe(value)}'
    lines=['TOKEN ANALYSIS: '+title,'─'*40,line('Mint',mint),
        line('Price momentum',record.get('momentum','Unknown')),line('Buy volume',record.get('buy_volume','Unavailable')),
        line('Watched wallets',f"{record.get('watched_buyers',0)} recent buyers"),
        line('Social activity',f"{record.get('fresh_callouts',0)} fresh callouts / {record.get('fresh_authors',0)} authors"),line('Liquidity',record.get('liquidity','Unknown')),
        line('On-chain risk',record.get('onchain_risk','Not checked yet')),'',line('Strategy',record.get('strategy','Unknown')),
        line('Decision',record.get('decision','Unknown')),line('Reason',record.get('reason','Unknown'))]
    if record.get('watchlist',{}).get('retained'):
        lines.append(line('Watchlist',f"Retained; revisit after {record['watchlist']['revisit_after_seconds']}s"))
    score=record.get('scorecard')
    if score:
        value=f"{score['score']*100:.0f}/100" if score.get('score') is not None else 'Incomplete'
        coverage=f"{score['coverage_fraction']*100:.0f}% coverage" if score.get('coverage_fraction') is not None else 'coverage unavailable'
        lines.append(line('Shadow signal score',value+f" ({coverage}; diagnostic only)"))
    return '\n'.join(lines)
