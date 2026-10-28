"""Bounded public-history backfill for research only; never fills candle gaps."""
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit
from bot import SOL_MINT
from history import fetch_json
from scan_markets import parse_candles


def backfill_one(store,assets,now,fetch=fetch_json):
    candidates=[]
    for asset in assets:
        mint=asset['mint']
        rows=store.db.execute('SELECT timestamp FROM candles WHERE mint=? ORDER BY timestamp',(mint,)).fetchall()
        if not rows:continue
        last=store.db.execute('SELECT attempted,detail FROM backfills WHERE mint=?',(mint,)).fetchone()
        if last:
            retry=86400 if json.loads(last[1]).get('status')=='archived' else 3600
            if now-last[0]<retry:continue
        # Prefer a complete recent page, then the least recently attempted asset.
        candidates.append((len(rows)>=990,-(last[0] if last else 0),asset,rows[0][0]))
    if not candidates:return {'status':'no backfill due'}
    _,_,asset,earliest=max(candidates,key=lambda row:(row[0],row[1],row[2]['symbol']))
    mint=asset['mint']
    detail={'mint':mint,'symbol':asset['symbol'],'status':'unavailable'}
    try:
        path=Path('data/verified_pool_hourly.manifest.json') if mint==SOL_MINT else Path('data/markets')/mint/'provenance.json'
        manifest=json.loads(path.read_text())
        url=manifest['source_url']+'&before_timestamp='+str(earliest)
        parsed=urlsplit(url)
        if parsed.scheme!='https' or parsed.netloc!='api.geckoterminal.com' or not parsed.path.startswith('/api/v2/networks/solana/pools/'):
            raise ValueError('Unapproved historical source')
        payload,raw=fetch(url)
        if mint==SOL_MINT and payload.get('meta',{}).get('quote',{}).get('address')!=manifest['quote_mint']:
            raise ValueError('SOL backfill quote orientation mismatch')
        bars=parse_candles(payload,mint,now)
        if any(bar.timestamp>earliest for bar in bars):
            raise ValueError('Historical pagination moved forward')
        folder=Path('data/research_history')/mint
        folder.mkdir(parents=True,exist_ok=True)
        source=folder/(str(earliest)+'.json')
        source.write_bytes(raw)
        source.with_suffix('.manifest.json').write_text(json.dumps({'url':url,'sha256':hashlib.sha256(raw).hexdigest(),
            'retrieved_at':now,'mint':mint,'verification':'provider pair/schema checks only; research, not fully chain-reconstructed history'}))
        archived=store.archive(mint,bars,now)
        detail.update(status='archived',received_bars=len(bars),continuous_bars=len(archived))
    except Exception as error:
        detail['error_type']=type(error).__name__
        if hasattr(error,'code'):detail['http_status']=error.code
    with store.db:store.db.execute('INSERT OR REPLACE INTO backfills VALUES(?,?,?)',(mint,now,json.dumps(detail)))
    return detail
