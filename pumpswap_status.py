"""Read-only direct PumpSwap readiness check. Never signs or sends."""
import json
from config import LiveConfig
from wallet import load_wallet
from live import Rpc
from pumpswap import PumpSwap
from bot import SOL_MINT


def main():
    config=LiveConfig.from_env()
    adapter=PumpSwap(Rpc(config.rpc_url),load_wallet(config))
    snapshot=adapter.snapshot()
    sol_index=0 if str(snapshot['base'])==SOL_MINT else 1
    balances=snapshot['user_balances']
    print(json.dumps(dict(pool=str(adapter.pool),base=str(snapshot['base']),quote=str(snapshot['quote']),
        reference_sol_usdc_price=snapshot['price'],estimated_pool_fee_bps=snapshot['fee_bps'],
        native_sol=snapshot['native_lamports']/1e9,wrapped_sol=balances[sol_index]/1e9,
        usdc=balances[1-sol_index]/1e6,slot=snapshot['slot'],trades_submitted=0),indent=2))


if __name__=='__main__':
    try:
        main()
    except ValueError as error:
        print(str(error))
        raise SystemExit(1)
    except Exception as error:
        print('PumpSwap readiness failed ('+type(error).__name__+'); no transaction submitted.')
        raise SystemExit(1)
