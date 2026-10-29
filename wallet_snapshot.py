"""Read-only mainnet inventory/rent observation; never signs or submits orders."""
import argparse
import json
from pathlib import Path
import time
from live import Rpc
from pump_narratives import valid_mint
from meme_evidence import number

MAINNET='5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d'
USDC='EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'
TOKEN='TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA'
TOKEN_2022='TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb'


class ReadOnlyRpc(Rpc):
    ALLOWED={'getGenesisHash','getBalance','getTokenAccountsByOwner','getMinimumBalanceForRentExemption'}

    def call(self,method,params):
        if method not in self.ALLOWED:
            raise ValueError('Wallet snapshot RPC is read-only')
        return super().call(method,params)


def observe_inventory(rpc,address,sol_price_usd,now=None):
    number(sol_price_usd,1e-15)
    if not valid_mint(address):
        raise ValueError('Public address and positive SOL valuation required')
    if rpc.call('getGenesisHash',[])!=MAINNET:
        raise ValueError('Wallet inventory requires mainnet')
    native=number(rpc.call('getBalance',[address,{'commitment':'confirmed'}])['value'])/1e9
    accounts=[]
    for program in (TOKEN,TOKEN_2022):
        result=rpc.call('getTokenAccountsByOwner',[address,{'programId':program},{'encoding':'jsonParsed','commitment':'confirmed'}])
        for record in result['value']:
            info=record['account']['data']['parsed']['info']
            if record['account']['owner']!=program or info['owner']!=address:
                raise ValueError('Inventory token-account identity mismatch')
            amount=info['tokenAmount']
            accounts.append({'address':record['pubkey'],'mint':info['mint'],'units':int(amount['amount']),
                             'decimals':amount['decimals'],'token_program':program})
    usdc=sum(row['units']/1e6 for row in accounts if row['mint']==USDC and row['decimals']==6)
    rents={str(size):rpc.call('getMinimumBalanceForRentExemption',[size,{'commitment':'confirmed'}]) for size in (165,170)}
    return {'observed_at':time.time() if now is None else now,'wallet':address,'native_sol':native,'usdc':usdc,
            'sol_price_usd':sol_price_usd,'liquid_equity_usd':native*sol_price_usd+usdc,
            'token_accounts':accounts,'minimum_rent_lamports':rents,'read_only':True,'transactions_submitted':0,
            'valuation_note':'Native SOL and USDC only; other holdings and locked account rent are not silently valued as cash.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path('state/wallet_observation.json'))
    args=parser.parse_args()
    try:
        from config import LiveConfig
        from wallet import load_wallet
        from pump_market import ROOT
        from paper_sources import PublicSources
        config=LiveConfig.from_env()
        wallet=load_wallet(config)
        price=PublicSources().fetch(ROOT+'/sol-price')
        if price.get('stale') is not False or not 0<=time.time()-price['asOfTimestamp']/1000<=120:
            raise ValueError('Fresh SOL valuation required')
        result=observe_inventory(ReadOnlyRpc(config.rpc_url),str(wallet.pubkey()),price['solPrice'])
        args.output.parent.mkdir(parents=True,exist_ok=True)
        temporary=args.output.with_suffix('.tmp')
        temporary.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n');temporary.chmod(0o600)
        temporary.replace(args.output)
        print(json.dumps({'status':'wallet_verified_read_only','wallet_abbreviated':result['wallet'][:4]+'...'+result['wallet'][-4:],
                          'native_sol':result['native_sol'],'usdc':result['usdc'],'liquid_equity_usd':result['liquid_equity_usd'],
                          'minimum_rent_lamports':result['minimum_rent_lamports'],'token_accounts':len(result['token_accounts']),
                          'transactions_submitted':0}),flush=True)
    except Exception as error:
        from urllib.error import HTTPError
        record={'status':'read_only_inventory_unavailable','error_type':type(error).__name__,'credentials_suppressed':True}
        if isinstance(error,HTTPError):record['http_status']=error.code
        print(json.dumps(record),flush=True)
        return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
