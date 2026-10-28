"""Local live configuration. Never print the configuration or wallet secret."""
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse


def load_env(path='.env'):
    """Read literal KEY=VALUE settings without shell expansion or execution."""
    path = Path(path)
    if not path.exists():
        return
    if path.stat().st_mode & 0o077:
        raise ValueError('.env must be accessible only to its owner (chmod 600 .env)')
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        key, separator, value = line.partition('=')
        key, value = key.strip(), value.strip()
        if not separator or not key.isidentifier():
            raise ValueError('Invalid .env assignment')
        if value[:1] in ('"', "'"):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError('Unterminated .env value')
            value = value[1:-1]
        os.environ.setdefault(key, value)


@dataclass(frozen=True)
class LiveConfig:
    rpc_url: str = field(repr=False)
    wallet_keypair_path: str = field(default='', repr=False)
    wallet_private_key: str = field(default='', repr=False)

    @classmethod
    def from_env(cls, path='.env'):
        load_env(path)
        rpc = os.environ.get('SOLANA_RPC_URL', '')
        parsed = urlparse(rpc)
        if parsed.scheme != 'https' or not parsed.hostname:
            raise ValueError('Set SOLANA_RPC_URL to an HTTPS RPC endpoint')
        keyfile = os.environ.get('SOLANA_KEYPAIR_PATH', '')
        secret = os.environ.get('SOLANA_PRIVATE_KEY', '')
        if bool(keyfile) == bool(secret):
            raise ValueError('Set exactly one of SOLANA_KEYPAIR_PATH or SOLANA_PRIVATE_KEY locally')
        return cls(rpc, keyfile, secret)
