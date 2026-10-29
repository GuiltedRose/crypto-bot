"""Paper hypothesis: popular uptrend, then early controlled decline; no ATH rule."""
from candle_quality import CandleQualityPolicy
from meme_evidence import number


def social_interest(snapshot,now,rules):
    context=snapshot.get('research_context',{})
    authors,ids=set(),set()
    views,watched=0,False
    for row in context.get('callouts',[]):
        try:
            if row['mint']!=snapshot['mint'] or not 0<=now-number(row['published_at'])<=900 or not 0<=now-number(row['observed_at'])<=120:
                continue
            if row['id'] in ids:
                continue
            ids.add(row['id'])
            authors.add(row['author_wallet'])
            views+=number(row.get('views') or 0)
            watched=watched or row.get('watched_author') is True
        except (KeyError,ValueError,TypeError):
            continue
    wallets=set()
    for group in context.get('wallet_signals',[]):
        if group.get('mint')!=snapshot['mint']:
            continue
        for row in group.get('observations',[]):
            try:
                if row['mint']==snapshot['mint'] and row['action']=='buy' and 0<=now-number(row['observed_at'])<=300:
                    wallets.add(row['wallet'])
            except (KeyError,ValueError,TypeError):
                continue
    return {'present':bool(wallets or watched or authors and views>=rules.social_min_views),
            'callout_authors':len(authors),'reported_views':views,'watched_buy_wallets':len(wallets),
            'independent_human_verified':False}


def assess_trend_pullback(snapshot,now,rules):
    try:
        history=snapshot.get('candle_history')
        if not isinstance(history,dict) or not isinstance(history.get('candles'),list):
            upstream=snapshot.get('source_errors',{}).get('candles',{})
            cause=upstream.get('category') or upstream.get('error_type') if isinstance(upstream,dict) else None
            return {'action':'reject','reasons':['one-minute candle data unavailable'+(' ('+str(cause)+')' if cause else '')]}
        rows=history['candles'][-12:]
        if len(rows)<rules.trend_min_bars:
            return {'action':'reject','reasons':[f'only {len(rows)} completed 1m candles available; need {rules.trend_min_bars}']}
        chart=CandleQualityPolicy(require_uptrend=False,min_bars=len(rows),
                                  max_bar_range_fraction=rules.max_candle_range_fraction,
                                  max_wicky_bar_fraction=rules.max_wicky_bar_fraction).assess(snapshot,now)
        if not chart['passed']:
            return {'action':'reject','reasons':chart['reasons']}
        social=social_interest(snapshot,now,rules)
        if not social['present']:
            return {'action':'reject','reasons':['fresh callout interest or watched-wallet buying required']}
        peak_index=max(range(len(rows)),key=lambda index:rows[index]['high'])
        run_bars=min(6,len(rows)-1)
        if peak_index<run_bars-1:
            return {'action':'watch','reason':'insufficient run-up before local peak','metrics':social}
        run=rows[peak_index-run_bars+1:peak_index+1]
        rising=sum(b['close']>a['close'] for a,b in zip(run,run[1:]))/(len(run)-1)
        gain=run[-1]['close']/run[0]['close']-1
        if gain<rules.trend_min_gain_fraction or rising<rules.min_rising_close_fraction or run[-1]['low']<=run[0]['low']:
            return {'action':'watch','reason':'local peak lacks an established upward trend','metrics':social}
        decline_bars=len(rows)-1-peak_index
        peak=rows[peak_index]['high']
        price=number(snapshot['price_usd'],1e-15)
        pullback=1-price/peak
        metrics=dict(social,peak_price=peak,pullback_fraction=pullback,runup_gain_fraction=gain,
                     rising_close_fraction=rising,decline_bars=decline_bars,completed_bars=len(rows),runup_bars=len(run),
                     evidence_window="short_launch" if len(rows)<8 else "longer_history")
        if decline_bars==0 or pullback<rules.trend_pullback_min_fraction:
            return {'action':'watch','reason':'trending candidate; waiting for an early pullback','metrics':metrics}
        if decline_bars>3 or pullback>rules.trend_pullback_max_fraction:
            return {'action':'reject','reasons':['decline is too deep or prolonged for an early pullback'],'metrics':metrics}
        if rows[-1]['close']>=rows[-2]['close']:
            return {'action':'watch','reason':'waiting for a completed declining candle','metrics':metrics}
        return {'action':'buy','reason':'popular uptrend entering controlled pullback','metrics':metrics}
    except (KeyError,ValueError,TypeError,ZeroDivisionError,IndexError):
        return {'action':'reject','reasons':['invalid trend/pullback candle values']}
