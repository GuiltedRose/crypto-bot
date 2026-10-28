"""Read-only keyless route audit: no wallet loading, signatures or sends."""
import argparse
import json
import time
from pathlib import Path
from solders.pubkey import Pubkey
from scan_markets import load_assets
from jupiter import fetch_build,validate_build
from market import USDC_MINT
from bot import SOL_MINT


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wallet',required=True,help='Public wallet address only')
    args=parser.parse_args()
    user=Pubkey.from_string(args.wallet)
    results=[]
    for asset in load_assets():
        document=None
        reverse=None
        if asset['mint']==SOL_MINT:
            results.append({'symbol':asset['symbol'],'status':'direct PumpSwap adapter'})
            continue
        record={'symbol':asset['symbol'],'mint':asset['mint']}
        from venue_policy import entry_allowed
        if not entry_allowed('jupiter'):
            record.update(status='blocked',reason='US venue eligibility not cleared')
            print(json.dumps(record),flush=True)
            results.append(record)
            continue
        try:
            document=fetch_build(user,USDC_MINT,asset['mint'],40000)
            validate_build(document,user,USDC_MINT,asset['mint'],40000)
            # Quote reverse route too; never assume a buy implies an exit.
            reverse=fetch_build(user,asset['mint'],USDC_MINT,int(document['outAmount']))
            validate_build(reverse,user,asset['mint'],USDC_MINT,int(document['outAmount']))
            record.update(status='validated unsigned buy and sell routes',buy_output_units=document['outAmount'],
                          round_trip_usdc_units=reverse['outAmount'])
        except Exception as error:
            if document:
                folder=Path('data/route_audits')
                folder.mkdir(exist_ok=True)
                (folder/(asset['mint']+'.json')).write_text(json.dumps(document))
                if reverse:
                    (folder/(asset['mint']+'-sell.json')).write_text(json.dumps(reverse))
            record.update(status='blocked',error_type=type(error).__name__)
            if isinstance(error,ValueError):
                record['reason']=str(error)
            if hasattr(error,'code'):
                record['http_status']=error.code
        print(json.dumps(record),flush=True)
        results.append(record)
    Path('state/route_audit.json').write_text(json.dumps({'checked_at':int(time.time()),'unsigned_only':True,'routes':results},indent=2))


if __name__=='__main__':
    main()
