"""Public wallet token discovery. Holdings/receipts are not buy or profit proofs."""
import argparse
import json
from pathlib import Path
import time
from solders.pubkey import Pubkey
from live import Rpc
from pumpswap import TOKEN


def inspect_wallet(rpc,address,recent_transactions=8):
    Pubkey.from_string(address)
    accounts=rpc.call('getTokenAccountsByOwner',[address,{'programId':str(TOKEN)},
        {'encoding':'jsonParsed','commitment':'confirmed'}])['value']
    candidates={}
    for account in accounts:
        info=account['account']['data']['parsed']['info']
        amount=info['tokenAmount']
        if int(amount['amount'])<=0:
            continue
        mint=info['mint']
        Pubkey.from_string(mint)
        candidates.setdefault(mint,{'mint':mint,'sources':[]})['sources'].append('current standard-SPL holding')
    signatures=rpc.call('getSignaturesForAddress',[address,{'limit':recent_transactions,'commitment':'confirmed'}])
    examined=0
    for record in signatures:
        if record.get('err') is not None:
            continue
        tx=rpc.call('getTransaction',[record['signature'],{'encoding':'json','commitment':'confirmed','maxSupportedTransactionVersion':1}])
        if not tx or not tx.get('meta') or tx['meta']['err'] is not None:
            continue
        examined+=1
        meta=tx['meta']
        before={(b['accountIndex'],b['mint']):int(b['uiTokenAmount']['amount'])
                for b in meta.get('preTokenBalances',[]) if b.get('owner')==address}
        for balance in meta.get('postTokenBalances',[]):
            if balance.get('owner')!=address:
                continue
            mint=balance['mint'];key=(balance['accountIndex'],mint)
            change=int(balance['uiTokenAmount']['amount'])-before.get(key,0)
            if change<=0:
                continue
            candidate=candidates.setdefault(mint,{'mint':mint,'sources':[]})
            candidate['sources'].append('recent token receipt; trade vs transfer unclassified')
            candidate.setdefault('receipt_signatures',[]).append(record['signature'])
        time.sleep(.3)
    return {'address':address,'checked_at':int(time.time()),'transactions_examined':examined,
            'candidates':list(candidates.values()),'automatic_trade_approval':False,
            'limitations':'Standard SPL accounts only. Current holdings and recent receipts do not establish entry price, realized P&L, buy intent, trader ranking or safety. Candidates require independent identity/age/liquidity/risk screening.'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('address')
    parser.add_argument('--transactions',type=int,default=8)
    args=parser.parse_args()
    if not 0<=args.transactions<=20:
        parser.error('Use 0 through 20 transactions per query')
    try:
        report=inspect_wallet(Rpc('https://api.mainnet-beta.solana.com'),args.address,args.transactions)
        Path('data/discovery').mkdir(parents=True,exist_ok=True)
        Path('data/discovery/'+args.address+'.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps({'address':args.address,'candidate_mints':len(report['candidates']),
                          'transactions_examined':report['transactions_examined'],
                          'automatic_trade_approval':False,
                          'report':'data/discovery/'+args.address+'.json'},indent=2))
    except Exception as error:
        print('Public profile discovery failed ('+type(error).__name__+'); no trade submitted.')
        raise SystemExit(1)
