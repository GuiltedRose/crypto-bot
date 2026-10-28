"""Local wallet loading and read-only mainnet connection check."""
import json
from pathlib import Path
from config import LiveConfig
from live import Rpc

MAINNET_GENESIS = '5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d'


def load_wallet(config):
    from solders.keypair import Keypair
    try:
        if config.wallet_keypair_path:
            path = Path(config.wallet_keypair_path).expanduser()
            if path.stat().st_mode & 0o077:
                raise ValueError('Keypair file must be owner-only')
            return Keypair.from_bytes(bytes(json.loads(path.read_text())))
        return Keypair.from_base58_string(config.wallet_private_key)
    except Exception:
        raise ValueError('Cannot load wallet: check secret format and keypair file permissions locally') from None


def check_wallet(config, rpc=None):
    wallet = load_wallet(config)
    rpc = rpc or Rpc(config.rpc_url)
    if rpc.call('getGenesisHash', []) != MAINNET_GENESIS:
        raise ValueError('RPC is not Solana mainnet; no trade submitted')
    address = str(wallet.pubkey())
    balance = rpc.call('getBalance', [address, {'commitment': 'confirmed'}])
    return {'wallet': address, 'network': 'mainnet-beta',
            'native_sol': balance['value']/1_000_000_000,
            'slot': balance['context']['slot'], 'trades_submitted': 0}


def main():
    import sys
    try:
        config = LiveConfig.from_env()
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    try:
        result = check_wallet(config)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception:
        # Network exceptions may contain the credential-bearing RPC URL.
        print('Wallet check failed: RPC or SDK error. No trade submitted.', file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
