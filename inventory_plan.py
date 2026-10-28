"""Read-only one-time inventory setup estimate. No signing or submission."""
import json
from config import LiveConfig
from wallet import load_wallet
from live import Rpc
from pumpswap import PumpSwap,pda


def plan(adapter):
    s=adapter.snapshot()
    volume=pda([b'user_volume_accumulator',bytes(adapter.user)])
    accounts,_=adapter.accounts([s['user_base'],s['user_quote'],volume])
    sizes=[165,165,90]  # SPL Token account / current pinned accumulator fields
    required=[]
    for label,account,size in zip(['USDC token account','WSOL token account','PumpSwap volume account'],accounts,sizes):
        if account is None:
            rent=adapter.rpc.call('getMinimumBalanceForRentExemption',[size,{'commitment':'confirmed'}])
            required.append({'account':label,'estimated_rent_sol':rent/1e9})
    rent=sum(a['estimated_rent_sol'] for a in required)
    available=max(0,s['native_lamports']/1e9-.005-rent-.00002)
    return {'mode':'plan only; no signatures or orders','missing_accounts':required,
            'estimated_setup_rent_sol':rent,'estimated_setup_rent_usd':rent*s['price'],
            'native_fee_reserve_sol':.005,'native_sol_available_after_estimated_setup':available,
            'potential_usdc_from_one_time_conversion':available*s['price']*(1-s['fee_bps']/10000-.005),
            'note':'Rent may be recoverable on account closure. Actual allocations/rent must be simulated. Conversion is a separate allocation decision and is not executed by this script.'}


if __name__=='__main__':
    try:
        config=LiveConfig.from_env()
        print(json.dumps(plan(PumpSwap(Rpc(config.rpc_url),load_wallet(config))),indent=2))
    except Exception as error:
        print('Inventory planning stopped ('+type(error).__name__+'); no transaction submitted.')
        raise SystemExit(1)
